"""Tests de visibilidad de conexion (v3.0.1): m5wireless ports, autodeteccion,
hints de placa y estado de las fuentes para el dashboard."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from m5wireless import cli
from m5wireless.source import FileSource, SerialSource
from m5wireless.source.serial_source import PortInfo, pick_port, port_hint


def _mk(device: str, description: str = "", vid: str | None = None, pid: str | None = None) -> PortInfo:
    return PortInfo(device=device, description=description, vid=vid, pid=pid)


# ---- _vid_pid_from_hwid ----


@pytest.mark.parametrize(
    ("hwid", "expected"),
    [
        ("USB VID:PID=2E8A:0003 SER=5", ("2E8A", "0003")),
        ("PCI VEN_10DE&DEV_1B81", (None, None)),
        ("", (None, None)),
    ],
)
def test_vid_pid_from_hwid(hwid: str, expected: tuple[str | None, str | None]) -> None:
    from m5wireless.source.serial_source import _vid_pid_from_hwid

    assert _vid_pid_from_hwid(hwid) == expected


# ---- port_hint ----


@pytest.mark.parametrize(
    ("info", "expected"),
    [
        (_mk("COM3", "USB Serial (COM3)", "2E8A", "0003"), "posible M5Stick (M5Stack)"),
        (_mk("/dev/ttyACM0", "M5Stack-ESP32"), "posible M5Stick (M5Stack)"),
        (_mk("COM4", "USB JTAG CDC"), "ESP32 (CDC/JTAG)"),
        (_mk("COM5", "CP210x Universal Serial Bus to UART Bridge (COM5)"), "CP210x (UART)"),
        (_mk("COM6", "USB-SERIAL CH340 (COM6)"), "CH34x (UART)"),
        (_mk("COM7", "Intel(R) Managed I/O"), None),
    ],
)
def test_port_hint(info: PortInfo, expected: str | None) -> None:
    assert port_hint(info) == expected


# ---- pick_port ----


def test_pick_port_preferred_passthrough() -> None:
    info = pick_port("COM9")
    assert info is not None and info.device == "COM9"


def test_pick_port_prefers_m5stack(monkeypatch: pytest.MonkeyPatch) -> None:
    from m5wireless.source import serial_source

    ports = [
        _mk("COM1", "Intel(R) Managed I/O"),
        _mk("COM2", "USB Serial (COM2)", "2E8A", "0003"),
    ]
    monkeypatch.setattr(serial_source, "list_ports", lambda: ports)
    info = pick_port(None)
    assert info is not None and info.device == "COM2"


def test_pick_port_first_when_no_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from m5wireless.source import serial_source

    monkeypatch.setattr(
        serial_source, "list_ports", lambda: [_mk("COM1"), _mk("COM2")]
    )
    info = pick_port(None)
    assert info is not None and info.device == "COM1"


def test_pick_port_none_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    from m5wireless.source import serial_source

    monkeypatch.setattr(serial_source, "list_ports", list)
    assert pick_port(None) is None


# ---- m5wireless ports (CLI) ----


def test_cmd_ports_lists_with_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from m5wireless.source import serial_source

    monkeypatch.setattr(
        serial_source,
        "list_ports",
        lambda: [_mk("COM3", "USB Serial (COM3)", "2E8A", "0003")],
    )
    rc = cli.main(["ports"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "COM3" in out
    assert "M5Stick" in out


def test_cmd_ports_none_found(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from m5wireless.source import serial_source

    monkeypatch.setattr(serial_source, "list_ports", list)
    rc = cli.main(["ports"])
    out = capsys.readouterr()
    assert rc == 2
    assert "no se encontro ningun puerto serial" in out.err


def test_cmd_run_serial_no_port_fails_with_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from m5wireless.source import serial_source

    monkeypatch.setattr(serial_source, "list_ports", list)
    rc = cli.main(["run", "--source", "serial"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "no se encontro ningun puerto serial" in err


def test_cmd_run_serial_autodetect_prints_connection(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import uvicorn

    from m5wireless.source import serial_source

    monkeypatch.setattr(
        serial_source,
        "list_ports",
        lambda: [_mk("COM4", "USB Serial (COM4)", "2E8A", "0003")],
    )
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    rc = cli.main(["run", "--source", "serial"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "COM4 @ 115200" in out
    assert "M5Stick" in out


# ---- estado de fuentes para el dashboard ----


def test_file_source_status() -> None:
    source = FileSource(Path("scan.log"))
    status = source.status()
    assert status["state"] == "esperando"
    assert status["path"] == "scan.log"


class _FakePort:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        time.sleep(0.01)  # simula el timeout de pyserial.
        return b""

    def close(self) -> None:  # pragma: no cover - no usado aqui
        pass


def test_serial_source_status_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePort([b"linea\n"])

    def fake_open(self: SerialSource) -> _FakePort:
        return fake

    monkeypatch.setattr(SerialSource, "_open_port", fake_open)
    source = SerialSource(port="COM9")
    assert source.status()["state"] == "esperando"

    async def run_brief() -> None:
        done = asyncio.Event()

        def cb(line: str) -> None:
            done.set()

        task = asyncio.create_task(source.start(cb))
        try:
            await asyncio.wait_for(done.wait(), timeout=2.0)
            assert source.status()["state"] == "conectado"
            assert source.status()["port"] == "COM9"
        finally:
            await source.stop()
            await task

    asyncio.run(run_brief())


def test_serial_source_status_reconnecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_open(self: SerialSource) -> _FakePort:
        raise ConnectionError("no hay dispositivo")

    monkeypatch.setattr(SerialSource, "_open_port", fake_open)
    source = SerialSource(port="COM9", max_retries=1, base_backoff=0.01)

    async def run_brief() -> None:
        task = asyncio.create_task(source.start(lambda line: None))
        try:
            await asyncio.wait_for(task, timeout=2.0)
        finally:
            await source.stop()
        assert source.status()["state"] == "reconectando"

    asyncio.run(run_brief())


# ---- modo demo ----


def test_demo_log_shipped_in_package() -> None:
    path = cli._demo_log_path()
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "Found network:" in text


def test_run_help_mentions_demo(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["run", "--help"])
    assert excinfo.value.code == 0
    assert "--demo" in capsys.readouterr().out
