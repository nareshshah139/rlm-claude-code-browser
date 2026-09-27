#!/usr/bin/env python3
"""Same launcher in the repository, a copied skill, and either plugin archive."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Installed skills carry their runtime next to this script. Source uses the repo.
for base in [HERE, *HERE.parents]:
    if (base / "rlm_portable" / "__init__.py").is_file():
        sys.path.insert(0, str(base))
        break
else:
    raise SystemExit("Missing rlm_portable runtime; reinstall using scripts/install_portable.py")

if len(sys.argv) > 1 and sys.argv[1] == "web":
    from rlm_portable.web_cli import main

    raise SystemExit(main(sys.argv[2:]))
from rlm_portable.cli import main  # noqa: E402 - runtime path is selected above

raise SystemExit(main())
