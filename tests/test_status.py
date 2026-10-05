"""Reading Athena's answers: what the board can use, and what it refuses."""

import pytest

from pi_display_microservice.status import API_VERSION, Snapshot, State, Status, parse

NOW = 1_759_651_200.0


def answer(**status):
    return {
        "api": API_VERSION,
        "version": "3f9a1c2e-17",
        "now": NOW,
        "assistant": {"name": "Pallas", "timezone": "Europe/Athens"},
        "status": {"state": "idle", **status},
    }


def test_reads_a_status():
    status = parse(answer(state="approval", tool="send_email", tools=["fetch"], started=NOW - 60), NOW)
    assert (status.name, status.timezone, status.version) == ("Pallas", "Europe/Athens", "3f9a1c2e-17")
    assert status.snapshot == Snapshot(State.APPROVAL, tool="send_email", tools=("fetch",), started=NOW - 60)
    assert status.online


def test_ignores_what_it_does_not_know_so_athena_can_add_things():
    payload = answer(state="working", mood="cheerful")
    payload["weather"] = {"London": "rain"}
    payload["assistant"]["avatar"] = "owl.png"
    assert parse(payload, NOW).snapshot == Snapshot(State.WORKING)


def test_missing_fields_take_their_defaults():
    status = parse({"api": 1, "status": {}}, NOW)
    assert status == Status(Snapshot(State.IDLE), "Athena", "", "")


def test_problem_is_only_ever_set_here():
    assert parse(answer(problem="ignored"), NOW).snapshot.problem == ""


@pytest.mark.parametrize(
    ("payload", "says"),
    [
        ([], "wasn't a status"),
        ({"api": 1}, "wasn't a status"),
        ({**answer(), "api": API_VERSION + 1}, "newer than this board knows"),
        ({**answer(), "api": "1"}, "didn't say which version"),
        (answer(state="offline"), "can't be"),
        (answer(state="asleep"), "doesn't know: 'asleep'"),
    ],
)
def test_refuses_what_it_cannot_follow(payload, says):
    with pytest.raises(ValueError, match=says):
        parse(payload, NOW)


def test_moves_athenas_times_onto_this_clock():
    status = parse(answer(state="approval", started=NOW - 60, deadline=NOW + 240, updated=NOW), NOW + 3.5)
    s = status.snapshot
    assert (s.started, s.deadline, s.updated) == (NOW - 56.5, NOW + 243.5, NOW + 3.5)
    assert s.last_finished == 0.0  # "never" stays never


def test_offline_by_default():
    assert not Status().online and Status().snapshot.state is State.OFFLINE
