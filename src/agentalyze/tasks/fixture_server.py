"""Local HTTP server that serves the ``fixtures/`` directory.

Implementation choice: the standard library's ``http.server`` on a daemon
thread rather than starlette/uvicorn. The server only has to serve static
files from one directory; adding an ASGI stack would be an extra dependency
with zero benefit, and stdlib keeps fixture runs fully deterministic and
dependency-light. The port is picked by the OS (``socket.bind((host, 0))``)
so parallel test runs never collide.

``Task.fixture_url_path`` values are *relative* URL paths; the absolute base
URL (including the runtime port) is ``FixtureServer.base_url``.
"""

from __future__ import annotations

import socket
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import TracebackType
from typing import Self

_DEFAULT_FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures"


class _QuietHandler(SimpleHTTPRequestHandler):
    """Static file handler without per-request stderr noise."""

    def log_message(self, format: str, *args: object) -> None:
        return None


class FixtureServer:
    """Serves a directory over HTTP on a random free localhost port.

    Usable as an async context manager (the Phase 3 runner is async)::

        async with FixtureServer() as server:
            url = server.base_url + "/form_fill/basic_01.html"
    """

    def __init__(self, root: Path | None = None, host: str = "127.0.0.1") -> None:
        self._root = (root or _DEFAULT_FIXTURES_DIR).resolve()
        if not self._root.is_dir():
            msg = f"Fixtures directory does not exist: {self._root}"
            raise NotADirectoryError(msg)
        self._host = host
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def root(self) -> Path:
        return self._root

    @property
    def base_url(self) -> str:
        if self._httpd is None:
            raise RuntimeError("FixtureServer is not started.")
        return f"http://{self._host}:{self._httpd.server_address[1]}"

    def start(self) -> None:
        """Bind a free OS-assigned port and start serving in a daemon thread."""
        if self._httpd is not None:
            return
        with socket.socket(self._resolve_family(), socket.SOCK_STREAM) as probe:
            probe.bind((self._host, 0))
            free_port = int(probe.getsockname()[1])
        handler = partial(_QuietHandler, directory=str(self._root))
        self._httpd = ThreadingHTTPServer((self._host, free_port), handler)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="agentalyze-fixture-server",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Shut the server down and release the port."""
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._httpd = None
        self._thread = None

    async def __aenter__(self) -> Self:
        self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()

    def _resolve_family(self) -> socket.AddressFamily:
        import socket as _socket

        return _socket.AF_INET6 if ":" in self._host and _socket.has_ipv6 else _socket.AF_INET
