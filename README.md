# m5stick-wireless-viewer

Pipeline de datos y dashboard para firmwares de hardware hacking WiFi en ESP32.

Fusiona `wifi-marauder-viewer` y `Visualizacion_extendida_M5StickPlus2` sobre una
arquitectura modular: parsers por firmware, fuentes serial/file, store con
historico (SQLite), API web (FastAPI + SSE) y exporter a Splunk HEC.

## Estado

v3.0.0 funcional: modelos, parsers, stores, fuentes, colector, dashboard web en
vivo (SSE), export CSV/JSON, CLI unificada y Docker. Seguimiento de fases en
`SEGUIMIENTO.md`.

## Instalacion

```bash
pip install .                    # core + CLI (export offline, sin dependencias web)
pip install .[serial,web,splunk] # completo: serial en vivo + dashboard + Splunk HEC
```

El paquete crea el comando `m5wireless` (`m5wireless --version`).

## Uso rapido

```bash
# Captura serial en vivo + dashboard web (http://localhost:8000)
m5wireless run

# Leer un log existente (modo offline/file), dashboard en :8000
m5wireless run --source file --log-path scan.log

# Export offline de un log a CSV/JSON
m5wireless export csv  --input scan.log --firmware evil_m5project --output out.csv
m5wireless export json --input scan.log --firmware marauder      --output out.json

# Guardar el dashboard HTML periodicamente (legacy auto_save_html)
m5wireless snapshot --url http://localhost:8000 --interval 60 --dir snapshots
```

Configuracion: CLI > variables de entorno `M5W_*` > fichero `m5wireless.toml`
(./ o ~/.config/m5wireless/). Ejemplo de toml:

```toml
[run]
source = "file"
log_path = "scan.log"
firmware = "auto"
web_port = 8000

[splunk]
url = "https://splunk:8088/services/collector/event"
token = "..."
verify_ssl = true
```

## Splunk HEC

Se activa automaticamente cuando hay URL y token configurados (`M5W_SPLUNK_HEC_URL`
y `M5W_SPLUNK_HEC_TOKEN`, o la seccion `[splunk]` del toml); el colector reenvia
cada evento sin accion manual. Robustez: envio por lotes, cola en memoria con
spool a disco opcional (`max_queue_size`/`spool_path`) y circuit breaker tras fallos
consecutivos. `verify=True` por defecto; desactivar la verificacion TLS es solo
vía configuracion explicita (`verify_ssl = false`).

## Docker (modo file por defecto)

```bash
docker compose up -d m5wireless          # dashboard en http://localhost:8000
docker compose --profile splunk up -d    # con Splunk para pruebas HEC
```

La captura serial en vivo se recomienda como host nativo (USB); el log lo aporta
el volumen `./data` (`/data/scan.log`).

## Firmwares soportados

| Firmware | Estado |
|----------|--------|
| WiFi Marauder (M5StickC/ESP32) | Implementado |
| Evil-M5Project (M5Stick Plus 2) | Implementado |
| WiFi Duck, Hash Monster, PacketMonitor | Stubs documentados, pendiente de fixtures reales |

## Desarrollo

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"   # Windows
# o: source .venv/bin/activate && pip install -e ".[dev]"  # Linux/macOS

.venv/Scripts/python -m pytest
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m mypy src/m5wireless --strict
```

## Licencia

MIT. Ver `AUTHORS.md` para creditos de los proyectos originales.
