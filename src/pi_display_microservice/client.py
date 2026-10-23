"""Keeps up with Hermes: asks its status API what it's doing, on a thread of its own.

Each request says which version of the status the board already has, and Hermes holds
the answer until that changes or ``wait`` seconds pass. So a change reaches the screen
straight away, while the board asks only about twice a minute when nothing happens.
If Hermes doesn't answer, the board shows it as offline and tries again every
``retry`` seconds.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from pi_display_microservice.status import Snapshot, State, Status, parse

log = logging.getLogger(__name__)

TIMEOUT_MARGIN = 10.0  # how much longer than the wait to give an answer before giving up on it
ANSWER_TIMEOUT = 10.0  # how long to give Hermes to take an approval's answer
ERROR_SECONDS = 8.0  # how long the board says an answer didn't go through


@dataclass(frozen=True)
class _Reply:
    """An answer to an approval, pressed on the board."""

    approval_id: str
    approve: bool
    sent: float
    error: str = ""  # why it didn't go through, once it's known that it didn't


class HermesClient:
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
        self._latest = Status()  # offline, until Hermes answers
        self._reply: _Reply | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def latest(self) -> Status:
        """The newest status, with any answer to its approval that's been pressed on the board. Reading
        it never waits for the network."""
        status, reply = self._latest, self._reply
        s = status.snapshot
        if reply is None or s.state is not State.APPROVAL or s.approval_id != reply.approval_id:
            return status
        if reply.error:
            if self.clock() - reply.sent > ERROR_SECONDS:
                return status
            return dataclasses.replace(status, snapshot=dataclasses.replace(s, answer_error=reply.error))
        answering = "approve" if reply.approve else "deny"
        return dataclasses.replace(status, snapshot=dataclasses.replace(s, answering=answering))

    def answer(self, approval_id: str, approve: bool) -> None:
        """Approve or deny the approval the board is showing, as /approve or /deny in the chat would. It's
        sent on a thread of its own, and latest() shows it's on its way, or why it didn't go through."""
        reply = _Reply(approval_id, approve, self.clock())
        self._reply = reply
        threading.Thread(target=self._send_answer, args=(reply,), name="hermes-answer", daemon=True).start()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="hermes-poll", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def ask(self, wait: float = 0.0) -> Status:
        """One request. With ``wait``, Hermes answers once its status differs from the latest we have, or after
        ``wait`` seconds. Raises OSError if Hermes can't be reached, and ValueError if its answer isn't usable."""
        query: dict[str, Any] = {}
        if wait and self._latest.version:
            query = {"wait": f"{wait:g}", "after": self._latest.version}
        request = urllib.request.Request(f"{self.url}?{urlencode(query)}" if query else self.url, headers=self.headers)
        try:
            with urllib.request.urlopen(request, timeout=wait + TIMEOUT_MARGIN) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise ValueError("Hermes refused the token. Check token in config.toml.") from None
            raise ValueError(f"Hermes' status API answered {exc.code}. Check hermes_url in config.toml.") from None
        except ValueError:  # not JSON, or not UTF-8
            raise ValueError("Hermes' answer wasn't JSON. Check hermes_url in config.toml.") from None
        return parse(payload, self.clock())

    def refresh(self, wait: float = 0.0) -> bool:
        """Ask Hermes once, and update latest() with the answer, or with "offline". True if it answered."""
        try:
            self._latest = self.ask(wait)
            return True
        except Exception as exc:  # anything at all: the board must keep going, and say what's wrong
            self._went_offline(exc)
            return False

    def _send_answer(self, reply: _Reply) -> None:
        body = json.dumps({"id": reply.approval_id, "choice": "approve" if reply.approve else "deny"}).encode()
        url = self.url.removesuffix("/v1/status") + "/v1/approval"
        headers = {**self.headers, "Content-Type": "application/json"}
        try:
            with urllib.request.urlopen(urllib.request.Request(url, body, headers), timeout=ANSWER_TIMEOUT):
                return  # Hermes carries on, and its status says so
        except urllib.error.HTTPError as exc:
            error = _said(exc)
        except Exception as exc:
            error = "Couldn't reach Hermes."
            log.debug("Answering the approval failed: %s", _reason(exc))
        log.warning("Couldn't %s from the board: %s", "approve" if reply.approve else "deny", error)
        if self._reply is reply:
            self._reply = dataclasses.replace(reply, error=error, sent=self.clock())

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self.refresh(self.wait):
                self._stop.wait(self.retry)

    def _went_offline(self, exc: Exception) -> None:
        problem = str(exc) if isinstance(exc, ValueError) else ""
        before = self._latest.snapshot
        if before.state is not State.OFFLINE or before.problem != problem:
            if problem:
                log.warning("Can't use Hermes' status: %s", problem)
            else:
                log.info("Hermes isn't answering (%s); showing it as offline", _reason(exc))
        since = before.started if before.state is State.OFFLINE and before.started else self.clock()
        offline = Snapshot(State.OFFLINE, started=since, problem=problem)
        self._latest = Status(offline, self._latest.name, self._latest.timezone)


def _said(exc: urllib.error.HTTPError) -> str:
    """What the plugin said when it couldn't take an answer."""
    if exc.code == 405:  # a plugin from before the board could answer
        return "Update the board's plugin in Hermes to answer from here."
    try:
        said = json.load(exc).get("error")
    except Exception:
        said = None
    return str(said) if said else f"Hermes answered {exc.code}."


def _reason(exc: Exception) -> str:
    if isinstance(exc, urllib.error.URLError):
        return str(exc.reason)
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
