"""FixtureServer lifecycle tests: free-port binding, static serving, clean stop."""

from __future__ import annotations

import socket
import urllib.request

import pytest

from agentalyze.tasks.fixture_server import FixtureServer


def _http_get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.read().decode()


def _port_is_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex((host, port)) == 0


async def test_serves_static_files_from_custom_root(tmp_path) -> None:
    root = tmp_path / "fixtures"
    (root / "form_fill").mkdir(parents=True)
    (root / "form_fill" / "page.html").write_text("<html>hello</html>", encoding="utf-8")

    async with FixtureServer(root=root) as server:
        assert server.base_url.startswith("http://127.0.0.1:")
        body = _http_get(server.base_url + "/form_fill/page.html")
        assert "hello" in body


async def test_default_root_serves_repo_fixtures(fixture_server) -> None:
    body = _http_get(fixture_server.base_url + "/form_fill/basic_01.html")
    assert "#success-marker" in body


async def test_base_url_requires_started_server(tmp_path) -> None:
    server = FixtureServer(root=tmp_path)
    try:
        with pytest.raises(RuntimeError):
            _ = server.base_url
    finally:
        server.stop()


async def test_stop_releases_the_port_and_restart_works(tmp_path) -> None:
    root = tmp_path / "fixtures"
    root.mkdir()
    (root / "a.html").write_text("<html>a</html>", encoding="utf-8")

    server = FixtureServer(root=root)
    server.start()
    host, port = "127.0.0.1", int(server.base_url.rsplit(":", 1)[1])
    assert _http_get(f"http://{host}:{port}/a.html")

    server.stop()
    assert not _port_is_open(host, port), "port was not released after stop()"

    # A fresh server (as in the next test / next run) must start without
    # "port already in use" errors.
    again = FixtureServer(root=root)
    again.start()
    try:
        assert _http_get(again.base_url + "/a.html")
    finally:
        again.stop()


async def test_start_is_idempotent(tmp_path) -> None:
    server = FixtureServer(root=tmp_path)
    server.start()
    try:
        first_url = server.base_url
        server.start()  # must be a no-op, not a second bind
        assert server.base_url == first_url
    finally:
        server.stop()
