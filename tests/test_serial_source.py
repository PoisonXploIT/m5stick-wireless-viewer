"""Tests de SerialSource sin hardware: transporte falso inyectado.

Cubre los dos comportamientos que no se pueden probar contra un M5Stick real:
que las lineas llegan al callback, y que una conexion perdida se reintenta con
backoff hasta recuperarse.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from m5wireless.source import SerialSource


class _FakePort:
    """Transporte falso: entrega unas lineas fijas y luego 'no hay datos'."""

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)
        self.closed = False

    def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        time.sleep(0.01)  # simula el timeout de pyserial para no hacer spin.
        return b""

    def close(self) -> None:
        self.closed = True


async def _run_to_completion(source: SerialSource, timeout: float = 3.0) -> list[str]:
    """Ejecuta start() hasta que termine solo (p. ej. reintentos agotados)."""
    received: list[str] = []
    task = asyncio.create_task(source.start(lambda line: received.append(line)))
    try:
        await asyncio.wait_for(task, timeout)
    finally:
        await source.stop()
    return received


async def _collect(source: SerialSource, expected: int, timeout: float = 3.0) -> list[str]:
    received: list[str] = []
    done = asyncio.Event()

    def cb(line: str) -> None:
        received.append(line)
        if len(received) >= expected:
            done.set()

    task = asyncio.create_task(source.start(cb))
    try:
        await asyncio.wait_for(done.wait(), timeout)
    finally:
        await source.stop()
        await task
    return received


def test_delivers_lines_and_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakePort([b"linea uno\n", b"linea dos\n"])

    def fake_open(self: SerialSource) -> _FakePort:
        return fake

    monkeypatch.setattr(SerialSource, "_open_port", fake_open)
    source = SerialSource(port="COM9")

    received = asyncio.run(_collect(source, expected=2))
    assert received == ["linea uno", "linea dos"]
    assert fake.closed  # el transporte se cierra al parar.


def test_reconnects_after_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    recovered = _FakePort([b"recovered\n"])

    def fake_open(self: SerialSource) -> _FakePort:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("no hay dispositivo")
        return recovered

    monkeypatch.setattr(SerialSource, "_open_port", fake_open)
    source = SerialSource(port="COM9", max_retries=3, base_backoff=0.01)

    received = asyncio.run(_collect(source, expected=1))
    assert received == ["recovered"]
    assert calls["n"] >= 2  # la primera fallo y la segunda reconecto.


def test_gives_up_after_max_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_open(self: SerialSource) -> _FakePort:
        calls["n"] += 1
        raise ConnectionError("siempre fallando")

    monkeypatch.setattr(SerialSource, "_open_port", fake_open)
    source = SerialSource(port="COM9", max_retries=2, base_backoff=0.01)

    # No llega ninguna linea; start debe terminar solo tras agotar reintentos.
    received = asyncio.run(_run_to_completion(source))
    assert received == []
    assert calls["n"] == 3  # 1 intento inicial + 2 reintentos.
