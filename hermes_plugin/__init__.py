"""A Hermes plugin that tells pi-display-microservice's status board what Hermes is doing.

Hermes calls the hooks below as it works: when a turn starts, a tool starts or finishes, an
approval is asked for or answered, and a turn ends. The plugin keeps track of what they say
and, inside Hermes' gateway (``hermes gateway run``), serves it the way the board expects:
``GET /v1/status`` on 127.0.0.1:8091 (see the board's README, "The status API"). The board
can answer an approval Hermes is waiting for in a chat, with ``POST /v1/approval``, as
/approve or /deny in the chat would.

It can't slow Hermes down or stop it. Each hook only updates a few fields under a lock, and a
problem with the server is logged, never raised.

Settings, which go in ~/.hermes/.env:
    PI_DISPLAY_HOST   where to listen (default 127.0.0.1: this machine only)
    PI_DISPLAY_PORT   default 8091
    PI_DISPLAY_TOKEN  if set, the board must send the same one (its ``token`` setting)

Install it with ``bash scripts/install-hermes-plugin.sh``, from the board's repo. Only the
standard library is used, because this runs in Hermes' own Python, not the board's.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import sys
import threading
import time
import types
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qsl, urlsplit

log = logging.getLogger("pi_display_status")

API_VERSION = 1  # the version of the board's status API this serves
NAME = "Hermes"
MAX_WAIT = 30.0  # the longest a request may wait for a change
STALE_SECONDS = 60 * 60  # a turn Hermes says nothing about for this long has ended without saying so
HOOKS = (
    "pre_llm_call",
    "pre_tool_call",
    "post_tool_call",
    "pre_approval_request",
    "post_approval_response",
    "on_session_end",
)
# Hermes' names for its platforms, as the board should say them: "Approve in Telegram".
CHANNELS = {"cli": "the terminal", "whatsapp": "WhatsApp", "imessage": "iMessage", "api_server": "the API"}


@dataclass
class _Turn:
    session_id: str
    task: str  # what you asked
    channel: str  # where you asked it
    started: float
    seen: float  # when Hermes last said anything about it
    tools: list[str] = field(default_factory=list)  # used so far, in the order first used
    running: dict[str, str] = field(default_factory=dict)  # tool call id -> tool, for the ones running now


@dataclass
class _Approval:
    id: str  # for the board to say which one it's answering
    session_key: str  # Hermes' name for the chat it's waiting in
    what: str  # what Hermes wants to do, e.g. "recursive delete"
    channel: str
    asked: float
    deadline: float  # when it's denied if you don't answer; 0 if never
    answerable: bool  # waiting in Hermes' queue of chat approvals, so the board can answer it
    request_id: str | None = None  # Hermes' id for it, when it has one
    waiting: int = 1  # Hermes asks once for several tool calls wanting the same thing


class HermesApprovals:
    """Hermes' own queue of approvals waiting in a chat: the one its /approve and /deny commands answer."""

    def pending(self, session_key: str) -> list[dict[str, Any]]:
        from tools.approval import list_gateway_approvals

        return list_gateway_approvals(session_key)

    def resolve(self, session_key: str, choice: str, request_id: str | None) -> int:
        from tools.approval import resolve_gateway_approval

        return resolve_gateway_approval(session_key, choice, request_id=request_id)


class Tracker:
    """What Hermes is doing, from its hooks, in the board's status API's terms. Safe to use from any thread."""

    def __init__(
        self,
        clock: Callable[[], float] = time.time,
        approval_timeout: Callable[[], float] | None = None,
        approvals: Any = None,
    ):
        self.clock = clock
        self.approval_timeout = approval_timeout or _approval_timeout
        self.approvals = approvals or HermesApprovals()
        self._changed = threading.Condition()
        self._boot = uuid.uuid4().hex[:8]  # so the version is new after Hermes restarts
        self._changes = 0
        # By turn: Hermes gives each its own id, which, unlike the session's, stays the same
        # all the way through (compressing a long conversation starts a new session mid-turn).
        self._turns: dict[str, _Turn] = {}
        self._approvals: dict[tuple[str, str], _Approval] = {}
        self._last_task, self._last_error, self._last_finished = "", "", 0.0
        self._updated = clock()
        self._closing = False

    @property
    def version(self) -> str:
        return f"{self._boot}-{self._changes}"

    # -- Hermes' hooks. Each takes the keywords Hermes passes, and ignores any others.

    def pre_llm_call(
        self,
        session_id: str = "",
        turn_id: str = "",
        user_message: Any = None,
        platform: str = "",
        parent_session_id: str = "",
        **_: Any,
    ) -> None:
        """A turn starts: you've asked Hermes something."""
        if parent_session_id or not (turn_id or session_id):
            return  # a subagent's turn, which is part of its parent's
        now = self.clock()
        with self._changed:
            # A session has one turn at a time, so any other turn of this one is over.
            for key in [k for k, t in self._turns.items() if session_id and t.session_id == session_id]:
                del self._turns[key]
            self._turns[turn_id or session_id] = _Turn(session_id, _text(user_message), _channel(platform), now, now)
            self._publish()

    def pre_tool_call(
        self, tool_name: str = "", session_id: str = "", turn_id: str = "", tool_call_id: str = "", **_: Any
    ) -> None:
        with self._changed:
            turn = self._turn(turn_id, session_id)
            if turn is None or not tool_name:
                return
            turn.seen = self.clock()
            turn.running[tool_call_id or tool_name] = tool_name
            if tool_name not in turn.tools:
                turn.tools.append(tool_name)
            self._publish()

    def post_tool_call(
        self, tool_name: str = "", session_id: str = "", turn_id: str = "", tool_call_id: str = "", **_: Any
    ) -> None:
        with self._changed:
            turn = self._turn(turn_id, session_id)
            if turn is None:
                return
            turn.seen = self.clock()
            if turn.running.pop(tool_call_id or tool_name, None) is not None:
                self._publish()

    def pre_approval_request(
        self,
        command: str = "",
        description: str = "",
        pattern_key: str = "",
        session_key: str = "",
        surface: str = "",
        **_: Any,
    ) -> None:
        """Hermes needs your approval to go on."""
        if surface == "smart":
            return  # Hermes' own model decides these, without asking you
        key = (session_key, pattern_key or command)
        timeout = self.approval_timeout()
        # Hermes queues a chat's approval before it calls this, so it's there to find.
        try:
            queued = [e for e in self.approvals.pending(session_key) if e.get("command", command) == command]
        except Exception:
            queued = []  # not waiting in a chat (a terminal asks there), or not Hermes: the board can't answer
        with self._changed:
            if key in self._approvals:
                self._approvals[key].waiting += 1
                return
            turn = self._latest_turn()
            now = self.clock()
            self._approvals[key] = _Approval(
                id=uuid.uuid4().hex,
                session_key=session_key,
                what=description or command or "a command",
                channel=turn.channel if turn else "",  # the board then says "Approve in the chat"
                asked=now,
                deadline=now + timeout if timeout > 0 else 0.0,
                answerable=bool(queued),
                request_id=queued[-1].get("request_id") if queued else None,
            )
            self._publish()

    def post_approval_response(self, command: str = "", pattern_key: str = "", session_key: str = "", **_: Any) -> None:
        """You've answered, or it timed out."""
        key = (session_key, pattern_key or command)
        with self._changed:
            approval = self._approvals.get(key)
            if approval is None:
                return
            approval.waiting -= 1
            if approval.waiting <= 0:
                del self._approvals[key]
                self._publish()

    def on_session_end(
        self,
        session_id: str = "",
        turn_id: str = "",
        completed: bool = True,
        failed: bool = False,
        interrupted: bool = False,
        **_: Any,
    ) -> None:
        """A turn has ended. (Hermes fires this at the end of every turn, however it ends.)"""
        with self._changed:
            turn = self._turn(turn_id, session_id)
            if turn is None:
                return
            self._forget(turn)
            if failed:
                error = "Failed"
            elif interrupted:
                error = "Stopped"
            else:
                error = "" if completed else "Didn't finish"
            self._last_task, self._last_error, self._last_finished = turn.task, error, self.clock()
            self._publish()

    # -- the board

    def answer(self, after: str = "", wait: float = 0.0) -> dict[str, Any]:
        """The status, as the board asks for it: if ``after`` is the current version, wait up to ``wait``
        seconds for a change first."""
        with self._changed:
            self._drop_stale()
            if wait > 0 and after == self.version:
                self._changed.wait_for(lambda: self._closing or after != self.version, min(wait, MAX_WAIT))
            return self._payload()

    def decide(self, approval_id: str, approve: bool) -> tuple[int, dict[str, Any]]:
        """Approve or deny the approval the board showed as ``approval_id``, as /approve or /deny in the
        chat would. Returns an HTTP status and a body saying what happened."""
        with self._changed:
            approval = next((a for a in self._approvals.values() if a.id == approval_id), None)
        if approval is None:
            return 404, {"error": "Hermes isn't waiting for that any more."}
        if not approval.answerable:
            return 409, {"error": f"Answer this one in {approval.channel or 'the chat'}."}
        choice = "once" if approve else "deny"
        try:
            # Outside the lock: answering wakes Hermes, whose hooks then take it.
            answered = self.approvals.resolve(approval.session_key, choice, approval.request_id)
        except Exception as exc:
            log.exception("pi-display-status: couldn't answer an approval")
            return 500, {"error": f"Hermes couldn't take the answer ({type(exc).__name__})."}
        if not answered:
            return 404, {"error": "Hermes isn't waiting for that any more."}
        log.info("pi-display-status: %s %s from the board", "approved" if approve else "denied", approval.what)
        return 200, {"answered": answered, "choice": choice}

    def close(self) -> None:
        """Answer every waiting request now."""
        with self._changed:
            self._closing = True
            self._changed.notify_all()

    def _payload(self) -> dict[str, Any]:
        status: dict[str, Any] = {
            "state": "idle",
            "task": "",
            "step": "",
            "tools": [],
            "tool": "",
            "channel": "",
            "started": 0.0,
            "deadline": 0.0,
            "approval_id": "",
            "last_task": self._last_task,
            "last_error": self._last_error,
            "last_finished": self._last_finished,
            "updated": self._updated,
        }
        turn = self._latest_turn()
        if turn:
            step = f"Using {list(turn.running.values())[-1]}" if turn.running else "Thinking"
            status.update(
                state="working",
                task=turn.task,
                step=step,
                tools=list(turn.tools),
                channel=turn.channel,
                started=turn.started,
            )
        if self._approvals:
            # The oldest, as it's the one an /approve or /deny in the chat would answer.
            approval = min(self._approvals.values(), key=lambda a: a.asked)
            status.update(
                state="approval",
                tool=approval.what,
                channel=approval.channel or status["channel"],
                deadline=approval.deadline,
                started=turn.started if turn else approval.asked,
                approval_id=approval.id if approval.answerable else "",
            )
        return {
            "api": API_VERSION,
            "version": self.version,
            "now": self.clock(),
            "assistant": {"name": NAME, "timezone": ""},  # "": the board's, since they share the Pi
            "status": status,
        }

    def _turn(self, turn_id: str, session_id: str) -> _Turn | None:
        """The turn a hook is about: by its id, or for a hook that doesn't give one, its session's."""
        if turn_id and turn_id in self._turns:
            return self._turns[turn_id]
        return next((t for t in self._turns.values() if session_id and t.session_id == session_id), None)

    def _forget(self, turn: _Turn) -> None:
        for key in [k for k, t in self._turns.items() if t is turn]:
            del self._turns[key]
        if not self._turns:
            self._approvals.clear()  # nothing's left running to approve for

    def _drop_stale(self) -> None:
        """Forget turns Hermes hasn't mentioned for a long time, in case one ended without the hook that
        says so. (Not while one is waiting for an approval: that wait is up to you.)"""
        if self._approvals:
            return
        now = self.clock()
        stale = [t for t in self._turns.values() if now - t.seen > STALE_SECONDS]
        for turn in stale:
            self._forget(turn)
        if stale:
            self._publish()

    def _latest_turn(self) -> _Turn | None:
        return max(self._turns.values(), key=lambda t: t.started, default=None)

    def _publish(self) -> None:
        """Something changed (the lock is held): a new version, and wake the requests waiting for one."""
        self._changes += 1
        self._updated = self.clock()
        self._changed.notify_all()


class StatusServer:
    """Serves ``tracker`` as the board's status API, on a thread of its own."""

    def __init__(self, tracker: Tracker, host: str = "127.0.0.1", port: int = 8091, token: str = ""):
        self.tracker = tracker

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                url = urlsplit(self.path)
                if url.path != "/v1/status":
                    return self._send(404, {"error": "Not found. The status is at /v1/status."})
                if not self._allowed():
                    return
                query = dict(parse_qsl(url.query))
                try:
                    wait = max(0.0, float(query.get("wait", 0)))
                except ValueError:
                    return self._send(400, {"error": "wait should be a number of seconds."})
                self._send(200, tracker.answer(query.get("after", ""), wait))

            def do_POST(self) -> None:
                if urlsplit(self.path).path != "/v1/approval":
                    return self._send(405 if urlsplit(self.path).path == "/v1/status" else 404, {"error": "Not here."})
                if not self._allowed():
                    return
                # JSON only: a web page can't send that to another site without asking first, which
                # this server never agrees to, so no page you open can answer for you.
                if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                    return self._send(415, {"error": "Send JSON."})
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(min(length, 4096)) or b"{}")
                    approval_id, choice = str(body["id"]), body["choice"]
                    if choice not in ("approve", "deny"):
                        raise ValueError(choice)
                except (ValueError, KeyError, TypeError):
                    return self._send(400, {"error": 'Send {"id": "<approval_id>", "choice": "approve" or "deny"}.'})
                self._send(*tracker.decide(approval_id, choice == "approve"))

            def _allowed(self) -> bool:
                if token and not hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {token}"):
                    self._send(401, {"error": "Wrong or missing token."})
                    return False
                return True

            def _refuse(self) -> None:
                self._send(405, {"error": "Only GET /v1/status and POST /v1/approval."})

            do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _refuse

            def _send(self, code: int, body: dict[str, Any]) -> None:
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: Any) -> None:
                log.debug("%s " + format, self.address_string(), *args)

        self._server = ThreadingHTTPServer((host, port), Handler)
        self._server.daemon_threads = True
        self._serving: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def start(self) -> None:
        self._serving = threading.Thread(target=self._server.serve_forever, name="pi-display-status", daemon=True)
        self._serving.start()

    def close(self) -> None:
        self.tracker.close()  # so requests waiting for a change answer now
        if self._serving:  # shutdown() waits for serving to stop, so it would wait forever if it never started
            self._server.shutdown()
        self._server.server_close()


_SHARED = "pi_display_status_shared"  # see shared()


def shared() -> types.ModuleType:
    """The tracker and server, one of each per process, whichever copy of this module asks.

    Hermes imports a plugin afresh each time it reloads its plugins, so this module's own globals
    would start again: a new tracker the board can't see, and a new server that can't have the port
    the first one still holds. So they're kept under a module name of their own, which Hermes
    doesn't clear, and a reloaded copy carries on with them.
    """
    state = sys.modules.get(_SHARED)
    if state is None:
        state = types.ModuleType(_SHARED)
        state.tracker, state.server = Tracker(), None
        sys.modules[_SHARED] = state
    return state


def register(ctx: Any) -> None:
    """Called by Hermes when it loads the plugin."""
    tracker = shared().tracker
    for hook in HOOKS:
        ctx.register_hook(hook, getattr(tracker, hook))
    # Every Hermes command loads plugins, but only the gateway keeps running, so it alone serves the
    # status (a quick `hermes plugins list` mustn't take the port).
    if in_gateway(sys.argv):
        start_server()


def start_server() -> None:
    state = shared()
    if state.server:
        return
    host, port = os.environ.get("PI_DISPLAY_HOST") or "127.0.0.1", os.environ.get("PI_DISPLAY_PORT") or "8091"
    try:
        state.server = StatusServer(state.tracker, host, int(port), os.environ.get("PI_DISPLAY_TOKEN", ""))
    except ValueError:
        log.error("pi-display-status: PI_DISPLAY_PORT should be a number, so the board can't see Hermes")
        return
    except OSError as exc:  # most likely, something else has the port
        log.error("pi-display-status: can't listen on %s port %s (%s), so the board can't see Hermes", host, port, exc)
        return
    state.server.start()
    log.info("pi-display-status: the board can see what Hermes is doing at http://%s:%s/v1/status", host, port)


def in_gateway(argv: list[str]) -> bool:
    """Whether this process is Hermes' gateway: ``hermes [options] gateway [run] ...``."""
    args = argv[1:]
    if "gateway" not in args:
        return False
    command = next((a for a in args[args.index("gateway") + 1 :] if not a.startswith("-")), None)
    return command in (None, "run")


def _approval_timeout() -> float:
    """How long Hermes waits for an approval (its approvals.timeout setting), or 0 if that can't be read."""
    try:
        from tools.approval_context import _get_approval_timeout  # Hermes' own

        return float(_get_approval_timeout())
    except Exception:
        return 0.0  # the board then shows how long it's been waiting instead of a countdown


def _text(message: Any) -> str:
    """The words of a user message, which Hermes may pass as text or as a list of parts (text, images...)."""
    if isinstance(message, str):
        return " ".join(message.split())
    if isinstance(message, list):
        parts = [p.get("text", "") for p in message if isinstance(p, dict) and p.get("type") == "text"]
        return " ".join(" ".join(str(p) for p in parts).split())
    return ""


def _channel(platform: str) -> str:
    platform = (platform or "").strip()
    return CHANNELS.get(platform.lower(), platform[:1].upper() + platform[1:])
