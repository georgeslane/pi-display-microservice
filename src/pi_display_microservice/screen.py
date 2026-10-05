"""Where the board's pictures go: the Display HAT Mini, or a PNG file on any computer.

Both have the same methods (Screen), so the rest of the board doesn't know or care
which it's drawing on.
"""

from __future__ import annotations

import errno
import os
import time
from datetime import timedelta
from pathlib import Path
from typing import Protocol

from PIL import Image

WIDTH, HEIGHT = 320, 240


class DisplayError(Exception):
    """The screen can't be used. The message says how to fix it."""


class Screen(Protocol):
    def show(self, image: Image.Image) -> None: ...

    def set_led(self, red: bool, green: bool, blue: bool) -> None: ...

    def set_backlight(self, on: bool) -> None: ...

    def wait(self, seconds: float) -> str | None:
        """Sleep for ``seconds``, returning early with the button pressed ("A", "B", "X" or "Y"), if any."""
        ...

    def close(self) -> None: ...


class DisplayHATMini:
    """Pimoroni Display HAT Mini: a 320x240 ST7789 LCD, an RGB LED and four buttons.

    Pins (BCM) are from https://pinout.xyz/pinout/display_hat_mini and Pimoroni's own
    library. That library is built on RPi.GPIO, which doesn't work on a Pi 5, so this
    drives the hardware with st7789 and gpiod instead, which work on a Pi 4 and 5.
    """

    SPI_PORT, SPI_CS, DC, BACKLIGHT = 0, 1, 9, 13
    LED = (17, 27, 22)  # red, green, blue; lit when low
    BUTTONS = {5: "A", 6: "B", 16: "X", 24: "Y"}  # low when pressed

    def __init__(self) -> None:
        try:
            import gpiod
            import gpiodevice
            import st7789
            from gpiod.line import Bias, Direction, Edge, Value
        except ImportError as exc:
            raise DisplayError(
                f"The display drivers aren't installed ({exc.name}). Run: bash scripts/install.sh"
            ) from exc
        self._on, self._off = Value.ACTIVE, Value.INACTIVE
        try:
            self._lcd = st7789.ST7789(
                port=self.SPI_PORT,
                cs=self.SPI_CS,
                dc=self.DC,
                backlight=self.BACKLIGHT,
                width=WIDTH,
                height=HEIGHT,
                rotation=180,
                spi_speed_hz=60_000_000,
            )
            chip = gpiodevice.find_chip_by_platform()
            self._led = chip.request_lines(
                consumer="pi-display-microservice-led",
                config={
                    self.LED: gpiod.LineSettings(direction=Direction.OUTPUT, active_low=True, output_value=self._off)
                },
            )
            self._buttons = chip.request_lines(
                consumer="pi-display-microservice-buttons",
                config={
                    tuple(self.BUTTONS): gpiod.LineSettings(
                        direction=Direction.INPUT,
                        bias=Bias.PULL_UP,
                        edge_detection=Edge.FALLING,
                        debounce_period=timedelta(milliseconds=30),
                    )
                },
            )
        except FileNotFoundError as exc:
            raise DisplayError(
                "SPI is turned off, so the screen can't be reached. Run: sudo raspi-config nonint do_spi 0"
            ) from exc
        except PermissionError as exc:
            raise DisplayError(
                f"Not allowed to use the screen ({exc}). Run: sudo usermod -aG spi,gpio $USER, then log in again"
            ) from exc
        except ValueError as exc:
            if errno.EINVAL not in exc.args and "Invalid argument" not in str(exc):
                raise
            # Since kernel 6.18 the Pi 5 won't share SPI's MISO pin (GPIO 9), which this HAT uses for its screen.
            raise DisplayError(
                "The Pi won't let the screen use GPIO 9, because SPI has it. Add the line "
                "dtoverlay=spi0-2cs,no_miso to /boot/firmware/config.txt and reboot (scripts/install.sh does this)."
            ) from exc
        except OSError as exc:
            raise DisplayError(
                f"Couldn't set up the Display HAT Mini's pins ({exc}). Is another status board running?"
            ) from exc

    def show(self, image: Image.Image) -> None:
        self._lcd.display(image)

    def set_led(self, red: bool, green: bool, blue: bool) -> None:
        self._led.set_values(
            {pin: self._on if lit else self._off for pin, lit in zip(self.LED, (red, green, blue), strict=True)}
        )

    def set_backlight(self, on: bool) -> None:
        self._lcd.set_backlight(on)

    def wait(self, seconds: float) -> str | None:
        if not self._buttons.wait_edge_events(timedelta(seconds=seconds)):
            return None
        pressed = [self.BUTTONS.get(event.line_offset) for event in self._buttons.read_edge_events()]
        return next((button for button in pressed if button), None)

    def close(self) -> None:
        # Don't leave a stale picture on the screen (it keeps showing one while powered).
        self.set_led(False, False, False)
        self.show(Image.new("RGB", (WIDTH, HEIGHT)))
        self.set_backlight(False)
        self._led.release()
        self._buttons.release()


class PreviewScreen:
    """Draws to a PNG file instead of a screen, to try the board on any computer."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def show(self, image: Image.Image) -> None:
        tmp = self.path.with_name(f".{self.path.name}.tmp")
        image.save(tmp, format="PNG")
        os.replace(tmp, self.path)  # so an image viewer never reads half a file

    def set_led(self, red: bool, green: bool, blue: bool) -> None:
        pass

    def set_backlight(self, on: bool) -> None:
        pass

    def wait(self, seconds: float) -> str | None:
        time.sleep(seconds)
        return None

    def close(self) -> None:
        pass
