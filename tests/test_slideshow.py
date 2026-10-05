"""The photo page: which photo is up, for how long, and how likes and dislikes show."""

import random
from collections import Counter

from PIL import Image

from pi_display_microservice import slideshow as slideshow_module
from pi_display_microservice.album import PhotoAlbum
from pi_display_microservice.board import FAILED, MUTED, _font
from pi_display_microservice.scores import Scores
from pi_display_microservice.slideshow import FEEDBACK_SECONDS, PHOTO_SECONDS, Slideshow, _clean, fit

RED, BLUE, GREEN = (220, 30, 30), (30, 30, 220), (30, 200, 30)


def album(tmp_path, size=(320, 240), **photos):
    """An album whose photos were downloaded before, each all in one colour."""
    folder = tmp_path / "photos"
    folder.mkdir(exist_ok=True)
    for name, colour in photos.items():
        Image.new("RGB", size, colour).save(folder / f"{name}.jpg", quality=95)
    return PhotoAlbum("https://photos.example/share/album", folder)


def slideshow(tmp_path, scores=None, rng=None, **photos):
    return Slideshow(album(tmp_path, **photos), scores or Scores(tmp_path / "scores.json"), rng=rng or random.Random(1))


def close(pixel, colour, tolerance=8):
    return all(abs(a - b) <= tolerance for a, b in zip(pixel, colour, strict=True))


def test_the_photo_fills_the_screen(tmp_path):
    show = slideshow(tmp_path, red=RED)
    frame = show.render(0)
    assert (frame.size, frame.mode, show.current) == ((320, 240), "RGB", "red")
    assert all(close(frame.getpixel(xy), RED) for xy in [(0, 0), (160, 120), (319, 239)])


def test_a_photo_the_screen_is_the_wrong_shape_for_is_shown_whole_over_a_blurred_copy(tmp_path):
    show = Slideshow(album(tmp_path, size=(120, 240), tall=BLUE), Scores(tmp_path / "scores.json"))
    frame = show.render(0)
    assert close(frame.getpixel((160, 120)), BLUE)  # all of the photo, in the middle
    assert close(frame.getpixel((10, 120)), tuple(round(c * 0.45) for c in BLUE))  # either side: it, darker


def test_a_new_photo_every_five_minutes(tmp_path):
    assert PHOTO_SECONDS == 5 * 60
    show = slideshow(tmp_path, red=RED, blue=BLUE, green=GREEN)
    show.render(1000)
    first = show.current
    show.render(1000 + PHOTO_SECONDS - 1)
    assert show.current == first
    show.render(1000 + PHOTO_SECONDS)
    assert show.current not in (None, first)


def test_never_the_same_photo_twice_in_a_row(tmp_path):
    show = slideshow(tmp_path, red=RED, blue=BLUE)
    shown = []
    for i in range(20):
        show.render(i * PHOTO_SECONDS)
        shown.append(show.current)
    assert all(a != b for a, b in zip(shown, shown[1:], strict=False))


def test_an_album_of_one_photo_keeps_showing_it(tmp_path):
    show = slideshow(tmp_path, red=RED)
    show.render(0)
    show.render(PHOTO_SECONDS)
    assert show.current == "red" and show.since == PHOTO_SECONDS


def test_a_clock_set_back_doesnt_stop_the_photos_changing(tmp_path):
    show = slideshow(tmp_path, red=RED, blue=BLUE)
    show.render(10_000)
    first = show.current
    show.render(9_000)  # the network put the clock back
    assert show.current != first and show.since == 9_000


class Recording(random.Random):
    """A seeded random that remembers the weights it's asked to choose with."""

    def __init__(self):
        super().__init__(7)
        self.asked = []

    def choices(self, population, weights=None, **kwargs):
        self.asked.append(dict(zip(population, weights, strict=True)))
        return super().choices(population, weights, **kwargs)


def test_photos_are_picked_at_random_weighted_by_their_scores(tmp_path):
    scores = Scores(tmp_path / "scores.json")
    for _ in range(3):
        scores.like("red")  # x = 3: 2 - 1/4
    scores.dislike("blue")  # x = -1: 1/2
    rng = Recording()
    show = slideshow(tmp_path, scores, rng, red=RED, blue=BLUE, green=GREEN)
    show.render(0)
    assert rng.asked[0] == {"blue": 0.5, "green": 1.0, "red": 1.75}
    first = show.current
    show.render(PHOTO_SECONDS)
    assert set(rng.asked[1]) == {"red", "blue", "green"} - {first}  # never the one already up


def test_liked_photos_come_up_more_often_and_disliked_ones_less(tmp_path, monkeypatch):
    monkeypatch.setattr(slideshow_module, "fit", lambda path: Image.new("RGB", (1, 1)))  # only which, not how
    scores = Scores(tmp_path / "scores.json")
    for _ in range(3):
        scores.like("liked")
        scores.dislike("disliked")
    plain = {f"plain{i}": GREEN for i in range(4)}
    show = slideshow(tmp_path, scores, random.Random(3), liked=RED, disliked=BLUE, **plain)
    seen = Counter()
    for i in range(4000):
        show.render(i * PHOTO_SECONDS)
        seen[show.current] += 1
    each_plain = sum(seen[photo] for photo in plain) / len(plain)
    # Weights 1.75 and 0.25 against 1, a little nearer 1 because no photo comes twice in a row.
    assert 1.3 < seen["liked"] / each_plain < 1.75
    assert 0.2 < seen["disliked"] / each_plain < 0.4


def test_likes_and_dislikes_count_for_the_photo_on_screen_and_show_for_a_moment(tmp_path):
    show = slideshow(tmp_path, red=RED, blue=BLUE)
    plain = show.render(0).copy()
    photo = show.current

    show.like(10)
    liked = show.render(10.5).copy()
    assert show.scores.score(photo) == 1 and show.current == photo  # the photo stays up
    assert liked.crop((0, 0, 320, 180)).tobytes() == plain.crop((0, 0, 320, 180)).tobytes()
    assert liked.crop((0, 180, 320, 240)).tobytes() != plain.crop((0, 180, 320, 240)).tobytes()  # a pill
    assert show.render(10 + FEEDBACK_SECONDS).tobytes() == plain.tobytes()  # and then it's gone

    show.dislike(20)
    show.dislike(20.5)
    disliked = show.render(21)
    assert show.scores.score(photo) == -1
    assert disliked.tobytes() not in (plain.tobytes(), liked.tobytes())


def test_a_like_just_before_the_photo_changes_doesnt_show_on_the_next(tmp_path):
    show = slideshow(tmp_path, red=RED, blue=BLUE)
    show.render(0)
    show.like(PHOTO_SECONDS - 0.5)
    frame = show.render(PHOTO_SECONDS)  # a new photo, within moments of the like
    assert frame.tobytes() == fit(show.album.path(show.current)).tobytes()


def test_there_is_nothing_to_like_without_a_photo(tmp_path):
    show = slideshow(tmp_path)
    show.render(0)
    show.like(0)
    show.dislike(0)
    assert not (tmp_path / "scores.json").exists()


def test_a_photo_taken_out_of_the_album_is_replaced_at_once(tmp_path):
    show = slideshow(tmp_path, red=RED, blue=BLUE, green=GREEN)
    show.render(0)
    first = show.current
    show.album._have = show.album.photos() - {first}  # the album no longer has it
    show.render(1)
    assert show.current not in (None, first)


def test_a_damaged_photo_is_passed_over(tmp_path):
    show = slideshow(tmp_path, red=RED)
    (show.album.folder / "broken.jpg").write_bytes(b"not a photo")
    show.album._have = show.album.photos() | {"broken"}
    for i in range(4):
        show.render(i * PHOTO_SECONDS)
        assert show.current == "red"


def test_says_why_there_are_no_photos(tmp_path):
    empty = album(tmp_path)
    show = Slideshow(empty, Scores(tmp_path / "scores.json"))
    assert show._says() == ("Photos", "Getting the album from Google Photos…", MUTED, "")
    loading = show.render(0).copy()
    empty.title, empty.count = "Summer", 0
    assert show._says() == ("Summer", "The album is empty. Add photos to it in Google Photos.", MUTED, "")
    empty.count = 1
    assert show._says()[1] == "Downloading 1 photo…"
    empty.count = 12
    assert show._says()[1] == "Downloading 12 photos…"
    empty.problem = "Google Photos answered 500."
    assert show._says() == ("Summer", "Google Photos answered 500.", FAILED, "journalctl -u pi-display-microservice -f")
    problem = show.render(1)
    assert problem.size == (320, 240) and problem.tobytes() != loading.tobytes()


def test_characters_the_font_lacks_are_dropped_from_album_names():
    assert _clean("SUMMER 🏖️ IN ZÜRICH\x07", _font("Cinzel", 22, 700)) == "SUMMER IN ZÜRICH"
