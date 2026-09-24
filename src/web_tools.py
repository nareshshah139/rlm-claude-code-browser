"""
Browser-use layer for RLM.

Adds a web "lane" to the RLM REPL so that recursive decomposition can start
from live web pages instead of only from files already in context.

Three ways to get a page, fastest first:

1. ``http``     - plain parallel HTTP fetch (httpx). Milliseconds per page.
2. ``headless`` - parallel headless Chromium via Playwright, one isolated
                  browser context per page. Used for JavaScript-rendered pages
                  or when the plain fetch looks empty / blocked.
3. ``cdp``      - attach to the user's own, already logged-in Chrome over the
                  Chrome DevTools Protocol (``RLM_BROWSER_CDP_URL``). Opens
                  background tabs in parallel, so logged-in pages do not have
                  to be driven one at a time through the screen.

Anything none of these can reach (SSO walls, CAPTCHAs, sites that must be
clicked through by a human-supervised agent) is put on the ``chrome_queue``.
The Claude Code agent works that queue serially with its own browser tools
(Claude in Chrome / Playwright MCP) and hands text back with ``ingest_page``.

Everything that is fetched lands in two REPL-visible dicts:

- ``web``      url -> extracted text  (feed straight into search/map_reduce/llm_batch)
- ``web_meta`` url -> metadata        (status, title, links, via, error, timing)

Safety: http(s) only; private / loopback / link-local addresses are refused
unless ``allow_private`` is set; optional domain allow/deny lists; hard caps on
pages per call, characters per page and wall-clock per page.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import socket
import threading
import time
from collections.abc import Callable, Coroutine, Iterable
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Literal, TypeVar
from urllib.parse import urldefrag, urljoin, urlparse

RenderMode = Literal["auto", "never", "always"]
T = TypeVar("T")

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0 Safari/537.36 rlm-claude-code-browser/0.1"
)

# Phrases that usually mean "this page needs JavaScript to show anything".
_JS_REQUIRED_HINTS = (
    "enable javascript",
    "javascript is required",
    "javascript is disabled",
    "you need to enable javascript",
    "please turn on javascript",
    "requires javascript",
)

# Phrases / paths that usually mean "you have to be logged in".
_LOGIN_HINTS = ("sign in", "log in", "login", "sso", "single sign-on", "authenticate")
_LOGIN_PATH_RE = re.compile(r"/(login|signin|sign-in|sso|auth|oauth|account/login)\b", re.I)


def _proxy_bypass() -> str:
    """Chromium bypass list built from NO_PROXY (loopback always bypassed)."""
    raw = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    rules = ["localhost", "127.0.0.1", "[::1]"]
    for entry in (e.strip() for e in raw.split(",")):
        if not entry or entry in rules:
            continue
        rules.append("*" + entry if entry.startswith(".") else entry)
    return ",".join(rules)


class WebSecurityError(Exception):
    """Raised when a URL is refused by the web safety policy."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def _default_executable() -> str | None:
    """Pick a Chromium binary if the environment pins one."""
    explicit = os.environ.get("RLM_BROWSER_EXECUTABLE")
    if explicit:
        return explicit
    return None


@dataclass
class BrowserConfig:
    """Settings for the web lane. Every field can be set from the environment."""

    enabled: bool = True
    max_concurrency: int = 8
    timeout_s: float = 20.0
    max_chars: int = 200_000
    max_pages_per_call: int = 200
    render: RenderMode = "auto"
    min_text_chars: int = 400  # below this a plain fetch counts as "looks empty"
    allow_private: bool = False
    allowed_domains: list[str] = field(default_factory=list)
    blocked_domains: list[str] = field(default_factory=list)
    cdp_url: str | None = None
    executable_path: str | None = None
    proxy: str | None = None
    user_agent: str = DEFAULT_USER_AGENT
    block_media: bool = True  # skip images/fonts/media in headless mode for speed

    @classmethod
    def from_env(cls) -> BrowserConfig:
        render = os.environ.get("RLM_BROWSER_RENDER", "auto").lower()
        if render not in ("auto", "never", "always"):
            render = "auto"
        proxy = (
            os.environ.get("RLM_BROWSER_PROXY")
            or os.environ.get("HTTPS_PROXY")
            or os.environ.get("https_proxy")
            or None
        )
        return cls(
            enabled=_env_bool("RLM_BROWSER", True),
            max_concurrency=int(os.environ.get("RLM_BROWSER_CONCURRENCY", "8")),
            timeout_s=float(os.environ.get("RLM_BROWSER_TIMEOUT", "20")),
            max_chars=int(os.environ.get("RLM_BROWSER_MAX_CHARS", "200000")),
            max_pages_per_call=int(os.environ.get("RLM_BROWSER_MAX_PAGES", "200")),
            render=render,  # type: ignore[arg-type]
            allow_private=_env_bool("RLM_BROWSER_ALLOW_PRIVATE", False),
            allowed_domains=_env_list("RLM_BROWSER_ALLOWED_DOMAINS"),
            blocked_domains=_env_list("RLM_BROWSER_BLOCKED_DOMAINS"),
            cdp_url=os.environ.get("RLM_BROWSER_CDP_URL") or None,
            executable_path=_default_executable(),
            proxy=proxy,
        )


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class WebPage:
    """One fetched page."""

    url: str
    final_url: str = ""
    status: int | None = None
    title: str = ""
    text: str = ""
    links: list[str] = field(default_factory=list)
    via: str = ""  # http | headless | cdp | ingested
    error: str | None = None
    elapsed_ms: float = 0.0
    truncated: bool = False
    needs_login: bool = False

    @property
    def ok(self) -> bool:
        return (
            self.error is None
            and self.status is not None
            and self.status < 400
            and not self.needs_login
        )

    def meta(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("text")
        d["ok"] = self.ok
        d["chars"] = len(self.text)
        d["links"] = self.links[:200]
        return d


# ---------------------------------------------------------------------------
# HTML -> text
# ---------------------------------------------------------------------------

_SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "iframe", "canvas", "head"}
_BLOCK_TAGS = {
    "p", "div", "section", "article", "header", "footer", "main", "aside", "nav",
    "li", "ul", "ol", "table", "tr", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
    "pre", "blockquote", "form", "dd", "dt", "figcaption",
}  # fmt: skip


class _TextExtractor(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.parts: list[str] = []
        self.links: list[str] = []
        self.title = ""
        self._skip_depth = 0
        self._in_title = False
        self._heading: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
            return
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "a":
            href = dict(attrs).get("href")
            if href and not href.startswith(("javascript:", "mailto:", "tel:", "#")):
                self.links.append(urldefrag(urljoin(self.base_url, href))[0])
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag in {"h1", "h2", "h3", "h4"}:
            self.parts.append("#" * int(tag[1]) + " ")
            self._heading = tag
        if tag in {"td", "th"}:
            self.parts.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
            return
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if self._skip_depth:
            return
        if data.strip():
            self.parts.append(data)

    def result(self) -> tuple[str, str, list[str]]:
        raw = "".join(self.parts)
        raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
        raw = re.sub(r" *\n *", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw).strip()
        seen: set[str] = set()
        links = [u for u in self.links if u.startswith("http") and not (u in seen or seen.add(u))]
        return self.title.strip(), raw, links


def html_to_text(html: str, base_url: str = "") -> tuple[str, str, list[str]]:
    """Return (title, text, links) for an HTML document."""
    parser = _TextExtractor(base_url)
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # malformed HTML: keep whatever was parsed
        pass
    return parser.result()


# ---------------------------------------------------------------------------
# URL policy
# ---------------------------------------------------------------------------


def _host_matches(host: str, patterns: Iterable[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == p or host.endswith("." + p) for p in patterns)


def check_url(url: str, config: BrowserConfig) -> str:
    """Validate a URL against the policy. Returns the normalized URL."""
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise WebSecurityError(f"Only http(s) URLs are allowed: {url!r}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise WebSecurityError(f"URL has no host: {url!r}")
    if config.blocked_domains and _host_matches(host, config.blocked_domains):
        raise WebSecurityError(f"Domain is blocked: {host}")
    if config.allowed_domains and not _host_matches(host, config.allowed_domains):
        raise WebSecurityError(f"Domain not in allow-list: {host}")
    if not config.allow_private:
        if host in ("localhost", "localhost.localdomain") or host.endswith(".local"):
            raise WebSecurityError(f"Private host refused: {host}")
        addrs: list[str] = []
        try:
            ipaddress.ip_address(host)
            addrs = [host]
        except ValueError:
            try:
                addrs = [ai[4][0] for ai in socket.getaddrinfo(host, None)]
            except OSError:
                addrs = []  # cannot resolve locally (e.g. behind a proxy): allow
        for a in addrs:
            ip = ipaddress.ip_address(a.split("%")[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise WebSecurityError(f"Private address refused: {host} -> {a}")
    return urldefrag(url)[0]


def _looks_like_login(page: WebPage) -> bool:
    if page.status in (401, 403, 407):
        return True
    final = page.final_url or page.url
    if final != page.url and _LOGIN_PATH_RE.search(urlparse(final).path or ""):
        return True
    if len(page.text) < 1500:
        low = (page.title + " " + page.text[:1500]).lower()
        if "password" in low and any(h in low for h in _LOGIN_HINTS):
            return True
    return False


def _looks_empty(page: WebPage, config: BrowserConfig) -> bool:
    if page.error or (page.status and page.status >= 400):
        return True
    if len(page.text) < config.min_text_chars:
        return True
    low = page.text[:3000].lower()
    return any(h in low for h in _JS_REQUIRED_HINTS)


# ---------------------------------------------------------------------------
# Async runner: lets sync REPL code call async fetchers even when the caller
# is itself running inside an event loop (the orchestrator is async).
# ---------------------------------------------------------------------------


class _LoopThread:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, name="rlm-web-loop", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run(self, coro: Coroutine[Any, Any, T], timeout: float | None = None) -> T:
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return fut.result(timeout)

    def stop(self) -> None:
        async def _drain() -> None:
            me = asyncio.current_task()
            tasks = [t for t in asyncio.all_tasks() if t is not me]
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.sleep(0.05)  # let subprocess transports close

        try:
            self.run(_drain(), timeout=10)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        # The loop is deliberately left unclosed: Playwright's driver transport is
        # finalized by the GC later and would otherwise hit "Event loop is closed".


# ---------------------------------------------------------------------------
# Toolkit
# ---------------------------------------------------------------------------


class WebToolkit:
    """Parallel web access for the RLM REPL."""

    def __init__(self, config: BrowserConfig | None = None):
        self.config = config or BrowserConfig.from_env()
        self.pages: dict[str, WebPage] = {}
        self.chrome_queue: list[dict[str, str]] = []
        self._runner: _LoopThread | None = None
        self._pw: Any = None
        self._browser: Any = None
        self._cdp_browser: Any = None
        self._lock = threading.Lock()
        self.stats = {"http": 0, "headless": 0, "cdp": 0, "ingested": 0, "errors": 0}

    # -- lifecycle ---------------------------------------------------------

    def _loop(self) -> _LoopThread:
        with self._lock:
            if self._runner is None:
                self._runner = _LoopThread()
            return self._runner

    def _run(self, coro: Coroutine[Any, Any, T], n_pages: int = 1) -> T:
        budget = self.config.timeout_s * (1 + n_pages / max(1, self.config.max_concurrency)) + 30
        return self._loop().run(coro, timeout=budget)

    def close(self) -> None:
        if self._runner is None:
            return

        async def _shutdown() -> None:
            for b in (self._browser, self._cdp_browser):
                if b is not None:
                    try:
                        await b.close()
                    except Exception:
                        pass
            if self._pw is not None:
                try:
                    await self._pw.stop()
                except Exception:
                    pass

        try:
            self._runner.run(_shutdown(), timeout=20)
        except Exception:
            pass
        self._runner.stop()
        self._runner = None
        self._browser = self._cdp_browser = self._pw = None

    def __del__(self) -> None:  # pragma: no cover - best effort
        try:
            self.close()
        except Exception:
            pass

    # -- public API (sync, REPL-friendly) ---------------------------------

    def fetch(
        self,
        urls: str | Iterable[str],
        render: RenderMode | bool | None = None,
        concurrency: int | None = None,
        selector: str | None = None,
        use_cdp: bool | None = None,
    ) -> dict[str, WebPage]:
        """Fetch many URLs in parallel. Returns url -> WebPage."""
        url_list = [urls] if isinstance(urls, str) else list(urls)
        url_list = list(dict.fromkeys(u.strip() for u in url_list if u and u.strip()))
        if len(url_list) > self.config.max_pages_per_call:
            raise WebSecurityError(
                f"{len(url_list)} URLs requested; limit is {self.config.max_pages_per_call} "
                "per call (RLM_BROWSER_MAX_PAGES)"
            )
        if render is True:
            render = "always"
        elif render is False:
            render = "never"
        mode: RenderMode = render or self.config.render  # type: ignore[assignment]
        if use_cdp and not self.config.cdp_url:
            raise WebSecurityError("use_cdp=True but RLM_BROWSER_CDP_URL is not set")
        conc = max(1, min(concurrency or self.config.max_concurrency, 32))
        results = self._run(
            self._fetch_all(
                url_list,
                mode,
                conc,
                selector,
                force_cdp=bool(use_cdp),
                allow_cdp=use_cdp is not False,
            ),  # fmt: skip
            n_pages=len(url_list),
        )
        for page in results.values():
            self._record(page)
        return results

    def browse(
        self,
        url: str,
        steps: list[dict[str, Any]] | None = None,
        use_cdp: bool | None = None,
        screenshot: str | None = None,
    ) -> WebPage:
        """Open one page in a real (headless or CDP) browser and run scripted steps.

        Steps are dicts, run in order:
            {"click": "css"}            {"fill": ["css", "text"]}
            {"press": ["css", "Enter"]} {"wait": "css"} or {"wait": 1500}
            {"scroll": 3}               {"select": ["css", "value"]}
            {"extract": "css"}          # final text = inner text of this selector
        """
        safe = check_url(url, self.config)
        page = self._run(self._browse(safe, steps or [], use_cdp, screenshot))
        self._record(page)
        return page

    def crawl(
        self,
        start_url: str,
        max_pages: int = 20,
        same_domain: bool = True,
        match: str | None = None,
        depth: int = 2,
        render: RenderMode | bool | None = None,
    ) -> dict[str, WebPage]:
        """Breadth-first crawl, fetching each frontier level in parallel."""
        max_pages = min(max_pages, self.config.max_pages_per_call)
        start = check_url(start_url, self.config)
        root_host = urlparse(start).hostname or ""
        pattern = re.compile(match) if match else None
        seen: set[str] = set()
        out: dict[str, WebPage] = {}
        frontier = [start]
        for _level in range(depth + 1):
            batch = [u for u in frontier if u not in seen][: max_pages - len(out)]
            if not batch:
                break
            seen.update(batch)
            got = self.fetch(batch, render=render)
            out.update(got)
            if len(out) >= max_pages:
                break
            nxt: list[str] = []
            for p in got.values():
                for link in p.links:
                    host = urlparse(link).hostname or ""
                    if same_domain and host != root_host:
                        continue
                    if pattern and not pattern.search(link):
                        continue
                    if link not in seen:
                        nxt.append(link)
            frontier = list(dict.fromkeys(nxt))
        return out

    def ingest(self, url: str, text: str, title: str = "", via: str = "ingested") -> WebPage:
        """Add text that was obtained elsewhere (e.g. Claude in Chrome)."""
        text = text or ""
        truncated = len(text) > self.config.max_chars
        page = WebPage(
            url=url,
            final_url=url,
            status=200,
            title=title,
            text=text[: self.config.max_chars],
            via=via,
            truncated=truncated,
        )
        self._record(page)
        self.chrome_queue = [q for q in self.chrome_queue if q["url"] != url]
        return page

    def queue_for_chrome(self, urls: str | Iterable[str], reason: str = "") -> list[dict[str, str]]:
        """Mark URLs that need the user's own, supervised browser."""
        for u in [urls] if isinstance(urls, str) else urls:
            if not any(q["url"] == u for q in self.chrome_queue):
                self.chrome_queue.append({"url": u, "reason": reason or "manual"})
        return self.chrome_queue

    # -- persistence (used by the CLI and by Claude Code between steps) ----

    def save(self, out_dir: str | Path) -> Path:
        """Write pages as markdown files + an index.json + chrome_queue.json."""
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        index = []
        for url, page in self.pages.items():
            slug = re.sub(r"[^a-zA-Z0-9]+", "-", urlparse(url).netloc + urlparse(url).path)
            digest = hashlib.sha1(url.encode()).hexdigest()[:8]
            name = f"{slug.strip('-')[:70] or 'page'}-{digest}.md"
            keep = bool(page.text) and page.ok  # no 404 bodies or login walls
            if keep:
                header = f"# {page.title or url}\n\nSource: {page.final_url or url}\n\n"
                (out / name).write_text(header + page.text, encoding="utf-8")
            entry = page.meta()
            entry["file"] = name if keep else None
            index.append(entry)
        (out / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
        (out / "chrome_queue.json").write_text(
            json.dumps(self.chrome_queue, indent=2), encoding="utf-8"
        )
        return out

    def load(self, out_dir: str | Path) -> dict[str, WebPage]:
        """Load a directory written by save() (plus any files ingested since)."""
        out = Path(out_dir)
        index = json.loads((out / "index.json").read_text(encoding="utf-8"))
        loaded: dict[str, WebPage] = {}
        for e in index:
            text = ""
            if e.get("file") and (out / e["file"]).exists():
                body = (out / e["file"]).read_text(encoding="utf-8")
                text = body.split("\n\n", 2)[2] if body.count("\n\n") >= 2 else body
            page = WebPage(
                url=e["url"],
                final_url=e.get("final_url", ""),
                status=e.get("status"),
                title=e.get("title", ""),
                text=text,
                links=e.get("links", []),
                via=e.get("via", ""),
                error=e.get("error"),
                elapsed_ms=e.get("elapsed_ms", 0.0),
                truncated=e.get("truncated", False),
                needs_login=e.get("needs_login", False),
            )
            self.pages[page.url] = page
            loaded[page.url] = page
        q = out / "chrome_queue.json"
        if q.exists():
            self.chrome_queue = json.loads(q.read_text(encoding="utf-8"))
        return loaded

    # -- internals ---------------------------------------------------------

    def _record(self, page: WebPage) -> None:
        self.pages[page.url] = page
        if page.via in self.stats:
            self.stats[page.via] += 1
        if page.error:
            self.stats["errors"] += 1
        if page.needs_login:
            self.queue_for_chrome(page.url, "login required")

    def _finish(self, page: WebPage, html: str, t0: float) -> WebPage:
        title, text, links = html_to_text(html, page.final_url or page.url)
        page.title = page.title or title
        if not page.text:
            page.text = text
        page.links = links
        if len(page.text) > self.config.max_chars:
            page.text = page.text[: self.config.max_chars]
            page.truncated = True
        page.elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
        page.needs_login = _looks_like_login(page)
        return page

    async def _fetch_all(
        self,
        urls: list[str],
        mode: RenderMode,
        conc: int,
        selector: str | None,
        force_cdp: bool,
        allow_cdp: bool = True,
    ) -> dict[str, WebPage]:
        results: dict[str, WebPage] = {}
        checked: list[str] = []
        for u in urls:
            try:
                checked.append(check_url(u, self.config))
            except WebSecurityError as e:
                results[u] = WebPage(url=u, error=f"refused: {e}", via="policy")

        need_headless: list[str] = []
        need_cdp: list[str] = []
        if force_cdp:
            need_cdp = checked
        elif mode == "always":
            need_headless = checked
        else:
            fetched = await self._http_many(checked, conc)
            for u, p in fetched.items():
                results[u] = p
                if mode != "auto":
                    continue
                if p.needs_login:
                    if self.config.cdp_url and allow_cdp:
                        need_cdp.append(u)  # the user's logged-in Chrome may get through
                    elif p.status == 403:
                        need_headless.append(u)  # often bot-blocking, not a real login wall
                elif _looks_empty(p, self.config):
                    need_headless.append(u)

        for batch, cdp in ((need_headless, False), (need_cdp, True)):
            if not batch:
                continue
            rendered = await self._browser_many(batch, conc, selector, cdp)
            for u, p in rendered.items():
                prev = results.get(u)
                # keep the better of the two attempts
                if prev is None or (p.error is None and len(p.text) >= len(prev.text)):
                    results[u] = p
        return {u: results[u] for u in urls if u in results}

    async def _http_many(self, urls: list[str], conc: int) -> dict[str, WebPage]:
        import httpx

        sem = asyncio.Semaphore(conc)
        headers = {"User-Agent": self.config.user_agent, "Accept-Language": "en;q=0.9"}
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=self.config.timeout_s,
            headers=headers,
            trust_env=True,
        ) as client:

            async def one(u: str) -> WebPage:
                async with sem:
                    t0 = time.perf_counter()
                    page = WebPage(url=u, via="http")
                    try:
                        r = await client.get(u)
                        page.status = r.status_code
                        page.final_url = str(r.url)
                        ctype = r.headers.get("content-type", "")
                        if "html" in ctype or "xml" in ctype or not ctype:
                            return self._finish(page, r.text, t0)
                        if ctype.startswith("text/") or "json" in ctype:
                            page.text = r.text
                            return self._finish(page, "", t0)
                        page.error = f"unsupported content-type: {ctype}"
                    except Exception as e:  # network errors are data, not crashes
                        page.error = f"{type(e).__name__}: {e}"
                    page.elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
                    return page

            pages = await asyncio.gather(*(one(u) for u in urls))
        return {p.url: p for p in pages}

    async def _ensure_browser(self, cdp: bool) -> Any:
        from playwright.async_api import async_playwright

        if self._pw is None:
            # Playwright otherwise forces loopback through any proxy (<-loopback>);
            # read by the driver process at start-up, so set it first.
            os.environ.setdefault("PLAYWRIGHT_DISABLE_FORCED_CHROMIUM_PROXIED_LOOPBACK", "1")
            self._pw = await async_playwright().start()
        if cdp:
            if self._cdp_browser is None:
                self._cdp_browser = await self._pw.chromium.connect_over_cdp(
                    self.config.cdp_url, timeout=self.config.timeout_s * 1000
                )
            return self._cdp_browser
        if self._browser is None:
            kwargs: dict[str, Any] = {"headless": True}
            if self.config.executable_path:
                kwargs["executable_path"] = self.config.executable_path
            if self.config.proxy:
                kwargs["proxy"] = {"server": self.config.proxy, "bypass": _proxy_bypass()}
            self._browser = await self._pw.chromium.launch(**kwargs)
        return self._browser

    async def _new_context(self, cdp: bool) -> tuple[Any, bool]:
        """Return (context, owned). CDP reuses the user's default context."""
        browser = await self._ensure_browser(cdp)
        if cdp:
            if browser.contexts:
                return browser.contexts[0], False
            return await browser.new_context(), True
        ctx = await browser.new_context(user_agent=self.config.user_agent)
        if self.config.block_media:

            async def _route(route: Any) -> None:
                if route.request.resource_type in ("image", "media", "font"):
                    await route.abort()
                else:
                    await route.continue_()

            await ctx.route("**/*", _route)
        return ctx, True

    async def _browser_many(
        self, urls: list[str], conc: int, selector: str | None, use_cdp: bool
    ) -> dict[str, WebPage]:
        sem = asyncio.Semaphore(conc)

        async def one(u: str) -> WebPage:
            async with sem:
                return await self._render_one(u, [], use_cdp, None, selector)

        pages = await asyncio.gather(*(one(u) for u in urls))
        return {p.url: p for p in pages}

    async def _browse(
        self, url: str, steps: list[dict[str, Any]], use_cdp: bool | None, screenshot: str | None
    ) -> WebPage:
        cdp = bool(use_cdp)  # scripted steps run headless unless explicitly asked
        if cdp and not self.config.cdp_url:
            raise WebSecurityError("use_cdp=True but RLM_BROWSER_CDP_URL is not set")
        return await self._render_one(url, steps, cdp, screenshot, None)

    async def _render_one(
        self,
        url: str,
        steps: list[dict[str, Any]],
        cdp: bool,
        screenshot: str | None,
        selector: str | None,
    ) -> WebPage:
        t0 = time.perf_counter()
        via = "cdp" if cdp else "headless"
        page = WebPage(url=url, via=via)
        ctx = tab = None
        owned = False
        try:
            ctx, owned = await self._new_context(cdp)
            tab = await ctx.new_page()
            tab.set_default_timeout(self.config.timeout_s * 1000)
            resp = await tab.goto(url, wait_until="domcontentloaded")
            try:
                await tab.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            page.status = resp.status if resp else None
            extract_sel = selector
            for step in steps:
                extract_sel = await self._run_step(tab, step) or extract_sel
            page.final_url = tab.url
            html = await tab.content()
            if extract_sel:
                loc = tab.locator(extract_sel)
                texts = await loc.all_inner_texts()
                page.text = "\n\n".join(t.strip() for t in texts if t.strip())
            else:
                page.text = (await tab.inner_text("body")).strip()
            page.title = await tab.title()
            if screenshot:
                await tab.screenshot(path=screenshot, full_page=True)
            return self._finish(page, html, t0)
        except Exception as e:
            page.error = f"{type(e).__name__}: {str(e).splitlines()[0][:300]}"
            page.elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
            return page
        finally:
            if tab is not None:
                try:
                    await tab.close()
                except Exception:
                    pass
            if owned and ctx is not None:
                try:
                    await ctx.close()
                except Exception:
                    pass

    @staticmethod
    async def _run_step(tab: Any, step: dict[str, Any]) -> str | None:
        if "click" in step:
            await tab.click(step["click"])
        elif "fill" in step:
            sel, val = step["fill"]
            await tab.fill(sel, str(val))
        elif "press" in step:
            sel, key = step["press"]
            await tab.press(sel, key)
        elif "select" in step:
            sel, val = step["select"]
            await tab.select_option(sel, str(val))
        elif "wait" in step:
            w = step["wait"]
            if isinstance(w, (int, float)):
                await tab.wait_for_timeout(float(w))
            else:
                await tab.wait_for_selector(str(w))
        elif "scroll" in step:
            for _ in range(int(step["scroll"])):
                await tab.mouse.wheel(0, 2000)
                await tab.wait_for_timeout(300)
        elif "goto" in step:
            await tab.goto(str(step["goto"]), wait_until="domcontentloaded")
        elif "extract" in step:
            return str(step["extract"])
        else:
            raise ValueError(f"Unknown browse step: {step}")
        try:
            await tab.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# REPL binding
# ---------------------------------------------------------------------------

WEB_FUNCTIONS = frozenset(
    {"fetch_pages", "browse", "crawl", "ingest_page", "queue_for_chrome", "web_status"}
)


def bind_to_repl(env: Any, toolkit: WebToolkit | None = None) -> WebToolkit:
    """Install web functions and the ``web`` / ``web_meta`` dicts into an RLMEnvironment."""
    kit = toolkit or WebToolkit()
    web: dict[str, str] = env.globals.setdefault("web", {})
    web_meta: dict[str, dict[str, Any]] = env.globals.setdefault("web_meta", {})

    def _sync(pages: dict[str, WebPage] | WebPage) -> None:
        items = pages.values() if isinstance(pages, dict) else [pages]
        for p in items:
            if p.text and not p.needs_login:  # a login wall's text is noise
                web[p.url] = p.text
            web_meta[p.url] = p.meta()
        env.globals["working_memory"]["chrome_queue"] = list(kit.chrome_queue)

    def _summary(pages: dict[str, WebPage]) -> dict[str, dict[str, Any]]:
        return {
            u: {
                "ok": p.ok,
                "status": p.status,
                "title": p.title[:120],
                "chars": len(p.text),
                "via": p.via,
                "error": p.error,
                "needs_login": p.needs_login,
            }
            for u, p in pages.items()
        }

    def fetch_pages(
        urls: Any,
        render: Any = None,
        concurrency: int | None = None,
        selector: str | None = None,
        use_cdp: bool | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Fetch URLs in parallel. Text goes to web[url]; returns a short status per URL."""
        pages = kit.fetch(urls, render=render, concurrency=concurrency, selector=selector,
                          use_cdp=use_cdp)  # fmt: skip
        _sync(pages)
        return _summary(pages)

    def browse(
        url: str,
        steps: list[dict[str, Any]] | None = None,
        use_cdp: bool | None = None,
    ) -> dict[str, Any]:
        """Run scripted browser steps on one page. Text goes to web[url]."""
        page = kit.browse(url, steps=steps, use_cdp=use_cdp)
        _sync(page)
        return _summary({page.url: page})[page.url]

    def crawl(
        start_url: str,
        max_pages: int = 20,
        same_domain: bool = True,
        match: str | None = None,
        depth: int = 2,
        render: Any = None,
    ) -> dict[str, dict[str, Any]]:
        """Breadth-first crawl with parallel levels. Text goes to web[url]."""
        pages = kit.crawl(start_url, max_pages=max_pages, same_domain=same_domain,
                          match=match, depth=depth, render=render)  # fmt: skip
        _sync(pages)
        return _summary(pages)

    def ingest_page(url: str, text: str, title: str = "") -> dict[str, Any]:
        """Add page text obtained by another browser (e.g. Claude in Chrome)."""
        page = kit.ingest(url, text, title)
        _sync(page)
        return _summary({url: page})[url]

    def queue_for_chrome(urls: Any, reason: str = "") -> list[dict[str, str]]:
        """Hand URLs to the agent's supervised browser; see working_memory['chrome_queue']."""
        q = kit.queue_for_chrome(urls, reason)
        env.globals["working_memory"]["chrome_queue"] = list(q)
        return list(q)

    def web_status() -> dict[str, Any]:
        """Counts by lane, pages held and the pending chrome queue."""
        return {
            "pages": len(web),
            "stats": dict(kit.stats),
            "chrome_queue": list(kit.chrome_queue),
            "cdp": bool(kit.config.cdp_url),
        }

    fns: dict[str, Callable[..., Any]] = {
        "fetch_pages": fetch_pages,
        "browse": browse,
        "crawl": crawl,
        "ingest_page": ingest_page,
        "queue_for_chrome": queue_for_chrome,
        "web_status": web_status,
    }
    env.globals.update(fns)
    env._web_toolkit = kit
    return kit


__all__ = [
    "BrowserConfig",
    "WEB_FUNCTIONS",
    "WebPage",
    "WebSecurityError",
    "WebToolkit",
    "bind_to_repl",
    "check_url",
    "html_to_text",
]
