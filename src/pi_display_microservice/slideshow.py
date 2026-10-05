"""The photo page: the album's photos, a new one every five minutes, the ones you like more often.

Each new photo is picked at random, weighted by its score (scores.py), and is never the
one already up. Liking or disliking the photo on screen changes its score, and a heart
shows for a moment to say so.

Only choosing and drawing happen here: album.py downloads the photos, and app.py decides
when this page is on screen.
"""

from __future__ import annotations

import logging
import random
import unicodedata
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from pi_display_microservice.album import PhotoAlbum
from pi_display_microservice.board import BACKGROUND, COLORS, FAILED, MUTED, TEXT, Color, _font, _mix, wrap
from pi_display_microservice.scores import Scores
from pi_display_microservice.screen import HEIGHT, WIDTH
from pi_display_microservice.status import State

log = logging.getLogger(__name__)

PHOTO_SECONDS = 5 * 60  # how long each photo stays up
FEEDBACK_SECONDS = 2.0  # how long a like or dislike shows

LIKED = (240, 96, 112)  # rose
DISLIKED = COLORS[State.OFFLINE]  # stone
SUN = COLORS[State.APPROVAL]  # amber


class Slideshow:
    def __init__(
        self,
        album: PhotoAlbum,
        scores: Scores,
        *,
        seconds: float = PHOTO_SECONDS,
        rng: random.Random | None = None,
    ):
        self.album, self.scores, self.seconds = album, scores, seconds
        self.rng = rng or random.Random()
        self.current: str | None = None  # the photo on screen
        self.since = 0.0  # when it went up
        self._picture: Image.Image | None = None  # the current photo, fitted to the screen
        self._feedback: tuple[str, bool, int, float] | None = None  # the last (photo, liked, its new score, when)
        self._drawn: tuple[object, Image.Image] | None = None  # the last frame drawn on top of a photo, or a notice

    def render(self, now: float) -> Image.Image:
        photos = self.album.photos()
        # A clock set back (by the network, say) mustn't keep a photo up until it catches up.
        if self.current not in photos or not 0 <= now - self.since < self.seconds:
            self._next(sorted(photos), now)  # sorted, so a seeded random picks the same photos every time
        if self._picture is None:
            return self._notice()
        feedback = self._feedback
        if feedback and feedback[0] == self.current and 0 <= now - feedback[3] < FEEDBACK_SECONDS:
            _, liked, score, _ = feedback
            picture = self._picture
            return self._drawn_once((feedback, self.since), lambda: _with_feedback(picture, liked, score))
        return self._picture

    def like(self, now: float) -> None:
        """Like the photo on screen: it comes up more often from now on."""
        self._rate(now, liked=True)

    def dislike(self, now: float) -> None:
        """Dislike the photo on screen: it comes up less often from now on."""
        self._rate(now, liked=False)

    def _rate(self, now: float, liked: bool) -> None:
        if self.current is None:
            return  # no photo on screen
        score = self.scores.like(self.current) if liked else self.scores.dislike(self.current)
        self._feedback = (self.current, liked, score, now)
        log.info("%s photo %s; its score is now %+d", "Liked" if liked else "Disliked", self.current, score)

    def _next(self, photos: list[str], now: float) -> None:
        """Put up a photo picked at random, weighted by its score, other than the one up now."""
        others = [photo for photo in photos if photo != self.current]
        while others:
            photo = self.rng.choices(others, weights=[self.scores.weight(p) for p in others])[0]
            try:
                picture = fit(self.album.path(photo))
            except Exception as exc:  # deleted since, say, or damaged: try another
                log.warning("Couldn't show photo %s (%s)", photo, exc)
                others.remove(photo)
                continue
            self.current, self.since, self._picture = photo, now, picture
            return
        if self.current in photos:
            self.since = now  # it's the only photo there is to show, so it stays up
        else:
            self.current, self._picture = None, None

    def _notice(self) -> Image.Image:
        says = self._says()
        return self._drawn_once(says, lambda: _notice(*says))

    def _says(self) -> tuple[str, str, Color, str]:
        """What the page says while it has no photos to show: a title, a message in a colour, and a footer."""
        album = self.album
        title = album.title or "Photos"
        if album.problem:
            return title, album.problem, FAILED, "journalctl -u pi-display-microservice -f"
        if album.count is None:
            return title, "Getting the album from Google Photos…", MUTED, ""
        if album.count == 0:
            return title, "The album is empty. Add photos to it in Google Photos.", MUTED, ""
        return title, f"Downloading {album.count} photo{'' if album.count == 1 else 's'}…", MUTED, ""

    def _drawn_once(self, key: object, draw: Callable[[], Image.Image]) -> Image.Image:
        """``draw()``, kept and reused for as long as ``key`` stays the same."""
        if self._drawn is None or self._drawn[0] != key:
            self._drawn = (key, draw())
        return self._drawn[1]


def fit(path: Path) -> Image.Image:
    """The whole photo, as big as fits on the screen, over a blurred and darkened copy of it that fills the rest."""
    with Image.open(path) as image:
        photo = ImageOps.exif_transpose(image).convert("RGB")
    backdrop = ImageOps.fit(photo, (WIDTH, HEIGHT)).filter(ImageFilter.GaussianBlur(12))
    frame = ImageEnhance.Brightness(backdrop).enhance(0.45)
    photo = ImageOps.contain(photo, (WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    frame.paste(photo, ((WIDTH - photo.width) // 2, (HEIGHT - photo.height) // 2))
    return frame


def _with_feedback(picture: Image.Image, liked: bool, score: int) -> Image.Image:
    """``picture`` with a pill at the bottom saying the photo was liked or disliked, and its score now."""
    color = LIKED if liked else DISLIKED
    word, number = "Liked" if liked else "Disliked", f"{score:+d}".replace("-", "−")
    font, small = _font("Inter", 15, 600), _font("Inter", 13, 500)
    heart = _heart(18, color, broken=not liked)
    height = 34
    width = 14 + heart.width + 9 + font.getlength(word) + 8 + small.getlength(number) + 16
    left, top = (WIDTH - width) / 2, HEIGHT - 12 - height
    middle = top + height / 2

    layer = Image.new("RGBA", picture.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.rounded_rectangle(
        [left, top, left + width, top + height], radius=height / 2, fill=BACKGROUND + (220,), outline=_mix(color, 0.6)
    )
    layer.alpha_composite(heart, (round(left + 14), round(middle - heart.height / 2)))
    x = left + 14 + heart.width + 9
    draw.text((x, middle), word, font=font, fill=TEXT, anchor="lm")
    draw.text((x + font.getlength(word) + 8, middle), number, font=small, fill=MUTED, anchor="lm")
    return Image.alpha_composite(picture.convert("RGBA"), layer).convert("RGB")


def _notice(title: str, text: str, color: Color, footer: str) -> Image.Image:
    """The page while there are no photos: a picture frame, the album's name, and what's happening."""
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    icon = _picture_icon()
    image.paste(icon, ((WIDTH - icon.width) // 2, 30), icon)
    draw = ImageDraw.Draw(image)
    width = WIDTH - 48
    size = 22
    title = _clean(title.upper(), _font("Cinzel", size, 700))
    while size > 14 and _font("Cinzel", size, 700).getlength(title) > width:
        size -= 1
    font = _font("Cinzel", size, 700)
    draw.text((WIDTH / 2, 124), (wrap(title, font, width, 1) or [""])[0], font=font, fill=TEXT, anchor="ms")
    body = _font("Inter", 14, 400)
    for i, line in enumerate(wrap(_clean(text, body), body, width, 3)):
        draw.text((WIDTH / 2, 156 + 19 * i), line, font=body, fill=color, anchor="ms")
    if footer:
        draw.text((WIDTH / 2, 229), footer, font=_font("Inter", 11, 500), fill=MUTED, anchor="ms")
    return image


def _clean(text: str, font: ImageFont.FreeTypeFont) -> str:
    """``text`` without the characters ``font`` can't draw (emoji, mostly), rather than with boxes for them."""
    missing = _bitmap("￿", font)  # a noncharacter, so it comes out as the "missing" glyph
    kept = []
    for ch in text:
        if ch.isspace():
            kept.append(" ")
        elif unicodedata.category(ch)[0] != "C" and _bitmap(ch, font) != missing:
            kept.append(ch)
    return " ".join("".join(kept).split())


@lru_cache(maxsize=1024)
def _bitmap(ch: str, font: ImageFont.FreeTypeFont) -> bytes:
    image = Image.new("L", (64, 64))
    ImageDraw.Draw(image).text((8, 8), ch, font=font, fill=255)
    return image.tobytes()


@lru_cache(maxsize=4)
def _heart(size: int, color: Color, broken: bool) -> Image.Image:
    """A heart, or a broken one, drawn 4x larger and scaled down for smooth edges."""
    k = 4 * size

    def at(x: float, y: float) -> tuple[float, float]:
        return x * k, y * k

    layer = Image.new("RGBA", (k, k), color + (0,))
    draw = ImageDraw.Draw(layer)
    fill = color + (255,)
    for centre in (0.29, 0.71):  # the two lobes, which the point below meets at a tangent
        draw.ellipse([*at(centre - 0.25, 0.08), *at(centre + 0.25, 0.58)], fill=fill)
    draw.polygon([at(0.107, 0.501), at(0.5, 0.92), at(0.893, 0.501), at(0.5, 0.33)], fill=fill)
    if broken:  # a crack down the middle
        crack = [at(0.5, 0.15), at(0.4, 0.42), at(0.58, 0.58), at(0.45, 0.76), at(0.5, 0.95)]
        draw.line(crack, fill=color + (0,), width=round(0.08 * k), joint="curve")
    return layer.resize((size, size), Image.Resampling.LANCZOS)


@lru_cache(maxsize=1)
def _picture_icon() -> Image.Image:
    """A small framed picture of hills under the sun, drawn 4x larger and scaled down for smooth edges."""
    width, height, k = 76, 58, 4
    layer = Image.new("RGBA", (width * k, height * k), MUTED + (0,))
    draw = ImageDraw.Draw(layer)
    line = MUTED + (255,)
    draw.rounded_rectangle([2 * k, 2 * k, (width - 2) * k, (height - 2) * k], radius=7 * k, outline=line, width=3 * k)
    draw.ellipse([50 * k, 12 * k, 61 * k, 23 * k], fill=SUN + (255,))
    hills = [(9, 47), (28, 24), (41, 38), (49, 30), (67, 47)]
    draw.polygon([(x * k, y * k) for x, y in hills], fill=line)
    return layer.resize((width, height), Image.Resampling.LANCZOS)
