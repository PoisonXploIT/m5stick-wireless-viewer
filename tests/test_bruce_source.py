"""Tests de BruceStorageSource (poller storage list/read) sin hardware.

El transporte serial es un fake inyectado en ``_open_port`` que reacciona a
los comandos como lo hace la CLI real de Bruce:

- ``storage list <dir>`` -> lineas ``<ruta> <tamano>`` y silencio.
- ``storage read <path>`` -> echo ``COMMAND: ...\\r\\n`` + bytes crudos.
"""

from __future__ import annotations

import asyncio

from m5wireless.source.bruce_source import BruceStorageSource


class FakeSerial:
    """Transporte serial falso con storage en memoria."""

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files = dict(files or {})
        self._line_queue: list[bytes] = []
        self._pending_raw = b""
        self.written: list[str] = []

    # --- API de transporte (misma forma que serial.Serial, lo que usa la fuente) ---
    def write(self, data: bytes) -> int:
        text = data.decode("utf-8")
        self.written.append(text)
        if text.startswith("storage list "):
            directory = text.strip().split(None, 2)[2]
            for path in self.files:
                if path == directory or path.startswith(directory + "/"):
                    size = len(self.files[path])
                    self._line_queue.append(f"{path} {size}\r\n".encode())
        elif text.startswith("storage read "):
            path = text.strip().split(None, 2)[2]
            data_bytes = self.files.get(path, b"")
            # Echo COMMAND de longitud variable + bytes crudos.
            self._pending_raw = f"COMMAND: storage read {path}\r\n".encode() + data_bytes
        return len(data)

    def readline(self) -> bytes:
        if self._line_queue:
            return self._line_queue.pop(0)
        return b""

    def read(self, size: int) -> bytes:
        chunk = self._pending_raw[:size]
        self._pending_raw = self._pending_raw[size:]
        return chunk

    def close(self) -> None:  # pragma: no cover - sin efecto en el fake.
        pass


def _make_source(fake: FakeSerial, poll_interval: float = 0.2) -> BruceStorageSource:
    source = BruceStorageSource(port="FAKE", baudrate=115200, poll_interval=poll_interval)
    source._open_port = lambda: fake
    return source


def _run(source: BruceStorageSource, lines: list[str], files: dict[str, list[bytes]]) -> None:
    async def scenario() -> None:
        source.observe_files(lambda p, d: files.setdefault(p, []).append(d))
        task = asyncio.create_task(source.start(lines.append))
        await asyncio.sleep(0.9)  # varios polls a 0.2s.
        await source.stop()
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(scenario())


class TestBruceStorageSource:
    def test_new_file_extracted_exactly_once(self) -> None:
        file_bytes = b"\xd4\xc3\xb2\xa1" + b"\x00" * 20
        fake = FakeSerial({"BrucePCAP/handshakes/HS_test.pcap": file_bytes})
        source = _make_source(fake)
        lines: list[str] = []
        files: dict[str, list[bytes]] = {}
        _run(source, lines, files)
        assert files == {"BrucePCAP/handshakes/HS_test.pcap": [file_bytes]}

    def test_changed_file_is_reread(self) -> None:
        file_bytes_v1 = b"\xd4\xc3\xb2\xa1" + b"\x00" * 20
        fake = FakeSerial({"BrucePCAP/handshakes/HS_test.pcap": file_bytes_v1})
        source = _make_source(fake)
        lines: list[str] = []
        files: dict[str, list[bytes]] = {}

        async def scenario() -> None:
            source.observe_files(lambda p, d: files.setdefault(p, []).append(d))
            task = asyncio.create_task(source.start(lines.append))
            await asyncio.sleep(0.5)  # primer read OK.
            fake.files["BrucePCAP/handshakes/HS_test.pcap"] = (
                b"\xd4\xc3\xb2\xa1" + b"\x00" * 40  # tamano nuevo -> re-lectura.
            )
            await asyncio.sleep(0.6)
            await source.stop()
            await asyncio.wait_for(task, timeout=5)

        asyncio.run(scenario())
        assert len(files["BrucePCAP/handshakes/HS_test.pcap"]) == 2
        assert (
            files["BrucePCAP/handshakes/HS_test.pcap"][1]
            == fake.files["BrucePCAP/handshakes/HS_test.pcap"]
        )

    def test_console_lines_are_delivered(self) -> None:
        fake = FakeSerial()
        fake._line_queue.append(b"[   7.8s] Selected: Sniffer\r\n")
        source = _make_source(fake)
        lines: list[str] = []
        files: dict[str, list[bytes]] = {}
        _run(source, lines, files)
        assert "[   7.8s] Selected: Sniffer" in lines

    def test_non_pcap_entries_are_ignored(self) -> None:
        fake = FakeSerial(
            {
                "BrucePCAP/creds.csv": b"a,b,c\r\n1,2,3",
                "BrucePCAP/handshakes/HS_a.pcap": b"\xd4\xc3\xb2\xa1" + b"\x00" * 8,
            }
        )
        source = _make_source(fake)
        lines: list[str] = []
        files: dict[str, list[bytes]] = {}
        _run(source, lines, files)
        assert list(files) == ["BrucePCAP/handshakes/HS_a.pcap"]

    def test_status_reports_state(self) -> None:
        fake = FakeSerial()
        source = _make_source(fake)
        status = source.status()
        assert status["port"] == "FAKE"
        assert status["baudrate"] == 115200
        assert "BrucePCAP/handshakes" in tuple(status["dirs"])

    def test_commands_sent_are_storage_list_and_read(self) -> None:
        file_bytes = b"\xd4\xc3\xb2\xa1" + b"\x00" * 8
        fake = FakeSerial({"BrucePCAP/handshakes/HS_x.pcap": file_bytes})
        source = _make_source(fake)
        lines: list[str] = []
        files: dict[str, list[bytes]] = {}
        _run(source, lines, files)
        assert any(w.startswith("storage list BrucePCAP/handshakes") for w in fake.written)
        assert any(w == "storage read BrucePCAP/handshakes/HS_x.pcap\r\n" for w in fake.written)
