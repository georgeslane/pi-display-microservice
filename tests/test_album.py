"""Reading a shared album, and keeping a copy of its photos, against a stand-in for Google Photos
(conftest.FakeGooglePhotos)."""

import logging
import urllib.request

import pytest
from conftest import album_page
from PIL import Image
from test_client import free_port, wait_until

from pi_display_microservice.album import PhotoAlbum, explain, parse, read_album

LH3 = "https://lh3.googleusercontent.com/pw/AP1GczExample"
RED, BLUE, GREEN = (220, 30, 30), (30, 30, 220), (30, 200, 30)


def test_reads_the_photos_a_shared_album_lists():
    page = album_page('Summer "24" in Zürich', [("AF1QipPhotoOne", f"{LH3}One"), ("AF1Qip-Photo_2", f"{LH3}Two")])
    listing = parse(page)
    assert listing.title == 'Summer "24" in Zürich'
    assert [(p.id, p.url) for p in listing.photos] == [("AF1QipPhotoOne", f"{LH3}One"), ("AF1Qip-Photo_2", f"{LH3}Two")]
    assert listing.complete


def test_knows_when_the_album_has_more_photos_than_its_page_lists():
    assert not parse(album_page("Big", [("AF1QipPhotoOne", f"{LH3}One")], more=True)).complete


def test_an_empty_album_is_still_an_album():
    listing = parse(album_page("Nothing yet", []))
    assert (listing.title, listing.photos, listing.complete) == ("Nothing yet", (), True)


def test_only_photos_with_safe_ids_and_web_addresses_are_read():
    page = album_page(
        "Odd",
        [
            ("AF1QipGood", f"{LH3}Good"),
            ("../../.bashrc", f"{LH3}Climbs"),  # ids become file names
            ("a/b", f"{LH3}Slash"),
            ("", f"{LH3}Empty"),
            ("AF1QipLocal", "file:///etc/passwd"),
            ("AF1QipScript", "javascript:alert(1)"),
            ("AF1QipGood", f"{LH3}Again"),  # listed twice: the first counts
        ],
    )
    assert [(p.id, p.url) for p in parse(page).photos] == [("AF1QipGood", f"{LH3}Good")]


@pytest.mark.parametrize(
    ("page", "says"),
    [
        ("", "isn't a shared album"),
        ("<html><body>Sign in to continue to Google Photos</body></html>", "isn't a shared album"),
        ("<script>AF_initDataCallback({key: 'ds:1', hash: '2', data:[1, 2], sideChannel: {}});</script>", "isn't a"),
        (album_page("Changed", [("../nope", f"{LH3}One")]), "changed how it lists an album's photos"),
    ],
)
def test_refuses_pages_it_cannot_read(page, says):
    with pytest.raises(ValueError, match=says):
        parse(page)


def test_downloads_the_photos_at_the_screens_size(google_photos, tmp_path):
    google_photos.photos = {"AF1QipRed": RED, "AF1QipBlue": BLUE}
    album = PhotoAlbum(google_photos.album_url, tmp_path / "photos")
    assert (album.photos(), album.count, album.title) == (frozenset(), None, "")  # nothing until it's read

    assert album.refresh()
    assert album.photos() == {"AF1QipRed", "AF1QipBlue"}
    assert (album.title, album.count, album.problem) == ("Summer", 2, "")
    with Image.open(album.path("AF1QipRed")) as photo:
        assert photo.format == "JPEG" and photo.size == (320, 240)
    assert "/photo/AF1QipRed=w320-h240-rj" in google_photos.requests  # fitted to the screen, as a JPEG
    assert (tmp_path / "photos").stat().st_mode & 0o777 == 0o700  # your photos: only you can open them


def test_downloads_only_new_photos_and_deletes_those_taken_out(google_photos, tmp_path):
    google_photos.photos = {"AF1QipRed": RED, "AF1QipBlue": BLUE}
    album = PhotoAlbum(google_photos.album_url, tmp_path)
    album.refresh()
    google_photos.photos = {"AF1QipBlue": BLUE, "AF1QipGreen": GREEN}
    google_photos.requests.clear()

    assert album.refresh()
    assert album.photos() == {"AF1QipBlue", "AF1QipGreen"}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["AF1QipBlue.jpg", "AF1QipGreen.jpg"]
    assert [r for r in google_photos.requests if r.startswith("/photo/")] == ["/photo/AF1QipGreen=w320-h240-rj"]


def test_photos_past_the_end_of_a_long_albums_page_are_kept(google_photos, tmp_path):
    google_photos.photos = {"AF1QipRed": RED, "AF1QipBlue": BLUE}
    album = PhotoAlbum(google_photos.album_url, tmp_path)
    album.refresh()
    google_photos.photos, google_photos.more = {"AF1QipBlue": BLUE}, True  # Red may still be in the album
    assert album.refresh()
    assert album.photos() == {"AF1QipRed", "AF1QipBlue"} and not album.complete


def test_photos_from_before_show_straight_away(tmp_path):
    Image.new("RGB", (8, 8)).save(tmp_path / "AF1QipOld.jpg")
    (tmp_path / ".AF1QipHalf.tmp").write_bytes(b"half a download")
    (tmp_path / "notes.txt").write_text("not a photo")
    album = PhotoAlbum("https://photos.example/share/album", tmp_path)
    assert album.photos() == {"AF1QipOld"}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["AF1QipOld.jpg", "notes.txt"]  # the half download is gone


@pytest.mark.parametrize(
    ("answer", "says"),
    [
        ((404, b"Not found."), "Google Photos doesn't know album_url. Check it in config.toml"),
        ((500, b"Oops"), "Google Photos answered 500"),
        ((200, b"<html>Something else entirely</html>"), "isn't a shared album"),
    ],
)
def test_problems_with_the_album_are_explained(google_photos, tmp_path, answer, says):
    google_photos.answer = answer
    album = PhotoAlbum(google_photos.album_url, tmp_path)
    assert not album.refresh()
    assert says in album.problem and album.count is None


class Landed:
    """What urlopen returns when Google sends the request somewhere other than the album."""

    def __init__(self, url):
        self.url = url

    def read(self, limit):
        return b"<html>Somewhere else</html>"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


@pytest.mark.parametrize(
    ("landed", "says"),
    [
        # Where Google sends an album's own address, rather than its share link.
        ("https://accounts.google.com/v3/signin/identifier?continue=x", "needs a Google sign-in, so it isn't a share"),
        ("https://consent.google.com/ml?continue=x", "asked about cookies"),
    ],
)
def test_pages_google_shows_instead_of_the_album_are_explained(monkeypatch, landed, says):
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: Landed(landed))
    with pytest.raises(ValueError, match=says):
        read_album("https://photos.google.com/album/AF1QipNotShared")


def test_album_url_must_be_a_web_address():
    with pytest.raises(ValueError, match="should be the album's share link"):
        read_album("photos.app.goo.gl/abc")


def test_keeps_its_photos_when_google_photos_cannot_be_reached(google_photos, tmp_path, caplog):
    google_photos.photos = {"AF1QipRed": RED}
    album = PhotoAlbum(google_photos.album_url, tmp_path)
    album.refresh()
    album.url = f"http://127.0.0.1:{free_port()}/share/AF1QipTheAlbum?key=k3y"  # nothing listening
    with caplog.at_level(logging.WARNING):
        assert not album.refresh()
        assert not album.refresh()
    assert album.photos() == {"AF1QipRed"}
    assert album.problem.startswith("Couldn't reach Google Photos")
    assert caplog.text.count("Can't update the photos") == 1  # said once, not every time

    album.url = google_photos.album_url
    assert album.refresh() and album.problem == ""


def test_something_that_isnt_a_photo_isnt_kept(google_photos, tmp_path):
    google_photos.photos = {"AF1QipRed": RED, "AF1QipBad": BLUE}
    google_photos.not_photos = {"AF1QipBad"}
    album = PhotoAlbum(google_photos.album_url, tmp_path)
    assert not album.refresh()
    assert album.photos() == {"AF1QipRed"}
    assert album.problem == (
        "Couldn't download 1 of the album's photos. Google Photos sent something that isn't a photo."
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == ["AF1QipRed.jpg"]

    google_photos.not_photos = set()  # tried again next time
    assert album.refresh() and album.photos() == {"AF1QipRed", "AF1QipBad"}


def test_explains_problems_on_this_machine_apart_from_the_network():
    assert explain(PermissionError(13, "Permission denied", "/home/pi/photos")) == (
        "Couldn't save the photos in /home/pi/photos (Permission denied)."
    )
    assert explain(ConnectionRefusedError(61, "Connection refused")) == (
        "Couldn't reach Google Photos (Connection refused)."
    )
    assert explain(TimeoutError()) == "Google Photos didn't answer in time."


def test_keeps_up_on_its_own_thread(google_photos, tmp_path):
    google_photos.photos = {"AF1QipRed": RED}
    album = PhotoAlbum(google_photos.album_url, tmp_path, refresh=0.05, retry=0.05)
    album.start()
    try:
        wait_until(lambda: album.photos() == {"AF1QipRed"})
        google_photos.photos["AF1QipBlue"] = BLUE
        wait_until(lambda: album.photos() == {"AF1QipRed", "AF1QipBlue"})
    finally:
        album.stop()
