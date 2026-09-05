"""Tests de BruceWebSource (poller /listfiles + download) sin hardware.

El servidor fake va por ``httpx.MockTransport``: mismo listado que el
firmware (``pa:/Fo:/Fi:<nombre>:<tamano legible>``) y download por bytes.
La logica de dedup se prueba contra ``_poll_once`` directamente, sin worker
ni sleeps.
"""

from __future__ import annotations

import httpx
import pytest

from m5wireless.source.bruce_web_source import BruceWebSource

BASE_URL = "http://bruce.test"


class FakeWebUI:
    """Estado de ficheros + handler MockTransport (sin auth: la cubre
    test_bruce_api; aqui solo importa el flujo del poller)."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {
            "/BrucePCAP/handshakes/a.pcap": b"PCAP-A-V1",
            "/BrucePCAP/notes.txt": b"no es un pcap",
        }
        self.size_texts: dict[str, str] = {
            "/BrucePCAP/handshakes/a.pcap": "10 B",
            "/BrucePCAP/notes.txt": "12 B",
        }
        # Ficheros que aparecen en el listado pero fallan en /file (ghosts).
        self.ghosts: dict[str, str] = {}
        self.downloads: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/listfiles":
            folder = request.url.params.get("folder", "/")
            prefix = folder if folder.endswith("/") else folder + "/"
            lines = [f"pa:{folder}:0"]
            for path in sorted({**self.size_texts, **self.ghosts}):
                if not path.startswith(prefix):
                    continue
                rest = path[len(prefix) :]
                if "/" in rest:
                    lines.append(f"Fo:{rest.split('/')[0]}:0")
                else:
                    size_text = self.size_texts.get(path, self.ghosts.get(path, "0 B"))
                    lines.append(f"Fi:{rest}:{size_text}")
            return httpx.Response(200, text="\n".join(lines) + "\n")

        if request.url.path == "/file":
            name = request.url.params.get("name", "")
            self.downloads.append(name)
            if name not in self.files:
                return httpx.Response(400, text="ERROR: file does not exist")
            return httpx.Response(200, content=self.files[name])

        return httpx.Response(404, text="not found")


@pytest.fixture
def fake() -> FakeWebUI:
    return FakeWebUI()


@pytest.fixture
def source(fake: FakeWebUI) -> BruceWebSource:
    transport = httpx.MockTransport(fake.handler)
    http_client = httpx.Client(base_url=BASE_URL, transport=transport)
    src = BruceWebSource(BASE_URL, client=http_client, dirs=("/BrucePCAP/handshakes",))
    src._running = True  # el worker no corre; _poll_once lo comprueba.
    return src


def test_poll_downloads_new_pcap_once(source: BruceWebSource, fake: FakeWebUI) -> None:
    collected: list[tuple[str, bytes]] = []
    source.observe_files(lambda path, data: collected.append((path, data)))

    source._poll_once()
    assert collected == [("/BrucePCAP/handshakes/a.pcap", b"PCAP-A-V1")]

    # Segundo poll: mismo (nombre, size_text) -> sin re-download.
    source._poll_once()
    assert collected == [("/BrucePCAP/handshakes/a.pcap", b"PCAP-A-V1")]
    assert fake.downloads == ["/BrucePCAP/handshakes/a.pcap"]


def test_poll_ignores_non_pcap_files(source: BruceWebSource, fake: FakeWebUI) -> None:
    collected: list[tuple[str, bytes]] = []
    source.observe_files(lambda path, data: collected.append((path, data)))

    source._poll_once()
    assert all(path.endswith(".pcap") for path, _ in collected)


def test_poll_redownloads_when_visible_size_changes(
    source: BruceWebSource, fake: FakeWebUI
) -> None:
    """Un pcap que crece se re-descarga entero cuando cambia el tamano visible."""
    collected: list[tuple[str, bytes]] = []
    source.observe_files(lambda path, data: collected.append((path, data)))

    source._poll_once()
    fake.files["/BrucePCAP/handshakes/a.pcap"] = b"PCAP-A-V2-MAS-LARGO"
    fake.size_texts["/BrucePCAP/handshakes/a.pcap"] = "19 B"
    source._poll_once()

    assert [data for _, data in collected] == [b"PCAP-A-V1", b"PCAP-A-V2-MAS-LARGO"]


def test_poll_same_size_text_no_redownload_even_if_bytes_change(
    source: BruceWebSource, fake: FakeWebUI
) -> None:
    """Limitacion documentada: el listado no trae bytes ni mtime; si el tamano
    legible no cambia, el contenido tampoco se re-descarga."""
    collected: list[tuple[str, bytes]] = []
    source.observe_files(lambda path, data: collected.append((path, data)))

    source._poll_once()
    fake.files["/BrucePCAP/handshakes/a.pcap"] = b"OTROS-BYTES-MISMO-TAMANO-!"[:10]
    # size_text sigue siendo "10 B".
    source._poll_once()

    assert len(collected) == 1


def test_status_reflects_files_read(source: BruceWebSource) -> None:
    collected: list[tuple[str, bytes]] = []
    source.observe_files(lambda path, data: collected.append((path, data)))
    source._poll_once()

    status = source.status()
    assert status["files_read"] == 1
    assert status["base_url"] == BASE_URL
    assert status["state"] == "conectado"


def test_download_error_is_swallowed_and_not_marked_seen(
    source: BruceWebSource, fake: FakeWebUI
) -> None:
    """Un 400 en /file no marca el fichero como visto: se reintenta al
    siguiente poll (p. ej. tras un reboot del dispositivo)."""
    collected: list[tuple[str, bytes]] = []
    source.observe_files(lambda path, data: collected.append((path, data)))

    ghost = "/BrucePCAP/handshakes/ghost.pcap"
    fake.ghosts[ghost] = "5 B"
    source._poll_once()
    # El ghost NO esta en collected ni marcado como visto; el pcap si.
    assert (ghost, b"GHOST") not in collected
    assert source.status()["files_read"] == 1

    # Se materializa el ghost: se descarga en el poll siguiente.
    fake.files[ghost] = b"GHOST"
    fake.size_texts[ghost] = "5 B"
    del fake.ghosts[ghost]
    source._poll_once()
    assert (ghost, b"GHOST") in collected
