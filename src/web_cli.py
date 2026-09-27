"""Compatibility entry point; browser CLI no longer imports the Rust stack."""

from rlm_portable.web_cli import *  # noqa: F403
from rlm_portable.web_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
