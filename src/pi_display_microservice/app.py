"""The board's main loop: keep the screen showing Athena's latest status, or the photos.

Reading the status never waits on the network: the client (client.py) keeps the latest
one up to date on its own thread, as the album (album.py) does the photos. This loop
draws the page on screen several times a second while Athena is busy, and once a second
otherwise, and sends a frame only when it has changed.
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Callable

from pi_display_microservice.board import LED_OFF, Board
from pi_display_microservice.config import Config
from pi_display_microservice.screen import Screen
from pi_display_microservice.slideshow import Slideshow
from pi_display_microservice.status import Snapshot, State, Status

ACTIVE_FPS = 6  # while something on screen is moving
IDLE_FPS = 1
DEMO_SECONDS = 5  # how long the demo shows each example
PRESS_GAP = 0.3  # presses closer together than this are a button bouncing, not you
PHOTOS, STATUS = "photos", "status"  # the pages, in the order B goes through them


class Boards:
    """A Board for the assistant's name and timezone, made again only if Athena reports new ones."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._key: tuple[str, str] | None = None
        self._board: Board | None = None

    def __call__(self, status: Status) -> Board:
        key = (self.cfg.name or status.name, self.cfg.timezone or status.timezone)
        if key != self._key or self._board is None:
            self._key, self._board = key, Board(*key)
        return self._board


def run_board(
    board_for: Callable[[Status], Board],
    read: Callable[[], Status],
    screen: Screen,
    *,
    slideshow: Slideshow | None = None,
    page: str | None = None,
    led: bool = True,
    once: bool = False,
    clock: Callable[[], float] = time.time,
) -> None:
    """Keep ``screen`` showing the status from ``read()``, and the photos too if there's a ``slideshow``.

    Runs until interrupted, or for one frame with ``once``. It starts on ``page`` (PHOTOS or STATUS),
    or the first there is.

    The buttons: A turns the screen off and on again, and B moves to the next page. On the
    photos, X likes the photo and Y dislikes it. While the screen is off, any of them turns
    it back on.

    The screen also comes on by itself while Athena is working or waiting for you, showing
    its status, so you never miss an approval. For the same reason, an approval takes over
    from the photos until it's answered.
    """
    pages = [PHOTOS, STATUS] if slideshow else [STATUS]
    chosen = pages.index(page) if page else 0  # the page B last moved to
    shown: bytes | None = None
    lit: tuple[bool, bool, bool] | None = None
    backlight: bool | None = None
    wanted = True
    offline_since = 0.0
    last_press = 0.0
    while True:
        now = clock()
        status = read()
        s = status.snapshot
        if s.state is State.OFFLINE:
            offline_since = offline_since or now
            if not s.started:
                s = dataclasses.replace(s, started=offline_since)
        else:
            offline_since = 0.0
        active = s.state in (State.WORKING, State.APPROVAL)
        board = board_for(status)
        # Athena's status shows when it needs you, and when it's why the screen is on.
        page_on_screen = STATUS if s.state is State.APPROVAL or not wanted else pages[chosen]

        if (on := wanted or active) != backlight:
            screen.set_backlight(on)
            backlight, shown = on, None
        if on:
            frame = slideshow.render(now) if slideshow and page_on_screen == PHOTOS else board.render(s, now)
            if (data := frame.tobytes()) != shown:  # unchanged frames aren't sent again
                screen.show(frame)
                shown = data
        if (colour := board.led(s, now) if led else LED_OFF) != lit:
            screen.set_led(*colour)
            lit = colour

        if once:
            return
        button = screen.wait(1 / (ACTIVE_FPS if active else IDLE_FPS))
        if not button or 0 <= now - last_press <= PRESS_GAP:  # (a clock set back isn't a bounce)
            continue
        last_press = now
        if not on:
            wanted = True  # any button wakes the screen
        elif button == "A":
            wanted = False  # off, or as soon as Athena no longer needs it
        elif button == "B":
            chosen, wanted = (pages.index(page_on_screen) + 1) % len(pages), True
        elif slideshow and page_on_screen == PHOTOS and button in ("X", "Y"):
            (slideshow.like if button == "X" else slideshow.dislike)(now)


def demo(clock: Callable[[], float] = time.time) -> Callable[[], Status]:
    """Example states, a few seconds each, for checking the screen without Athena."""
    start = clock()
    task = "Check the weather in London and, if it's going to rain, put 'Take umbrella' in my calendar for 8am"
    examples = [
        Snapshot(State.IDLE, last_task="What's on my calendar tomorrow?", last_finished=start - 720),
        Snapshot(State.WORKING, task=task, step="Thinking", channel="Telegram", started=start),
        Snapshot(State.WORKING, task=task, step="Using fetch", tools=("fetch",), channel="Telegram", started=start),
        Snapshot(
            State.APPROVAL,
            task=task,
            tools=("fetch",),
            tool="create_calendar_event",
            channel="Telegram",
            started=start,
            deadline=start + 300,
        ),
        Snapshot(State.IDLE, last_task=task, last_error="Couldn't reach the model server", last_finished=start - 60),
        Snapshot(State.OFFLINE, started=start),
    ]
    return lambda: Status(examples[int((clock() - start) // DEMO_SECONDS) % len(examples)])
