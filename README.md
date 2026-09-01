# m5stick-wireless-viewer

Pipeline de datos y dashboard para firmwares de hardware hacking WiFi en ESP32.

Fusiona `wifi-marauder-viewer` y `Visualizacion_extendida_M5StickPlus2` sobre una
arquitectura modular: parsers por firmware, fuentes serial/file, store con
historico (SQLite) y API web (FastAPI + SSE).

## Estado

En desarrollo segun `m5stick-wireless-viewer-plan/PLAN.md`. Fase 1 completa:
modelos normalizados y parsers de Marauder y Evil-M5Project con tests.

## Instalacion (desarrollo)

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"   # Windows
# o: source .venv/bin/activate && pip install -e ".[dev]"  # Linux/macOS
```

## Tests

```bash
.venv/Scripts/python -m pytest
```

## Firmwares soportados

| Firmware | Estado |
|----------|--------|
| WiFi Marauder (M5StickC/ESP32) | Implementado |
| Evil-M5Project (M5Stick Plus 2) | Implementado |
| WiFi Duck, Hash Monster, PacketMonitor | Stubs documentados, pendiente de fixtures reales |

## Licencia

MIT. Ver `AUTHORS.md` para creditos de los proyectos originales.
