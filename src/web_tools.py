"""Compatibility alias for the dependency-light browser implementation."""

import sys

from rlm_portable import web_tools as _implementation

sys.modules[__name__] = _implementation
