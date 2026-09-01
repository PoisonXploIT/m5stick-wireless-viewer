"""Backend web (Fase 3): FastAPI + SSE sobre el store inyectado."""

from __future__ import annotations

from .app import create_app
from .sse import EventHub

__all__ = ["EventHub", "create_app"]
