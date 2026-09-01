# SEGUIMIENTO — m5stick-wireless-viewer

Documento de seguimiento para vaciar contexto sin perder el hilo. Cada fase/cambio
lleva su **mini prompt**: bloques copy-paste para situar a un agente en sesión nueva
tras overflow de contexto, sin necesidad de compactar.

Última actualización: Fase 1 completa (commit `61e1d20`).

---

## PROMPT DE RETOMADA (copiar en sesión nueva)

```text
Reanuda el proyecto m5stick-wireless-viewer en C:/Users/Sammi/m5stick-wireless-viewer.
Lee primero SEGUIMIENTO.md y PLAN.md (en C:/Users/Sammi/m5stick-wireless-viewer-plan/PLAN.md).
Estado: Fase 1 completa (modelos + parsers Marauder/Evil-M5Project, 27 tests pasando,
ruff y mypy --strict limpios). Venv en .venv (usar .venv/Scripts/python -m pytest).
Siguiente: Fase 2 del plan (stores memory/sqlite, sources serial/file async, Collector).
Reglas de proyecto: sin datetime.utcnow (usar utc_now() aware), parsers con fixtures
reales solo, registro de parsers almacena clases no instancias, ruff+mypy strict limpios
en cada commit. No uses emojis en salidas ni commits.
```

---

## Estado actual

| Item | Estado |
|------|--------|
| Fase 0 (preparación) | Parcial: git init local hecho; falta repo remoto GitHub y `git remote add` (acción del usuario) |
| Fase 1 (modelos + parsers) | **Completa** — commit `61e1d20` |
| Fase 2 (stores + sources + collector) | Pendiente |
| Fase 3-7 | Pendientes |

Tests: 27 pasando. Lint: ruff limpio. Tipos: mypy --strict limpio.
Venv: `.venv/` (pytest, ruff, mypy; paquete instalado editable).

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

---

## Estructura actual del repo

```text
m5stick-wireless-viewer/
├── .gitattributes, .gitignore, AUTHORS.md, README.md, SEGUIMIENTO.md
├── pyproject.toml          # hatchling, requires-python >=3.10, extra [dev]: pytest/ruff/mypy
├── src/m5wireless/
│   ├── __init__.py         # __version__ = "3.0.0a1"
│   ├── models.py           # Network, Client, Observation, NetworkSeen, ClientAssociated, normalize_mac, utc_now
│   └── parser/
│       ├── __init__.py     # importa marauder + evil_m5project (dispara registro)
│       ├── base.py         # AbstractParser, CompositeParser
│       ├── registry.py     # ParserRegistry (almacena clases), register_parser, get_parser("auto" -> composite)
│       ├── marauder.py     # LINE_RE: Ch/RSSI/BSSID/ESSID
│       ├── evil_m5project.py  # NETWORK_RE + CLIENT_RE, _last_bssid, _line_timestamp
│       └── wifi_duck.py / hash_monster.py / packet_monitor.py  # stubs
└── tests/
    ├── conftest.py         # FIXTURES path, NOW = 2026-01-15T10:00:00Z (determinista)
    ├── fixtures/           # marauder_scan.log, evil_m5project_scan.log, malformed_lines.log
    └── test_parsers.py     # 27 tests
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
