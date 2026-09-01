# m5wireless v3.0.0 — modo file por defecto (Docker = modo secundario; la captura
# serial en vivo se documenta como host nativo, decision del plan).
FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir ".[serial,web,splunk]"

# El log lo aporta el volumen; se crea vacio si no existe para que `run` arranque.
VOLUME ["/data"]
EXPOSE 8000

ENV M5W_LOG_PATH=/data/scan.log \
    PYTHONUNBUFFERED=1

CMD ["sh", "-c", "touch /data/scan.log && exec m5wireless run --source file --log-path /data/scan.log --host 0.0.0.0"]
