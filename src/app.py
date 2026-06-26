#!/usr/bin/env python3
"""
Dashboard web para visualizar datos del M5Stick Plus 2 en tiempo real.
Mejoras v2.0:
  - Tail eficiente con seek-from-end (no carga el archivo entero en memoria)
  - API JSON en /api/networks para integracion con sec-dashboard o Splunk
  - API CSV en /api/networks.csv
  - Envio directo a Splunk HEC (opcional, variables de entorno)
  - Health check en /api/health
  - Refresh configurable via M5_REFRESH
  - Binding a 127.0.0.1 por defecto (no expone a la LAN sin querer)
  - Parseo de redes en vivo desde el log (no solo texto plano)
"""
import argparse
import csv
import io
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, render_template_string, request

# ---------------------------------------------------------------------------
# Configuracion
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_LOG = BASE_DIR.parent / "wifi_scan_evil.log"

LINE_RE = re.compile(
    r"\[(.*?)\] (.+) \(([0-9a-fA-F:]{17})\) on channel (\d+) has (\d+) clients:"
)
CLIENT_RE = re.compile(r"\[(.*?)\] - ([0-9a-fA-F:]{17})")

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------

TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <meta http-equiv="refresh" content="{{ refresh }}">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>M5Stick Plus 2 - Monitor</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { background: #0d1117; color: #c9d1d9; font-family: "Courier New", monospace; padding: 1.5em; }
        h2 { color: #58a6ff; margin-bottom: 0.3em; }
        .meta { color: #8b949e; font-size: 0.85em; margin-bottom: 1em; }
        .actions { margin-bottom: 1em; }
        .actions a { color: #58a6ff; text-decoration: none; margin-right: 1em; font-size: 0.9em; }
        .actions a:hover { text-decoration: underline; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1em; }
        @media (max-width: 768px) { .grid { grid-template-columns: 1fr; } }
        .card { background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 1em; }
        .card h3 { color: #58a6ff; font-size: 0.9em; margin-bottom: 0.5em; }
        table { border-collapse: collapse; width: 100%; font-size: 0.85em; }
        th, td { border: 1px solid #30363d; padding: 4px 8px; text-align: left; }
        th { color: #58a6ff; }
        tr:hover { background: #21262d; }
        pre { white-space: pre-wrap; word-wrap: break-word; font-size: 0.82rem; line-height: 1.4; max-height: 400px; overflow-y: auto; }
        .footer { margin-top: 1em; color: #8b949e; font-size: 0.8em; }
    </style>
</head>
<body>
    <h2>M5Stick Plus 2 - Monitor</h2>
    <div class="meta">{{ net_count }} redes | {{ client_count }} clientes | Log: {{ log_file }} | Refresh: {{ refresh }}s</div>
    <div class="actions">
        <a href="/api/networks.json">JSON</a>
        <a href="/api/networks.csv">CSV</a>
        <a href="/api/health">Health</a>
    </div>
    <div class="grid">
        <div class="card">
            <h3>Redes detectadas</h3>
            <table>
                <tr><th>SSID</th><th>BSSID</th><th>Canal</th><th>Clientes</th></tr>
                {% for net in networks %}
                <tr>
                    <td>{{ net.ssid }}</td>
                    <td>{{ net.bssid }}</td>
                    <td>{{ net.canal }}</td>
                    <td>{{ net.n_clients }}</td>
                </tr>
                {% endfor %}
            </table>
        </div>
        <div class="card">
            <h3>Salida serial (ultimas lineas)</h3>
            <pre>{{ console_tail }}</pre>
        </div>
    </div>
    <div class="footer">Visualizacion Extendida M5Stick Plus 2 v2.0</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_networks(log_path: str) -> list[dict]:
    """Parsea redes del log Evil-M5Project. Deduplica por BSSID."""
    networks: dict[str, dict] = {}

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line.startswith("=") or "[SESSION" in line:
                    continue

                match = LINE_RE.search(line)
                if not match:
                    continue

                ts, ssid, bssid, canal, n_clients = match.groups()
                bssid = bssid.lower()
                if bssid not in networks:
                    networks[bssid] = {
                        "ssid": ssid,
                        "bssid": bssid,
                        "channel": int(canal),
                        "n_clients": int(n_clients),
                        "last_seen": ts,
                    }
                else:
                    networks[bssid]["n_clients"] = max(
                        networks[bssid]["n_clients"], int(n_clients)
                    )
                    networks[bssid]["last_seen"] = ts
    except (FileNotFoundError, OSError):
        pass

    return list(networks.values())


def read_tail(log_path: str, max_bytes: int = 10000) -> str:
    """Lee los ultimos bytes del log sin cargar el archivo entero."""
    try:
        size = os.path.getsize(log_path)
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            if size > max_bytes:
                f.seek(-max_bytes, os.SEEK_END)
                f.readline()  # descartar linea parcial
            return f.read()
    except (FileNotFoundError, OSError):
        return "Esperando datos del M5Stick..."


# ---------------------------------------------------------------------------
# Splunk HEC
# ---------------------------------------------------------------------------

def send_to_splunk(networks: list[dict], hec_url: str, hec_token: str) -> None:
    import json
    import requests

    headers = {"Authorization": f"Splunk {hec_token}"}
    payload = "".join(
        json.dumps({
            "time": time.time(),
            "sourcetype": "m5stick:networks",
            "event": net,
        })
        for net in networks
    )
    try:
        requests.post(hec_url, data=payload, headers=headers, timeout=5, verify=False)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    log_file = app.config["log_file"]
    refresh = app.config["refresh"]
    max_chars = app.config["max_chars"]

    networks = parse_networks(log_file)
    console_tail = read_tail(log_file, max_chars)
    client_count = sum(n["n_clients"] for n in networks)

    return render_template_string(
        TEMPLATE,
        networks=networks,
        net_count=len(networks),
        client_count=client_count,
        console_tail=console_tail,
        log_file=log_file,
        refresh=refresh,
    )


@app.route("/api/networks.json")
def api_json():
    """API JSON con redes detectadas para sec-dashboard / Splunk."""
    log_file = app.config["log_file"]
    networks = parse_networks(log_file)

    hec_url = os.environ.get("SPLUNK_HEC_URL", "")
    hec_token = os.environ.get("SPLUNK_HEC_TOKEN", "")
    if hec_url and hec_token:
        send_to_splunk(networks, hec_url, hec_token)

    return jsonify({
        "scan_time": datetime.now(timezone.utc).isoformat(),
        "total_networks": len(networks),
        "total_clients": sum(n["n_clients"] for n in networks),
        "networks": networks,
    })


@app.route("/api/networks.csv")
def api_csv():
    """Export CSV para Splunk lookup o sec-dashboard."""
    log_file = app.config["log_file"]
    networks = parse_networks(log_file)

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["ssid", "bssid", "channel", "n_clients", "last_seen"],
    )
    writer.writeheader()
    writer.writerows(networks)

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=m5stick_networks.csv"},
    )


@app.route("/api/health")
def api_health():
    """Health check para monitoring."""
    log_file = app.config["log_file"]
    exists = os.path.exists(log_file)
    size = os.path.getsize(log_file) if exists else 0
    return jsonify({
        "status": "ok",
        "log_file": log_file,
        "log_exists": exists,
        "log_size_bytes": size,
    })


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="M5Stick Plus 2 Dashboard")
    parser.add_argument("--log", default=None, help="Ruta al log")
    parser.add_argument("--port", type=int, default=None, help="Puerto")
    parser.add_argument("--host", default=None, help="Host")
    parser.add_argument("--refresh", type=int, default=None, help="Segundos de refresh")
    parser.add_argument("--max-chars", type=int, default=None, help="Max caracteres consola")
    args = parser.parse_args()

    log_file = args.log or os.environ.get("M5_LOG_PATH", str(DEFAULT_LOG))
    port = args.port or int(os.environ.get("M5_PORT", "5000"))
    host = args.host or os.environ.get("M5_HOST", "127.0.0.1")
    refresh = args.refresh or int(os.environ.get("M5_REFRESH", "2"))
    max_chars = args.max_chars or int(os.environ.get("M5_MAX_CHARS", "10000"))

    app.config["log_file"] = log_file
    app.config["refresh"] = refresh
    app.config["max_chars"] = max_chars

    print(f"M5Stick Plus 2 Monitor")
    print(f"  Log:   {log_file}")
    print(f"  Host:  {host}:{port}")
    print(f"  Splunk HEC: {'configurado' if os.environ.get('SPLUNK_HEC_URL') else 'no configurado'}")
    print(f"  Endpoints: / | /api/networks.json | /api/networks.csv | /api/health")

    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()