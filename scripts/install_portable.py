#!/usr/bin/env python3
"""Copy a self-contained skill into an explicitly chosen skills directory."""

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def copy_skill(destination):
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"Refusing to overwrite {destination}; choose an empty destination")
    shutil.copytree(
        ROOT / "skills/rlm-context",
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copytree(
        ROOT / "rlm_portable",
        destination / "scripts/rlm_portable",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target", type=Path, required=True, help="skills directory, e.g. /project/.agents/skills"
    )
    args = parser.parse_args()
    destination = args.target.expanduser().resolve() / "rlm-context"
    if destination.exists() or destination.is_symlink():
        parser.error(f"{destination} already exists; no files were changed")
    args.target.expanduser().mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as staging:
        copied = copy_skill(Path(staging) / "rlm-context")
        copied.rename(destination)
    print(destination)
    return 0


if __name__ == "__main__":
    sys.exit(main())
