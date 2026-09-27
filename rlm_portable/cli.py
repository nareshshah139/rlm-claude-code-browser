"""Portable command surface for host-native Claude/Codex workflows."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

from .context import Run


def doctor():
    return {
        "core_ready": sys.version_info >= (3, 10),
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "http_fetch_ready": importlib.util.find_spec("httpx") is not None,
        "playwright_package": importlib.util.find_spec("playwright") is not None,
        "browser_binary": "not probed; use the optional browser smoke test",
        "model_execution": "host-native agents; no API key needed",
        "network": "controlled by host; offline file ingestion works without network",
        "cdp": "explicit opt-in only; localhost must be reachable from this execution environment",
    }


def hook(event):
    """Advisory Claude hook: no environment bootstrap, model call, or global writes."""
    if os.environ.get("RLM_DISABLED") == "1":
        return {}
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return {}
    if not isinstance(payload, dict):
        return {}
    if event == "SessionStart":
        context = (
            "RLM portable context tools are available through the rlm-context skill. "
            "Use it for large collections of file/URL pointers. It uses host-native "
            "agents, immutable source chunks and a coverage ledger; no API key required."
        )
    elif event == "UserPromptSubmit":
        prompt = payload.get("prompt", payload.get("user_prompt", ""))
        if not isinstance(prompt, str):
            return {}
        pointers = re.findall(r"https?://\S+|[\w./-]+\.(?:md|txt|csv|json|pdf|py|js|ts)\b", prompt)
        large = (
            len(pointers) >= 2
            or len(prompt) > 32000
            or re.search(
                r"\b(rlm|large context|all documents|all files|across.*(?:files|sources|documents))\b",
                prompt,
                re.I,
            )
        )
        if not large:
            return {}
        context = (
            "Use the rlm-context skill to inventory the referenced sources before reading "
            "them all. Create bounded tasks, use native subagents when available and "
            "permitted, otherwise process tasks sequentially. Track failures and coverage. "
            "A short list of pointers can represent a very large corpus."
        )
    else:
        return {}
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}}


def parser():
    p = argparse.ArgumentParser(description="Portable RLM context workflow (host executes models)")
    p.add_argument("--run", default=".rlm/context", help="persistent run directory")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor")
    h = sub.add_parser("hook")
    h.add_argument("event", choices=["SessionStart", "UserPromptSubmit"])
    i = sub.add_parser("init")
    i.add_argument("--query", required=True)
    i.add_argument("--chunk-bytes", type=int, default=16000)
    a = sub.add_parser("add-files")
    a.add_argument("paths", nargs="+")
    a.add_argument("--pointer", help="source URL/connector URI when adding one captured text file")
    w = sub.add_parser("add-web")
    w.add_argument("directory")
    u = sub.add_parser("unavailable")
    u.add_argument("pointer")
    u.add_argument("--reason", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--max-bytes", type=int, default=64000)
    ts = sub.add_parser("tasks")
    ts.add_argument("--pending", action="store_true")
    ts.add_argument("--offset", type=int, default=0)
    ts.add_argument("--limit", type=int, default=20)
    t = sub.add_parser("task")
    t.add_argument("id")
    r = sub.add_parser("read")
    r.add_argument("id")
    r.add_argument("--start", type=int, default=0)
    r.add_argument("--limit", type=int, default=4000)
    s = sub.add_parser("split")
    s.add_argument("id")
    s.add_argument("--max-bytes", type=int, required=True)
    submit = sub.add_parser("submit")
    submit.add_argument("id")
    submit.add_argument("--result", required=True)
    status = sub.add_parser("status")
    status.add_argument("--require-complete", action="store_true")
    sub.add_parser("export")
    return p


def task_summary(t):
    return {k: t[k] for k in ("id", "parent", "state", "bytes", "depth")}


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        run = Run(args.run)
        code = 0
        if args.cmd == "doctor":
            result = doctor()
        elif args.cmd == "hook":
            result = hook(args.event)
        elif args.cmd == "init":
            result = run.init(args.query, args.chunk_bytes)
        elif args.cmd == "add-files":
            if args.pointer and len(args.paths) != 1:
                raise ValueError("--pointer requires exactly one file")
            errors = []
            for path in args.paths:
                try:
                    run.add_file(path, pointer=args.pointer)
                except ValueError as exc:
                    errors.append(str(exc))
            result = dict(run.status(), errors=errors)
            code = 1 if errors else 0
        elif args.cmd == "add-web":
            result = run.add_web(args.directory)
        elif args.cmd == "unavailable":
            result = {"source_id": run.unavailable(args.pointer, args.reason)}
        elif args.cmd == "plan":
            tasks = run.plan(args.max_bytes)
            result = dict(run.status(), task_count=len(tasks), next="tasks --pending")
        elif args.cmd == "tasks":
            if not 1 <= args.limit <= 100 or args.offset < 0:
                raise ValueError("limit must be 1–100 and offset nonnegative")
            tasks = run.tasks(pending=args.pending)
            end = args.offset + args.limit
            result = {
                "tasks": [task_summary(t) for t in tasks[args.offset : end]],
                "next_offset": end if end < len(tasks) else None,
                "total": len(tasks),
            }
        elif args.cmd == "task":
            result = run.task(args.id)
        elif args.cmd == "read":
            result = run.read(args.id, args.start, args.limit)
        elif args.cmd == "split":
            result = {"children": [task_summary(t) for t in run.split(args.id, args.max_bytes)]}
        elif args.cmd == "submit":
            result = run.submit(args.id, json.loads(Path(args.result).read_text(encoding="utf-8")))
        elif args.cmd == "export":
            result = run.export()
            result = {k: v for k, v in result.items() if k != "results"}
            result["coverage_file"] = str(run.root / "coverage.json")
        else:
            result = run.status()
            code = 3 if args.require_complete and not result["complete"] else 0
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return code
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
