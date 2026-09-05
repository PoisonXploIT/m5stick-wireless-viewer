"""Cliente HTTP para la WebUI de Bruce (M5Stick).

API validada en vivo y confirmada contra la fuente del firmware
(``embedded_resources`` / ``src/core/wifi/webInterface.cpp``):

- **Todos los endpoints exigen autenticacion**: cookie ``BRUCESESSION`` que
  se obtiene con ``POST /login`` (form ``username``/``password``, responde
  302 + ``Set-Cookie``; si falla, 302 a ``/?failed`` sin cookie). Credenciales
  de fabrica: ``admin``/``bruce``.
- ``GET /systeminfo`` -> JSON con version y uso SD/LittleFS.
- ``GET /listfiles?fs=&folder=`` -> texto plano, una entrada por linea:
  ``pa:<carpeta>:0``, ``Fo:<nombre>:0``, ``Fi:<nombre>:<tamano>``. El tamano
  es *humano* (``humanReadableSize``: "12.5 kB"), no bytes: es la unica
  clave disponible para dedup y su granularidad es la decima de la unidad.
- ``GET /file?fs=&name=<ruta>&action=download`` -> bytes del fichero
  (validado: identicos al extraido por serial).
- ``POST /cm`` (form ``cmnd``) -> ejecuta la shell serial remota. AVISO: con
  el sniffer corriendo la WebUI bloquea la shell; la unica salida limpia es
  ``/reboot``.
- ``GET /reboot`` -> ``ESP.restart()`` SIN respuesta: la conexion puede caer
  antes del 200; :meth:`BruceWebClient.reboot` tolera el corte de transporte.

Seguridad: los defaults publicados (AP ``BruceNet``/``brucenet``, WebUI
``admin``/``bruce``) hacen que un dispositivo con la WebUI activa en campo sea
accesible para cualquiera en esa red. Cambiar credenciales antes de usarlo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Self

import httpx

logger = logging.getLogger(__name__)


class BruceWebError(Exception):
    """Error de la WebUI de Bruce (HTTP >= 400 o respuesta inesperada)."""


class BruceWebAuthError(BruceWebError):
    """Credenciales rechazadas o sesion no autenticada (401)."""


@dataclass(frozen=True, slots=True)
class BruceFileEntry:
    """Entrada de fichero de ``/listfiles``.

    ``size_text`` es el tamano *legible* que envia el firmware ("12.5 kB");
    no existe tamano en bytes en la API, asi que (nombre, size_text) es la
    unica clave de dedup disponible.
    """

    name: str
    size_text: str


class BruceWebClient:
    """Cliente de la WebUI de Bruce con login por cookie.

    ``client`` inyectable para tests (``httpx.Client`` sobre
    ``httpx.MockTransport``); si no se pasa, se crea uno propio con
    ``base_url``. El jar de cookies de httpx guarda la ``BRUCESESSION`` del
    login y la envia en cada peticion siguiente.
    """

    def __init__(
        self,
        base_url: str,
        *,
        username: str | None = None,
        password: str | None = None,
        timeout: float = 15.0,
        client: httpx.Client | None = None,
    ) -> None:
        if (username is None) != (password is None):
            raise ValueError("username y password van juntos (o ninguno)")
        self._base_url = base_url
        self._username = username
        self._password = password
        self._owns_client = client is None
        if client is not None:
            self._client = client
        else:
            self._client = httpx.Client(base_url=base_url, timeout=timeout)
        self._logged_in = False

    # ---- autenticacion ----
    def _ensure_login(self) -> None:
        if self._logged_in or (self._username is None and self._password is None):
            return
        response = self._client.post(
            "/login",
            data={"username": self._username, "password": self._password},
        )
        set_cookie = response.headers.get("set-cookie", "")
        if "BRUCESESSION=" in set_cookie:
            self._logged_in = True
        else:
            raise BruceWebAuthError(
                "login rechazado por la WebUI de Bruce (usuario o password incorrectos)"
            )

    def _check(self, response: httpx.Response) -> None:
        if response.status_code == 401:
            raise BruceWebAuthError("no autenticado (401); revisa usuario y password")
        if response.status_code >= 400:
            raise BruceWebError(f"HTTP {response.status_code} de la WebUI: {response.text[:200]!r}")

    # ---- endpoints ----
    def systeminfo(self) -> dict[str, Any]:
        """``GET /systeminfo``: version y uso de SD/LittleFS."""
        self._ensure_login()
        response = self._client.get("/systeminfo")
        self._check(response)
        data: dict[str, Any] = response.json()
        return data

    def list_files(self, fs: str = "SD", folder: str = "/") -> list[BruceFileEntry]:
        """``GET /listfiles``: ficheros de un directorio (sin carpetas)."""
        self._ensure_login()
        response = self._client.get("/listfiles", params={"fs": fs, "folder": folder})
        self._check(response)
        entries: list[BruceFileEntry] = []
        for line in response.text.splitlines():
            parts = line.split(":", 2)
            if len(parts) != 3 or parts[0] != "Fi":
                continue  # pa:/Fo:/lineas malformadas no interesan.
            entries.append(BruceFileEntry(name=parts[1], size_text=parts[2]))
        return entries

    def download_file(self, name: str, *, fs: str = "SD") -> bytes:
        """``GET /file ... action=download``: bytes crudos del fichero."""
        self._ensure_login()
        response = self._client.get("/file", params={"fs": fs, "name": name, "action": "download"})
        self._check(response)
        return response.content

    def run_command(self, cmnd: str) -> str:
        """``POST /cm``: ejecuta un comando de la shell serial remota.

        Devuelve el texto de respuesta (p. ej. ``command X queued``).
        AVISO: con el sniffer activo la WebUI bloquea la shell; la unica
        salida limpia es :meth:`reboot`.
        """
        self._ensure_login()
        response = self._client.post("/cm", data={"cmnd": cmnd})
        self._check(response)
        return response.text

    def reboot(self) -> None:
        """``GET /reboot``: reinicia el dispositivo.

        El firmware hace ``ESP.restart()`` sin responder, asi que el corte de
        transporte se trata como exito (el dispositivo ya esta reiniciando).
        Los errores 401/4xx SÍ se propagan: no se debe asumir un reboot que
        no llego.
        """
        self._ensure_login()
        try:
            response = self._client.get("/reboot")
        except httpx.TransportError:
            logger.debug("conexion cortada en /reboot (esperado: el dispositivo reinicia)")
            return
        self._check(response)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
