"""Tests de empaquetado (Fase 5): metadata, version y entry point.

El paquete se instala editable en el venv (`pip install -e .`), asi que la
metadata consultada aqui es la del pyproject.toml real.
"""

from __future__ import annotations

import tomllib
from importlib.metadata import entry_points, version
from pathlib import Path

import m5wireless


def _pyproject_version() -> str:
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    with pyproject.open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def test_distribution_version_matches_pyproject() -> None:
    assert version("m5stick-wireless-viewer") == _pyproject_version()


def test_package_version_matches_distribution() -> None:
    assert m5wireless.__version__ == version("m5stick-wireless-viewer")


def test_console_script_entry_point_exists() -> None:
    eps = entry_points(group="console_scripts")
    names = {ep.name for ep in eps}
    assert "m5wireless" in names
    ep = next(ep for ep in eps if ep.name == "m5wireless")
    assert ep.value == "m5wireless.cli:main"
