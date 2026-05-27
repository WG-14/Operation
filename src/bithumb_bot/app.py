"""Compatibility shim for historical ``bithumb_bot.app`` imports."""

from __future__ import annotations

import sys

from . import app_impl as _legacy_app
from .cli.main import main as _cli_main

_legacy_app.legacy_main = _legacy_app.main
_legacy_app.main = _cli_main

sys.modules[__name__] = _legacy_app
