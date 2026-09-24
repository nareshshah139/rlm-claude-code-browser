"""
Command-line front end for the browser-use lane.

Claude Code calls this through Bash (see commands/web.md) so that the slow,
serial part of browsing - the user's own Chrome - is only used for the pages
that really need it, and everything else is fetched in parallel.

    python -m src.web_cli fetch URL [URL ...] [--urls-file f] [--render auto|never|always]
    python -m src.web_cli crawl START_URL [--max-pages 30] [--match REGEX] [--depth 2]
    python -m src.web_cli browse URL --steps '[{"click": "#more"}]'
    python -m src.web_cli ingest URL --text-file page.txt [--title T]
    python -m src.web_cli show

All commands read and write one run directory (default .rlm/web/latest):
    index.json         one entry per URL: status, title, chars, via, error, needs_login, file
    <slug>-<hash>.md   extracted text per page
    chrome_queue.json  URLs that need the user's logged-in browser
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .web_tools import BrowserConfig, WebToolkit

DEFAULT_DIR = ".rlm/web/latest"


def _kit(args: argparse.Namespace) -> WebToolkit:
    config = BrowserConfig.from_env()
    if getattr(args, "concurrency", None):
        config.max_concurrency = args.concurrency
    if getattr(args, "cdp", None):
        config.cdp_url = args.cdp
    if getattr(args, "allow_private", False):
        config.allow_private = True
    kit = WebToolkit(config)
    out = Path(args.out)
    if (out / "index.json").exists() and not getattr(args, "fresh", False):
        kit.load(out)
    return kit


def _collect_urls(args: argparse.Namespace) -> list[str]:
    urls = list(args.urls or [])
    if args.urls_file:
        for line in Path(args.urls_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    return urls


def _report(kit: WebToolkit, urls: list[str] | None, elapsed: float, out: Path) -> None:
    pages = [kit.pages[u] for u in (urls or kit.pages) if u in kit.pages]
    ok = sum(1 for p in pages if p.ok and p.text)
    print(f"{ok}/{len(pages)} pages with text in {elapsed:.1f}s -> {out}")
    for p in pages:
        flag = "OK " if p.ok and p.text else "ERR"
        extra = p.error or ("needs login" if p.needs_login else "")
        print(f"  {flag} [{p.via:8}] {len(p.text):>7} chars  {p.url}  {extra}".rstrip())
    if kit.chrome_queue:
        print(f"\n{len(kit.chrome_queue)} URL(s) need the user's own browser:")
        for q in kit.chrome_queue:
            print(f"  - {q['url']}  ({q['reason']})")
        print(f"  (listed in {out / 'chrome_queue.json'}; add text back with `web_cli ingest`)")


def cmd_fetch(args: argparse.Namespace) -> int:
    urls = _collect_urls(args)
    if not urls:
        print("no URLs given", file=sys.stderr)
        return 2
    kit = _kit(args)
    t0 = time.perf_counter()
    try:
        kit.fetch(urls, render=args.render, use_cdp=True if args.use_cdp else None)
        out = kit.save(args.out)
    finally:
        kit.close()
    _report(kit, urls, time.perf_counter() - t0, out)
    return 0


def cmd_crawl(args: argparse.Namespace) -> int:
    kit = _kit(args)
    t0 = time.perf_counter()
    try:
        got = kit.crawl(
            args.start_url,
            max_pages=args.max_pages,
            same_domain=not args.any_domain,
            match=args.match,
            depth=args.depth,
            render=args.render,
        )
        out = kit.save(args.out)
    finally:
        kit.close()
    _report(kit, list(got), time.perf_counter() - t0, out)
    return 0


def cmd_browse(args: argparse.Namespace) -> int:
    steps = json.loads(args.steps) if args.steps else []
    kit = _kit(args)
    t0 = time.perf_counter()
    try:
        kit.browse(args.url, steps=steps, use_cdp=args.use_cdp, screenshot=args.screenshot)
        out = kit.save(args.out)
    finally:
        kit.close()
    _report(kit, [args.url], time.perf_counter() - t0, out)
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    kit = _kit(args)
    text = Path(args.text_file).read_text(encoding="utf-8") if args.text_file else sys.stdin.read()
    kit.ingest(args.url, text, title=args.title or "", via="chrome")
    out = kit.save(args.out)
    print(f"ingested {len(text)} chars for {args.url} -> {out}")
    if kit.chrome_queue:
        print(f"{len(kit.chrome_queue)} URL(s) still waiting for the browser")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    kit = _kit(args)
    _report(kit, None, 0.0, Path(args.out))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="web_cli", description="RLM browser-use lane")
    p.add_argument("--out", default=DEFAULT_DIR, help=f"run directory (default {DEFAULT_DIR})")
    p.add_argument("--concurrency", type=int, help="parallel pages (default 8)")
    p.add_argument(
        "--cdp", help="Chrome DevTools URL of the user's Chrome, e.g. http://127.0.0.1:9222"
    )
    p.add_argument("--allow-private", action="store_true", help="allow localhost/private IPs")
    p.add_argument("--fresh", action="store_true", help="ignore pages already in --out")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="fetch URLs in parallel")
    f.add_argument("urls", nargs="*")
    f.add_argument("--urls-file")
    f.add_argument("--render", choices=["auto", "never", "always"], default=None)
    f.add_argument("--use-cdp", action="store_true", help="fetch every URL through --cdp Chrome")
    f.set_defaults(func=cmd_fetch)

    c = sub.add_parser("crawl", help="breadth-first crawl, parallel per level")
    c.add_argument("start_url")
    c.add_argument("--max-pages", type=int, default=20)
    c.add_argument("--depth", type=int, default=2)
    c.add_argument("--match", help="only follow links matching this regex")
    c.add_argument("--any-domain", action="store_true")
    c.add_argument("--render", choices=["auto", "never", "always"], default=None)
    c.set_defaults(func=cmd_crawl)

    b = sub.add_parser("browse", help="scripted interaction with one page")
    b.add_argument("url")
    b.add_argument("--steps", help="JSON list of steps")
    b.add_argument("--use-cdp", action="store_true")
    b.add_argument("--screenshot")
    b.set_defaults(func=cmd_browse)

    i = sub.add_parser("ingest", help="add text captured by another browser")
    i.add_argument("url")
    i.add_argument("--text-file", help="file with page text (default: stdin)")
    i.add_argument("--title")
    i.set_defaults(func=cmd_ingest)

    s = sub.add_parser("show", help="summarize the run directory")
    s.set_defaults(func=cmd_show)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
