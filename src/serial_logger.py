"""Captura datos seriales del M5Stick Plus 2 y los guarda en un log."""
import argparse
import serial
from datetime import datetime


def main():
    parser = argparse.ArgumentParser(description="Serial logger for M5Stick Plus 2")
    parser.add_argument("-p", "--port", default="COM4", help="Puerto serie (default: COM4)")
    parser.add_argument("-b", "--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    parser.add_argument("-o", "--output", default="wifi_scan_evil.log", help="Archivo de salida")
    args = parser.parse_args()

    print(f"[*] Conectando a {args.port} @ {args.baud} bps...")
    print(f"[*] Guardando en: {args.output}")

    with serial.Serial(args.port, args.baud, timeout=1) as ser:
        with open(args.output, "a", encoding="utf-8") as log:
            while True:
                linea = ser.readline().decode("utf-8", errors="ignore").strip()
                if linea:
                    timestamp = datetime.now().strftime("%H:%M:%S")
                    entry = f"[{timestamp}] {linea}"
                    log.write(entry + "\n")
                    log.flush()
                    print(entry)


if __name__ == "__main__":
    main()
