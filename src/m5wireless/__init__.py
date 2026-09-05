"""m5wireless: pipeline de datos para firmwares WiFi de hardware hacking en ESP32."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("m5stick-wireless-viewer")
except PackageNotFoundError:  # pragma: no cover - solo ocurre sin instalar
    __version__ = "0.0.0"
