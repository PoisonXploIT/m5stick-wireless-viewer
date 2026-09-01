"""Fuentes de datos: serial en vivo y archivo (tail -f / reproduccion)."""

from __future__ import annotations

from .base import AbstractSource, LineCallback
from .file_source import FileSource
from .serial_source import SerialSource

__all__ = ["AbstractSource", "FileSource", "LineCallback", "SerialSource"]
