from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import pytest


class FakeAthena:
    """Athena's status API, as the README describes it, on a free port on this machine."""

    def __init__(self, token: str = ""):
        self.token = token
        self.status: dict[str, Any] = {"state": "idle"}
        self.assistant = {"name": "Athena", "timezone": "Europe/London"}
        self.api = 1
        self.clock_offset = 0.0  # how far Athena's clock is ahead of ours
        self.answer: tuple[int, bytes] | None = None  # answer every request with this instead
        self.requests: list[dict[str, str]] = []  # each request's query, plus its Authorization header
        self._changes = 0
        self._closing = False
        self._changed = threading.Condition()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                url = urlsplit(self.path)
                query = dict(parse_qsl(url.query))
                fake.requests.append({**query, "authorization": self.headers.get("Authorization", "")})
                if fake.answer:
                    return self._send(*fake.answer)
                if url.path != "/v1/status":
                    return self._send(404, b"Not found.")
                if fake.token and self.headers.get("Authorization") != f"Bearer {fake.token}":
                    return self._send(401, b"Wrong or missing token.")
                wait = min(float(query.get("wait", 0)), 30.0)
                with fake._changed:
                    if wait and query.get("after") == fake.version:
                        fake._changed.wait_for(lambda: fake._closing or query["after"] != fake.version, wait)
                    body = json.dumps(fake.payload()).encode()
                self._send(200, body, "application/json")

            def _send(self, code: int, body: bytes, kind: str = "text/plain") -> None:
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    @property
    def version(self) -> str:
        return f"fake-{self._changes}"

    def set(self, **status: Any) -> None:
        """Athena starts doing something else."""
        with self._changed:
            self.status = status
            self._changes += 1
            self._changed.notify_all()

    def payload(self) -> dict[str, Any]:
        return {
            "api": self.api,
            "version": self.version,
            "now": time.time() + self.clock_offset,
            "assistant": self.assistant,
            "status": self.status,
        }

    def close(self) -> None:
        with self._changed:
            self._closing = True
            self._changed.notify_all()
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def athena():
    fake = FakeAthena()
    yield fake
    fake.close()
