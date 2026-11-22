import asyncio
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from jobbot import launcher
from jobbot.config import Config


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _cfg(port: int, tmp_path: Path) -> Config:
    return Config(brave_exe=Path("brave.exe"), profile_dir=tmp_path / "prof", port=port,
                  data_dir=tmp_path, cv_path=tmp_path / "cv.pdf")


class _Version(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"Browser": "Brave/fake"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def test_reuses_running_brave(tmp_path, monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), _Version)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        def no_launch(*a, **k):
            raise AssertionError("should not launch Brave when the port already answers")
        monkeypatch.setattr(launcher.subprocess, "Popen", no_launch)
        launched = asyncio.run(launcher.ensure_brave(_cfg(srv.server_port, tmp_path)))
        assert launched is False
    finally:
        srv.shutdown()


def test_times_out_with_clear_error(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda argv: calls.append(argv))
    port = _free_port()
    with pytest.raises(RuntimeError, match="debugging port"):
        asyncio.run(launcher.ensure_brave(_cfg(port, tmp_path), wait_s=0.5))
    argv = calls[0]
    assert f"--remote-debugging-port={port}" in argv
    assert any(a.startswith("--user-data-dir=") for a in argv)
    assert (tmp_path / "prof").is_dir()
