"""Tests de la CLI unificada (Fase 5)."""

from __future__ import annotations

import csv
import http.server
import json
import threading
from pathlib import Path

import pytest

from m5wireless import cli


def test_version_prints_3_0_0(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    assert excinfo.value.code == 0
    assert "3.0.0" in capsys.readouterr().out


def test_export_csv_offline(marauder_log_path: Path, tmp_path: Path) -> None:
    output = tmp_path / "out.csv"
    rc = cli.main(
        [
            "export",
            "csv",
            "--input",
            str(marauder_log_path),
            "--firmware",
            "marauder",
            "--output",
            str(output),
        ]
    )
    assert rc == 0
    with output.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == list(cli.CSV_COLUMNS)
    # El fixture marauder produce 4 eventos.
    assert len(rows) == 5
    assert all(row[3] in ("network_seen", "client_associated") for row in rows[1:])


def test_export_json_offline(marauder_log_path: Path, tmp_path: Path) -> None:
    output = tmp_path / "out.json"
    rc = cli.main(
        [
            "export",
            "json",
            "--input",
            str(marauder_log_path),
            "--firmware",
            "marauder",
            "--output",
            str(output),
        ]
    )
    assert rc == 0
    data = json.loads(output.read_text(encoding="utf-8"))
    assert isinstance(data, list) and len(data) == 4
    assert set(data[0]) >= {"timestamp", "firmware", "event_type", "bssid", "rssi", "raw_line"}


def test_export_missing_input_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(
        [
            "export",
            "csv",
            "--input",
            str(tmp_path / "nope.log"),
            "--firmware",
            "marauder",
            "--output",
            str(tmp_path / "o.csv"),
        ]
    )
    assert rc == 2
    assert "no existe" in capsys.readouterr().err


def test_run_missing_log_file_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(["run", "--source", "file", "--log-path", str(tmp_path / "missing.log")])
    assert rc == 2
    assert "no existe" in capsys.readouterr().err


def test_snapshot_saves_html(tmp_path: Path) -> None:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>dashboard</body></html>")

        def log_message(self, *args: object) -> None:  # silencio.
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    try:
        rc = cli.main(
            [
                "snapshot",
                "--url",
                f"http://127.0.0.1:{port}",
                "--interval",
                "0.05",
                "--dir",
                str(tmp_path / "snaps"),
                "--max",
                "2",
            ]
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
    assert rc == 0
    snaps = sorted((tmp_path / "snaps").glob("snapshot-*.html"))
    assert len(snaps) == 2
    assert "dashboard" in snaps[0].read_text(encoding="utf-8")


def test_config_precedence_cli_over_env_over_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    toml = tmp_path / "m5wireless.toml"
    toml.write_text(
        '[run]\nweb_port = 9000\n[splunk]\nurl = "https://splunk:8088"\n', encoding="utf-8"
    )
    monkeypatch.setenv("M5W_WEB_PORT", "9100")

    # toml < env:
    args = cli.build_parser().parse_args(["run", "--config", str(toml)])
    cfg = cli._resolve_run_config(args)
    assert cfg["web_port"] == 9100
    assert cfg["splunk_url"] == "https://splunk:8088"

    # env < CLI:
    args_cli = cli.build_parser().parse_args(["run", "--config", str(toml), "--web-port", "9200"])
    cfg_cli = cli._resolve_run_config(args_cli)
    assert cfg_cli["web_port"] == 9200


def test_splunk_exporter_built_only_with_url_and_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Sin URL/token: no exporter.
    assert (
        cli._build_splunk_exporter(
            {"splunk_url": None, "splunk_token": "t", "splunk_verify_ssl": True}
        )
        is None
    )
    monkeypatch.setenv("M5W_SPLUNK_HEC_URL", "https://splunk:8088/services/collector/event")
    monkeypatch.setenv("M5W_SPLUNK_HEC_TOKEN", "tok-abc")
    cfg = {
        "splunk_url": "https://splunk:8088/services/collector/event",
        "splunk_token": "tok-abc",
        "splunk_verify_ssl": True,
    }
    exporter = cli._build_splunk_exporter(cfg)
    assert exporter is not None
    assert exporter.verify is True
    # verify=False solo con configuracion explicita.
    cfg["splunk_verify_ssl"] = "false"
    exporter_off = cli._build_splunk_exporter(cfg)
    assert exporter_off is not None and exporter_off.verify is False
