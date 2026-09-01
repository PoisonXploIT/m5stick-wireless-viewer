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
    "splunk_url": "M5W_SPLUNK_HEC_URL",
    "splunk_token": "M5W_SPLUNK_HEC_TOKEN",
    "splunk_verify_ssl": "M5W_SPLUNK_VERIFY_SSL",
}


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
    cli_keys = ("source", "port", "baudrate", "log_path", "firmware", "host", "web_port", "db_path")
    for key in cli_keys:
        value = getattr(args, key)
        if value is not None:
            config[key] = value
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


def _cmd_run(args: argparse.Namespace) -> int:
    cfg = _resolve_run_config(args)
    try:
        parser = get_parser(str(cfg["firmware"]))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    source: AbstractSource
    if cfg["source"] == "serial":
        source = SerialSource(cfg["port"], int(cfg["baudrate"]))
    else:
        log_path = Path(str(cfg["log_path"]))
        if not log_path.exists():
            print(f"error: no existe el fichero de log: {log_path}", file=sys.stderr)
            return 2
        source = FileSource(log_path, follow=True)
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
    source_type: SourceType = "serial" if cfg["source"] == "serial" else "file"
    collector = Collector(source, parser, store, source_type=source_type)
    exporter = _build_splunk_exporter(cfg)
    app = create_app(store, collector=collector, exporter=exporter)
    import uvicorn

    host = str(cfg["host"])
    port = int(cfg["web_port"])
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
    run_p.add_argument("--source", choices=("serial", "file"), default=None)
    run_p.add_argument(
        "--port", default=None, help="puerto serial (p. ej. COM3); None = autodetección"
    )
    run_p.add_argument("--baudrate", type=int, default=None)
    run_p.add_argument("--log-path", default=None, help="fichero del log (source=file)")
    run_p.add_argument("--firmware", default=None, help="auto | marauder | evil_m5project")
    run_p.add_argument("--host", default=None)
    run_p.add_argument("--web-port", dest="web_port", type=int, default=None)
    run_p.add_argument("--db-path", default=None, help="SQLite persistente; sin valor = memoria")
    run_p.add_argument("--config", default=None, help="fichero m5wireless.toml específico")
    run_p.set_defaults(func=_cmd_run)

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
