"""Optional HTTP-only integration check; no external websites or browser binary."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from rlm_portable.context import Run

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(importlib.util.find_spec("httpx"), "optional httpx is not installed")
class WebAcquisitionTests(unittest.TestCase):
    def test_url_list_beyond_batch_cap_and_large_page_tail_survive(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                text = "long page " * 25000 if self.path == "/204" else "source text " * 40
                body = (
                    "<html><body><p>" + text + "TAIL_" + self.path + "</p></body></html>"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    urls = base / "urls.txt"
                    urls.write_text(
                        "\n".join(f"http://127.0.0.1:{server.server_port}/{i}" for i in range(205))
                    )
                    env = dict(os.environ)
                    env.pop("RLM_BROWSER_MAX_CHARS", None)
                    env.pop("RLM_BROWSER_CDP_URL", None)
                    p = subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "rlm_portable.web_cli",
                            "--out",
                            str(base / "web"),
                            "--allow-private",
                            "fetch",
                            "--urls-file",
                            str(urls),
                            "--render",
                            "never",
                        ],
                        cwd=ROOT,
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=60,
                    )
                    self.assertEqual(p.returncode, 0, p.stderr)
                finally:
                    server.shutdown()
                    thread.join()
            entries = json.loads((base / "web/index.json").read_text())
            self.assertEqual(len(entries), 205)
            self.assertTrue(all(e["ok"] and not e["truncated"] for e in entries))
            run = Run(base / "run")
            run.init("Find every source tail")
            run.add_web(base / "web")
            tasks = run.plan(max_bytes=32000)
            self.assertTrue(all(t["bytes"] <= 32000 for t in tasks))
            tails = [Path(c["path"]).read_text() for t in tasks for c in t["chunks"]]
            self.assertTrue(any("TAIL_/204" in t for t in tails))
            self.assertEqual(run.status()["source_count"], 205)


class WebManifestTests(unittest.TestCase):
    def test_failed_and_truncated_pages_cannot_report_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "page.txt").write_text("partial page")
            (base / "index.json").write_text(
                json.dumps(
                    [
                        {
                            "url": "https://example.com/partial",
                            "ok": True,
                            "file": "page.txt",
                            "truncated": True,
                        },
                        {"url": "https://example.com/denied", "ok": False, "error": "403"},
                    ]
                )
            )
            run = Run(base / "run")
            run.init("Analyze both sources")
            run.add_web(base)
            for task in run.plan():
                run.submit(
                    task["id"],
                    {
                        "status": "complete",
                        "summary": "Partial capture read",
                        "reviewed_chunks": [c["id"] for c in task["chunks"]],
                        "evidence": [],
                    },
                )
            status = run.status()
            self.assertFalse(status["complete"])
            self.assertEqual(len(status["unavailable_sources"]), 2)
