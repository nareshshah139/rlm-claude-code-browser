"""Exercise the shipped commands outside the repository, without site packages."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rlm install space ")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"PYTHONPATH", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "RLM_DISABLED"}
        }

    def invoke(self, script, *args, input=None):
        return subprocess.run(
            [sys.executable, "-S", str(script), *map(str, args)],
            input=input,
            text=True,
            capture_output=True,
            cwd=self.base,
            env=self.env,
        )

    def test_copied_skill_runs_entire_workflow_outside_checkout(self):
        target = self.base / "project/.agents/skills"
        p = self.invoke(ROOT / "scripts/install_portable.py", "--target", target)
        self.assertEqual(p.returncode, 0, p.stderr)
        helper = target / "rlm-context/scripts/rlm.py"
        self.assertTrue(json.loads(self.invoke(helper, "doctor").stdout)["core_ready"])
        source = self.base / "large source.txt"
        source.write_text("a" * 100_000 + "TAIL_843", encoding="utf-8")
        run = self.base / "run"
        for args in [
            ("init", "--query", "Find the tail"),
            ("add-files", source),
            ("plan", "--max-bytes", "32000"),
        ]:
            p = self.invoke(helper, "--run", run, *args)
            self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(
            self.invoke(helper, "--run", run, "status", "--require-complete").returncode, 3
        )
        tasks = json.loads(self.invoke(helper, "--run", run, "tasks").stdout)["tasks"]
        saw_tail = False
        for task in tasks:
            detail = json.loads(self.invoke(helper, "--run", run, "task", task["id"]).stdout)
            evidence = []
            for c in detail["chunks"]:
                if "TAIL_843" in Path(c["path"]).read_text(encoding="utf-8"):
                    saw_tail = True
                    evidence.append({"chunk_id": c["id"], "quote": "TAIL_843"})
            result = self.base / (task["id"] + ".json")
            result.write_text(
                json.dumps(
                    {
                        "status": "complete",
                        "summary": "Tail checked",
                        "reviewed_chunks": [c["id"] for c in detail["chunks"]],
                        "evidence": evidence,
                    }
                )
            )
            p = self.invoke(helper, "--run", run, "submit", task["id"], "--result", result)
            self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(saw_tail)
        self.assertEqual(
            self.invoke(helper, "--run", run, "status", "--require-complete").returncode, 0
        )
        self.assertEqual(self.invoke(helper, "--run", run, "export").returncode, 0)
        coverage = json.loads((run / "coverage.json").read_text())
        self.assertTrue(coverage["complete"])
        exported = json.loads(Path(coverage["results"][0]["path"]).read_text())
        self.assertTrue(exported["sources"][0]["pointer"].startswith("file:"))
        # Reinstallation cannot overwrite a locally edited skill.
        before = helper.read_bytes()
        self.assertNotEqual(
            self.invoke(ROOT / "scripts/install_portable.py", "--target", target).returncode, 0
        )
        self.assertEqual(helper.read_bytes(), before)

    def test_both_archives_are_self_contained(self):
        out = self.base / "packages"
        p = self.invoke(ROOT / "scripts/build_portable.py", "--out", out)
        self.assertEqual(p.returncode, 0, p.stderr)
        archives = list(out.glob("*.zip"))
        self.assertEqual(len(archives), 2)
        for archive in archives:
            with zipfile.ZipFile(archive) as z:
                self.assertFalse(any("__pycache__" in n or ".venv" in n for n in z.namelist()))
                extracted = (
                    self.base
                    / archive.stem
                    / ("rlm-claude-code" if "cowork" in archive.name else "rlm-claude-code-browser")
                )
                z.extractall(extracted)
            (helper,) = extracted.glob("skills/rlm-context/scripts/rlm.py")
            self.assertEqual(self.invoke(helper, "doctor").returncode, 0)
            plugin = helper.parents[3]
            if "cowork" in archive.name:
                hooks = json.loads((plugin / "hooks/hooks.json").read_text())
                self.assertIn("UserPromptSubmit", hooks["hooks"])
            else:
                manifest = json.loads((plugin / ".codex-plugin/plugin.json").read_text())
                self.assertEqual(manifest["name"], plugin.name)
                self.assertFalse((plugin / "hooks").exists())

    def test_hook_contract_and_malformed_input(self):
        helper = ROOT / "skills/rlm-context/scripts/rlm.py"
        p = self.invoke(
            helper,
            "hook",
            "UserPromptSubmit",
            input=json.dumps({"prompt": "Compare https://example.com/a and https://example.com/b"}),
        )
        output = json.loads(p.stdout)["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "UserPromptSubmit")
        self.assertIn("rlm-context", output["additionalContext"])
        for value in ["bad JSON", "[]", "null", '{"prompt": 22}', '{"prompt": "hi"}']:
            p = self.invoke(helper, "hook", "UserPromptSubmit", input=value)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(json.loads(p.stdout), {})

    def test_api_cli_help_is_reachable_without_optional_dependencies(self):
        p = subprocess.run(
            [sys.executable, "-S", "-m", "src.orchestrator", "--help"],
            cwd=ROOT,
            env=self.env,
            text=True,
            capture_output=True,
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("--files-dir", p.stdout)
