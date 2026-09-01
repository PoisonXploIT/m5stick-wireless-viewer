# SEGUIMIENTO — m5stick-wireless-viewer

Documento de seguimiento para vaciar contexto sin perder el hilo. Cada fase/cambio
lleva su **mini prompt**: bloques copy-paste para situar a un agente en sesión nueva
tras overflow de contexto, sin necesidad de compactar.

Última actualización: Fase 4 completa (commit `87f8097`).

---

## PROMPT DE RETOMADA (copiar en sesión nueva)

```text
Reanuda el proyecto m5stick-wireless-viewer en C:/Users/Sammi/m5stick-wireless-viewer.
Lee primero SEGUIMIENTO.md y PLAN.md (en C:/Users/Sammi/m5stick-wireless-viewer-plan/PLAN.md).
Estado: Fase 4 completa (dashboard HTML/CSS/JS vanilla en web/templates/index.html +
web/static/, SSE en vivo con reconexion, filtros y ordenacion en cliente; backend FastAPI
+ SSE de la Fase 3 intacto; 82 tests pasando, ruff y mypy --strict limpios sobre
src/m5wireless). Venv en .venv (.venv/Scripts/python -m pytest / ruff / mypy).
Siguiente: vista de detalle de red pospuesta a v3.1 (el plan §Fase 4 la permitia mover)
y/o Fase 5 del plan (Exporters y CLI: Splunk HEC robusto + CLI unificado; CSV/JSON
streaming ya existe desde Fase 3). Reglas de proyecto: sin datetime.utcnow (usar utc_now() aware), parsers con fixtures
reales solo, registro de parsers almacena clases no instancias, ruff+mypy --strict limpios
en cada commit (mypy sobre src/m5wireless; NO sobre tests/). No uses emojis en salidas.
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
| Fase 5-7 | Pendientes |

Tests: 82 pasando. Lint: ruff limpio. Tipos: mypy --strict limpio (sobre `src/m5wireless`).
Cobertura web/: 97% (criterio minimo 80%).
Venv: `.venv/` (pytest, ruff, mypy; paquete instalado editable; extras serial + types-pyserial;
fastapi/uvicorn en extra [web] e httpx en dev para TestClient).

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

---

## Estructura actual del repo

```text
m5stick-wireless-viewer/
├── .gitattributes, .gitignore, AUTHORS.md, README.md, SEGUIMIENTO.md
├── pyproject.toml          # hatchling, requires-python >=3.10, extra [dev]: pytest/ruff/mypy
├── src/m5wireless/
│   ├── __init__.py         # __version__ = "3.0.0a1"
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
│   │   ├── static/         # .gitkeep (Fase 4)
│   │   └── templates/index.html  # placeholder (Fase 4)
│   └── worker/
│       └── collector.py    # source -> parser -> store, stats, reloj inyectable, observe()
└── tests/
    ├── conftest.py         # FIXTURES path, NOW = 2026-01-15T10:00:00Z (determinista)
    ├── fixtures/           # marauder_scan.log, evil_m5project_scan.log, malformed_lines.log
    ├── test_parsers.py       # parsers (Fase 1)
    ├── test_store.py         # stores parametrizado memoria/SQLite
    ├── test_serial_source.py # SerialSource con transporte falso (sin hardware)
    ├── test_integration.py   # end-to-end source -> parser -> store
    ├── test_api.py           # API REST con TestClient + MemoryStore (fixture seeded_store)
    └── test_sse.py           # SSE con app ASGI a mano + hub unitario + wiring collector
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

---

## Pendientes / riesgos abiertos

- **Fixtures no verificados contra log real**: los fixtures reproducen el formato documentado en el código original; la primera corrida contra M5Stick real puede revelar líneas que no parsean (riesgo §17 del plan: añadir test de equivalencia old/new).
- **Fase 0 incompleta**: falta repo remoto y `git remote add` (acción del usuario).
- **Python version note**: `pyproject` pide >=3.10 (dataclass slots + union types en runtime).

## Convenciones de trabajo

- Commits en español, sin emojis, mensaje descriptivo.
- Cada fase termina con: tests pasando, ruff limpio, mypy --strict limpio, commit, y actualización de este SEGUIMIENTO.md (nueva entrada en changelog con su mini prompt).
- No inventar parsers sin fixture real.
- `datetime.now(timezone.utc)` vía `utc_now()`; nunca `utcnow`.
