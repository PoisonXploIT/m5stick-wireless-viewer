"""Tests de BruceWebClient contra un fake de la WebUI con httpx.MockTransport.

El fake replica el comportamiento del firmware real
(``src/core/wifi/webInterface.cpp``): login por cookie ``BRUCESESSION``,
401 en todo endpoint sin sesion, listado ``pa:/Fo:/Fi:``, download por bytes,
``/cm`` por form y ``/reboot`` sin cuerpo.
"""

from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from m5wireless.bruce_api import (
    BruceFileEntry,
    BruceWebAuthError,
    BruceWebClient,
    BruceWebError,
)

BASE_URL = "http://bruce.test"
SESSION_TOKEN = "tok-abc123"


class FakeWebUI:
    """Estado + handler httpx.MockTransport fiel al firmware."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {"/BrucePCAP/handshakes/a.pcap": b"PCAPBYTES"}
        self.size_texts: dict[str, str] = {"/BrucePCAP/handshakes/a.pcap": "11 B"}
        self.username = "admin"
        self.password = "bruce"
        self.seen_cookies: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login":
            form = parse_qs(request.content.decode())
            user = form.get("username", [""])[0]
            pwd = form.get("password", [""])[0]
            headers: dict[str, str] = {"location": "/"}
            if user == self.username and pwd == self.password:
                headers["set-cookie"] = f"BRUCESESSION={SESSION_TOKEN}; Path=/; HttpOnly"
            else:
                headers["location"] = "/?failed"
            return httpx.Response(302, headers=headers)

        # Todos los demas endpoints exigen sesion (checkUserWebAuth).
        cookie = request.headers.get("cookie", "")
        self.seen_cookies.append(cookie)
        if f"BRUCESESSION={SESSION_TOKEN}" not in cookie:
            return httpx.Response(401, text="Unauthorized")

        if request.url.path == "/systeminfo":
            body = {
                "BRUCE_VERSION": "1.2.3",
                "SD": {"free": "7.9 GB", "used": "100 kB", "total": "8.0 GB"},
                "LittleFS": {"free": "1.4 MB", "used": "100 kB", "total": "1.5 MB"},
            }
            return httpx.Response(200, json=body)

        if request.url.path == "/listfiles":
            folder = request.url.params.get("folder", "/")
            prefix = folder if folder.endswith("/") else folder + "/"
            lines = [f"pa:{folder}:0"]
            for path in sorted(self.files):
                if not path.startswith(prefix):
                    continue
                rest = path[len(prefix):]
                if "/" in rest:  # subdirectorio: solo la primera parte.
                    lines.append(f"Fo:{rest.split('/')[0]}:0")
                else:
                    lines.append(f"Fi:{rest}:{self.size_texts[path]}")
            return httpx.Response(200, text="\n".join(lines) + "\n")

        if request.url.path == "/file":
            name = request.url.params.get("name", "")
            action = request.url.params.get("action", "")
            if action != "download" or name not in self.files:
                return httpx.Response(400, text="ERROR: file does not exist")
            return httpx.Response(200, content=self.files[name])

        if request.url.path == "/cm":
            form = parse_qs(request.content.decode())
            cmnd = form.get("cmnd", [""])[0]
            if not cmnd:
                return httpx.Response(400, text="http request missing required arg: cmnd")
            return httpx.Response(200, text=f"command {cmnd} queued")

        if request.url.path == "/reboot":
            return httpx.Response(200, text="")

        return httpx.Response(404, text="not found")


@pytest.fixture
def fake() -> FakeWebUI:
    return FakeWebUI()


@pytest.fixture
def client(fake: FakeWebUI) -> BruceWebClient:
    transport = httpx.MockTransport(fake.handler)
    http_client = httpx.Client(base_url=BASE_URL, transport=transport)
    c = BruceWebClient(BASE_URL, username="admin", password="bruce", client=http_client)
    yield c
    c.close()


def test_systeminfo_returns_json(client: BruceWebClient) -> None:
    info = client.systeminfo()
    assert info["BRUCE_VERSION"] == "1.2.3"
    assert info["SD"]["total"] == "8.0 GB"


def test_login_success_sends_cookie_on_subsequent_requests(
    client: BruceWebClient, fake: FakeWebUI
) -> None:
    client.systeminfo()
    # La cookie BRUCESESSION viaja en la peticion (jar de httpx).
    assert any(f"BRUCESESSION={SESSION_TOKEN}" in c for c in fake.seen_cookies)


def test_login_failure_raises_auth_error(fake: FakeWebUI) -> None:
    transport = httpx.MockTransport(fake.handler)
    http_client = httpx.Client(base_url=BASE_URL, transport=transport)
    bad = BruceWebClient(
        BASE_URL, username="admin", password="NOESLA", client=http_client
    )
    with pytest.raises(BruceWebAuthError):
        bad.systeminfo()
    bad.close()


def test_unauthenticated_401_raises_auth_error(fake: FakeWebUI) -> None:
    transport = httpx.MockTransport(fake.handler)
    http_client = httpx.Client(base_url=BASE_URL, transport=transport)
    anon = BruceWebClient(BASE_URL, client=http_client)
    with pytest.raises(BruceWebAuthError):
        anon.list_files()
    anon.close()


def test_list_files_parses_only_file_entries(client: BruceWebClient) -> None:
    entries = client.list_files(fs="SD", folder="/BrucePCAP/handshakes")
    assert entries == [BruceFileEntry(name="a.pcap", size_text="11 B")]


def test_download_file_returns_bytes(client: BruceWebClient) -> None:
    data = client.download_file("/BrucePCAP/handshakes/a.pcap", fs="SD")
    assert data == b"PCAPBYTES"


def test_run_command_returns_reply(client: BruceWebClient) -> None:
    reply = client.run_command("power reboot")
    assert reply == "command power reboot queued"


def test_http_error_raises_bruce_web_error(client: BruceWebClient) -> None:
    with pytest.raises(BruceWebError):
        client.download_file("no-existe.pcap", fs="SD")


def test_reboot_swallows_transport_drop(fake: FakeWebUI) -> None:
    """El firmware hace ESP.restart() sin responder: el corte no es error."""

    def dropping(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/reboot":
            raise httpx.ConnectError("conexion cortada (reinicio)")
        return fake.handler(request)

    transport = httpx.MockTransport(dropping)
    http_client = httpx.Client(base_url=BASE_URL, transport=transport)
    c = BruceWebClient(BASE_URL, username="admin", password="bruce", client=http_client)
    c.reboot()  # no lanza.
    c.close()


def test_username_password_must_come_together(fake: FakeWebUI) -> None:
    with pytest.raises(ValueError):
        BruceWebClient(BASE_URL, username="solo-usuario")
