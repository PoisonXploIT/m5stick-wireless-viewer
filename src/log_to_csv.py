#!/usr/bin/env python3
"""
Parsea el log del M5Stick y genera CSV o JSON estructurado.
Mejoras v2.0:
  - No descarta bloques incompletos: emite con status=incomplete
  - Columna status (complete/incomplete) en CSV
  - Flag --json para salida JSON (integracion con sec-dashboard/Splunk)
  - Error handling en apertura de archivos
  - Timestamps ISO 8601 preservados del log
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

REDEX_NETWORK = re.compile(
    r"\[(.*?)\] (.+) \(([0-9a-fA-F:]{17})\) on channel (\d+) has (\d+) clients:"
)
REDEX_CLIENT = re.compile(r"\[(.*?)\] - ([0-9a-fA-F:]{17})")


def parse_log(log_path: str) -> list[dict]:
    """
    Parsea el log y devuelve registros de redes y clientes.
    Los bloques incompletos (n_clientes no coincide) se emiten con
    status='incomplete' en vez de descartarse silenciosamente.
    """
    registros: list[dict] = []
    red_actual: dict | None = None

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for linea in f:
                linea = linea.strip()

                # Saltar marcadores de sesion
                if linea.startswith("=") or "[SESSION" in linea:
                    if red_actual and len(red_actual["clientes"]) < red_actual["n_clientes"]:
                        _flush_red(registros, red_actual, status="incomplete")
                        red_actual = None
                    continue

                match_red = REDEX_NETWORK.match(linea)
                if match_red:
                    # Si hay una red anterior sin cerrar, emitirla como incompleta
                    if red_actual and len(red_actual["clientes"]) < red_actual["n_clientes"]:
                        _flush_red(registros, red_actual, status="incomplete")
                    elif red_actual:
                        _flush_red(registros, red_actual, status="complete")

                    hora, ssid, bssid, canal, n_clientes = match_red.groups()
                    red_actual = {
                        "timestamp": hora,
                        "ssid": ssid,
                        "bssid": bssid.lower(),
                        "canal": int(canal),
                        "n_clientes": int(n_clientes),
                        "clientes": [],
                    }
                    continue

                match_cliente = REDEX_CLIENT.match(linea)
                if match_cliente and red_actual:
                    _, mac_cliente = match_cliente.groups()
                    red_actual["clientes"].append(mac_cliente.lower())
                    if len(red_actual["clientes"]) >= red_actual["n_clientes"]:
                        _flush_red(registros, red_actual, status="complete")
                        red_actual = None

    except FileNotFoundError:
        print(f"[!] Archivo no encontrado: {log_path}", file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        print(f"[!] Error leyendo {log_path}: {e}", file=sys.stderr)
        sys.exit(1)

    # Red sin cerrar al final del archivo
    if red_actual:
        status = "complete" if len(red_actual["clientes"]) >= red_actual["n_clientes"] else "incomplete"
        _flush_red(registros, red_actual, status=status)

    return registros


def _flush_red(registros: list[dict], red: dict, status: str) -> None:
    """Emite los registros de una red con el status dado."""
    for cliente in red["clientes"]:
        registros.append({
            "timestamp": red["timestamp"],
            "ssid": red["ssid"],
            "bssid": red["bssid"],
            "canal": red["canal"],
            "n_clientes": red["n_clientes"],
            "cliente_mac": cliente,
            "status": status,
        })


def main():
    parser = argparse.ArgumentParser(description="Convert M5Stick log to CSV or JSON")
    parser.add_argument("-i", "--input", default="wifi_scan_evil.log", help="Archivo de log")
    parser.add_argument("-o", "--output", default=None, help="Archivo de salida (default: stdout)")
    parser.add_argument("--json", action="store_true", help="Salida JSON en vez de CSV")
    args = parser.parse_args()

    registros = parse_log(args.input)

    if args.json:
        output_data = json.dumps(registros, indent=2, ensure_ascii=False)
        if args.output:
            Path(args.output).write_text(output_data, encoding="utf-8")
            print(f"[+] JSON generado: {args.output} ({len(registros)} registros)")
        else:
            print(output_data)
    else:
        out_path = args.output or "wifi_scan_parsed.csv"
        with open(out_path, "w", newline="", encoding="utf-8") as csvfile:
            fieldnames = ["timestamp", "ssid", "bssid", "canal", "n_clientes", "cliente_mac", "status"]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(registros)
        print(f"[+] CSV generado: {out_path} ({len(registros)} registros)")


if __name__ == "__main__":
    main()