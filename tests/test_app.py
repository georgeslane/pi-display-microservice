"""The main loop, with a stand-in board and screen that record what they're asked to do."""

import itertools
from types import SimpleNamespace

import pytest
from PIL import Image

from pi_display_microservice.app import PHOTOS, STATUS, Boards, demo, run_board
from pi_display_microservice.board import LED_OFF, Board
from pi_display_microservice.config import Config
from pi_display_microservice.status import Snapshot, State, Status

BLUE = (False, False, True)
COLOURS = {
    State.IDLE: (0, 128, 0),
    State.WORKING: (0, 0, 255),
    State.APPROVAL: (255, 165, 0),
    State.OFFLINE: (99, 99, 99),
}
PHOTO = (200, 100, 50)  # what the stand-in slideshow draws


class SpyBoard:
    """Draws each state as a plain colour and remembers what it was asked to draw."""

    def __init__(self):
        self.seen: list[Snapshot] = []

    def render(self, s, now):
        self.seen.append(s)
        return Image.new("RGB", (4, 4), COLOURS[s.state])

    def led(self, s, now):
        return BLUE if s.state is State.WORKING else LED_OFF


class SpySlideshow:
    """Draws a photo as a plain colour and remembers each like and dislike."""

    def __init__(self):
        self.rated: list[str] = []

    def render(self, now):
        return Image.new("RGB", (4, 4), PHOTO)

    def like(self, now):
        self.rated.append("like")

    def dislike(self, now):
        self.rated.append("dislike")


class Stop(Exception):
    pass


class FakeScreen:
    def __init__(self, presses=()):
        self.presses = list(presses)  # what each wait() returns; it raises Stop when they run out
        self.log = []

    def show(self, image):
        self.log.append(("show", image.getpixel((0, 0))))

    def set_led(self, *rgb):
        self.log.append(("led", rgb))

    def set_backlight(self, on):
        self.log.append(("backlight", on))

    def wait(self, seconds):
        self.log.append(("wait", seconds))
        if not self.presses:
            raise Stop
        return self.presses.pop(0)

    def close(self):
        self.log.append(("close",))

    def calls(self, kind):
        return [args[0] for name, *args in self.log if name == kind]


def run(states, presses=(), board=None, slideshow=None, page=None):
    board = board or SpyBoard()
    screen = FakeScreen(presses)
    reads = iter(Status(Snapshot(state)) if isinstance(state, State) else state for state in states)
    with pytest.raises(Stop):
        run_board(
            lambda status: board,
            lambda: next(reads),
            screen,
            slideshow=slideshow,
            page=page,
            clock=itertools.count(1000).__next__,
        )
    return screen


def test_frames_are_only_sent_when_they_change_and_the_led_follows():
    screen = run([State.IDLE, State.IDLE, State.WORKING, State.WORKING, State.IDLE], presses=[None] * 4)
    assert screen.calls("show") == [COLOURS[State.IDLE], COLOURS[State.WORKING], COLOURS[State.IDLE]]
    assert screen.calls("led") == [LED_OFF, BLUE, LED_OFF]
    assert screen.calls("wait") == [1.0, 1.0, 1 / 6, 1 / 6, 1.0]  # faster while something moves


def test_a_button_turns_the_screen_off_and_activity_wakes_it():
    screen = run([State.IDLE, State.IDLE, State.WORKING, State.APPROVAL, State.IDLE], presses=["A", None, None, None])
    assert screen.calls("backlight") == [True, False, True, False]
    assert screen.calls("show") == [COLOURS[State.IDLE], COLOURS[State.WORKING], COLOURS[State.APPROVAL]]


def test_the_photos_come_first_and_b_moves_from_page_to_page():
    screen = run([State.IDLE] * 4, presses=["B", "B", None], slideshow=SpySlideshow())
    assert screen.calls("show") == [PHOTO, COLOURS[State.IDLE], PHOTO]


def test_the_board_can_start_on_either_page():
    screen = run([State.IDLE], slideshow=SpySlideshow(), page=STATUS)
    assert screen.calls("show") == [COLOURS[State.IDLE]]
    screen = run([State.IDLE], slideshow=SpySlideshow(), page=PHOTOS)
    assert screen.calls("show") == [PHOTO]


def test_a_turns_the_screen_off_and_any_button_turns_it_back_on():
    for wake in ["A", "B", "X", "Y"]:
        photos = SpySlideshow()
        screen = run([State.IDLE] * 3, presses=["A", wake], slideshow=photos)
        assert screen.calls("backlight") == [True, False, True]
        assert screen.calls("show") == [PHOTO, PHOTO]  # the same page as before: waking it does nothing else
        assert photos.rated == []


def test_x_likes_and_y_dislikes_the_photo_on_screen():
    photos = SpySlideshow()
    run([State.IDLE] * 7, presses=["X", "Y", "X", "B", "X", "Y"], slideshow=photos)
    assert photos.rated == ["like", "dislike", "like"]  # and nothing once it's on Athena's status


def test_an_approval_takes_over_from_the_photos_until_it_is_answered():
    states = [State.IDLE, State.WORKING, State.APPROVAL, State.APPROVAL, State.WORKING, State.IDLE]
    photos = SpySlideshow()
    screen = run(states, presses=[None, None, "X", None, None], slideshow=photos)
    assert screen.calls("show") == [PHOTO, COLOURS[State.APPROVAL], PHOTO]
    assert screen.calls("led") == [LED_OFF, BLUE, LED_OFF, BLUE, LED_OFF]  # it follows Athena, whatever's up
    assert photos.rated == []  # X meant nothing while the approval was up


def test_when_athena_turns_the_screen_on_it_shows_athenas_status():
    states = [State.IDLE, State.IDLE, State.WORKING, State.WORKING, State.IDLE]
    screen = run(states, presses=["A", None, None, None], slideshow=SpySlideshow())
    assert screen.calls("backlight") == [True, False, True, False]
    assert screen.calls("show") == [PHOTO, COLOURS[State.WORKING]]


def test_b_on_a_screen_athena_turned_on_moves_on_to_the_photos_and_keeps_the_screen_on():
    states = [State.IDLE, State.IDLE, State.WORKING, State.WORKING, State.IDLE]
    screen = run(states, presses=["A", None, "B", None], slideshow=SpySlideshow())
    assert screen.calls("backlight") == [True, False, True]
    assert screen.calls("show") == [PHOTO, COLOURS[State.WORKING], PHOTO]


def test_without_photos_there_is_only_athenas_status():
    screen = run([State.IDLE] * 4, presses=["B", "X", "Y"])
    assert screen.calls("show") == [COLOURS[State.IDLE]]
    assert screen.calls("backlight") == [True]


def test_offline_since_is_when_the_board_first_noticed():
    board = SpyBoard()
    run([State.OFFLINE, State.OFFLINE, State.IDLE, State.OFFLINE], presses=[None] * 3, board=board)
    assert [s.started for s in board.seen if s.state is State.OFFLINE] == [1000, 1000, 1003]


def test_the_led_can_be_turned_off():
    screen = FakeScreen()
    run_board(lambda s: SpyBoard(), lambda: Status(Snapshot(State.WORKING)), screen, led=False, once=True)
    assert screen.calls("led") == [LED_OFF]


def test_buttons_still_work_after_the_clock_is_set_back():
    screen = FakeScreen(presses=["A", "A"])
    times = iter([1000.0, 500.0, 501.0])  # the network put the clock back between the presses
    with pytest.raises(Stop):
        run_board(lambda s: SpyBoard(), lambda: Status(Snapshot(State.IDLE)), screen, clock=times.__next__)
    assert screen.calls("backlight") == [True, False, True]


def test_boards_follow_athenas_name_unless_the_config_sets_one():
    boards = Boards(Config())
    athena = boards(Status(name="Athena", timezone="Europe/London"))
    assert athena.title == "ATHENA" and boards(Status(name="Athena", timezone="Europe/London")) is athena
    assert boards(Status(name="Pallas", timezone="Europe/London")).title == "PALLAS"  # Athena was renamed

    fixed = Boards(Config(name="Owl", timezone="UTC"))
    assert fixed(Status(name="Athena", timezone="Europe/London")).title == "OWL"
    assert isinstance(fixed(Status()), Board)


def test_demo_cycles_through_examples():
    clock = SimpleNamespace(now=0.0)
    read = demo(lambda: clock.now)
    states = []
    for t in range(0, 35, 5):
        clock.now = t
        states.append(read().snapshot.state)
    assert states == [State.IDLE, State.WORKING, State.WORKING, State.APPROVAL, State.IDLE, State.OFFLINE, State.IDLE]
