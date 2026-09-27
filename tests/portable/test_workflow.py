"""Portable runtime contract: actual files, persistent state, no model/API mocks."""

import concurrent.futures
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from rlm_portable.context import Run


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.run = Run(self.base / "run")
        self.run.init("Find the identifiers and their relationships", chunk_bytes=1024)

    def add(self, text, name="source.txt"):
        path = self.base / name
        path.write_text(text, encoding="utf-8")
        self.run.add_file(path)
        return path

    def complete(self, task):
        return self.run.submit(
            task["id"],
            {
                "status": "complete",
                "summary": "Reviewed the assigned source spans.",
                "reviewed_chunks": [c["id"] for c in task["chunks"]],
                "evidence": [],
            },
        )

    def test_large_unicode_tail_lossless_and_bounded(self):
        text = "α🙂跨源\n" * 20000 + "TAIL_SENTINEL_314159"
        self.add(text)
        self.run.plan(max_bytes=4096)
        tasks = self.run.tasks()
        chunks = [c for t in tasks for c in t["chunks"]]
        restored = "".join(Path(c["path"]).read_text(encoding="utf-8") for c in chunks)
        self.assertEqual(restored, text)
        self.assertTrue(all(t["bytes"] <= 4096 for t in tasks))
        self.assertTrue(all(c["bytes"] <= 1024 for c in chunks))
        self.assertFalse(self.run.status()["complete"])

    def test_completion_requires_full_coverage_and_real_evidence(self):
        self.add("A unique fact: reference 723.\n" * 400)
        self.run.plan(max_bytes=2048)
        task = self.run.tasks()[0]
        bad = {"status": "complete", "summary": "Done", "reviewed_chunks": [], "evidence": []}
        with self.assertRaises(ValueError):
            self.run.submit(task["id"], bad)
        bad["reviewed_chunks"] = [c["id"] for c in task["chunks"]]
        bad["evidence"] = [{"chunk_id": task["chunks"][0]["id"], "quote": "invented evidence"}]
        with self.assertRaises(ValueError):
            self.run.submit(task["id"], bad)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(self.complete, self.run.tasks()))
        self.assertTrue(Run(self.run.root).status()["complete"])

    def test_recursive_split_replaces_parent_without_losing_coverage(self):
        self.add("child must find the last bytes\n" * 1000)
        self.run.plan(max_bytes=4096)
        parent = self.run.tasks()[0]
        children = self.run.split(parent["id"], max_bytes=512)
        self.assertGreater(len(children), 1)
        self.assertTrue(all(c["bytes"] <= 512 for c in children))
        self.assertNotIn(parent["id"], [t["id"] for t in self.run.tasks()])
        for task in self.run.tasks():
            self.complete(task)
        self.assertTrue(self.run.status()["complete"])

    def test_snapshot_tampering_blocks_submission(self):
        self.add("fact\n" * 300)
        self.run.plan(max_bytes=2048)
        task = self.run.tasks()[0]
        Path(task["chunks"][0]["path"]).write_text("corrupted")
        with self.assertRaises(ValueError):
            self.complete(task)

    def test_missing_pointer_stays_visible_and_prevents_complete(self):
        self.add("present")
        with self.assertRaises(ValueError):
            self.run.add_file(self.base / "missing.txt")
        self.run.plan(max_bytes=4096)
        for task in self.run.tasks():
            self.complete(task)
        status = self.run.status()
        self.assertFalse(status["complete"])
        self.assertEqual(len(status["unavailable_sources"]), 1)

    def test_read_has_explicit_continuation(self):
        self.add("z" * 4000)
        self.run.plan(max_bytes=4096)
        chunk = self.run.tasks()[0]["chunks"][0]
        result = self.run.read(chunk["id"], start=0, limit=23)
        self.assertEqual(result["text"], "z" * 23)
        self.assertEqual(result["next_start"], 23)

    def test_plain_python_imports_and_doctor_need_no_dependencies(self):
        root = Path(__file__).resolve().parents[2]
        p = subprocess.run(
            [sys.executable, "-S", "-m", "rlm_portable", "doctor"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(json.loads(p.stdout)["core_ready"])


if __name__ == "__main__":
    unittest.main()
