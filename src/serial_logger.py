#!/usr/bin/env python3
"""
Captura datos seriales del M5Stick Plus 2 y los guarda en un log.
Mejoras v2.0:
  - Autodeteccion de puerto serie (list_ports)
  - Default multi-OS (COM en Windows, /dev/ttyUSB en Linux)
  - Ctrl-C graceful sin traceback
  - Error handling en apertura del puerto
  - Marcador de sesion al iniciar/terminar
  - Timestamp ISO 8601 completo (no solo HH:MM:SS)
"""
import argparse
import signal
import sys

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("Error: pyserial no instalado. Ejecuta: pip install pyserial")
    sys.exit(1)

from datetime import datetime


def find_serial_port() -> str | None:
    """Autodetecta el primer puerto serie disponible."""
    ports = list(list_ports.comports())
    if not ports:
        return None
    # Preferir dispositivos que parezcan M5Stick/ESP32
    for p in ports:
        desc = (p.description or "").lower()
        if any(k in desc for k in ("usb", "ch340", "ch9102", "cp210", "m5", "esp32")):
            return p.device
    return ports[0].device


def main():
    parser = argparse.ArgumentParser(description="Serial logger for M5Stick Plus 2")
    parser.add_argument("-p", "--port", default=None, help="Puerto serie (autodetectar si omitted)")
    parser.add_argument("-b", "--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    parser.add_argument("-o", "--output", default="wifi_scan_evil.log", help="Archivo de salida")
    args = parser.parse_args()

    port = args.port
    if port is None:
        port = find_serial_port()
        if port is None:
            print("[!] No se encontro ningun puerto serie disponible.")
            print("    Puertos detectados:")
            for p in list_ports.comports():
                print(f"      {p.device} - {p.description}")
            print("    Especifica el puerto manualmente: -p COM4 o -p /dev/ttyUSB0")
            sys.exit(1)

    print(f"[*] Conectando a {port} @ {args.baud} bps...")
    print(f"[*] Guardando en: {args.output}")

    try:
        ser = serial.Serial(port, args.baud, timeout=1)
    except serial.SerialException as e:
        print(f"[!] Error abriendo {port}: {e}")
        print("    Puertos disponibles:")
        for p in list_ports.comports():
            print(f"      {p.device} - {p.description}")
        sys.exit(1)

    # Ctrl-C limpio
    interrupted = False

    def sig_handler(sig, frame):
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGINT, sig_handler)

    session_start = datetime.now().isoformat()
    line_count = 0

    with open(args.output, "a", encoding="utf-8") as log:
        log.write(f"\n{'='*60}\n")
        log.write(f"[SESSION START] {session_start} | {port} @ {args.baud}\n")
        log.write(f"{'='*60}\n")
        log.flush()

        try:
            while not interrupted:
                raw = ser.readline()
                if not raw:
                    continue
                linea = raw.decode("utf-8", errors="replace").strip()
                if linea:
                    ts = datetime.now().isoformat()
                    entry = f"[{ts}] {linea}"
                    log.write(entry + "\n")
                    log.flush()
                    print(entry)
                    line_count += 1
        finally:
            session_end = datetime.now().isoformat()
            log.write(f"\n[SESSION END] {session_end} | {line_count} lineas capturadas\n")
            log.flush()
            ser.close()
            print(f"\n[*] Sesion cerrada. {line_count} lineas capturadas en {args.output}")


if __name__ == "__main__":
    main()