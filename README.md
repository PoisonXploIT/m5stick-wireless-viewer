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
                                              log_to_csv.py     -->  CSV export
```

## Componentes

| Script | Funcion |
|--------|---------|
| `serial_logger.py` | Captura datos seriales del M5Stick y los guarda en un log |
| `app.py` | Dashboard web en tiempo real (Flask, auto-refresh cada 2s) |
| `auto_save_html.py` | Captura periodica del dashboard para respaldo offline |
| `log_to_csv.py` | Convierte el log a CSV estructurado (redes, clientes, canales) |

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
python src/serial_logger.py -p COM4 -b 115200 -o wifi_scan_evil.log
```

### 2. Abrir dashboard web

```bash
python src/app.py
```

Abrir `http://localhost:5000` en el navegador. Se actualiza automaticamente cada 2 segundos.

Variables de entorno opcionales:
- `M5_LOG_PATH` -- ruta al archivo de log (default: `wifi_scan_evil.log`)
- `M5_PORT` -- puerto del servidor (default: `5000`)
- `M5_MAX_CHARS` -- caracteres maximos mostrados (default: `10000`)

### 3. Exportar a CSV

```bash
python src/log_to_csv.py -i wifi_scan_evil.log -o resultado.csv
```

### 4. Respaldo automatico (opcional)

```bash
python src/auto_save_html.py -u http://localhost:5000 -i 60 -d snapshots
```

## Hardware

- [M5Stick Plus 2](https://shop.m5stack.com/products/m5stickc-plus2-esp32-mini-iot-development-kit)
- [Evil-M5Project firmware](https://github.com/7h30th3r0n3/Evil-M5Project)
- Cable USB-C

## Licencia

MIT -- ver [LICENSE](LICENSE).
