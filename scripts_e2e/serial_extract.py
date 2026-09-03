"""Extraccion serial (BruceStorageSource) de los pcaps para cmp contra HTTP.

Uso: .venv/Scripts/python.exe scripts_e2e/serial_extract.py COM7
Guarda en data/artifacts_serial/<nombre>.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from m5wireless.source.bruce_source import BruceStorageSource


async def main(port: str, out_dir: Path) -> int:
    files: dict[str, bytes] = {}
    src = BruceStorageSource(port=port, baudrate=115200, poll_interval=3.0)
    src.observe_files(lambda path, data: files.__setitem__(path, data))

    task = asyncio.ensure_future(src.start(lambda line: None))
    for _ in range(90):
        if len(files) >= 2:
            break
        await asyncio.sleep(1.0)
    await src.stop()
    await task

    out_dir.mkdir(parents=True, exist_ok=True)
    for path, data in files.items():
        name = Path(path).name
        (out_dir / name).write_bytes(data)
        print(f"{name}: {len(data)} bytes")
    return 0 if len(files) >= 2 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1], Path("data/artifacts_serial"))))
