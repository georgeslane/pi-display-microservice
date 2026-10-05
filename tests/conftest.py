from __future__ import annotations

import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import pytest
from PIL import Image


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
        threading.Thread(target=self._server.serve_forever, args=(0.05,), daemon=True).start()  # quick to shut down

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


def album_page(title: str, photos: list[tuple[str, str]], more: bool = False) -> str:
    """A shared album's page, as Google Photos writes it: its data is JSON in a call to AF_initDataCallback.

    ``photos`` are (id, url) pairs. Everything else is made up, in the shape Google's pages have.
    """
    items = [
        [
            photo,
            [url, 4032, 3024, None, None, None, None, None, [None, None, 1], [2481152], 2],
            1717171717000,
            "c2lnbmF0dXJlLW9mLXRoZS1waG90bw",
            3600000,
            1720000000000,
            ["AF1QipOwnerOfTheAlbum"],
            [[2], [31, 0, 1], [36, 0, 1]],
            2,
            {"15": 15110, "525000002": [["AF1QipTheAlbum"]]},
        ]
        for photo, url in photos
    ]
    album = ["AF1QipTheAlbum", title, [1717171717000, 1720000000000], None, items[0][1] if items else None]
    data = [None, items, "Cg5uZXh0LXBhZ2U" if more else "", album, None, 0]
    calls = [
        "AF_initDataCallback({key: 'ds:0', hash: '1', data:[], sideChannel: {}});",
        f"AF_initDataCallback({{key: 'ds:1', hash: '2', data:{json.dumps(data, separators=(',', ':'))}, "
        "sideChannel: {}});",
    ]
    scripts = "".join(f'<script class="ds:{i}" nonce="n0nce">{call}</script>' for i, call in enumerate(calls))
    head = f"<title>{title} - Google Photos</title>{scripts}"
    return f"<!doctype html><html lang=en><head>{head}</head><body></body></html>"


class FakeGooglePhotos:
    """A shared Google Photos album's page and its photos, on a free port on this machine."""

    def __init__(self):
        self.title = "Summer"
        self.photos: dict[str, tuple[int, int, int]] = {}  # each photo's id, and the one colour it's all in
        self.more = False  # whether the page says the album has more photos than it lists
        self.not_photos: set[str] = set()  # photos that come back as something that isn't a picture
        self.answer: tuple[int, bytes] | None = None  # answer every request with this instead
        self.requests: list[str] = []  # each request's path
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                fake.requests.append(self.path)
                if fake.answer:
                    return self._send(*fake.answer)
                if self.path == "/share/AF1QipTheAlbum?key=k3y":
                    return self._send(200, fake.page().encode(), "text/html; charset=utf-8")
                photo = self.path.removeprefix("/photo/").partition("=")[0]
                if self.path.startswith("/photo/") and photo in fake.photos:
                    if photo in fake.not_photos:
                        return self._send(200, b"<html>Something went wrong</html>", "text/html")
                    image = io.BytesIO()
                    Image.new("RGB", (320, 240), fake.photos[photo]).save(image, "JPEG", quality=95)
                    return self._send(200, image.getvalue(), "image/jpeg")
                self._send(404, b"Not found.")

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
        threading.Thread(target=self._server.serve_forever, args=(0.05,), daemon=True).start()  # quick to shut down

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    @property
    def album_url(self) -> str:
        """The album's share link."""
        return f"{self.url}/share/AF1QipTheAlbum?key=k3y"

    def page(self) -> str:
        return album_page(self.title, [(photo, f"{self.url}/photo/{photo}") for photo in self.photos], self.more)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def google_photos():
    fake = FakeGooglePhotos()
    yield fake
    fake.close()
