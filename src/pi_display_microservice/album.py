"""Keeps a copy of a shared Google Photos album on this machine, on a thread of its own.

Since March 2025, Google's Photos API can only read photos that an app uploaded itself,
so it can't read your albums. Instead, the board reads an album the way a browser does:
from its share link, whose page lists the photos in it. That page isn't a documented API,
so Google could change it. If it does, the slideshow says it can't read the album, and
keeps showing the photos it already has.

Photos are downloaded at the screen's size into a folder, so the slideshow doesn't need
the network to show them. Each time the album is read, photos new to it are downloaded,
and photos taken out of it are deleted.
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import os
import re
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image

from pi_display_microservice.screen import HEIGHT, WIDTH

log = logging.getLogger(__name__)

REFRESH_SECONDS = 60 * 60  # how often to look for photos added to the album, or taken out
RETRY_SECONDS = 5 * 60  # how soon to try again when something went wrong
TIMEOUT = 30.0
MAX_PAGE_BYTES = 32 * 1024 * 1024
MAX_PHOTO_BYTES = 4 * 1024 * 1024
SIZE = f"=w{WIDTH}-h{HEIGHT}-rj"  # added to a photo's URL: fitted within the screen, as a JPEG
HEADERS = {"User-Agent": "pi-display-microservice"}
# Google can show visitors from Europe a cookie consent page instead of the album. This
# cookie is the answer the page would remember: "reject all".
PAGE_HEADERS = {**HEADERS, "Cookie": "SOCS=CAI"}

_ID = re.compile(r"[A-Za-z0-9_-]{1,200}")  # a photo's id, which becomes a file name, so it's checked
_DATA = re.compile(r"AF_initDataCallback\(\{[^{}]*?\bdata:")  # where the page's data starts
_JSON = json.JSONDecoder()


@dataclass(frozen=True)
class Photo:
    id: str  # Google's id for it, which stays the same even if its URL changes
    url: str  # where to download it from, once SIZE is added


@dataclass(frozen=True)
class Listing:
    """What a shared album's page says is in it."""

    title: str
    photos: tuple[Photo, ...]
    complete: bool = True  # False if the album has more photos than its page lists


def parse(html: str) -> Listing:
    """The photos a shared album's page lists. Raises ValueError if it isn't a page this can read.

    The page carries its data as JavaScript calls, ``AF_initDataCallback({key: 'ds:1', ..., data: [...]})``.
    The album's ``data`` is a JSON array: [_, [photo, ...], more, [album id, title, ...], ...], where each
    photo is [id, [url, width, height, ...], ...] and ``more`` is "" unless there are more photos to come.
    """
    for match in _DATA.finditer(html):
        try:
            data, _ = _JSON.raw_decode(html, match.end())
        except ValueError:
            continue
        if _is_album(data):
            return _listing(data)
    raise ValueError("Google Photos' page for album_url isn't a shared album. Check album_url in config.toml.")


def read_album(url: str) -> Listing:
    """Ask Google Photos what's in the album at share link ``url``.

    Raises ValueError saying what's wrong if the link isn't a shared album's, and OSError if Google
    Photos can't be reached.
    """
    if urlsplit(url).scheme not in ("http", "https"):
        raise ValueError("album_url in config.toml should be the album's share link, starting with https://")
    request = urllib.request.Request(url, headers=PAGE_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            landed = urlsplit(response.url).hostname or ""
            html = response.read(MAX_PAGE_BYTES).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ValueError(
                "Google Photos doesn't know album_url. Check it in config.toml, and that the album is still shared."
            ) from None
        raise ValueError(f"Google Photos answered {exc.code} when asked for the album.") from None
    if landed == "accounts.google.com":
        raise ValueError(
            "album_url needs a Google sign-in, so it isn't a share link. In Google Photos, open the album, "
            "choose Share, then Create link, and put that link in config.toml."
        )
    if landed == "consent.google.com":
        raise ValueError("Google asked about cookies instead of showing the album.")
    return parse(html)


def explain(exc: Exception) -> str:
    """What went wrong with the album, for a person."""
    if isinstance(exc, ValueError):
        return str(exc)  # already says what's wrong
    if isinstance(exc, urllib.error.HTTPError):
        return f"Google Photos answered {exc.code}."
    if isinstance(exc, urllib.error.URLError):
        exc = exc.reason if isinstance(exc.reason, Exception) else exc
    if isinstance(exc, TimeoutError):
        return "Google Photos didn't answer in time."
    if isinstance(exc, OSError) and exc.filename:  # this machine's disk, not the network
        return f"Couldn't save the photos in {exc.filename} ({exc.strerror or exc})."
    if isinstance(exc, OSError):
        return f"Couldn't reach Google Photos ({exc.strerror or exc})."
    return f"Couldn't read the album ({type(exc).__name__}: {exc})."


class PhotoAlbum:
    """A shared album's photos, downloaded into ``folder`` and kept up to date with the album."""

    def __init__(self, url: str, folder: str | Path, *, refresh: float = REFRESH_SECONDS, retry: float = RETRY_SECONDS):
        self.url = url
        self.folder = Path(folder)
        self.refresh_seconds, self.retry_seconds = refresh, retry
        self.title = ""  # the album's name, once it's been read
        self.count: int | None = None  # how many photos are in it; None until it's been read
        self.complete = True  # False if the album has more photos than its page lists
        self.problem = ""  # why the album couldn't be read or downloaded last time, if it couldn't
        self._have = self._downloaded()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def photos(self) -> frozenset[str]:
        """The ids of the photos downloaded so far. Reading them never waits for the network."""
        return self._have

    def path(self, photo: str) -> Path:
        return self.folder / f"{photo}.jpg"

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="photo-album", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def refresh(self) -> bool:
        """Read the album once: download the photos that are new to it, and delete those taken out of it.

        True if it all worked. Otherwise ``problem`` says what didn't.
        """
        try:
            listing = read_album(self.url)
        except Exception as exc:  # anything at all: the slideshow must keep going, and say what's wrong
            self._went_wrong(explain(exc))
            return False
        if self.complete and not listing.complete:
            log.warning(
                'The album "%s" has more photos than its page lists, so the slideshow only has the first %d',
                listing.title,
                len(listing.photos),
            )
        self.title, self.count, self.complete = listing.title, len(listing.photos), listing.complete

        new = [photo for photo in listing.photos if photo.id not in self._have]
        failed, why = 0, ""
        for photo in new:
            if self._stop.is_set():
                return False
            try:
                self._download(photo)
            except Exception as exc:
                failed, why = failed + 1, explain(exc)
                continue
            self._have = self._have | {photo.id}
        if len(new) > failed:
            log.info("Downloaded %d new photos from %s", len(new) - failed, self._name())

        # Only a full list says what's no longer in the album.
        gone = self._have - {photo.id for photo in listing.photos} if listing.complete else frozenset()
        for photo in gone:
            with contextlib.suppress(OSError):  # it's not shown either way
                self.path(photo).unlink(missing_ok=True)
        if gone:
            self._have = self._have - gone
            log.info("Deleted %d photos that were taken out of %s", len(gone), self._name())

        if failed:
            self._went_wrong(f"Couldn't download {failed} of the album's photos. {why}")
            return False
        if self.problem:
            log.info("Reading %s works again", self._name())
            self.problem = ""
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                worked = self.refresh()
            except Exception:  # a bug: say so, and keep trying rather than stop updating for good
                log.exception("Updating the photos failed")
                worked = False
            self._stop.wait(self.refresh_seconds if worked else self.retry_seconds)

    def _download(self, photo: Photo) -> None:
        request = urllib.request.Request(photo.url + SIZE, headers=HEADERS)
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            data = response.read(MAX_PHOTO_BYTES + 1)
        if len(data) > MAX_PHOTO_BYTES:
            raise ValueError("Google Photos sent a photo far bigger than the screen.")
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.verify()  # a whole picture, not an error page or half of one
        except Exception:
            raise ValueError("Google Photos sent something that isn't a photo.") from None
        self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)  # they're your photos: only you can open them
        tmp = self.folder / f".{photo.id}.tmp"
        tmp.write_bytes(data)
        os.replace(tmp, self.path(photo.id))  # so the slideshow never opens half a photo

    def _downloaded(self) -> frozenset[str]:
        """The photos in the folder from before, tidying away any download that didn't finish."""
        try:
            files = list(self.folder.iterdir())
        except FileNotFoundError:
            return frozenset()
        except OSError as exc:  # downloading will say what's wrong, on screen
            log.warning("Can't look in %s (%s)", self.folder, exc)
            return frozenset()
        have = set()
        for file in files:
            if file.name.endswith(".tmp"):
                with contextlib.suppress(OSError):
                    file.unlink(missing_ok=True)
            elif file.suffix == ".jpg" and _ID.fullmatch(file.stem):
                have.add(file.stem)
        return frozenset(have)

    def _went_wrong(self, problem: str) -> None:
        if problem != self.problem:
            log.warning("Can't update the photos: %s", problem)
        self.problem = problem

    def _name(self) -> str:
        return f'the album "{self.title}"' if self.title else "the album"


def _is_album(data: object) -> bool:
    return (
        isinstance(data, list)
        and len(data) > 3
        and isinstance(data[1], list | None)
        and isinstance(data[3], list)
        and len(data[3]) > 1
        and isinstance(data[3][0], str)
    )


def _listing(data: list) -> Listing:
    items = data[1] or []
    photos: dict[str, Photo] = {}
    for photo in filter(None, map(_photo, items)):
        photos.setdefault(photo.id, photo)  # in the album's order, once each
    if items and not photos:
        raise ValueError(
            "Google Photos has changed how it lists an album's photos, so this board can't read them. "
            "Update pi-display-microservice."
        )
    title = data[3][1] if isinstance(data[3][1], str) else ""
    more = isinstance(data[2], str) and bool(data[2])
    return Listing(title.strip(), tuple(photos.values()), complete=not more)


def _photo(item: object) -> Photo | None:
    if not (isinstance(item, list) and len(item) > 1 and isinstance(item[1], list) and item[1]):
        return None
    photo, url = item[0], item[1][0]
    if not (isinstance(photo, str) and _ID.fullmatch(photo) and isinstance(url, str)):
        return None
    if urlsplit(url).scheme not in ("http", "https"):
        return None
    return Photo(photo, url)
