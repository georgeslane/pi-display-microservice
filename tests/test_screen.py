"""The screens: the Display HAT Mini, with stand-ins for its driver libraries, and the PNG preview."""

import sys
import types
from datetime import timedelta
from types import SimpleNamespace

import pytest
from PIL import Image

from pi_display_microservice.screen import DisplayError, DisplayHATMini, PreviewScreen


def fake_drivers(monkeypatch, lcd_error=None):
    hardware = SimpleNamespace(lcd=None, requests=[])

    class LCD:
        def __init__(self, **kwargs):
            if lcd_error:
                raise lcd_error
            self.kwargs, self.frames, self.backlight = kwargs, [], []
            hardware.lcd = self

        def display(self, image):
            self.frames.append(image)

        def set_backlight(self, on):
            self.backlight.append(on)

    class Request:
        def __init__(self, consumer, config):
            self.consumer, self.config = consumer, config
            self.values, self.edges, self.timeout, self.released = [], [], None, False

        def set_values(self, values):
            self.values.append(values)

        def wait_edge_events(self, timeout):
            self.timeout = timeout
            return bool(self.edges)

        def read_edge_events(self):
            events, self.edges = self.edges, []
            return events

        def release(self):
            self.released = True

    class Chip:
        def request_lines(self, consumer, config):
            hardware.requests.append(Request(consumer, config))
            return hardware.requests[-1]

    line = types.ModuleType("gpiod.line")
    line.Value = SimpleNamespace(ACTIVE="on", INACTIVE="off")
    line.Direction = SimpleNamespace(INPUT="in", OUTPUT="out")
    line.Bias = SimpleNamespace(PULL_UP="pull-up")
    line.Edge = SimpleNamespace(FALLING="falling")
    gpiod = types.ModuleType("gpiod")
    gpiod.line = line
    gpiod.LineSettings = lambda **settings: settings
    gpiodevice = types.ModuleType("gpiodevice")
    gpiodevice.find_chip_by_platform = Chip
    st7789 = types.ModuleType("st7789")
    st7789.ST7789 = LCD
    for name, module in {"gpiod": gpiod, "gpiod.line": line, "gpiodevice": gpiodevice, "st7789": st7789}.items():
        monkeypatch.setitem(sys.modules, name, module)
    return hardware


def test_display_hat_mini_uses_the_right_pins(monkeypatch):
    hardware = fake_drivers(monkeypatch)
    hat = DisplayHATMini()

    assert hardware.lcd.kwargs == {
        "port": 0,
        "cs": 1,
        "dc": 9,
        "backlight": 13,
        "width": 320,
        "height": 240,
        "rotation": 180,
        "spi_speed_hz": 60_000_000,
    }
    led, buttons = hardware.requests
    assert led.config == {(17, 27, 22): {"direction": "out", "active_low": True, "output_value": "off"}}
    button_settings = buttons.config[(5, 6, 16, 24)]
    assert (button_settings["direction"], button_settings["bias"], button_settings["edge_detection"]) == (
        "in",
        "pull-up",
        "falling",
    )

    hat.set_led(True, False, True)
    assert led.values[-1] == {17: "on", 27: "off", 22: "on"}
    hat.show(Image.new("RGB", (320, 240), "red"))
    hat.close()
    assert led.values[-1] == {17: "off", 27: "off", 22: "off"}
    assert hardware.lcd.frames[-1].getbbox() is None  # cleared to black, not left showing old news
    assert hardware.lcd.backlight[-1] is False
    assert led.released and buttons.released


def test_buttons_say_which_was_pressed(monkeypatch):
    hardware = fake_drivers(monkeypatch)
    hat = DisplayHATMini()
    buttons = hardware.requests[1]

    assert hat.wait(0.5) is None and buttons.timeout == timedelta(seconds=0.5)
    for pin, name in [(5, "A"), (6, "B"), (16, "X"), (24, "Y")]:
        buttons.edges = [SimpleNamespace(line_offset=pin)]
        assert hat.wait(0.5) == name
    buttons.edges = [SimpleNamespace(line_offset=24), SimpleNamespace(line_offset=5)]
    assert hat.wait(0.5) == "Y"  # the first, if two arrive together


def test_hardware_problems_explain_the_fix(monkeypatch):
    monkeypatch.setitem(sys.modules, "st7789", None)  # not installed
    with pytest.raises(DisplayError, match="bash scripts/install.sh"):
        DisplayHATMini()

    fake_drivers(monkeypatch, lcd_error=FileNotFoundError(2, "No such file or directory: '/dev/spidev0.1'"))
    with pytest.raises(DisplayError, match="raspi-config nonint do_spi 0"):
        DisplayHATMini()

    fake_drivers(monkeypatch, lcd_error=PermissionError(13, "Permission denied: '/dev/spidev0.1'"))
    with pytest.raises(DisplayError, match="usermod -aG spi,gpio"):
        DisplayHATMini()

    # What a Pi 5 says, since kernel 6.18, when SPI holds on to GPIO 9.
    fake_drivers(monkeypatch, lcd_error=ValueError(22, "Invalid argument"))
    with pytest.raises(DisplayError, match="dtoverlay=spi0-2cs,no_miso"):
        DisplayHATMini()

    fake_drivers(monkeypatch, lcd_error=ValueError("something else entirely"))
    with pytest.raises(ValueError, match="something else"):  # not mistaken for that
        DisplayHATMini()


def test_preview_screen_replaces_the_file_whole(tmp_path):
    screen = PreviewScreen(tmp_path / "board.png")
    screen.show(Image.new("RGB", (320, 240), "red"))
    screen.show(Image.new("RGB", (320, 240), "blue"))
    assert Image.open(tmp_path / "board.png").getpixel((0, 0)) == (0, 0, 255)
    assert [p.name for p in tmp_path.iterdir()] == ["board.png"]
    assert screen.wait(0.01) is None
