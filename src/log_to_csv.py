"""Parsea el log del M5Stick y genera un CSV estructurado."""
import argparse
import csv
import re

REDEX_NETWORK = re.compile(
    r"\[(.*?)\] (.+) \(([\da-f:]+)\) on channel (\d+) has (\d+) clients:"
)
REDEX_CLIENT = re.compile(r"\[(.*?)\] - ([\da-f:]+)")


def parse_log(log_path: str) -> list[dict]:
    registros = []
    red_actual = None

    with open(log_path, "r", encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            match_red = REDEX_NETWORK.match(linea)
            if match_red:
                hora, ssid, bssid, canal, n_clientes = match_red.groups()
                red_actual = {
                    "timestamp": hora,
                    "ssid": ssid,
                    "bssid": bssid,
                    "canal": canal,
                    "n_clientes": int(n_clientes),
                    "clientes": [],
                }
                continue

            match_cliente = REDEX_CLIENT.match(linea)
            if match_cliente and red_actual:
                _, mac_cliente = match_cliente.groups()
                red_actual["clientes"].append(mac_cliente)
                if len(red_actual["clientes"]) == red_actual["n_clientes"]:
                    for cliente in red_actual["clientes"]:
                        registros.append({
                            "timestamp": red_actual["timestamp"],
                            "ssid": red_actual["ssid"],
                            "bssid": red_actual["bssid"],
                            "canal": red_actual["canal"],
                            "cliente_mac": cliente,
                        })
                    red_actual = None

    return registros


def main():
    parser = argparse.ArgumentParser(description="Convert M5Stick log to CSV")
    parser.add_argument("-i", "--input", default="wifi_scan_evil.log", help="Archivo de log")
    parser.add_argument("-o", "--output", default="wifi_scan_parsed.csv", help="Archivo CSV de salida")
    args = parser.parse_args()

    registros = parse_log(args.input)

    with open(args.output, "w", newline="", encoding="utf-8") as csvfile:
        fieldnames = ["timestamp", "ssid", "bssid", "canal", "cliente_mac"]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(registros)

    print(f"[+] CSV generado: {args.output} ({len(registros)} registros)")


if __name__ == "__main__":
    main()
