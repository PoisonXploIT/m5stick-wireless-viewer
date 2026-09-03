"""CLI unificada de m5wireless (Fase 5).

Reemplaza los scripts dispersos del repo legado (``serial_logger.py``,
``auto_save_html.py``, ``log_to_csv.py``):

- ``m5wireless run``: captura en vivo + dashboard web. Modo por defecto:
  fuente serial; ``--source file --log-path scan.log`` para leer un log.
- ``m5wireless export csv|json``: conversión offline de un log a CSV/JSON,
  sin servidor y sin dependencias web.
- ``m5wireless snapshot``: polling del dashboard HTML (equivalente legacy de
  ``auto_save_html.py``).

Precedencia de configuración: CLI > variables de entorno ``M5W_*`` >
fichero ``m5wireless.toml`` > valores por defecto. El fichero toml se busca
en la ruta de ``--config``, luego ``./m5wireless.toml`` y
``~/.config/m5wireless/m5wireless.toml``.

El exporter Splunk HEC se activa solo cuando hay URL y token configurados
(``M5W_SPLUNK_HEC_URL`` / ``M5W_SPLUNK_HEC_TOKEN`` o toml); el colector lo
dispara automáticamente, no es una acción manual.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__
from .models import SourceType, utc_now
from .parser.registry import get_parser
from .source.base import AbstractSource
from .source.file_source import FileSource
from .source.serial_source import SerialSource
from .store.base import ObservationRow, event_to_observation_row
from .store.memory_store import MemoryStore
from .store.sqlite_store import SQLiteStore
from .worker.collector import Collector

logger = logging.getLogger(__name__)

CSV_COLUMNS = (
    "timestamp",
    "firmware",
    "source",
    "event_type",
    "bssid",
    "rssi",
    "client_mac",
    "raw_line",
)

DEFAULTS: dict[str, Any] = {
    "source": "file",
    "port": None,
    "baudrate": 115200,
    "log_path": "scan.log",
    "firmware": "auto",
    "host": "0.0.0.0",
    "web_port": 8000,
    "db_path": None,
    "url": None,
    "bruce_user": None,
    "bruce_password": None,
    "splunk_url": None,
    "splunk_token": None,
    "splunk_verify_ssl": True,
}

_ENV_MAP: dict[str, str] = {
    "source": "M5W_SOURCE",
    "log_path": "M5W_LOG_PATH",
    "firmware": "M5W_FIRMWARE",
    "host": "M5W_HOST",
    "web_port": "M5W_WEB_PORT",
    "db_path": "M5W_DB_PATH",
    "url": "M5W_URL",
    "bruce_user": "M5W_BRUCE_USER",
    "bruce_password": "M5W_BRUCE_PASSWORD",
    "splunk_url": "M5W_SPLUNK_HEC_URL",
    "splunk_token": "M5W_SPLUNK_HEC_TOKEN",
    "splunk_verify_ssl": "M5W_SPLUNK_VERIFY_SSL",
}

# IP por defecto del softAP de Bruce (BruceNet) segun la fuente del firmware.
BRUCE_WEB_DEFAULT_URL = "http://192.168.4.1"


def _find_config_file(explicit: str | None) -> Path | None:
    if explicit is not None:
        path = Path(explicit)
        return path if path.exists() else None
    candidates = (
        Path("m5wireless.toml"),
        Path.home() / ".config" / "m5wireless" / "m5wireless.toml",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _load_config_file(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    config: dict[str, Any] = {}
    for section in ("run", "splunk"):
        raw = data.get(section)
        if not isinstance(raw, dict):
            continue
        prefix = "" if section == "run" else "splunk_"
        for key, value in raw.items():
            config[f"{prefix}{key}"] = value
    return config


def _demo_log_path() -> Path:
    """Log de ejemplo incluido en el paquete (modo ``run --demo``)."""
    return Path(__file__).parent / "data" / "demo_scan.log"


def _resolve_run_config(args: argparse.Namespace) -> dict[str, Any]:
    """CLI > env (M5W_*) > m5wireless.toml > defaults."""
    config: dict[str, Any] = dict(DEFAULTS)
    file_path = _find_config_file(getattr(args, "config", None))
    if file_path is not None:
        config.update(_load_config_file(file_path))
    for key, env_name in _ENV_MAP.items():
        value = os.environ.get(env_name)
        if value is not None and value != "":
            config[key] = value
    # CLI por último; solo lo que el usuario escribió (default=None en el parser).
    cli_keys = (
        "source",
        "port",
        "baudrate",
        "log_path",
        "firmware",
        "host",
        "web_port",
        "db_path",
        "url",
    )
    for key in cli_keys:
        value = getattr(args, key)
        if value is not None:
            config[key] = value
    # --user/--password solo existen en `run`; mapean a bruce_user/bruce_password.
    user = getattr(args, "user", None)
    password = getattr(args, "password", None)
    if user is not None:
        config["bruce_user"] = user
    if password is not None:
        config["bruce_password"] = password
    # Normalización de tipos (env/toml llegan como str).
    config["baudrate"] = int(config["baudrate"])
    config["web_port"] = int(config["web_port"])
    verify = config["splunk_verify_ssl"]
    if isinstance(verify, str):
        config["splunk_verify_ssl"] = verify.strip().lower() in ("1", "true", "yes", "on")
    else:
        config["splunk_verify_ssl"] = bool(verify)
    return config


def _row_to_values(row: ObservationRow) -> tuple[Any, ...]:
    return (
        row.timestamp.isoformat(),
        row.firmware,
        row.source,
        row.event_type,
        row.bssid or "",
        "" if row.rssi is None else str(row.rssi),
        row.client_mac or "",
        row.raw_line,
    )


def _row_to_dict(row: ObservationRow) -> dict[str, Any]:
    return {
        "timestamp": row.timestamp.isoformat(),
        "firmware": row.firmware,
        "source": row.source,
        "event_type": row.event_type,
        "bssid": row.bssid,
        "rssi": row.rssi,
        "client_mac": row.client_mac,
        "raw_line": row.raw_line,
    }


def _parse_file(path: Path, firmware: str) -> list[ObservationRow]:
    """Parsea un log completo offline (sin store): una fila por evento."""
    parser = get_parser(firmware)
    rows: list[ObservationRow] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        event = parser.parse(line, received_at=utc_now(), source="file")
        if event is not None:
            rows.append(event_to_observation_row(event))
    return rows


def _cmd_export(fmt: str, args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"error: no existe el fichero de entrada: {input_path}", file=sys.stderr)
        return 2
    rows = _parse_file(input_path, args.firmware)
    output_path = Path(args.output)
    if fmt == "csv":
        with output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(CSV_COLUMNS)
            writer.writerows(_row_to_values(row) for row in rows)
    else:
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump([_row_to_dict(row) for row in rows], handle, ensure_ascii=False, indent=2)
    print(f"{len(rows)} eventos -> {output_path}")
    return 0


def _cmd_snapshot(args: argparse.Namespace) -> int:
    """Polling del dashboard HTML (legacy auto_save_html)."""
    dir_path = Path(args.dir)
    dir_path.mkdir(parents=True, exist_ok=True)
    count = 0
    while True:
        try:
            with urllib.request.urlopen(args.url, timeout=15) as response:
                html = response.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"aviso: no se pudo descargar {args.url}: {exc}", file=sys.stderr)
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        target = dir_path / f"snapshot-{stamp}.html"
        # Evita pisar dos snapshots en el mismo segundo.
        suffix = 0
        while target.exists():
            suffix += 1
            target = dir_path / f"snapshot-{stamp}-{suffix}.html"
        target.write_text(html, encoding="utf-8")
        print(f"snapshot guardado: {target}")
        count += 1
        if args.max is not None and count >= args.max:
            break
        time.sleep(args.interval)
    return 0


def _build_splunk_exporter(cfg: dict[str, Any]) -> Any | None:
    url = cfg["splunk_url"]
    token = cfg["splunk_token"]
    if not url or not token:
        return None
    try:
        from .exporter.splunk_hec import SplunkHecConfig, SplunkHecExporter
    except ImportError:
        print(
            "aviso: Splunk HEC configurado pero httpx no está instalado; "
            "instala el extra con: pip install m5wireless[splunk]",
            file=sys.stderr,
        )
        return None
    verify = cfg["splunk_verify_ssl"]
    if isinstance(verify, str):
        verify = verify.strip().lower() in ("1", "true", "yes", "on")
    config = SplunkHecConfig(url=str(url), token=str(token), verify=bool(verify))
    return SplunkHecExporter(config)


def _cmd_bruce(action: str, args: argparse.Namespace) -> int:
    """Control remoto minimo de Bruce via WebUI (info/reboot/cmd)."""
    from .bruce_api import BruceWebClient, BruceWebError

    url = args.url or BRUCE_WEB_DEFAULT_URL
    try:
        with BruceWebClient(url, username=args.user, password=args.password) as client:
            if action == "info":
                info = client.systeminfo()
                print(json.dumps(info, indent=2, ensure_ascii=False))
                return 0
            if action == "reboot":
                client.reboot()
                print(f"reinicio enviado a {url}")
                return 0
            reply = client.run_command(args.cmnd)
            print(reply.strip())
            print(
                "aviso: con el sniffer activo la WebUI bloquea la shell; "
                "la unica salida limpia es 'm5wireless bruce reboot'"
            )
            return 0
    except BruceWebError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _cmd_ports(args: argparse.Namespace) -> int:
    """Lista puertos serie con una pista de que placa hay detras."""
    from .source.serial_source import list_ports, port_hint

    try:
        ports = list_ports()
    except ImportError:
        print(
            "error: pyserial no esta instalado; instala el extra con: "
            "pip install m5wireless[serial]",
            file=sys.stderr,
        )
        return 3
    if not ports:
        print("no se encontro ningun puerto serial", file=sys.stderr)
        print(
            "pista: comprueba el cable USB, cambia de puerto y vuelve a ejecutar "
            "este comando (el dispositivo suele tardar unos segundos en aparecer)"
        )
        return 2
    for info in ports:
        hint = port_hint(info)
        vid_pid = f"{info.vid}/{info.pid}" if info.vid and info.pid else "-"
        print(f"{info.device}  {vid_pid}  {hint or ''}  {info.description}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    cfg = _resolve_run_config(args)
    if getattr(args, "demo", False):
        demo_path = _demo_log_path()
        cfg["source"] = "file"
        cfg["log_path"] = str(demo_path)
    try:
        parser = get_parser(str(cfg["firmware"]))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    # El canal de ficheros de Bruce se cablea antes que el colector; la caja
    # se rellena cuando el colector existe (on_file no corre antes: el
    # poller arranca con `run`, ya despues del colector).
    collector_box: dict[str, Any] = {}
    source: AbstractSource
    if cfg["source"] == "bruce":
        from .parser.pcap import PcapParser
        from .source.bruce_source import BruceStorageSource
        from .source.bruce_source import artifacts_dir as _ensure_dir

        baudrate = int(cfg["baudrate"])
        source = BruceStorageSource(cfg["port"], baudrate)
        pcap_parser = PcapParser()
        raw_artifacts = getattr(args, "artifacts_dir", None)
        out_dir = _ensure_dir(raw_artifacts) if raw_artifacts else None

        def on_file(path: str, data: bytes) -> None:
            try:
                events = pcap_parser.parse(data, source="serial")
            except Exception:
                logger.exception("no se pudo parsear el pcap %s", path)
                return
            if out_dir is not None:
                (out_dir / Path(path).name).write_bytes(data)
            collector_box["collector"].submit_events(events)

        source.observe_files(on_file)
    elif cfg["source"] == "bruce-web":
        from .parser.pcap import PcapParser
        from .source.bruce_source import artifacts_dir as _ensure_dir
        from .source.bruce_web_source import BruceWebSource

        url = str(cfg["url"])
        source = BruceWebSource(
            url,
            username=cfg["bruce_user"],
            password=cfg["bruce_password"],
        )
        pcap_parser = PcapParser()
        raw_artifacts = getattr(args, "artifacts_dir", None)
        out_dir = _ensure_dir(raw_artifacts) if raw_artifacts else None

        def on_file(path: str, data: bytes) -> None:
            try:
                events = pcap_parser.parse(data, source="serial")
            except Exception:
                logger.exception("no se pudo parsear el pcap %s", path)
                return
            if out_dir is not None:
                (out_dir / Path(path).name).write_bytes(data)
            collector_box["collector"].submit_events(events)

        source.observe_files(on_file)
    elif cfg["source"] == "serial":
        from .source.serial_source import list_ports, pick_port

        baudrate = int(cfg["baudrate"])
        info = pick_port(cfg["port"])
        if info is None:
            print(
                "error: no se encontro ningun puerto serial para autodetecion",
                file=sys.stderr,
            )
            ports = list_ports()
            if ports:
                print("puertos encontrados (usa --port):", file=sys.stderr)
                for found in ports:
                    print(f"  {found.device}  {found.description}", file=sys.stderr)
            else:
                print("pista: ejecuta 'm5wireless ports' con el dispositivo conectado")
            return 2
        from .source.serial_source import port_hint

        desc = info.description if info.description else ""
        hint = port_hint(info)
        print(
            f"m5wireless: serial {info.device} @ {baudrate} ({desc})"
            + (f" [{hint}]" if hint else "")
        )
        source = SerialSource(info.device, baudrate)
    else:
        log_path = Path(str(cfg["log_path"]))
        if not log_path.exists():
            print(f"error: no existe el fichero de log: {log_path}", file=sys.stderr)
            return 2
        # El demo reproduce el log completo de una vez (follow=False); un log
        # real en modo file se sigue como tail -f (follow=True).
        follow = not getattr(args, "demo", False)
        source = FileSource(log_path, follow=follow)
    try:
        from .web.app import create_app
    except ImportError as exc:
        print(
            f"error: faltan dependencias web ({exc}); instala el extra con: "
            "pip install m5wireless[web]",
            file=sys.stderr,
        )
        return 3
    db_path = cfg["db_path"]
    store = SQLiteStore(db_path) if db_path else MemoryStore()
    source_type: SourceType = (
        "serial" if cfg["source"] in ("serial", "bruce", "bruce-web") else "file"
    )
    collector = Collector(source, parser, store, source_type=source_type)
    collector_box["collector"] = collector
    exporter = _build_splunk_exporter(cfg)
    app = create_app(store, collector=collector, exporter=exporter)
    import uvicorn

    host = str(cfg["host"])
    port = int(cfg["web_port"])
    if getattr(args, "demo", False):
        print(f"m5wireless: DEMO (log de ejemplo: {cfg['log_path']})")
    print(f"m5wireless: fuente={cfg['source']} web=http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="m5wireless",
        description="Pipeline de datos y dashboard para firmwares WiFi de hardware hacking en ESP32.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_p = subparsers.add_parser("run", help="captura + dashboard web (por defecto)")
    run_p.add_argument(
        "--source",
        choices=("serial", "file", "bruce", "bruce-web"),
        default=None,
        help=(
            "serial = Marauder/Evil-M5Project en vivo; bruce = CLI Bruce + poller de "
            "storage; bruce-web = WebUI HTTP de Bruce (sin serial)"
        ),
    )
    run_p.add_argument(
        "--demo",
        action="store_true",
        help="modo demo: reproduce el log de ejemplo incluido, sin hardware",
    )
    run_p.add_argument(
        "--port", default=None, help="puerto serial (p. ej. COM3); None = autodetección"
    )
    run_p.add_argument("--baudrate", type=int, default=None)
    run_p.add_argument("--log-path", default=None, help="fichero del log (source=file)")
    run_p.add_argument("--firmware", default=None, help="auto | marauder | evil_m5project")
    run_p.add_argument("--host", default=None)
    run_p.add_argument("--web-port", dest="web_port", type=int, default=None)
    run_p.add_argument("--url", default=None, help="URL de la WebUI de Bruce (source=bruce-web)")
    run_p.add_argument(
        "--user", default=None, help="usuario WebUI (source=bruce-web; fabrica: admin)"
    )
    run_p.add_argument(
        "--password",
        default=None,
        help="password WebUI (source=bruce-web; fabrica: bruce)",
    )
    run_p.add_argument(
        "--artifacts-dir",
        dest="artifacts_dir",
        default=None,
        help="guardar los pcaps extraidos de Bruce como artifacts (source=bruce/bruce-web)",
    )
    run_p.add_argument("--db-path", default=None, help="SQLite persistente; sin valor = memoria")
    run_p.add_argument("--config", default=None, help="fichero m5wireless.toml específico")
    run_p.set_defaults(func=_cmd_run)

    bruce_p = subparsers.add_parser(
        "bruce", help="control remoto de Bruce via WebUI HTTP (info/reboot/cmd)"
    )
    bruce_sub = bruce_p.add_subparsers(dest="bruce_action", required=True)
    for name, help_text in (
        ("info", "systeminfo: version y uso de almacenamiento"),
        ("reboot", "reinicia el dispositivo (unica salida limpia del sniffer)"),
        ("cmd", "ejecuta un comando de la shell serial remota"),
    ):
        p = bruce_sub.add_parser(name, help=help_text)
        p.add_argument(
            "--url",
            default=None,
            help=f"URL de la WebUI (por defecto {BRUCE_WEB_DEFAULT_URL})",
        )
        p.add_argument("--user", default=None, help="usuario WebUI (fabrica: admin)")
        p.add_argument("--password", default=None, help="password WebUI (fabrica: bruce)")
        if name == "cmd":
            p.add_argument("cmnd", help="comando de la shell de Bruce")
        p.set_defaults(func=lambda a, _action=name: _cmd_bruce(_action, a))

    ports_p = subparsers.add_parser(
        "ports", help="listar puertos serie y pistas de placa (M5Stick/ESP32)"
    )
    ports_p.set_defaults(func=_cmd_ports)

    export_p = subparsers.add_parser("export", help="conversión offline de un log")
    export_sub = export_p.add_subparsers(dest="format", required=True)
    for fmt in ("csv", "json"):
        fmt_p = export_sub.add_parser(fmt, help=f"log -> {fmt.upper()}")
        fmt_p.add_argument("--input", required=True)
        fmt_p.add_argument("--firmware", default="auto")
        fmt_p.add_argument("--output", required=True)
        fmt_p.set_defaults(func=lambda a, f=fmt: _cmd_export(f, a))

    snap_p = subparsers.add_parser("snapshot", help="guardar el dashboard HTML periódicamente")
    snap_p.add_argument("--url", default="http://localhost:8000")
    snap_p.add_argument("--interval", type=float, default=60.0)
    snap_p.add_argument("--dir", default="snapshots")
    snap_p.add_argument(
        "--max", type=int, default=None, help="número máximo de snapshots (debug/tests)"
    )
    snap_p.set_defaults(func=_cmd_snapshot)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada ``m5wireless``. Devuelve el código de salida."""
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("detenido por el usuario")
        return 130


if __name__ == "__main__":
    sys.exit(main())
