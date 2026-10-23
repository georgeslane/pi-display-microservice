"""The client, against a stand-in for Hermes' status API (conftest.FakeHermes)."""

import socket
import threading
import time

import pytest
from conftest import FakeHermes

from pi_display_microservice.client import HermesClient
from pi_display_microservice.status import State


def wait_until(condition, seconds=5.0):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_reads_what_hermes_is_doing(hermes):
    hermes.set(state="working", task="What's the weather?", step="Using fetch", tools=["fetch"], channel="Telegram")
    client = HermesClient(hermes.url)
    assert client.latest().snapshot.state is State.OFFLINE  # nothing heard yet

    assert client.refresh()
    status = client.latest()
    assert (status.name, status.timezone, status.version) == ("Hermes", "Europe/London", hermes.version)
    s = status.snapshot
    assert (s.state, s.task, s.step, s.tools, s.channel) == (
        State.WORKING,
        "What's the weather?",
        "Using fetch",
        ("fetch",),
        "Telegram",
    )
    assert hermes.requests == [{"authorization": ""}]  # the first request doesn't wait


def test_waits_for_hermes_to_change_and_hears_at_once(hermes):
    client = HermesClient(hermes.url)
    client.refresh()
    first = hermes.version
    threading.Timer(0.3, lambda: hermes.set(state="approval", tool="send_email", channel="Telegram")).start()

    started = time.monotonic()
    assert client.refresh(wait=10)
    took = time.monotonic() - started

    assert client.latest().snapshot.state is State.APPROVAL
    assert 0.25 < took < 3  # it heard straight away, not after the 10 seconds
    assert hermes.requests[-1] == {"wait": "10", "after": first, "authorization": ""}


def test_when_nothing_changes_hermes_answers_after_the_wait(hermes):
    client = HermesClient(hermes.url)
    client.refresh()
    started = time.monotonic()
    assert client.refresh(wait=0.4)
    assert 0.35 < time.monotonic() - started < 3
    assert client.latest().version == hermes.version


def test_times_are_moved_onto_this_machines_clock(hermes):
    hermes.clock_offset = 100.0  # Hermes' clock is 100s ahead
    hermes.set(state="working", started=time.time() + 100 - 5)
    client = HermesClient(hermes.url)
    client.refresh()
    assert time.time() - client.latest().snapshot.started == pytest.approx(5, abs=1)


def test_offline_when_nothing_answers_then_back(hermes):
    client = HermesClient(f"http://127.0.0.1:{free_port()}", clock=lambda: 1000.0)
    assert not client.refresh()
    offline = client.latest().snapshot
    assert (offline.state, offline.started, offline.problem) == (State.OFFLINE, 1000.0, "")

    client.clock = lambda: 1060.0
    client.refresh()
    assert client.latest().snapshot.started == 1000.0  # offline since it first noticed

    client.url = hermes.url + "/v1/status"
    assert client.refresh()
    assert client.latest().snapshot.state is State.IDLE


@pytest.mark.parametrize(
    ("setup", "problem"),
    [
        (lambda a: setattr(a, "token", "the-right-one"), "Hermes refused the token"),
        (lambda a: setattr(a, "answer", (404, b"Not found.")), "answered 404. Check hermes_url"),
        (lambda a: setattr(a, "answer", (200, b"<html>a router's login page</html>")), "wasn't JSON"),
        (lambda a: setattr(a, "api", 2), "version 2, newer than this board knows. Update pi-display-microservice"),
        (lambda a: a.set(state="dancing"), "a state this board doesn't know: 'dancing'"),
    ],
)
def test_problems_are_explained_on_the_board(hermes, setup, problem):
    setup(hermes)
    client = HermesClient(hermes.url)
    assert not client.refresh()
    s = client.latest().snapshot
    assert s.state is State.OFFLINE and problem in s.problem


def test_sends_the_token_when_there_is_one():
    hermes = FakeHermes(token="s3cret-token")
    try:
        client = HermesClient(hermes.url, "s3cret-token")
        assert client.refresh()
        assert hermes.requests[-1]["authorization"] == "Bearer s3cret-token"
    finally:
        hermes.close()


def test_keeps_up_on_its_own_thread():
    hermes = FakeHermes()
    client = HermesClient(hermes.url, wait=10, retry=0.05)
    client.start()
    try:
        wait_until(lambda: client.latest().snapshot.state is State.IDLE)
        hermes.set(state="working", step="Thinking")
        wait_until(lambda: client.latest().snapshot.state is State.WORKING, seconds=2)  # not the 10s wait
        hermes.close()
        wait_until(lambda: client.latest().snapshot.state is State.OFFLINE)
        assert client.latest().name == "Hermes"  # still the name Hermes last gave
    finally:
        client.stop()
        hermes.close()
