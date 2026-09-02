"""Tests de empaquetado (Fase 5): metadata, version y entry point.

El paquete se instala editable en el venv (`pip install -e .`), asi que la
metadata consultada aqui es la del pyproject.toml real.
"""

from __future__ import annotations

from importlib.metadata import entry_points, version

import m5wireless


def test_distribution_version_is_3_0_1() -> None:
    assert version("m5stick-wireless-viewer") == "3.0.1"


def test_package_version_matches_distribution() -> None:
    assert m5wireless.__version__ == version("m5stick-wireless-viewer")


def test_console_script_entry_point_exists() -> None:
    eps = entry_points(group="console_scripts")
    names = {ep.name for ep in eps}
    assert "m5wireless" in names
    ep = next(ep for ep in eps if ep.name == "m5wireless")
    assert ep.value == "m5wireless.cli:main"
