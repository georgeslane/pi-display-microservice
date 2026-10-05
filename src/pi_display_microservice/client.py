"""Keeps up with Athena: asks its status API what it's doing, on a thread of its own.

Each request says which version of the status the board already has, and Athena holds
the answer until that changes or ``wait`` seconds pass. So a change reaches the screen
straight away, while the board asks only about twice a minute when nothing happens.
If Athena doesn't answer, the board shows it as offline and tries again every
``retry`` seconds.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

from pi_display_microservice.status import Snapshot, State, Status, parse

log = logging.getLogger(__name__)

TIMEOUT_MARGIN = 10.0  # how much longer than the wait to give an answer before giving up on it


class AthenaClient:
    def __init__(
        self,
        url: str,
        token: str = "",
        *,
        wait: float = 25.0,
        retry: float = 5.0,
        clock: Callable[[], float] = time.time,
    ):
        self.url = url.rstrip("/") + "/v1/status"
        self.headers = {"Accept": "application/json", "User-Agent": "pi-display-microservice"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        self.wait, self.retry, self.clock = wait, retry, clock
        self._latest = Status()  # offline, until Athena answers
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def latest(self) -> Status:
        """The newest status. Reading it never waits for the network."""
        return self._latest

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="athena-poll", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def ask(self, wait: float = 0.0) -> Status:
        """One request. With ``wait``, Athena answers once its status differs from the latest we have, or after
        ``wait`` seconds. Raises OSError if Athena can't be reached, and ValueError if its answer isn't usable."""
        query: dict[str, Any] = {}
        if wait and self._latest.version:
            query = {"wait": f"{wait:g}", "after": self._latest.version}
        request = urllib.request.Request(f"{self.url}?{urlencode(query)}" if query else self.url, headers=self.headers)
        try:
            with urllib.request.urlopen(request, timeout=wait + TIMEOUT_MARGIN) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise ValueError("Athena refused the token. Check token in config.toml.") from None
            raise ValueError(f"Athena's status API answered {exc.code}. Check athena_url in config.toml.") from None
        except ValueError:  # not JSON, or not UTF-8
            raise ValueError("Athena's answer wasn't JSON. Check athena_url in config.toml.") from None
        return parse(payload, self.clock())

    def refresh(self, wait: float = 0.0) -> bool:
        """Ask Athena once, and update latest() with the answer, or with "offline". True if it answered."""
        try:
            self._latest = self.ask(wait)
            return True
        except Exception as exc:  # anything at all: the board must keep going, and say what's wrong
            self._went_offline(exc)
            return False

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self.refresh(self.wait):
                self._stop.wait(self.retry)

    def _went_offline(self, exc: Exception) -> None:
        problem = str(exc) if isinstance(exc, ValueError) else ""
        before = self._latest.snapshot
        if before.state is not State.OFFLINE or before.problem != problem:
            if problem:
                log.warning("Can't use Athena's status: %s", problem)
            else:
                log.info("Athena isn't answering (%s); showing it as offline", _reason(exc))
        since = before.started if before.state is State.OFFLINE and before.started else self.clock()
        offline = Snapshot(State.OFFLINE, started=since, problem=problem)
        self._latest = Status(offline, self._latest.name, self._latest.timezone)


def _reason(exc: Exception) -> str:
    if isinstance(exc, urllib.error.URLError):
        return str(exc.reason)
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
