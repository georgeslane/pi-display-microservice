"""What the board shows: Athena's status, as its status API reports it.

The API's answer looks like this (see README, "Athena's status API"):

    {
      "api": 1,
      "version": "3f9a1c2e-17",
      "now": 1759651200.5,
      "assistant": {"name": "Athena", "timezone": "Europe/London"},
      "status": {"state": "working", "task": "...", "step": "Using fetch", ...}
    }

Fields this board doesn't know are ignored, and missing ones take their defaults, so
Athena can add new ones without breaking it. Only a new "api" number means a change
this board can't follow.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

API_VERSION = 1  # the newest version of Athena's status API this board understands


class State(StrEnum):
    IDLE = "idle"
    WORKING = "working"
    APPROVAL = "approval"  # waiting for you to allow or deny a tool
    OFFLINE = "offline"  # Athena isn't answering (decided here, never sent by Athena)


@dataclass(frozen=True)
class Snapshot:
    """What Athena is doing. Times are Unix timestamps, on this machine's clock."""

    state: State = State.OFFLINE
    task: str = ""  # the request being worked on ("" when idle, or when Athena keeps it private)
    step: str = ""  # what's happening right now, e.g. "Using fetch"
    tools: tuple[str, ...] = ()  # tools used for this task so far
    tool: str = ""  # approval: the tool waiting for an answer
    channel: str = ""  # where approvals happen, e.g. "Telegram"
    started: float = 0.0  # when the task started; offline: since when
    deadline: float = 0.0  # approval: when an unanswered request is denied (0: no limit)
    last_task: str = ""  # idle: the previous task,
    last_error: str = ""  # why it failed ("" if it didn't),
    last_finished: float = 0.0  # and when it ended
    updated: float = 0.0  # when it last changed
    problem: str = ""  # offline: why, if it's something other than Athena not running


@dataclass(frozen=True)
class Status:
    """One reading of Athena's status API, or of its silence."""

    snapshot: Snapshot = field(default_factory=Snapshot)
    name: str = "Athena"
    timezone: str = ""  # "" means this machine's
    version: str = ""  # Athena's version of its status; "" when offline

    @property
    def online(self) -> bool:
        return self.snapshot.state is not State.OFFLINE


_TIMES = ("started", "deadline", "last_finished", "updated")


def parse(payload: Any, now: float) -> Status:
    """A Status from the API's JSON. ``now`` is this machine's clock when it arrived.

    Raises ValueError if it isn't a status this board understands.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("status"), dict):
        raise ValueError("Athena's answer wasn't a status")
    api = payload.get("api")
    if not isinstance(api, int) or isinstance(api, bool):
        raise ValueError("Athena's answer didn't say which version of its status API it is")
    if api > API_VERSION:
        raise ValueError(
            f"Athena's status API is version {api}, newer than this board knows. Update pi-display-microservice."
        )
    raw = payload["status"]
    known = {f.name: f for f in dataclasses.fields(Snapshot)}
    values = {name: raw[name] for name in known if name in raw and name != "problem"}
    try:
        values["state"] = State(values.get("state", "idle"))
    except ValueError:
        raise ValueError(f"Athena reported a state this board doesn't know: {values['state']!r}") from None
    if values["state"] is State.OFFLINE:
        raise ValueError("Athena said it's offline, which it can't be")
    values["tools"] = tuple(str(t) for t in values.get("tools") or ())
    # Times are Athena's clock. Move them onto ours, in case the board runs on another machine.
    offset = now - float(payload.get("now") or now)
    for name in _TIMES:
        if values.get(name):
            values[name] = float(values[name]) + offset
    assistant = payload.get("assistant") if isinstance(payload.get("assistant"), dict) else {}
    return Status(
        snapshot=Snapshot(**values),
        name=str(assistant.get("name") or "Athena"),
        timezone=str(assistant.get("timezone") or ""),
        version=str(payload.get("version") or ""),
    )
