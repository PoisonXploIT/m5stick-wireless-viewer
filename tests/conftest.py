"""Fixtures compartidos de los tests."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

# Instante de referencia para todos los tests (determinista).
NOW = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def marauder_log() -> str:
    return (FIXTURES / "marauder_scan.log").read_text(encoding="utf-8")


@pytest.fixture
def marauder_log_path() -> Path:
    return FIXTURES / "marauder_scan.log"


@pytest.fixture
def evil_m5project_log_path() -> Path:
    return FIXTURES / "evil_m5project_scan.log"


@pytest.fixture
def malformed_log_path() -> Path:
    return FIXTURES / "malformed_lines.log"


@pytest.fixture
def evil_m5project_log() -> str:
    return (FIXTURES / "evil_m5project_scan.log").read_text(encoding="utf-8")


@pytest.fixture
def malformed_log() -> str:
    return (FIXTURES / "malformed_lines.log").read_text(encoding="utf-8")
