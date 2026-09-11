"""Vercel serverless entry — exports the FastAPI ASGI app."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.control.app import app  # noqa: E402

# Vercel Python looks for `app` (ASGI) in this module.
__all__ = ["app"]
