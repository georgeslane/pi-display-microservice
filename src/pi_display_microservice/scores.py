"""Likes and dislikes for the slideshow's photos, and how much they make each photo come up.

A photo's score is x = likes - dislikes, and the slideshow picks photos at random with
this weight:

    x >= 0:  2 - 1/(1 + x)     1 to start with, rising towards 2 the more it's liked
    x < 0:   1/(1 + |x|)       falling towards 0 the more it's disliked

So a photo you like comes up at most twice as often as one you haven't rated, and one you
dislike comes up less and less, but never disappears altogether.

The counts are kept in a JSON file, so they survive restarts and updates.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


def weight(x: int) -> float:
    """How often a photo with score ``x`` (likes - dislikes) comes up, compared with one that has no score."""
    if x >= 0:
        return 2 - 1 / (1 + x)
    return 1 / (1 - x)  # 1/(1 + |x|)


class Scores:
    """Each photo's likes and dislikes, by its id, kept in a JSON file at ``path``."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._counts: dict[str, tuple[int, int]] = self._load()  # id -> (likes, dislikes)

    def like(self, photo: str) -> int:
        """Count a like for ``photo``, and return its new score."""
        likes, dislikes = self._counts.get(photo, (0, 0))
        return self._set(photo, likes + 1, dislikes)

    def dislike(self, photo: str) -> int:
        """Count a dislike for ``photo``, and return its new score."""
        likes, dislikes = self._counts.get(photo, (0, 0))
        return self._set(photo, likes, dislikes + 1)

    def score(self, photo: str) -> int:
        likes, dislikes = self._counts.get(photo, (0, 0))
        return likes - dislikes

    def weight(self, photo: str) -> float:
        return weight(self.score(photo))

    def _set(self, photo: str, likes: int, dislikes: int) -> int:
        self._counts[photo] = (likes, dislikes)
        self._save()
        return likes - dislikes

    def _load(self) -> dict[str, tuple[int, int]]:
        try:
            raw = json.loads(self.path.read_text())
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            return self._broken(f"it can't be read ({exc})")
        if not isinstance(raw, dict):
            return self._broken("it isn't a list of photos")
        counts = {}
        for photo, entry in raw.items():
            likes = entry.get("likes", 0) if isinstance(entry, dict) else None
            dislikes = entry.get("dislikes", 0) if isinstance(entry, dict) else None
            if not all(_is_count(n) for n in (likes, dislikes)):
                return self._broken(f"the entry for {photo!r} isn't a number of likes and dislikes")
            counts[photo] = (likes, dislikes)
        return counts

    def _broken(self, why: str) -> dict[str, tuple[int, int]]:
        # Keep the file for a person to look at, rather than overwrite it with the next like.
        kept = self.path.with_name(self.path.name + ".broken")
        log.warning("Starting the photo scores again, because %s: %s. The old file is now %s", self.path, why, kept)
        try:
            os.replace(self.path, kept)
        except OSError:
            pass
        return {}

    def _save(self) -> None:
        counts = {photo: {"likes": likes, "dislikes": dislikes} for photo, (likes, dislikes) in self._counts.items()}
        tmp = self.path.with_name(f".{self.path.name}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(counts, indent=1, sort_keys=True) + "\n")
            os.replace(tmp, self.path)  # all or nothing, even if the power goes
        except OSError as exc:  # a full or read-only disk mustn't stop the board: keep counting, and say so
            log.error("Couldn't save the photo scores to %s (%s). They'll be lost on a restart", self.path, exc)


def _is_count(n: object) -> bool:
    return isinstance(n, int) and not isinstance(n, bool) and n >= 0
