"""
Tests for the browser-use lane (src/web_tools.py, src/web_cli.py).

A local HTTP server provides: a static article, a JavaScript-only page, a
login-walled page (cookie-gated), a 403 page, an interactive search form and a
small link graph for crawling. Browser tests are skipped when Chromium cannot
be launched.
"""

from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from src.repl_environment import RLMEnvironment
from src.types import DeferredBatch, SessionContext
from src.web_tools import (
    BrowserConfig,
    WebSecurityError,
    WebToolkit,
    check_url,
    html_to_text,
)

# Playwright's sync API (used only by the test helpers below) leaves a driver
# transport to be finalized after its private event loop closes.
pytestmark = pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")

ARTICLE = "<p>" + "Recursive language models decompose long inputs. " * 40 + "</p>"

PAGES: dict[str, str] = {
    "/article": f"""<html><head><title>Article</title><style>.x{{}}</style></head>
        <body><nav>menu</nav><h1>RLM</h1>{ARTICLE}
        <a href="/crawl/a">A</a><script>var secret='no';</script></body></html>""",
    "/js": """<html><head><title>JS page</title></head><body>
        <noscript>You need to enable JavaScript to run this app.</noscript>
        <div id="root"></div>
        <script>document.getElementById('root').innerText =
          'RENDERED-BY-JS ' + 'dynamic content '.repeat(60);</script></body></html>""",
    "/login": """<html><head><title>Sign in</title></head><body><form>
        <input name=user><input type=password name=password>
        <button>Log in</button></form></body></html>""",
    "/form": """<html><head><title>Search</title></head><body>
        <input id=q><button id=go onclick="
          document.getElementById('out').innerText='RESULTS FOR '+document.getElementById('q').value">
        Go</button><div id=out></div></body></html>""",
    "/crawl/a": '<html><body><p>page A</p><a href="/crawl/b">B</a><a href="/crawl/c">C</a>'
    '<a href="https://example.org/offsite">off</a></body></html>',
    "/crawl/b": '<html><body><p>page B</p><a href="/crawl/d">D</a></body></html>',
    "/crawl/c": "<html><body><p>page C</p></body></html>",
    "/crawl/d": "<html><body><p>page D</p></body></html>",
}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/private":
            if "session=ok" in (self.headers.get("Cookie") or ""):
                return self._send(
                    200,
                    "<html><title>Inbox</title><body><p>"
                    + "PRIVATE-DATA quarterly numbers. " * 30
                    + "</p></body></html>",
                )
            self.send_response(302)
            self.send_header("Location", "/login")
            self.end_headers()
            return
        if path == "/forbidden":
            return self._send(403, "<html><body>Forbidden</body></html>")
        if path == "/binary":
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            self.wfile.write(b"\x00\x01")
            return
        if path in PAGES:
            return self._send(200, PAGES[path])
        self._send(404, "<html><body>nope</body></html>")

    def _send(self, code: int, body: str) -> None:
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture(scope="module")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _local_config(**kw) -> BrowserConfig:
    cfg = BrowserConfig.from_env()
    cfg.allow_private = True
    cfg.timeout_s = 15
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def _chromium_ok() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:
        return False


CHROMIUM = _chromium_ok()
needs_chromium = pytest.mark.skipif(not CHROMIUM, reason="Chromium not launchable")


@pytest.fixture
def kit():
    k = WebToolkit(_local_config())
    yield k
    k.close()


# --------------------------------------------------------------------- pure


class TestHtmlToText:
    def test_strips_scripts_styles_and_keeps_headings(self):
        title, text, links = html_to_text(PAGES["/article"], "http://h/article")
        assert title == "Article"
        assert "# RLM" in text
        assert "secret" not in text and ".x{" not in text
        assert links == ["http://h/crawl/a"]

    def test_malformed_html_does_not_raise(self):
        _, text, _ = html_to_text("<div><p>unclosed <b>bold", "")
        assert "unclosed bold" in text


class TestUrlPolicy:
    def test_rejects_non_http(self):
        with pytest.raises(WebSecurityError):
            check_url("file:///etc/passwd", BrowserConfig())

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/",
            "http://localhost:8080/",
            "http://10.0.0.5/",
            "http://169.254.169.254/latest",
        ],
    )
    def test_rejects_private_by_default(self, url):
        with pytest.raises(WebSecurityError):
            check_url(url, BrowserConfig())

    def test_allow_private_opt_in(self):
        assert check_url("http://127.0.0.1:1/x#frag", BrowserConfig(allow_private=True)) == (
            "http://127.0.0.1:1/x"
        )

    def test_allow_and_block_lists(self):
        cfg = BrowserConfig(allowed_domains=["example.com"], allow_private=True)
        assert check_url("https://docs.example.com/a", cfg)
        with pytest.raises(WebSecurityError):
            check_url("https://evil.com/", cfg)
        cfg = BrowserConfig(blocked_domains=["evil.com"], allow_private=True)
        with pytest.raises(WebSecurityError):
            check_url("https://api.evil.com/", cfg)

    def test_page_cap(self):
        k = WebToolkit(BrowserConfig(max_pages_per_call=2))
        with pytest.raises(WebSecurityError):
            k.fetch(["https://a.com", "https://b.com", "https://c.com"])

    def test_env_config(self, monkeypatch):
        monkeypatch.setenv("RLM_BROWSER_CONCURRENCY", "3")
        monkeypatch.setenv("RLM_BROWSER_ALLOWED_DOMAINS", "a.com, b.com")
        monkeypatch.setenv("RLM_BROWSER_RENDER", "never")
        cfg = BrowserConfig.from_env()
        assert cfg.max_concurrency == 3
        assert cfg.allowed_domains == ["a.com", "b.com"]
        assert cfg.render == "never"


# ---------------------------------------------------------------- http lane


class TestHttpLane:
    def test_parallel_fetch_static(self, kit, server):
        urls = [f"{server}/article", f"{server}/crawl/a", f"{server}/crawl/b"]
        got = kit.fetch(urls, render="never")
        assert list(got) == urls
        assert all(p.ok and p.via == "http" for p in got.values())
        assert "decompose long inputs" in got[urls[0]].text

    def test_refused_urls_are_reported_not_raised(self, server):
        k = WebToolkit(BrowserConfig())  # private not allowed
        got = k.fetch([f"{server}/article"], render="never")
        page = got[f"{server}/article"]
        assert page.via == "policy" and page.error.startswith("refused")
        k.close()

    def test_login_redirect_goes_to_chrome_queue(self, kit, server):
        got = kit.fetch([f"{server}/private"], render="never")
        page = got[f"{server}/private"]
        assert page.needs_login
        assert kit.chrome_queue[0]["url"] == f"{server}/private"

    def test_binary_is_an_error(self, kit, server):
        page = kit.fetch([f"{server}/binary"], render="never")[f"{server}/binary"]
        assert "unsupported content-type" in (page.error or "")

    def test_ingest_clears_queue(self, kit, server):
        kit.fetch([f"{server}/private"], render="never")
        kit.ingest(f"{server}/private", "text from chrome", "Inbox")
        assert kit.chrome_queue == []
        assert kit.pages[f"{server}/private"].via == "ingested"

    def test_save_and_load_roundtrip(self, kit, server, tmp_path):
        kit.fetch([f"{server}/article", f"{server}/private"], render="never")
        kit.save(tmp_path)
        index = json.loads((tmp_path / "index.json").read_text())
        assert {e["url"] for e in index} == {f"{server}/article", f"{server}/private"}
        k2 = WebToolkit(_local_config())
        loaded = k2.load(tmp_path)
        assert "decompose long inputs" in loaded[f"{server}/article"].text
        assert k2.chrome_queue and k2.chrome_queue[0]["url"] == f"{server}/private"


# ------------------------------------------------------------ browser lanes


@needs_chromium
class TestHeadlessLane:
    def test_auto_renders_js_page(self, kit, server):
        page = kit.fetch([f"{server}/js"])[f"{server}/js"]
        assert page.via == "headless"
        assert "RENDERED-BY-JS" in page.text

    def test_never_mode_does_not_render(self, kit, server):
        page = kit.fetch([f"{server}/js"], render="never")[f"{server}/js"]
        assert "RENDERED-BY-JS" not in page.text

    def test_static_pages_skip_the_browser(self, kit, server):
        kit.fetch([f"{server}/article"])
        assert kit.stats["headless"] == 0

    def test_parallel_headless_is_faster_than_serial(self, server):
        urls = [f"{server}/js?i={i}" for i in range(8)]
        k1 = WebToolkit(_local_config(max_concurrency=1))
        k1.fetch(urls[:1], render="always")  # warm up browser
        t = time.perf_counter()
        k1.fetch(urls, render="always")
        serial = time.perf_counter() - t
        k1.close()
        k8 = WebToolkit(_local_config(max_concurrency=8))
        k8.fetch(urls[:1], render="always")
        t = time.perf_counter()
        got = k8.fetch(urls, render="always")
        parallel = time.perf_counter() - t
        k8.close()
        assert all("RENDERED-BY-JS" in p.text for p in got.values())
        assert parallel < serial

    def test_browse_steps(self, kit, server):
        page = kit.browse(
            f"{server}/form",
            steps=[
                {"fill": ["#q", "recursion"]},
                {"click": "#go"},
                {"wait": 100},
                {"extract": "#out"},
            ],  # fmt: skip
        )
        assert page.text == "RESULTS FOR recursion"

    def test_crawl_same_domain(self, kit, server):
        got = kit.crawl(f"{server}/crawl/a", max_pages=10, depth=2, render="never")
        paths = sorted(u.replace(server, "") for u in got)
        assert paths == ["/crawl/a", "/crawl/b", "/crawl/c", "/crawl/d"]

    def test_crawl_respects_max_pages(self, kit, server):
        got = kit.crawl(f"{server}/crawl/a", max_pages=2, depth=3, render="never")
        assert len(got) == 2


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def logged_in_chrome(server, tmp_path):
    """A real Chromium with remote debugging whose profile holds a session cookie."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        exe = p.chromium.executable_path
    port = _free_port()
    proc = subprocess.Popen(
        [exe, "--headless=new", "--no-sandbox", f"--remote-debugging-port={port}",
         f"--user-data-dir={tmp_path / 'profile'}", "--no-first-run", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    cdp = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            import urllib.request

            urllib.request.urlopen(cdp + "/json/version", timeout=0.5)
            break
        except Exception:
            time.sleep(0.2)
    # "log in": put the session cookie into the user's default context
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(cdp)
        b.contexts[0].add_cookies([{"name": "session", "value": "ok", "url": server}])
    yield cdp
    proc.terminate()
    proc.wait(timeout=10)


@needs_chromium
class TestCdpLane:
    def test_login_page_goes_through_users_chrome(self, server, logged_in_chrome):
        k = WebToolkit(_local_config(cdp_url=logged_in_chrome))
        got = k.fetch([f"{server}/private", f"{server}/article"])
        k.close()
        private = got[f"{server}/private"]
        assert private.via == "cdp"
        assert "PRIVATE-DATA" in private.text
        assert got[f"{server}/article"].via == "http"  # public page never touches Chrome
        assert k.chrome_queue == []

    def test_without_cdp_it_is_queued(self, kit, server):
        page = kit.fetch([f"{server}/private"])[f"{server}/private"]
        assert "PRIVATE-DATA" not in page.text
        assert [q["url"] for q in kit.chrome_queue] == [f"{server}/private"]

    def test_parallel_tabs_in_users_chrome(self, server, logged_in_chrome):
        k = WebToolkit(_local_config(cdp_url=logged_in_chrome))
        urls = [f"{server}/private?i={i}" for i in range(6)]
        got = k.fetch(urls)
        k.close()
        assert all("PRIVATE-DATA" in p.text for p in got.values())


# --------------------------------------------------------------- REPL + CLI


class TestReplIntegration:
    def test_functions_bound_in_standard_mode(self):
        env = RLMEnvironment(SessionContext())
        for name in ("fetch_pages", "browse", "crawl", "ingest_page", "web_status"):
            assert name in env.globals
        assert env.globals["web"] == {}

    def test_not_bound_in_micro_mode(self):
        env = RLMEnvironment(SessionContext(), access_level="micro")
        assert "fetch_pages" not in env.globals

    def test_disabled_by_env(self, monkeypatch):
        monkeypatch.setenv("RLM_BROWSER", "0")
        env = RLMEnvironment(SessionContext())
        assert "fetch_pages" not in env.globals

    def test_fetch_then_search_and_llm_batch(self, server, monkeypatch):
        monkeypatch.setenv("RLM_BROWSER_ALLOW_PRIVATE", "1")
        env = RLMEnvironment(SessionContext())
        try:
            code = (
                f"status = fetch_pages(['{server}/article', '{server}/crawl/b', '{server}/private'],"
                " render='never')\n"
                "hits = search(web, 'decompose')\n"
                "batch = llm_batch([('Summarize', web[u]) for u in web])\n"
                "q = working_memory['chrome_queue']"
            )
            res = env.execute(code)
            assert res.success, res.error
            status = env.locals["status"]
            assert status[f"{server}/article"]["ok"] is True
            assert status[f"{server}/private"]["needs_login"] is True
            assert env.locals["hits"]
            assert isinstance(env.locals["batch"], DeferredBatch)
            assert len(env.locals["batch"].operations) == 2  # login page had no usable text
            assert env.locals["q"][0]["url"] == f"{server}/private"
            res = env.execute(f"ingest_page('{server}/private', 'numbers from chrome')")
            assert res.success
            assert env.globals["web"][f"{server}/private"] == "numbers from chrome"
            assert env.globals["working_memory"]["chrome_queue"] == []
        finally:
            env.close_web()

    def test_prompt_mentions_web(self):
        from src.prompts import build_rlm_system_prompt

        assert "fetch_pages" in build_rlm_system_prompt(SessionContext(), "q")


class TestCli:
    def test_fetch_ingest_show(self, server, tmp_path, capsys):
        from src.web_cli import main

        out = tmp_path / "run"
        rc = main(["--out", str(out), "--allow-private", "fetch",
                   f"{server}/article", f"{server}/private", "--render", "never"])  # fmt: skip
        assert rc == 0
        printed = capsys.readouterr().out
        assert "1/2 pages with text" in printed and "need the user's own browser" in printed
        queue = json.loads((out / "chrome_queue.json").read_text())
        assert queue[0]["url"] == f"{server}/private"

        page_txt = tmp_path / "p.txt"
        page_txt.write_text("captured by chrome")
        assert main(["--out", str(out), "--allow-private", "ingest", f"{server}/private",
                     "--text-file", str(page_txt)]) == 0  # fmt: skip
        assert json.loads((out / "chrome_queue.json").read_text()) == []
        md = list(Path(out).glob("*.md"))
        assert len(md) == 2
