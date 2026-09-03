"""Tests de BruceStorageSource (poller storage list/read) sin hardware.

El transporte serial es un fake inyectado en ``_open_port`` que replica el
formato real de la CLI de Bruce, validado contra COM7:

- ``storage list <dir>`` -> echo ``COMMAND: ...\\r\\r\\n``, lineas
  ``<nombre>\\t<tamano>``, subdirectorios como ``<nombre>\\t<DIR>``,
  prompt ``# `` y silencio. El listado trae NOMBRES RELATIVOS al directorio.
- ``storage read <ruta>`` -> echo ``COMMAND: ...\\r\\r\\n`` + bytes crudos;
  si la ruta no existe, solo el echo (sin bytes), como en el hardware real.
"""

from __future__ import annotations

import asyncio

from m5wireless.source.bruce_source import BruceStorageSource


class FakeSerial:
    """Transporte serial falso fiel al formato real de la CLI de Bruce."""

    def __init__(
        self, files: dict[str, bytes] | None = None, ghosts: dict[str, int] | None = None
    ) -> None:
        self.files = dict(files or {})
        # Rutas que aparecen en el listado pero no devuelven bytes al leerlas.
        self.ghosts = dict(ghosts or {})
        self._line_queue: list[bytes] = []
        self._pending_raw = b""
        self.written: list[str] = []

    def _list_dir(self, directory: str) -> list[bytes]:
        lines: list[bytes] = []
        seen_dirs: set[str] = set()
        for path in sorted({**self.files, **self.ghosts}):
            if not path.startswith(directory + "/"):
                continue
            rest = path[len(directory) + 1 :]
            slash = rest.find("/")
            if slash != -1:
                subdir = rest[:slash]
                if subdir not in seen_dirs:
                    seen_dirs.add(subdir)
                    lines.append(f"{subdir}\t<DIR>\r\n".encode())
            else:
                size = len(self.files.get(path, b"")) or self.ghosts.get(path, 0)
                lines.append(f"{rest}\t{size}\r\n".encode())
        return lines

    # --- API de transporte (misma forma que serial.Serial, lo que usa la fuente) ---
    def write(self, data: bytes) -> int:
        text = data.decode("utf-8")
        self.written.append(text)
        if text.startswith("storage list "):
            directory = text.strip().split(None, 2)[2]
            self._line_queue.append(f"COMMAND: storage list {directory}\r\r\n".encode())
            self._line_queue.extend(self._list_dir(directory))
            self._line_queue.append(b"# ")
        elif text.startswith("storage read "):
            path = text.strip().split(None, 2)[2]
            echo = f"COMMAND: storage read {path}\r\r\n".encode()
            if path in self.files:
                self._pending_raw = echo + self.files[path] + b"\r\n# "
            else:
                # Ruta inexistente: solo echo y prompt, sin bytes.
                self._pending_raw = echo + b"# "
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

    def test_ghost_file_does_not_hang_or_emit(self) -> None:
        # Listado con un pcap que no devuelve bytes al leerse (ruta fantasma):
        # la fuente debe abortar la lectura sin colgar y sin emitir nada.
        fake = FakeSerial(ghosts={"BrucePCAP/handshakes/HS_gone.pcap": 48})
        source = _make_source(fake)
        lines: list[str] = []
        files: dict[str, list[bytes]] = {}
        _run(source, lines, files)
        assert files == {}

    def test_status_reports_state(self) -> None:
        fake = FakeSerial()
        source = _make_source(fake)
        status = source.status()
        assert status["port"] == "FAKE"
        assert status["baudrate"] == 115200
        assert "BrucePCAP/handshakes" in tuple(status["dirs"])

    def test_commands_sent_are_storage_list_and_read(self) -> None:
        # El fake lista nombres relativos y exige ruta completa en `storage read`:
        # si el source no mapea a ruta completa, el fichero nunca se emite.
        file_bytes = b"\xd4\xc3\xb2\xa1" + b"\x00" * 8
        fake = FakeSerial({"BrucePCAP/handshakes/HS_x.pcap": file_bytes})
        source = _make_source(fake)
        lines: list[str] = []
        files: dict[str, list[bytes]] = {}
        _run(source, lines, files)
        assert any(w.startswith("storage list BrucePCAP/handshakes") for w in fake.written)
        assert "storage read BrucePCAP/handshakes/HS_x.pcap\r\n" in fake.written
