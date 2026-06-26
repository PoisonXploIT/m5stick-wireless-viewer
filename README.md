# Visualizacion Extendida M5Stick Plus 2

Herramienta para visualizar en tiempo real los datos del **M5Stick Plus 2** con firmware **Evil-M5Project** en una pantalla grande (PC o movil).

![Platform](https://img.shields.io/badge/Platform-ESP32-orange)
![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat&logo=python)
![Flask](https://img.shields.io/badge/Flask-3.x-000000?style=flat&logo=flask)
![License](https://img.shields.io/badge/License-MIT-green)

## Arquitectura

```
M5Stick Plus 2 (Evil-M5Project)
        |
    USB Serial
        |
  serial_logger.py  -->  wifi_scan_evil.log  -->  app.py (Flask)  -->  Navegador
                                                     |
                                              auto_save_html.py  -->  snapshots/
                                                     |
                                              log_to_csv.py     -->  CSV / JSON export
                                                     |
                                              /api/networks.json -->  sec-dashboard / Splunk
```

## Componentes

| Script | Funcion |
|--------|---------|
| `serial_logger.py` | Captura datos seriales del M5Stick. Autodeteccion de puerto, Ctrl-C graceful, marcadores de sesion |
| `app.py` | Dashboard web en tiempo real. Tail eficiente, API JSON/CSV, Splunk HEC opcional |
| `auto_save_html.py` | Captura periodica del dashboard. Backoff en errores, estadisticas |
| `log_to_csv.py` | Convierte el log a CSV o JSON. No descarta bloques incompletos |

## Requisitos

- Python 3.10+
- M5Stick Plus 2 con firmware Evil-M5Project
- Cable USB para conexion serial

## Instalacion

```bash
git clone https://github.com/PoisonXploIT/Visualizacion_extendida_M5StickPlus2.git
cd Visualizacion_extendida_M5StickPlus2
pip install -r requirements.txt
```

## Uso

### 1. Capturar datos seriales

```bash
python src/serial_logger.py -b 115200 -o wifi_scan_evil.log
```

El puerto se autodetecta. Para especificarlo manualmente:

```bash
python src/serial_logger.py -p COM4
python src/serial_logger.py -p /dev/ttyUSB0
```

### 2. Abrir dashboard web

```bash
python src/app.py
```

Abrir `http://127.0.0.1:5000` en el navegador. Se actualiza cada 2 segundos.

Argumentos:

```bash
python src/app.py --log wifi_scan_evil.log --port 5000 --host 127.0.0.1 --refresh 2
```

Variables de entorno:

| Variable | Default | Descripcion |
|----------|---------|-------------|
| `M5_LOG_PATH` | `wifi_scan_evil.log` | Ruta al log |
| `M5_PORT` | `5000` | Puerto del servidor |
| `M5_HOST` | `127.0.0.1` | Host de binding |
| `M5_REFRESH` | `2` | Segundos entre refrescos |
| `M5_MAX_CHARS` | `10000` | Max caracteres en consola |
| `SPLUNK_HEC_URL` | (vacio) | URL del Splunk HEC |
| `SPLUNK_HEC_TOKEN` | (vacio) | Token del HEC de Splunk |

### 3. Exportar a CSV o JSON

```bash
python src/log_to_csv.py -i wifi_scan_evil.log -o resultado.csv
python src/log_to_csv.py -i wifi_scan_evil.log --json -o resultado.json
python src/log_to_csv.py -i wifi_scan_evil.log --json  # stdout
```

### 4. Respaldo automatico (opcional)

```bash
python src/auto_save_html.py -u http://localhost:5000 -i 60 -d snapshots
```

## Endpoints API

| Ruta | Descripcion |
|------|-------------|
| `/` | Dashboard HTML con tabla de redes y consola serial |
| `/api/networks.json` | API JSON con redes y clientes detectados |
| `/api/networks.csv` | Export CSV |
| `/api/health` | Health check (estado del log, tamano) |

## Integracion con sec-dashboard / Splunk

### Splunk HEC directo

```bash
export SPLUNK_HEC_URL=https://127.0.0.1:8088/services/collector
export SPLUNK_HEC_TOKEN=tu-tok...thon src/app.py
```

Los eventos se envian con sourcetype `m5stick:networks`.

### CSV a Splunk

```bash
curl http://127.0.0.1:5000/api/networks.csv > m5stick_networks.csv
```

### JSON desde sec-dashboard

```bash
curl http://127.0.0.1:5000/api/networks.json
```

Respuesta:

```json
{
  "scan_time": "2026-06-26T18:00:00+00:00",
  "total_networks": 2,
  "total_clients": 5,
  "networks": [
    {
      "ssid": "MOVISTAR_9C29",
      "bssid": "4c:ab:fb:33:54:b7",
      "channel": 6,
      "n_clients": 3,
      "last_seen": "18:00:01.234"
    }
  ]
}
```

### Log a JSON con log_to_csv.py

```bash
python src/log_to_csv.py -i wifi_scan_evil.log --json | curl -H "Content-Type: application/json" -d @- https://sec-dashboard/api/ingest
```

## Hardware

- [M5Stick Plus 2](https://shop.m5stack.com/products/m5stickc-plus2-esp32-mini-iot-development-kit)
- [Evil-M5Project firmware](https://github.com/7h30th3r0n3/Evil-M5Project)
- Cable USB-C

## Licencia

MIT -- ver [LICENSE](LICENSE).