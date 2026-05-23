"""Compatibility shim: ``from vauxr import xxx`` re-exports flat src/ modules.

The server uses flat imports (``import channel_registry``).  Tests were
written against an earlier layout that had a ``vauxr`` package; this file
lets both styles coexist without touching every test file.
"""

from __future__ import annotations

import importlib
import sys


def __getattr__(name: str):  # noqa: ANN001
    """Lazily proxy attribute access to the flat top-level module."""
    try:
        mod = importlib.import_module(name)
    except ModuleNotFoundError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    # Cache so subsequent accesses are direct.
    setattr(sys.modules[__name__], name, mod)
    return mod
