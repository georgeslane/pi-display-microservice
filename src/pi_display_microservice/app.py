"""The board's main loop: keep the screen showing Athena's latest status.

Reading the status never waits on the network: the client (client.py) keeps the latest
one up to date on its own thread. This loop draws it several times a second while
something on screen moves, and once a second otherwise, and sends a frame only when it
has changed.
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Callable

from pi_display_microservice.board import LED_OFF, Board
from pi_display_microservice.config import Config
from pi_display_microservice.screen import Screen
from pi_display_microservice.status import Snapshot, State, Status

ACTIVE_FPS = 6  # while something on screen is moving
IDLE_FPS = 1
DEMO_SECONDS = 5  # how long the demo shows each example


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
    led: bool = True,
    once: bool = False,
    clock: Callable[[], float] = time.time,
) -> None:
    """Keep ``screen`` showing the status from ``read()``. Runs until interrupted, or for one frame with ``once``.

    Any button turns the screen off or back on. It also comes on by itself while Athena
    is working or waiting for you, so you never miss an approval.
    """
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

        if (on := wanted or active) != backlight:
            screen.set_backlight(on)
            backlight, shown = on, None
        if on:
            frame = board.render(s, now)
            if (data := frame.tobytes()) != shown:  # unchanged frames aren't sent again
                screen.show(frame)
                shown = data
        if (colour := board.led(s, now) if led else LED_OFF) != lit:
            screen.set_led(*colour)
            lit = colour

        if once:
            return
        if screen.wait(1 / (ACTIVE_FPS if active else IDLE_FPS)) and now - last_press > 0.3:
            wanted, last_press = not wanted, now


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
