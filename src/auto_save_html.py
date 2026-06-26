#!/usr/bin/env python3
"""
Captura periodica del dashboard web para respaldo offline.
Mejoras v2.0:
  - Ctrl-C graceful sin traceback
  - Backoff exponencial en errores consecutivos
  - Estadisticas al cerrar
"""
import argparse
import os
import signal
import sys

import requests
from datetime import datetime
import time


def main():
    parser = argparse.ArgumentParser(description="Auto-save M5Stick dashboard snapshots")
    parser.add_argument("-u", "--url", default="http://localhost:5000", help="URL del dashboard")
    parser.add_argument("-i", "--interval", type=int, default=60, help="Intervalo en segundos (default: 60)")
    parser.add_argument("-d", "--dir", default="snapshots", help="Directorio de respaldo")
    args = parser.parse_args()

    os.makedirs(args.dir, exist_ok=True)

    interrupted = False

    def sig_handler(sig, frame):
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGINT, sig_handler)

    print(f"[*] Capturando {args.url} cada {args.interval}s en {args.dir}/")
    print(f"[*] Ctrl+C para detener")

    saved = 0
    errors = 0
    consecutive_errors = 0

    while not interrupted:
        try:
            response = requests.get(args.url, timeout=10)
            response.raise_for_status()
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            filepath = os.path.join(args.dir, f"respaldo_{timestamp}.html")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(response.text)
            print(f"[+] Guardado {filepath}")
            saved += 1
            consecutive_errors = 0
        except Exception as e:
            errors += 1
            consecutive_errors += 1
            backoff = min(args.interval * (2 ** consecutive_errors), 600)
            print(f"[-] Error ({consecutive_errors} consecutivos): {e}")
            print(f"    Reintentando en {backoff}s...")
            time.sleep(backoff)
            continue

        time.sleep(args.interval)

    print(f"\n[*] Detenido. {saved} snapshots guardados, {errors} errores.")


if __name__ == "__main__":
    main()