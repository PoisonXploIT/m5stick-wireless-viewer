from flask import Flask, render_template_string
import os
from datetime import datetime

app = Flask(__name__)

LOG_PATH = os.environ.get("M5_LOG_PATH", "wifi_scan_evil.log")
PORT = int(os.environ.get("M5_PORT", "5000"))
MAX_CHARS = int(os.environ.get("M5_MAX_CHARS", "10000"))

HTML = """
<!doctype html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <title>M5Stick Monitor</title>
    <meta http-equiv="refresh" content="2">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { background: #0a0a0a; color: #00ff41; font-family: 'Courier New', monospace; padding: 1rem; }
        h2 { color: #00ff41; margin-bottom: 1rem; border-bottom: 1px solid #1a1a1a; padding-bottom: 0.5rem; }
        pre { white-space: pre-wrap; word-wrap: break-word; font-size: 0.85rem; line-height: 1.4; }
        .status { color: #666; font-size: 0.75rem; margin-top: 1rem; }
    </style>
</head>
<body>
    <h2>M5Stick Plus 2 - Console View</h2>
    <pre>{{ content }}</pre>
    <div class="status">Last update: {{ timestamp }} | File: {{ log_path }}</div>
</body>
</html>
"""


@app.route("/")
def index():
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            contenido = f.read()[-MAX_CHARS:]
    else:
        contenido = "Esperando datos del M5Stick..."
    return render_template_string(
        HTML,
        content=contenido,
        timestamp=datetime.now().strftime("%H:%M:%S"),
        log_path=LOG_PATH,
    )


@app.route("/health")
def health():
    return {"status": "up", "log_file": LOG_PATH}


if __name__ == "__main__":
    print(f"[*] M5Stick Monitor en http://0.0.0.0:{PORT}")
    print(f"[*] Leyendo log de: {LOG_PATH}")
    app.run(host="0.0.0.0", port=PORT)
