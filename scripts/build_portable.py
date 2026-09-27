#!/usr/bin/env python3
"""Build clean Claude/Cowork and Codex plugin archives, without local environments."""

import argparse
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

from install_portable import ROOT, copy_skill


def build(output: Path, host: str):
    name = "rlm-claude-code" if host == "cowork" else "rlm-claude-code-browser"
    archive = output / f"{name}-{host}-0.9.0.zip"
    unpacked = output / host / name
    if archive.exists() or unpacked.exists():
        raise ValueError("Output already exists; use a clean output directory")
    with tempfile.TemporaryDirectory() as staging:
        plugin = Path(staging) / name
        (plugin / "skills").mkdir(parents=True)
        copy_skill(plugin / "skills/rlm-context")
        manifest = {
            "name": name,
            "version": "0.9.0",
            "author": {
                "name": "Naresh Shah",
                "url": "https://github.com/nareshshah139/rlm-claude-code-browser",
            },
            "description": "Lossless pointer context, bounded native-agent work and checked coverage",
        }
        if host == "cowork":
            folder = plugin / ".claude-plugin"
            for part in ("commands", "agents", "hooks"):
                (plugin / part).mkdir()
            for command in ("web.md", "rlm-orchestrator.md", "rlm.md"):
                shutil.copy2(ROOT / "commands" / command, plugin / "commands" / command)
            shutil.copy2(ROOT / "agents/rlm-orchestrator.md", plugin / "agents/rlm-orchestrator.md")
            shutil.copy2(ROOT / "hooks/hooks.json", plugin / "hooks/hooks.json")
        else:
            folder = plugin / ".codex-plugin"
            manifest["skills"] = "./skills/"
            manifest["interface"] = {
                "displayName": "RLM Context",
                "shortDescription": "Analyze large source collections with checked coverage",
                "longDescription": "Preserve complete sources on disk, recursively split bounded tasks, and synthesize cited results using native host agents.",
                "developerName": "Naresh Shah",
                "category": "Productivity",
                "capabilities": ["Interactive"],
                "defaultPrompt": "Use rlm-context to analyze these sources and report coverage.",
            }
        folder.mkdir()
        (folder / "plugin.json").write_text(json.dumps(manifest, indent=2) + "\n")
        shutil.copy2(ROOT / "docs/portable-hosts.md", plugin / "INSTALL.md")
        output.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            for path in sorted(plugin.rglob("*")):
                if path.is_file():
                    z.write(path, path.relative_to(plugin))
        # Also retain a reviewable unpacked directory for plugin validation.
        unpacked.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(plugin, unpacked)
        return archive, unpacked


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--host", choices=["cowork", "codex", "all"], default="all")
    args = parser.parse_args()
    for host in ["cowork", "codex"] if args.host == "all" else [args.host]:
        archive, _ = build(args.out.resolve(), host)
        print(archive)
