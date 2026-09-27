"""Explicit entry point for the optional API-backed orchestrator."""

import argparse
import asyncio
import os
import sys
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True)
    parser.add_argument(
        "--files-dir",
        type=Path,
        required=True,
        help="directory of UTF-8 .md/.txt/.json context files",
    )
    parser.add_argument(
        "--verbosity", default="normal", choices=["minimal", "normal", "verbose", "debug"]
    )
    parser.add_argument("--export-trajectory")
    args = parser.parse_args(argv)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        parser.error(
            "This optional backend needs ANTHROPIC_API_KEY. For native host execution, use the rlm-context skill."
        )
    if not args.files_dir.is_dir():
        parser.error("--files-dir must be a readable directory")

    # CLI help and argument validation work before optional native/SDK dependencies load.
    from ..api_client import init_client
    from ..config import RLMConfig
    from ..types import SessionContext, TrajectoryEventType
    from .core import RLMOrchestrator

    files = {
        str(p.relative_to(args.files_dir)): p.read_text(encoding="utf-8")
        for p in sorted(args.files_dir.rglob("*"))
        if p.is_file() and p.suffix in {".md", ".txt", ".json"}
    }
    if not files:
        parser.error("No context files found; refusing an empty analysis")
    config = RLMConfig()
    config.activation.mode = "always"
    config.trajectory.verbosity = args.verbosity
    if args.export_trajectory:
        config.trajectory.export_enabled = True
        config.trajectory.export_path = args.export_trajectory
    orchestrator = RLMOrchestrator(config, init_client())

    async def execute():
        async for event in orchestrator.run(args.query, SessionContext(files=files)):
            if isinstance(event, str):
                print(event)
                continue
            print(event.content)
            if event.type == TrajectoryEventType.ERROR:
                return 1
        return 0

    return asyncio.run(execute())


if __name__ == "__main__":
    sys.exit(main())
