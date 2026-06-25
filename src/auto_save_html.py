"""Captura periodica del dashboard web para respaldo offline."""
import argparse
import os
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
    print(f"[*] Capturando {args.url} cada {args.interval}s en {args.dir}/")

    while True:
        try:
            response = requests.get(args.url, timeout=10)
            response.raise_for_status()
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            filepath = os.path.join(args.dir, f"respaldo_{timestamp}.html")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(response.text)
            print(f"[+] Guardado {filepath}")
        except Exception as e:
            print(f"[-] Error: {e}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
