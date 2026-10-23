"""The board's plugin for Hermes (hermes_plugin/): Hermes' hooks become the board's status, served the way
the board asks for it. The plugin is imported as Hermes imports it, and read by the board's own client."""

import importlib.util
import json
import logging
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from test_client import free_port

from pi_display_microservice.client import HermesClient
from pi_display_microservice.status import Snapshot, State, parse

REPO = Path(__file__).resolve().parents[1]
NOW = 1_800_000_000.0
TURN = {"session_id": "s1", "user_message": "Is it going to rain?", "platform": "telegram"}
APPROVAL = {
    "command": "rm -rf build",
    "description": "recursive delete",
    "pattern_key": "rm_rf",
    "session_key": "agent:main:telegram:dm:42",
    "surface": "gateway",
}


def load(name="hermes_plugins.pi_display_status"):
    """Import the plugin as Hermes does: from its folder, under a module name Hermes picks."""
    spec = importlib.util.spec_from_file_location(
        name, REPO / "hermes_plugin" / "__init__.py", submodule_search_locations=[str(REPO / "hermes_plugin")]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


plugin = load()


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def tracker(clock):
    return plugin.Tracker(clock, approval_timeout=lambda: 300)


def snapshot(tracker):
    """What the board makes of the plugin's answer, on the same clock."""
    answer = tracker.answer()
    return parse(answer, answer["now"]).snapshot


def test_a_turn_from_start_to_finish(tracker, clock):
    assert snapshot(tracker) == Snapshot(State.IDLE, updated=NOW)

    tracker.pre_llm_call(**TURN, turn_id="t1", is_first_turn=True)  # and whatever else Hermes passes
    assert snapshot(tracker) == Snapshot(
        State.WORKING, task="Is it going to rain?", step="Thinking", channel="Telegram", started=NOW, updated=NOW
    )
    clock.now += 2
    tracker.pre_tool_call(tool_name="web_search", session_id="s1", tool_call_id="c1")
    s = snapshot(tracker)
    assert (s.step, s.tools) == ("Using web_search", ("web_search",))
    tracker.post_tool_call(tool_name="web_search", session_id="s1", tool_call_id="c1", status="ok")
    tracker.pre_tool_call(tool_name="web_search", session_id="s1", tool_call_id="c2")
    tracker.pre_tool_call(tool_name="terminal", session_id="s1", tool_call_id="c3")  # at the same time
    s = snapshot(tracker)
    assert (s.step, s.tools) == ("Using terminal", ("web_search", "terminal"))  # each tool once, in order
    tracker.post_tool_call(tool_name="terminal", session_id="s1", tool_call_id="c3")
    assert snapshot(tracker).step == "Using web_search"
    tracker.post_tool_call(tool_name="web_search", session_id="s1", tool_call_id="c2")
    assert snapshot(tracker).step == "Thinking"

    clock.now += 3
    tracker.on_session_end(session_id="s1", completed=True, failed=False, interrupted=False)
    assert snapshot(tracker) == Snapshot(
        State.IDLE, last_task="Is it going to rain?", last_finished=NOW + 5, updated=NOW + 5
    )


@pytest.mark.parametrize(
    ("ending", "error"),
    [
        ({"completed": True}, ""),
        ({"completed": False, "failed": True}, "Failed"),
        ({"completed": False, "interrupted": True}, "Stopped"),
        ({"completed": False}, "Didn't finish"),
    ],
)
def test_says_how_the_last_turn_ended(tracker, ending, error):
    tracker.pre_llm_call(**TURN)
    tracker.on_session_end(session_id="s1", **ending)
    assert snapshot(tracker).last_error == error


def test_an_approval_takes_over_until_it_is_answered(tracker, clock):
    tracker.pre_llm_call(**TURN)
    tracker.pre_tool_call(tool_name="terminal", session_id="s1", tool_call_id="c1")
    clock.now += 10
    tracker.pre_approval_request(**APPROVAL)
    s = snapshot(tracker)
    assert (s.state, s.tool, s.channel, s.deadline) == (State.APPROVAL, "recursive delete", "Telegram", NOW + 310)
    assert (s.task, s.tools, s.started) == ("Is it going to rain?", ("terminal",), NOW)  # still the turn's

    tracker.post_approval_response(**APPROVAL, choice="once")
    assert snapshot(tracker).state is State.WORKING


def test_one_approval_shared_by_several_tool_calls_lasts_until_all_are_answered(tracker):
    tracker.pre_llm_call(**TURN)
    tracker.pre_approval_request(**APPROVAL)
    tracker.pre_approval_request(**APPROVAL, coalesced=True)
    tracker.post_approval_response(**APPROVAL, choice="once")
    assert snapshot(tracker).state is State.APPROVAL
    tracker.post_approval_response(**APPROVAL, choice="once")
    assert snapshot(tracker).state is State.WORKING


def test_approvals_hermes_decides_itself_dont_ask_for_you(tracker):
    tracker.pre_llm_call(**TURN)
    tracker.pre_approval_request(**{**APPROVAL, "surface": "smart"})
    assert snapshot(tracker).state is State.WORKING


def test_without_a_timeout_the_board_counts_how_long_its_been_waiting(clock):
    tracker = plugin.Tracker(clock, approval_timeout=lambda: 0)
    tracker.pre_approval_request(**APPROVAL)  # with no turn known, nor where it came from
    s = snapshot(tracker)
    assert (s.state, s.deadline, s.channel, s.started) == (State.APPROVAL, 0.0, "", NOW)


def test_the_end_of_the_last_turn_clears_any_approval_left_behind(tracker):
    tracker.pre_llm_call(**TURN)
    tracker.pre_approval_request(**APPROVAL)
    tracker.on_session_end(session_id="s1", completed=False, interrupted=True)
    assert snapshot(tracker).state is State.IDLE


def test_shows_the_latest_of_several_conversations(tracker, clock):
    tracker.pre_llm_call(**TURN)
    clock.now += 1
    tracker.pre_llm_call(session_id="s2", user_message="Book a table", platform="discord")
    assert (snapshot(tracker).task, snapshot(tracker).channel) == ("Book a table", "Discord")
    tracker.on_session_end(session_id="s2", completed=True)
    assert snapshot(tracker).task == "Is it going to rain?"  # the other is still going


def test_subagents_and_unknown_sessions_change_nothing(tracker):
    before = tracker.answer()["version"]
    tracker.pre_llm_call(session_id="child", user_message="Research", parent_session_id="s1")
    tracker.pre_tool_call(tool_name="terminal", session_id="child", tool_call_id="c1")
    tracker.post_tool_call(tool_name="terminal", session_id="nobody", tool_call_id="c1")
    tracker.on_session_end(session_id="nobody", completed=True)
    tracker.post_approval_response(**APPROVAL, choice="once")  # for a request it never saw
    assert tracker.answer()["version"] == before
    assert snapshot(tracker).state is State.IDLE


@pytest.mark.parametrize(
    ("message", "task"),
    [
        ("  Is it\n going   to rain? ", "Is it going to rain?"),
        (
            [
                {"type": "text", "text": "What's in"},
                {"type": "image_url", "image_url": "..."},
                {"type": "text", "text": "this photo?"},
            ],
            "What's in this photo?",
        ),
        (None, ""),
    ],
)
def test_the_task_is_the_words_of_your_message(tracker, message, task):
    tracker.pre_llm_call(session_id="s1", user_message=message, platform="cli")
    s = snapshot(tracker)
    assert (s.task, s.channel) == (task, "the terminal")


def test_approval_timeout_is_hermes_own_setting_when_it_can_be_read():
    assert plugin._approval_timeout() == 0.0  # outside Hermes, it can't


@pytest.mark.parametrize(
    ("argv", "gateway"),
    [
        (["hermes", "gateway", "run"], True),
        (["hermes", "gateway"], True),
        (["hermes", "-p", "work", "gateway", "run", "--replace"], True),
        (["/opt/hermes/hermes_cli/main.py", "gateway", "run"], True),
        (["hermes", "gateway", "status"], False),
        (["hermes", "plugins", "list"], False),
        (["hermes"], False),
    ],
)
def test_only_the_gateway_serves_the_status(argv, gateway):
    assert plugin.in_gateway(argv) is gateway


# -- serving it


@pytest.fixture
def server(tracker):
    served = plugin.StatusServer(tracker, "127.0.0.1", 0)
    served.start()
    yield served
    served.close()


def url(server):
    return f"http://127.0.0.1:{server.port}"


def test_the_board_reads_what_hermes_is_doing(tracker, server):
    tracker.pre_llm_call(**TURN)
    client = HermesClient(url(server))
    assert client.refresh()
    status = client.latest()
    assert (status.name, status.snapshot.state, status.snapshot.task) == ("Hermes", State.WORKING, TURN["user_message"])


def test_the_board_hears_a_change_at_once(tracker, server):
    client = HermesClient(url(server))
    client.refresh()
    threading.Timer(0.3, lambda: tracker.pre_llm_call(**TURN)).start()
    started = time.monotonic()
    assert client.refresh(wait=10)
    assert 0.25 < time.monotonic() - started < 3  # straight away, not after the 10 seconds
    assert client.latest().snapshot.state is State.WORKING


def get(address, **headers):
    try:
        with urllib.request.urlopen(urllib.request.Request(address, headers=headers), timeout=5) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def test_answers_errors_as_the_board_expects(tracker):
    served = plugin.StatusServer(tracker, "127.0.0.1", 0, token="s3cret")
    served.start()
    try:
        base = f"http://127.0.0.1:{served.port}"
        assert get(f"{base}/v1/status")[0] == 401
        assert get(f"{base}/v1/status", Authorization="Bearer wrong")[0] == 401
        assert get(f"{base}/v1/status", Authorization="Bearer s3cret")[0] == 200
        assert get(f"{base}/elsewhere", Authorization="Bearer s3cret")[0] == 404
        assert get(f"{base}/v1/status?wait=soon", Authorization="Bearer s3cret")[0] == 400
        post = urllib.request.Request(f"{base}/v1/status", data=b"", method="POST")
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(post, timeout=5)
        assert refused.value.code == 405
        client = HermesClient(base, "s3cret")
        assert client.refresh() and client.latest().snapshot.state is State.IDLE
    finally:
        served.close()


# -- inside Hermes


class Context:
    """The part of Hermes' plugin context that register() uses."""

    def __init__(self):
        self.hooks = {}

    def register_hook(self, name, callback):
        self.hooks[name] = callback


@pytest.fixture
def in_hermes(monkeypatch):
    """A fresh process's worth of plugin state, a free port, and cleaning up after."""
    monkeypatch.delitem(sys.modules, plugin._SHARED, raising=False)
    port = free_port()
    monkeypatch.setenv("PI_DISPLAY_PORT", str(port))
    monkeypatch.delenv("PI_DISPLAY_TOKEN", raising=False)
    yield port
    state = sys.modules.get(plugin._SHARED)
    if state and state.server:
        state.server.close()


def test_in_the_gateway_it_registers_its_hooks_and_serves_the_status(monkeypatch, in_hermes):
    monkeypatch.setattr(sys, "argv", ["hermes", "gateway", "run"])
    ctx = Context()
    plugin.register(ctx)
    assert set(ctx.hooks) == set(plugin.HOOKS)
    assert set(plugin.HOOKS) <= set((REPO / "hermes_plugin" / "plugin.yaml").read_text().split())  # as declared

    ctx.hooks["pre_llm_call"](**TURN)
    client = HermesClient(f"http://127.0.0.1:{in_hermes}")
    assert client.refresh() and client.latest().snapshot.state is State.WORKING


def test_other_hermes_commands_dont_take_the_port(monkeypatch, in_hermes):
    monkeypatch.setattr(sys, "argv", ["hermes", "plugins", "list"])
    plugin.register(Context())
    assert not HermesClient(f"http://127.0.0.1:{in_hermes}").refresh()


def test_a_reloaded_plugin_carries_on_with_the_same_status(monkeypatch, in_hermes):
    monkeypatch.setattr(sys, "argv", ["hermes", "gateway", "run"])
    plugin.register(Context())
    reloaded = load("hermes_plugins.pi_display_status_reloaded")  # Hermes imports it afresh on a reload
    ctx = Context()
    reloaded.register(ctx)  # no second server, which couldn't have the port anyway

    ctx.hooks["pre_llm_call"](**TURN)  # the new copy's hooks reach what's served
    client = HermesClient(f"http://127.0.0.1:{in_hermes}")
    assert client.refresh() and client.latest().snapshot.state is State.WORKING


def test_a_port_in_use_is_logged_not_raised(monkeypatch, in_hermes, tracker, caplog):
    taken = plugin.StatusServer(tracker, "127.0.0.1", in_hermes)  # something else has the port
    try:
        with caplog.at_level(logging.ERROR, "pi_display_status"):
            plugin.start_server()
        assert f"can't listen on 127.0.0.1 port {in_hermes}" in caplog.text
        assert sys.modules[plugin._SHARED].server is None
    finally:
        taken.close()
