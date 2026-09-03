# m5stick-wireless-viewer

Pipeline de datos y dashboard para firmwares de hardware hacking WiFi en ESP32.

Fusiona `wifi-marauder-viewer` y `Visualizacion_extendida_M5StickPlus2` sobre una
arquitectura modular: parsers por firmware, fuentes serial/file, store con
historico (SQLite), API web (FastAPI + SSE) y exporter a Splunk HEC.

## Conectar tu dispositivo

El firmware (Marauder, Evil-M5Project, ...) corre en la M5Stick; este proyecto
lee su **salida serial** por USB. No hay que instalar nada en el stick mas
allá del firmware habitual.

```bash
# 1. Conecta la M5Stick por USB y verifica que aparece (pista de placa):
m5wireless ports

# 2. Arranca captura + dashboard (http://localhost:8000).
#    --port es opcional: sin el, autodeteccion (prefiere M5Stick/ESP32).
m5wireless run --source serial --port COM3
```

Al arrancar se imprime la conexion resuelta:
`m5wireless: serial COM3 @ 115200 (USB Serial (COM3)) [posible M5Stick (M5Stack)]`.
Si no hay ningun puerto, el error lista los encontrados y sugiere `--port`.
El dashboard muestra en la barra superior puerto, baudrate, firmware y estado
(conectado / reconectando / esperando).

Sin hardware: `m5wireless run --demo` reproduce un log de ejemplo y deja el
dashboard listo para probarse. Si ya tienes un log grabado:
`m5wireless run --source file --log-path scan.log`.

## Estado

v3.0.1: claridad de conexion (`m5wireless ports`, salida explicita al arrancar,
widget de estado en el dashboard, modo `--demo`). v3.0.0: modelos, parsers,
stores, fuentes, colector, dashboard web en vivo (SSE), export CSV/JSON, CLI
unificada y Docker. Seguimiento de fases en `SEGUIMIENTO.md`.

## Instalacion

```bash
pip install .                    # core + CLI (export offline, sin dependencias web)
pip install .[serial,web,splunk] # completo: serial en vivo + dashboard + Splunk HEC
```

El paquete crea el comando `m5wireless` (`m5wireless --version`).

## Uso rapido

```bash
# Listar puertos serie con pista de placa (M5Stick/ESP32)
m5wireless ports

# Demo sin hardware: log de ejemplo incluido + dashboard
m5wireless run --demo

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

## Bruce (M5Stick)

Bruce no emite un log continuo: se lee su almacenamiento. Dos fuentes:

```bash
# Serial: CLI de Bruce + poller de `storage list/read` (requiere el extra [serial]).
m5wireless run --source bruce --port COM7

# WebUI HTTP: sin serial, lista y descarga pcaps por la web del dispositivo.
m5wireless run --source bruce-web --url http://172.0.0.1 --user admin --password bruce

# Si el dispositivo no tiene tarjeta SD, los pcaps viven en LittleFS:
m5wireless run --source bruce-web --url http://172.0.0.1 \
    --user admin --password bruce --fs LittleFS
```

Control remoto minimo (mismo `--url/--user/--password`):

```bash
m5wireless bruce info                 # version y uso de almacenamiento
m5wireless bruce reboot              # reinicia el dispositivo
m5wireless bruce cmd "power reboot"   # shell serial remota
```

Aviso: con el sniffer activo la WebUI bloquea la shell; `bruce reboot` es la
unica salida limpia. Los pcaps descargados por HTTP son byte-identicos a los
extraidos por serial y comparten el mismo parser.

### Seguridad antes de usar la WebUI en campo

Los defaults de fabrica del firmware Bruce son publicos:

- AP: SSID `BruceNet`, password `brucenet`, IP fija `172.0.0.1/24` sin DHCP
  (el cliente necesita IP estatica, p. ej. `172.0.0.2`).
- WebUI: usuario `admin`, password `bruce`.
- El sniffer emite con SSID oculto `BruceSniffer`.

Un dispositivo en modo sniffer + AP con credenciales publicas es accesible
para cualquiera en la red: cambia el user/pwd de la WebUI y la password del
AP antes de llevarlo a campo (configuracion desde la shell de Bruce).

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
