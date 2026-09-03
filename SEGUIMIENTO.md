# SEGUIMIENTO — m5stick-wireless-viewer

Documento de seguimiento para vaciar contexto sin perder el hilo. Cada fase/cambio
lleva su **mini prompt**: bloques copy-paste para situar a un agente en sesión nueva
tras overflow de contexto, sin necesidad de compactar.

Última actualización: e2e con sniffer real COMPLETADA (ver seccion 'Bruce (M5Stick)').
Proximo objetivo: v3.2.1 — WebUI Bruce para control remoto.

---

## PROMPT DE RETOMADA (copiar en sesión nueva)

```text
Continua m5stick-wireless-viewer en C:\Users\Sammi\m5stick-wireless-viewer
(rama main; v3.0.0 publicada en GitHub y PyPI; v3.0.1 local: claridad de conexion).
Lee SEGUIMIENTO.md (repo) y PLAN.md (C:\Users\Sammi\m5stick-wireless-viewer-plan).
Objetivo: v3.1 — (1) vista de detalle de red: GET /api/networks/{bssid} ya existe,
falta pagina HTML + link desde la tabla de index.html; (2) Chart.js con evolucion
RSSI/actividad temporal por red; (3) parsers stubs WiFi Duck / Hash Monster /
PacketMonitor SOLO con fixtures reales.
Reglas: ruff + mypy --strict limpios sobre src/m5wireless, commits en espanol sin
emojis, smoke test de navegador (CDP) para cambios de frontend, SEGUIMIENTO.md
actualizado al cerrar. Venv .venv; 127 tests pasando.
```

---

## Estado actual

| Item | Estado |
|------|--------|
| Fase 0 (preparación) | Parcial: git init local hecho; falta repo remoto GitHub y `git remote add` (acción del usuario) |
| Fase 1 (modelos + parsers) | **Completa** — commit `61e1d20` |
| Fase 2 (stores + sources + collector) | **Completa** — commit `09baa37` |
| Fase 3 (backend web FastAPI + SSE) | **Completa** — commit `3590475` |
| Fase 4 (frontend dashboard HTML/CSS/JS + SSE) | **Completa** — commit `87f8097` (vista de detalle pospuesta a v3.1) |
| Fase 5 (exporters + CLI unificada) | **Completa** — commit `a418dc5` |
| Fase 6 (calidad/empaquetado: CI/CD) | Parcial: pyproject/Docker listos; falta CI y releases automaticas |
| Fase 7 (release v3.0.0 + deprecacion) | Pendiente: tag, archivar repo legado, repo remoto |

Tests: 127 pasando. Lint: ruff limpio. Tipos: mypy --strict limpio (sobre `src/m5wireless`).

### v3.0.1 — claridad de conexion (completo, local)

- `m5wireless ports`: lista puertos serie con VID/PID y pista de placa (M5Stick
  por VID 2E8A, ESP32 CDC/JTAG, CP210x, CH34x). Commits `d83d823`, `ccbd91b`.
- `run --source serial` resuelve el puerto antes de arrancar e imprime
  `serial COM3 @ 115200 (desc) [hint]`; sin puertos, lista los encontrados,
  sugiere `--port` y sale con exit 2.
- `GET /api/status` + widget en la barra superior del dashboard (puerto,
  baudrate, firmware, estado conectado/reconectando/esperando/reproduciendo/
  terminado), polling 5s. Fuentes exponen `status()`; `Collector.status()`
  agrega el firmware_id.
- `run --demo`: reproduce el log de ejemplo incluido en el paquete
  (`src/m5wireless/data/demo_scan.log`, `follow=False`) sin hardware.
- README: seccion "Conectar tu dispositivo" al inicio. Version 3.0.1.
Venv: `.venv/` (paquete instalado editable v3.0.0 con entry point `m5wireless`; extras
serial/web/splunk + types-pyserial; fastapi/uvicorn/httpx instalados).

---

### Bruce (M5Stick) — hallazgos y ruta de integracion (2026-09-02)

Hardware en la mesa:

- **M5Stick + Bruce** en `COM7` @ 115200 (puente CH9102). SIN tarjeta SD: los
  ficheros van a LittleFS y funciona igual (Bruce cae a LittleFS sin SD).
- **ESP32 V6 + Marauder** con SD: fuente en vivo por serial (parser ya existe);
  para sacar sus ficheros, su web UI o extraer la SD.

Serial de Bruce = CLI tipo Flipper (115200 8N1), no stream continuo:

- Escribe eventos: `Selected: X`, `Sniffer started!`, errores (`SDCARD NOT
  mounted...`), rutas. Los scans vuelcan resultados sobre todo a pantalla; en
  sniffer la consola calla.
- Comandos probados (solo lectura, no detienen el ataque):
  - `storage list [dir]` → listado con tamanos; dirs: `BrucePCAP/`,
    `BrucePCAP/handshakes/`, `BruceRFID/`.
  - `storage read <path>` → echo `COMMAND: ...\r\n` (longitud variable) + bytes
    crudos. Validado extrayendo un handshake real:
    `BrucePCAP/handshakes/HS_E051630EB6EA_MiFibra-B6E8.pcap` (444 B, magic
    `d4c3b2a1`, linktype 105 = IEEE 802.11).
- Fixtures locales (NO commitear: datos reales de casa): `data/bruce_capture.log`
  y `data/bruce_HS_*.pcap`. `data/` esta en .gitignore.

Ruta de integracion (v3.2, detalle en PLAN.md §6.4 y §7.3):

1. `BruceConsoleParser` con el fixture local (eventos de ciclo de vida).
   **Hecho**: patterns `Selected: X`, `Sniffer started!`, `SDCARD NOT mounted`,
   errores ESP-IDF `[N][E]...`; emite `StatusEvent` (nuevo en models; el store
   lo ignora, Splunk lo salta, SSE lo serializa).
2. `PcapParser` para pcaps 802.11: handshake → `NetworkSeen` +
   `ClientAssociated`. **Hecho**: magic LE/BE, linktype 105, frames de gestion
   (SSID por tag IE) → `NetworkSeen`; frames de datos con LLC/SNAP 0x888E
   (EAPOL) → `ClientAssociated`; dedup por BSSID con upgrade de SSID.
   El fixture real (1 beacon + 1 frame EAPOL en mgmt, RA corrupto) produce
   2 `NetworkSeen` (con/sin SSID) y 0 `ClientAssociated`: la MAC del cliente
   no es fiable en esa captura, el caso EAPOL queda cubierto con frames
   sinteticos correctos.
3. `BruceStorageSource`: poller `storage list` → diff tamanios → `storage read`
   → parsers; worker thread propio (el handle serial vive ahi), callback de
   lineas para la consola + callback de ficheros para el pcap.
   **Hecho**, con inyeccion `_open_port` y test con transporte falso.
   VALIDADO CONTRA COM7 REAL (2026-09-03): el listado trae NOMBRES RELATIVOS
   al directorio con TAB como separador, subdirectorios como
   `<nombre>\t<DIR>`, echo `COMMAND: ...\r\r\n` y prompt `# `; `storage read`
   exige ruta COMPLETA (solo basename no devuelve bytes). Fix aplicado:
   `_poll_directory` mapea a `{dir}/{nombre}` + guarda anti-colgazo por
   lecturas vacias. Formato replicado en FakeSerial (tests).
   Nota para capturas nuevas: si aparecen EAPOL sanos en data frames, el
   path `ClientAssociated` ya esta cubierto con frames sinteticos; anotar
   aqui cualquier desviacion del fixture actual (RA corrupto).
4. Evolucin: WebUI Bruce (`bruce.local`, admin/bruce) para control remoto.
   **Pendiente** (v3.2.1).

Corrida end-to-end con sniffer real (2026-09-03, COM7):

- `m5wireless run --source bruce --port COM7 --artifacts-dir data/artifacts_e2e
  --web-port 8000` con el sniffer corriendo en Bruce: poller detecto y leyo
  `HS_E051630EB6EA_MiFibra-B6E8.pcap` (444 B) y `deauth_0.pcap` (24 B, solo header,
  sin frames; el parser no emite nada para este, comportamiento correcto).
- PcapParser emito 2 `NetworkSeen` (ssid=None y upgrade a `MiFibra-B6E8`,
  BSSID e0:51:63:0e:b6:ea); `/api/networks` y el dashboard lo muestran;
  artifacts guardados en disco. Sin EAPOL sanos en esta captura: el path
  `ClientAssociated` sigue cubierto solo con frames sinteticos (sin cambios).
- Hallazgos de la CLI Bruce v1.15 (`help`):
  - `sniffer` arranca el raw sniffer (escribe a LittleFS sin SD, igual que con
    SD). NO es toggle: no existe comando de stop y Ctrl-C no se intercepta
    (`ERROR: Command not found at ''`). Para parar el sniffer hay que hacer
    `power reboot` por serial.
  - Bruce trae su PROPIO web server: `webui - WebUI Webserver start`. Esto
    cambia el alcance del item pendiente: v3.2.1 puede ser "integrar/usar la
    WebUI de Bruce" (bruce.local, admin/bruce) en vez de construir control
    remoto desde cero; decidir al abrir la sesion.
- Limitacion de datos RESUELTA (fix local, pendiente de release): los
timestamps del pcap de Bruce salen sin reloj sincronizado (epoch ~0), asi
que `first_seen`/`last_seen` quedaban en 1970. Fix: `PcapParser.parse()`
admite `received_at` (default `utc_now()`) y `_sanitize_ts()` ancla al
momento de recepcion si el ts del frame es implausible (año < 2000 o futuro
mas alla de 24 h); lo plausible se conserva. Tests: reloj sin sincronizar,
reloj futuro, ts plausible intacto y fixture real anclado.

Mini prompt para la sesion de validacion Bruce (v3.2.1):

```text
Arranca la validacion de v3.2 Bruce de m5stick-wireless-viewer en
C:/Users/Sammi/m5stick-wireless-viewer (rama main; v3.2.0 publicada; 158 tests,
ruff + mypy --strict limpios; fix de timestamps Bruce local sin release).
Lee SEGUIMIENTO.md (seccion 'Bruce (M5Stick)') y PLAN.md §6.4/§7.3
(C:/Users/Sammi/m5stick-wireless-viewer-plan).
Hardware: M5Stick+Bruce en COM7 @ 115200 sin SD (LittleFS OK); ESP32 V6+Marauder
separado para el stream en vivo.
Objetivo v3.2.1: `BruceWebSource` (fuente HTTP paralela a BruceStorageSource,
compartiendo PcapParser) + control remoto via `/cm` y `/reboot`. Reconocimiento
completo de la API ya esta hecho (ver seccion 'Reconocimiento WebUI Bruce'
arriba): endpoints, formato listfiles, download validado byte-a-byte.
Credenciales: AP BruceNet/brucenet, web admin/bruce (defaults; cambiar).
E2E serial ya esta hecha (fix de timestamps 1970 incluido, pending de
release); anotar aqui si aparecen EAPOL sanos.
Reglas: ruff + mypy --strict limpios sobre src/m5wireless, commits en espanol sin
emojis, NO commitear data/ (datos reales), SEGUIMIENTO.md actualizado al cerrar.
```

---

## Decisiones clave (con justificación)

1. **Base del plan**: `m5stick-wireless-viewer-plan/PLAN.md` (fuente de verdad de fases y arquitectura).
2. **Fusión por copia manual**, sin git filter-repo: créditos en `AUTHORS.md`; repos originales quedan como referencia histórica.
3. **Modelos**: union discriminada `NetworkSeen | ClientAssociated`. La base `Observation` NO lleva campos por defecto (dataclass no permite campo sin default en subclase tras uno con default). Todo datetime aware UTC; helper `utc_now()`.
4. **Registro de parsers almacena CLASES**, no instancias: `get_parser()` instancia fresca (parsers stateful como EvilM5Project no comparten estado entre ejecuciones/tests).
5. **`CompositeParser` hereda de `AbstractParser`** (`firmware_id = "composite"`), para que `get_parser("auto")` tenga tipo uniforme.
6. **MarauderParser mejora al original**: regex del repo usaba `ESSID:\s*(.+?)` y descartaba redes ocultas; ahora `(.*?)` y SSID vacío -> `ssid=None`.
7. **EvilM5ProjectParser stateful**: las líneas de cliente `[ts] - MAC` heredan el último BSSID visto; si no hay red previa, `bssid=None`. Timestamps `[ts]` de la línea se anclan a la fecha de `received_at` (formatos `%H:%M:%S`, ISO).
8. **Stubs** (`wifi_duck`, `hash_monster`, `packet_monitor`): NO registrados; `can_parse -> False`, `parse` lanza `NotImplementedError`. Se implementarán solo con fixture real.
9. **Docker = modo secundario**: captura serial en vivo se documenta como host nativo (Windows + USB es fricción).
10. **Timeline**: 3-4 semanas a tiempo parcial; vista de detalle de red posiblemente en v3.1.
11. **`n_clients`/clientes se calculan en lectura**: `networks.n_clients` y `clients` salen de la tabla `clients` (PK mac) al consultar; no se mantiene contador denormalizado en `networks`.
12. **Timestamps SQLite = texto ISO-8601 UTC**: el orden lexicografico coincide con el cronologico; sin convertir a epoch.
13. **FK `clients.bssid -> networks(bssid)` declarada pero NO forzada** (sin `PRAGMA foreign_keys=ON`): un cliente puede referenciar una red aun no vista como linea de red.
14. **pyserial es extra opcional `[serial]`**: el paquete se importa y funciona sin el; `types-pyserial` va en dev solo para mypy --strict.
15. **mypy --strict sobre `src/m5wireless`** (no sobre `tests/`, que da error de py.typed; es la convencion del proyecto).
16. **fastapi/uvicorn = extra opcional `[web]`** (como pyserial en [serial]): el core sigue sin dependencias obligatorias; dev incluye fastapi + httpx para los tests.
17. **EventHub (opcion B del plan)**: pub-sub con un `asyncio.Queue` por cliente SSE; `publish_sync()` es thread-safe (fuera del hilo del loop usa `call_soon_threadsafe`, porque `asyncio.Queue` NO es thread-safe). El colector alimenta el hub via `Collector.observe(hub.publish_sync)`.
18. **TestClient de esta version de starlette (1.x) no soporta streaming**: `_TestClientTransport.handle_request` bloquea hasta que la app ASGI termina y bufferiza en BytesIO, asi que `client.stream()` se cuelga con SSE. Los tests SSE (`test_sse.py::_run_sse`) manejan la app ASGI a mano (lifespan + request) con asyncio puro.
19. **Store: metodos aditivos de Fase 3** — `get_network(bssid)`, `get_client(mac)` y `iter_observations(since, until)` (Iterator, para export CSV sin cargar todo en memoria). Sin cambios en la API existente de Fase 2.
20. **Collector: `observe(callback)` + property `source_type`** — el callback se invoca tras cada `apply` exitoso desde el hilo que procesa la linea; un fallo del observador NO para la pipeline (se cuenta en stats.errors y se loguea).
21. **`/` devuelve JSON minimo** `{"status": "ok", "phase": 3, "endpoints": [...]}`; `web/templates/index.html` es placeholder hasta Fase 4 (dashboard real con SSE).
22. **CLI = argparse, no typer**: cero dependencias nuevas para el core; subparsers `run` / `export csv|json` / `snapshot`. Entry point `m5wireless = "m5wireless.cli:main"` en pyproject.
23. **Splunk HEC se activa solo con URL y token** (`M5W_SPLUNK_HEC_URL` + `M5W_SPLUNK_HEC_TOKEN`, o `[splunk]` en toml): no es accion manual; el colector lo dispara via `Collector.observe(exporter.submit)`. `observe()` ahora admite VARIOS observadores (aditivo; antes era uno solo).
24. **HEC: fallos contados, no reenviados**: un evento cuyo POST falla se cuenta en `stats.failed` y no se reintenta (documentado); lo que SI se conserva sin perder es la cola pendiente durante caídas. Desborde de cola: spool JSONL a disco si hay `spool_path`, si no, drop contado.
25. **Version 3.0.0 + requires-python >=3.11** (tomllib para el toml de config). Consecuencia: ruff target py311 activo UP017/UP035 -> fixes mecanicos en ficheros antiguos (datetime.UTC, collections.abc.Callable).
26. **`m5wireless snapshot` = legacy `auto_save_html.py`**: polling con stdlib urllib (sin httpx), guarda HTML con timestamp; `--max N` para pruebas.
27. **Ciclo de vida del exporter en el lifespan** de `create_app(..., exporter=...)`: start/stop junto al collector; el cliente httpx se crea/cierra dentro (o se inyecta en tests).

---

## Estructura actual del repo

```text
m5stick-wireless-viewer/
├── .gitattributes, .gitignore, AUTHORS.md, README.md, SEGUIMIENTO.md
├── Dockerfile              # python:3.11-slim, modo file por defecto (Fase 5)
├── docker-compose.yml      # m5wireless + splunk opcional (profile "splunk")
├── pyproject.toml          # hatchling v3.0.0, >=3.11, scripts m5wireless, extras serial/web/splunk/dev
├── src/m5wireless/
│   ├── __init__.py         # __version__ = "3.0.0"
│   ├── cli.py              # CLI unificada: run / export csv|json / snapshot (argparse)
│   ├── exporter/
│   │   ├── __init__.py     # importable sin httpx
│   │   └── splunk_hec.py   # SplunkHecConfig + SplunkHecExporter (cola, breaker, spool)
│   ├── models.py           # Network, Client, Observation, NetworkSeen, ClientAssociated, normalize_mac, utc_now
│   ├── parser/
│       ├── __init__.py     # importa marauder + evil_m5project (dispara registro)
│       ├── base.py         # AbstractParser, CompositeParser
│       ├── registry.py     # ParserRegistry (almacena clases), register_parser, get_parser("auto" -> composite)
│       ├── marauder.py     # LINE_RE: Ch/RSSI/BSSID/ESSID
│       ├── evil_m5project.py  # NETWORK_RE + CLIENT_RE, _last_bssid, _line_timestamp
│       └── wifi_duck.py / hash_monster.py / packet_monitor.py  # stubs
│   ├── source/
│   │   ├── base.py         # AbstractSource (async start/stop), LineCallback
│   │   ├── file_source.py  # tail -f / reproduccion unica (fixtures/offline)
│   │   └── serial_source.py  # pyserial en hilo + cola asyncio, reconexion/backoff, passthrough
│   ├── store/
│   │   ├── base.py         # AbstractStore.apply(event), ObservationRow, event_to_observation_row
│   │   ├── memory_store.py # estado en dicts + historico append-only
│   │   └── sqlite_store.py # schema §8.2, indexes timestamp/last_seen, ISO-UTC
│   ├── web/
│   │   ├── __init__.py     # export create_app, EventHub
│   │   ├── app.py          # factory FastAPI + lifespan (arranca/detiene Collector)
│   │   ├── api.py          # endpoints REST; get_store() como dependencia FastAPI
│   │   ├── sse.py          # EventHub (pub-sub thread-safe) + GET /api/events
│   │   ├── schemas.py      # Pydantic: NetworkRead/ClientRead/HistoryRow/Health... + event_to_json
│   │   ├── static/         # css/js del dashboard (Fase 4)
│   │   └── templates/index.html  # dashboard (Fase 4)
│   └── worker/
│       └── collector.py    # source -> parser -> store, stats, reloj inyectable, observe() (multi)
└── tests/
    ├── conftest.py         # FIXTURES path, NOW = 2026-01-15T10:00:00Z (determinista)
    ├── fixtures/           # marauder_scan.log, evil_m5project_scan.log, malformed_lines.log
    ├── test_parsers.py       # parsers (Fase 1)
    ├── test_store.py         # stores parametrizado memoria/SQLite
    ├── test_serial_source.py # SerialSource con transporte falso (sin hardware)
    ├── test_integration.py   # end-to-end source -> parser -> store
    ├── test_api.py           # API REST con TestClient + MemoryStore (fixture seeded_store)
    ├── test_sse.py           # SSE con app ASGI a mano + hub unitario + wiring collector
    ├── test_web_ui.py        # dashboard HTML/assets + /api/console (Fase 4)
    ├── test_splunk_hec.py    # HEC: auth/sourcetype, breaker, spool, thread-safe (Fase 5)
    ├── test_cli.py           # CLI: version, export offline, snapshot, config precedence (Fase 5)
    └── test_packaging.py     # metadata/entry point v3.0.0 (Fase 5)
```

---

## Changelog (cada entrada con su mini prompt)

### Fase 1 — commit `61e1d20` (Fase 1 completa)

Cambios:
- `models.py`: modelos normalizados + union discriminada.
- `parser/`: AbstractParser, ParserRegistry, CompositeParser, MarauderParser, EvilM5ProjectParser, 3 stubs.
- Tests con fixtures; pyproject.toml; README; AUTHORS; .gitattributes (LF).

Mini prompt para retomar DESPUÉS de esta fase:

```text
Continúa m5stick-wireless-viewer en C:/Users/Sammi/m5stick-wireless-viewer
(Fase 1 hecha, commit 61e1d20). Lee SEGUIMIENTO.md y PLAN.md. Implementa la Fase 2:
store/base.py + memory_store.py + sqlite_store.py (esquema del plan §8: clients = estado
actual PK(mac), observations = histórico completo; indexes en observations.timestamp y
networks.last_seen), source/base.py + serial_source.py (pyserial, reconexión con backoff)
+ file_source.py (tail -f), worker/collector.py que orquesta source -> parser -> store
usando get_parser() de m5wireless.parser. Async. Tests: test_store.py y test_integration.py
con fixtures existentes. Mantén ruff + mypy --strict limpios y añade tests nuevos.
```

### Fase 2 — commit `09baa37` (Fase 2 completa)

Cambios:
- `store/`: `AbstractStore` con `apply(event)` como punto unico de entrada, `MemoryStore` y `SQLiteStore`.
- `source/`: `AbstractSource` (async start/stop), `FileSource` (tail -f / reproduccion unica) y `SerialSource` (pyserial en hilo + cola asyncio, autodeteccion, reconexion con backoff exponencial, passthrough a archivo).
- `worker/collector.py`: orquesta source -> parser -> store; stats (lines/events/errors); reloj inyectable para tests.
- Tests: `test_store.py` (parametrizado memoria/SQLite), `test_integration.py` (end-to-end con fixtures + composite), `test_serial_source.py` (transporte falso, sin hardware).
- `pyproject.toml`: extra `[serial]` (pyserial opcional) y `types-pyserial` en dev para mypy.

Mini prompt para retomar DESPUÉS de esta fase:

```text
Continúa m5stick-wireless-viewer en C:/Users/Sammi/m5stick-wireless-viewer
(Fase 2 hecha, commit 09baa37; 52 tests, ruff + mypy --strict limpios sobre src).
Lee SEGUIMIENTO.md y PLAN.md. Implementa la Fase 3 (Backend web, plan §9): crea
web/app.py (factory FastAPI), web/api.py con los endpoints del §9.1 leyendo de un
AbstractStore (get_networks/get_clients/get_channel_distribution/get_network_history),
web/sse.py con Server-Sent Events en /api/events que emita cada ObservationEvent nuevo,
y esquemas Pydantic (§9.3). Inyecta el store por dependencias para testear con MemoryStore.
Añade tests/test_api.py (TestClient) y mantén ruff + mypy --strict limpios.
```

### Fase 3 — commit `3590475` (Fase 3 completa)

Cambios:
- `web/app.py`: `create_app(store, collector=None)`; lifespan liga el EventHub al loop y arranca/detiene el Collector como task (stop + reap con timeout).
- `web/api.py`: endpoints REST del §9.1 (`/api/health`, `/api/networks` con filtros since/until/min_rssi/channel/ssid/firmware, `/api/networks/{bssid}`, `/api/clients[?bssid]`, `/api/clients/{mac}`, `/api/export/csv` (streaming por lotes), `/api/export/json`, `/api/stats/channels`). Store via `Depends(get_store)`; sin instancia global mutable.
- `web/sse.py`: `EventHub` (un `asyncio.Queue` por suscriptor; `publish_sync` thread-safe con `call_soon_threadsafe`) + `GET /api/events` (frames `data: {json}`, keep-alive 15 s, unsubscribe en finally).
- `web/schemas.py`: Pydantic v2 (NetworkRead, ClientRead, HistoryRow, NetworkDetail, HealthResponse...) + `event_to_json()` para SSE. Datetimes aware.
- Store (aditivo): `get_network`, `get_client`, `iter_observations` en MemoryStore y SQLiteStore.
- Collector: `observe(callback)` + property `source_type`; el observer no derriba la pipeline.
- Tests: `test_api.py` (TestClient + MemoryStore, fixture `seeded_store` en conftest) y `test_sse.py` (SSE manejando la app ASGI a mano porque el TestClient de starlette 1.x no soporta streaming; fan-out; keep-alive; publish desde hilo ajeno; lifespan con FileSource).
- `pyproject.toml`: extra `[web]` (fastapi, uvicorn); dev += fastapi + httpx; ruff ignora B008 (idioma FastAPI).

Mini prompt para retomar DESPUÉS de esta fase:

```text
Continúa m5stick-wireless-viewer en C:/Users/Sammi/m5stick-wireless-viewer
(Fase 3 hecha, commit 3590475; 75 tests, ruff + mypy --strict limpios sobre src).
Lee SEGUIMIENTO.md y PLAN.md. Implementa la Fase 4 (Frontend): dashboard en
web/templates/index.html que consuma GET /api/events (SSE) y los endpoints REST
(/api/networks con filtros, /api/networks/{bssid}, /api/clients, /api/stats/channels);
tabla de redes en vivo, filtros, graficas de distribucion por canal y vista de
detalle de red. Servir static/templates desde FastAPI (app.py). Mantén ruff +
mypy --strict limpios sobre src/m5wireless y los tests existentes pasando.
```

### Fase 4 — commit `87f8097` (Fase 4 completa)

Cambios:
- `web/templates/index.html`: dashboard con contadores (redes/clientes/fuente), tabla de redes (SSID, BSSID, canal, RSSI, clientes, ultima vista), panel de consola serial, distribucion por canal y enlaces a export CSV/JSON.
- `web/static/js/dashboard.js` (vanilla JS, sin frameworks): EventSource a `/api/events` con reconexion controlada (close explicito + timer de 3 s, indicador de estado); carga inicial via `/api/networks`, `/api/clients`, `/api/console?limit=200` y `/api/health`; actualizacion incremental (parchea solo la fila afectada; render completo solo para red nueva que pasa el filtro o cambio de filtro/ordenacion); filtros en cliente (texto SSID/BSSID, canal, RSSI minimo); ordenacion por columna (nulls siempre al final; default desc para RSSI y ultima vista); coloracion RSSI (>= -60 verde, -80..-60 naranja, < -80 rojo); consola con tope de 500 lineas y scroll automatico si el usuario esta cerca del fondo.
- `web/static/css/style.css`: tema oscuro, mobile-first; grid apilado en movil (< 768px) y dos columnas en escritorio; sin frameworks CSS.
- `app.py`: `/` sirve `templates/index.html` (adios al JSON placeholder de Fase 3); `/static` monta `web/static` con StaticFiles. Sin nuevas dependencias.
- `api.py`: nuevo `GET /api/console?limit` (1..1000, default 100) — ultimas N lineas del historico con `raw_line`, en orden cronologico. Alimenta el panel de consola.
- `schemas.py`: `ConsoleLine`/`ConsoleResponse` + `console_line()`. **Aditivo**: `event_to_json()` incluye ahora `raw_line` (los frames SSE llevan la linea raw y la consola se actualiza en vivo).
- Store: `get_recent_observations(limit)` en AbstractStore, MemoryStore y SQLiteStore (orden cronologico, de la mas antigua a la mas reciente; limit <= 0 -> vacio).
- Tests: `test_web_ui.py` (6 tests: `/` sirve el dashboard HTML, assets estaticos con content-type correcto, `/api/console` con limit/422) + `test_store.py` parametrizado para `get_recent_observations`; `test_api.test_root` actualizado a esperar HTML.

Decisiones (segun recomendaciones del prompt de Fase 4):
- Sin Chart.js en v3.0: la distribucion por canal es una lista con barras CSS; graficas de evolucion de RSSI y vista de detalle de red van a v3.1.
- Filtros en cliente sobre el estado ya cargado (rapidisimo, sin peticiones); consultas historicas via `/api/networks?since=...`.
- Actualizacion incremental de la tabla, no render total por evento.

Validacion en navegador real (CDP/Chrome): carga inicial, filtros de texto/canal/RSSI,
ordenacion asc/desc, y eventos SSE en vivo con parche incremental + insercion de red nueva.
El smoke test en navegador encontro 3 bugs que los tests unitarios no cubrian:
(1) `/api/clients` no se cargaba en init (contador de clientes a 0 hasta el primer evento
SSE), (2) `NetworkRead` trae `last_seen`, no `timestamp`, asi que la carga inicial dejaba
`last_seen` undefined y la ordenacion por ultima vista caia al tie-break por BSSID,
(3) una red nueva no creaba fila (solo se parchaban las existentes). Leccion: el frontend
siempre necesita un pase de navegador, no solo tests del backend.

Estado final: 82 tests pasando, ruff limpio, mypy --strict limpio (25 ficheros),
cobertura web/ 97%.

Mini prompt para retomar DESPUÉS de esta fase:

```text
Continúa m5stick-wireless-viewer en C:/Users/Sammi/m5stick-wireless-viewer
(Fase 4 hecha, commit 87f8097; 82 tests, ruff + mypy --strict limpios sobre src).
Lee SEGUIMIENTO.md y PLAN.md. Pendiente de la Fase 4 pospuesto a v3.1: vista de
detalle de red (GET /api/networks/{bssid} ya existe: estado + clientes + historico)
y graficas Chart.js opcionales. Alternativamente, Fase 5 del plan (Exporters y CLI):
Splunk HEC robusto + CLI unificado (CSV/JSON streaming ya esta en web/api.py).
Reglas: ruff + mypy --strict limpios sobre src/m5wireless, commits en espanol sin
emojis, smoke test de navegador con CDP para cualquier cambio de frontend.
```

### Fase 5 — commit `a418dc5` (Fase 5 completa, v3.0.0 funcional)

Cambios:
- `exporter/splunk_hec.py`: `SplunkHecConfig` + `SplunkHecExporter` con httpx async; `verify=True` por defecto (False solo via config explicita); envio por lotes (`batch_size`); cola en memoria thread-safe (`submit()` callable desde el hilo del colector) con spool JSONL a disco en desborde (`max_queue_size`/`spool_path`, se recarga al arrancar) y drop contado si no hay spool; circuit breaker (N fallos consecutivos -> pausa T s, cola intacta); `event_to_payload()` plano por evento. Ciclo de vida: `start()`/`stop(drain_timeout)` con drenado best-effort.
- `worker/collector.py`: `observe()` ahora admite VARIOS observadores (aditivo; el hub SSE y el exporter HEC conviven).
- `web/app.py`: `create_app(..., exporter=...)` opcional: lifespan arranca/detiene el exporter y cablea `collector.observe(exporter.submit)`. Version 3.0.0 en la app FastAPI.
- `cli.py` (CLI unificada, argparse, cero deps nuevas): `m5wireless run` (serial|file + web; store memoria o SQLite con `--db-path`; Splunk HEC auto si hay URL+token), `export csv|json` (offline, mismo esquema de columnas que `/api/export/*`), `snapshot` (legacy auto_save_html: polling urllib a la URL, guarda HTML con timestamp, `--max N`). Config: CLI > env `M5W_*` > `m5wireless.toml` (./ o ~/.config/m5wireless/; secciones [run]/[splunk]).
- `pyproject.toml`: version 3.0.0, requires-python >=3.11 (tomllib), `[project.scripts] m5wireless = "m5wireless.cli:main"`, extras `[serial]`/`[web]`/`[splunk]`(httpx)/`[dev]`(+uvicorn). hatchling empaqueta static/templates sin cambios.
- `Dockerfile` (python:3.11-slim, instala `. [serial,web,splunk]`, CMD modo file con `/data/scan.log`) y `docker-compose.yml` (m5wireless + splunk opcional en profile `splunk`).
- Tests: `test_splunk_hec.py` (9), `test_cli.py` (7), `test_packaging.py` (3). Fixes mecanicos ruff py311 (UP017/UP035) en ficheros antiguos + `fromisoformat` sin replace Z en test_api.

Validacion: 103 tests, ruff limpio, mypy --strict limpio (28 ficheros). Instalacion limpia no-editable en venv temporal con `. [serial,web,splunk]`: `m5wireless --version` -> 3.0.0; `run --source file` sirve `/` (HTML dashboard) + static + API desde el wheel; `export csv` offline correcto.

Decisiones: argparse (no typer) para no engordar el core; HEC activado solo con URL+token config y disparado por el colector (no manual); eventos con fallo POST se cuentan y NO se reenvian (la cola pendiente si se conserva); snapshot = subcomando legacy.

Mini prompt para retomar DESPUÉS de esta fase:

```text
Continúa m5stick-wireless-viewer en C:/Users/Sammi/m5stick-wireless-viewer
(Fase 5 hecha, commit a418dc5; v3.0.0 funcional; 103 tests, ruff + mypy --strict
limpios sobre src). Lee SEGUIMIENTO.md y PLAN.md. Pendiente: Fase 6/7 del plan —
CI (lint+test+typecheck), tag v3.0.0, archivar wifi-marauder-viewer con README de
redireccion, repo remoto (Fase 0 pendiente del usuario). v3.1: vista de detalle de
red (GET /api/networks/{bssid} ya existe) + graficas Chart.js + parsers stubs con
fixtures reales. Reglas: ruff + mypy --strict limpios sobre src/m5wireless, commits
en espanol sin emojis, smoke test de navegador con CDP para cambios de frontend.
```

### Fase 6/7 (release engineering) - prep local completada, acciones remotas pendientes del usuario

Cambios:
- `.github/workflows/ci.yml`: matriz Python 3.11/3.12; `ruff check`, `ruff format --check`, `mypy --strict src/m5wireless`, `pytest --cov=m5wireless`.
- `.github/workflows/release.yml`: trigger en tag `v*`; build sdist+wheel, GitHub Release con artifacts (softprops/action-gh-release); job `pypi` con trusted publishing (OIDC, sin token manual) que solo corre si la variable de repo `PUBLISH_TO_PYPI=true`.
- `ruff format` aplicado a src/tests (11 ficheros; mypy --strict y 103 tests siguen limpios).
- `.gitignore`: añadidos `*.db`, `*.log`, `snapshots/`.
- `pyproject.toml`: `pytest-cov>=5` en extra `[dev]` (lo usa CI).
- `docs/legacy-wifi-marauder-viewer-README.md`: borrador del aviso de fusion para el README de wifi-marauder-viewer antes de archivarlo.

Cierre de Fase 6/7 (ejecutado):
- Repo renombrado a `PoisonXploIT/m5stick-wireless-viewer`; historia vieja de Visualizacion_extendida conservada en la rama `legacy-visualizacion-v2` (+ tag `version2`) antes del force-push de `main`.
- `main` empujado, CI verde (3.11/3.12).
- PyPI: cuenta con 2FA, trusted publisher PENDIENTE registrado (GitHub, repo m5stick-wireless-viewer, workflow `release.yml`, sin entorno); variable de repo `PUBLISH_TO_PYPI=true`.
- Tag `v3.0.0` empujado: GitHub Release con wheel + sdist y publicacion en PyPI via OIDC (job `pypi` success).
- `wifi-marauder-viewer`: aviso de fusion en README (borrador en `docs/legacy-wifi-marauder-viewer-README.md`) y repo archivado.
- Nota: el pending publisher NO reserva el nombre; si el primer upload no hubiera creado el proyecto, otro usuario podia tomarlo. Se resolvio creando el proyecto mediante el propio tag v3.0.0.

### Estado actual (kickoff v3.1)

- Demo en localhost: `m5wireless run --source file --log-path data/demo_scan.log` (modo file = tail -f; para ver redes en vivo hay que appendear lineas al log, p. ej. las "Found network" del fixture marauder).
- Nota de continuidad tambien en el vault: `PROYECTOS/m5wireless/m5wireless - estado y kickoff v3.1.md`.
- Orden v3.1: (1) vista de detalle de red (endpoint `GET /api/networks/{bssid}` ya existe; falta pagina HTML + link desde la tabla), (2) Chart.js con evolucion RSSI/actividad, (3) parsers stubs SOLO con fixtures reales.

Mini prompt para retomar:

```text
Continúa m5stick-wireless-viewer en C:/Users/Sammi/m5stick-wireless-viewer
(rama main; v3.0.0 publicada en GitHub y PyPI; 103 tests, ruff + mypy --strict limpios).
Lee SEGUIMIENTO.md (repo) y PLAN.md (C:\Users\Sammi\m5stick-wireless-viewer-plan).
Objetivo: v3.1 — (1) vista de detalle de red: GET /api/networks/{bssid} ya existe, falta pagina
HTML + link desde la tabla de index.html; (2) Chart.js con evolucion RSSI/actividad temporal;
(3) parsers stubs WiFi Duck / Hash Monster / PacketMonitor SOLO con fixtures reales.
Reglas: ruff + mypy --strict limpios sobre src/m5wireless, commits en espanol sin emojis,
smoke test de navegador (CDP) para cambios de frontend, SEGUIMIENTO.md actualizado al cerrar.
```

---

## Pendientes / riesgos abiertos

- **Fixtures no verificados contra log real**: los fixtures reproducen el formato documentado en el código original; la primera corrida contra M5Stick real puede revelar líneas que no parsean (riesgo §17 del plan: añadir test de equivalencia old/new).
- **Repo remoto**: activo (`github.com/PoisonXploIT/m5stick-wireless-viewer`); v3.0.2 publicada en GitHub y PyPI (fix demo_scan.log).
- **v3.2 Bruce**: publicada como v3.2.0 (parsers consola/pcap + `BruceStorageSource`, 155 tests, validacion COM7 del formato `storage list` incluida). E2E con sniffer real COMPLETADA (ver seccion 'Bruce (M5Stick)'). Pendiente: WebUI Bruce para control remoto.
- **Python version note**: `pyproject` pide >=3.11 (tomllib, dataclass slots, union types en runtime).

## Convenciones de trabajo

- Commits en español, sin emojis, mensaje descriptivo.
- Cada fase termina con: tests pasando, ruff limpio, mypy --strict limpio, commit, y actualización de este SEGUIMIENTO.md (nueva entrada en changelog con su mini prompt).
- No inventar parsers sin fixture real.
- `datetime.now(timezone.utc)` vía `utc_now()`; nunca `utcnow`.
