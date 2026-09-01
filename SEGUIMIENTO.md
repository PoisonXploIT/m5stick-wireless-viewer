# SEGUIMIENTO — m5stick-wireless-viewer

Documento de seguimiento para vaciar contexto sin perder el hilo. Cada fase/cambio
lleva su **mini prompt**: bloques copy-paste para situar a un agente en sesión nueva
tras overflow de contexto, sin necesidad de compactar.

Última actualización: Fase 2 completa (commit `09baa37`).

---

## PROMPT DE RETOMADA (copiar en sesión nueva)

```text
Reanuda el proyecto m5stick-wireless-viewer en C:/Users/Sammi/m5stick-wireless-viewer.
Lee primero SEGUIMIENTO.md y PLAN.md (en C:/Users/Sammi/m5stick-wireless-viewer-plan/PLAN.md).
Estado: Fase 2 completa (modelos + parsers; stores MemoryStore/SQLiteStore; sources
FileSource/SerialSource async; Collector; 52 tests pasando, ruff y mypy --strict limpios
sobre src/m5wireless). Venv en .venv (.venv/Scripts/python -m pytest / ruff / mypy).
Siguiente: Fase 3 del plan (Backend web: FastAPI + SSE + esquemas Pydantic, §9).
Reglas de proyecto: sin datetime.utcnow (usar utc_now() aware), parsers con fixtures
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
| Fase 3-7 | Pendientes |

Tests: 52 pasando. Lint: ruff limpio. Tipos: mypy --strict limpio (sobre `src/m5wireless`).
Venv: `.venv/` (pytest, ruff, mypy; paquete instalado editable; extras serial + types-pyserial).

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
│   └── worker/
│       └── collector.py    # source -> parser -> store, stats, reloj inyectable
└── tests/
    ├── conftest.py         # FIXTURES path, NOW = 2026-01-15T10:00:00Z (determinista)
    ├── fixtures/           # marauder_scan.log, evil_m5project_scan.log, malformed_lines.log
    ├── test_parsers.py       # parsers (Fase 1)
    ├── test_store.py         # stores parametrizado memoria/SQLite
    ├── test_serial_source.py # SerialSource con transporte falso (sin hardware)
    └── test_integration.py   # end-to-end source -> parser -> store
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
