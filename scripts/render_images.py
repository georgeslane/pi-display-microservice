"""Render the images that are made from other files:

- src/pi_display_microservice/assets/athena.png, the shield the board draws, from athena.svg
- docs/board.png, the board in each state, for the README
- docs/photos.png, the photo page, for the README

    uv run --with resvg-py scripts/render_images.py

Re-run it after editing the SVG or the board's design. resvg is only needed here,
which keeps it off the Pi.
"""

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

import resvg_py
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src" / "pi_display_microservice" / "assets"
ICON_SIZE = 256  # the board scales it down from here


def render_icon() -> Path:
    png = resvg_py.svg_to_bytes(svg_path=str(ASSETS / "athena.svg"), width=ICON_SIZE, height=ICON_SIZE)
    (ASSETS / "athena.png").write_bytes(png)
    return ASSETS / "athena.png"


def render_board() -> Path:
    from pi_display_microservice.app import DEMO_SECONDS, demo
    from pi_display_microservice.board import Board

    # The --demo examples, at a fixed time so the picture only changes when the design does.
    start = datetime(2026, 10, 3, 14, 30, tzinfo=ZoneInfo("Europe/London")).timestamp()
    clock = [start]
    read = demo(lambda: clock[0])
    board = Board("Athena", "Europe/London")
    frames = []
    for i in range(6):
        clock[0] = start + i * DEMO_SECONDS + 2.6
        frames.append(board.render(read().snapshot, clock[0]))
    return save_sheet(frames, "board.png")


def render_photos() -> Path:
    from pi_display_microservice.album import PhotoAlbum
    from pi_display_microservice.scores import Scores
    from pi_display_microservice.slideshow import Slideshow

    # Made-up photos, drawn here: a landscape the screen is slightly too tall for, just liked, and a
    # portrait, just disliked. Then the page while it's getting an album for the first time.
    landscape = _scene(320, 213, (70, 120, 200), (60, 110, 60), (255, 230, 120))
    portrait = _scene(180, 240, (200, 90, 60), (90, 60, 40), (255, 240, 200))
    frames = []
    with TemporaryDirectory() as tmp:
        scores = Scores(Path(tmp) / "scores.json")
        for name, photo, liked in [("landscape", landscape, True), ("portrait", portrait, False)]:
            folder = Path(tmp) / name
            folder.mkdir()
            photo.save(folder / f"{name}.jpg", quality=95)
            show = Slideshow(PhotoAlbum("https://photos.example/share/album", folder), scores)
            show.render(0)
            (show.like if liked else show.dislike)(1)
            frames.append(show.render(1))
        frames.append(Slideshow(PhotoAlbum("https://photos.example/share/album", Path(tmp) / "none"), scores).render(0))
    return save_sheet(frames, "photos.png")


def _scene(width: int, height: int, sky: tuple, ground: tuple, sun: tuple) -> Image.Image:
    """A made-up photo: hills under the sun."""
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        fade = y / height
        haze = zip(sky, (250, 220, 180), strict=True)
        draw.line([(0, y), (width, y)], fill=tuple(round(c + (h - c) * fade) for c, h in haze))
    draw.ellipse([width * 0.65, height * 0.15, width * 0.8, height * 0.15 + width * 0.15], fill=sun)
    hills = [(0, 1), (0, 0.7), (0.3, 0.5), (0.55, 0.68), (0.8, 0.55), (1, 0.65), (1, 1)]
    draw.polygon([(x * width, y * height) for x, y in hills], fill=ground)
    return image


def save_sheet(frames: list[Image.Image], name: str) -> Path:
    """``frames`` side by side, three to a row, into docs/``name``."""
    from pi_display_microservice.board import HEIGHT, WIDTH

    gap, columns = 12, 3
    rows = -(-len(frames) // columns)
    sheet = Image.new("RGB", (columns * (WIDTH + gap) + gap, rows * (HEIGHT + gap) + gap), (40, 34, 28))
    for i, frame in enumerate(frames):
        sheet.paste(frame, (gap + (i % columns) * (WIDTH + gap), gap + (i // columns) * (HEIGHT + gap)))
    path = ROOT / "docs" / name
    path.parent.mkdir(exist_ok=True)
    sheet.save(path, optimize=True)
    return path


if __name__ == "__main__":
    print("Wrote", render_icon())
    print("Wrote", render_board())
    print("Wrote", render_photos())
