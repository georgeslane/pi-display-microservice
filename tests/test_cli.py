"""The command line, against a stand-in for Athena (conftest.FakeAthena)."""

import pytest
from PIL import Image
from test_client import free_port

from pi_display_microservice.cli import main

RED, BLUE = (220, 30, 30), (30, 30, 220)
IDLE = (156, 180, 106)  # the status pill's dot while Athena is idle


def run(*args):
    with pytest.raises(SystemExit) as exited:
        main(list(args))
    return exited.value.code


def config(tmp_path, url, album_url=""):
    path = tmp_path / "config.toml"
    path.write_text(f'athena_url = "{url}"\nalbum_url = "{album_url}"\n')
    return str(path)


@pytest.fixture
def data(tmp_path, monkeypatch):
    """Where the board keeps the photos and scores: in this test's folder, rather than your home."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return tmp_path / "data" / "pi-display-microservice"


def test_draws_what_athena_is_doing_into_a_preview(tmp_path, athena):
    athena.set(state="working", task="What's the weather?", step="Using fetch", tools=["fetch"])
    out = tmp_path / "board.png"
    assert run("--config", config(tmp_path, athena.url), "run", "--preview", str(out), "--once") == 0
    with Image.open(out) as image:
        assert image.size == (320, 240)
        assert image.getpixel((152, 62)) == (92, 178, 255)  # the status pill's dot, in "working" blue


def test_demo_draws_without_athena(tmp_path):
    out = tmp_path / "demo.png"
    assert (
        run("--config", config(tmp_path, f"http://127.0.0.1:{free_port()}"), "demo", "--preview", str(out), "--once")
        == 0
    )
    assert Image.open(out).size == (320, 240)


def test_draws_the_albums_photos_into_a_preview(tmp_path, athena, google_photos, data):
    google_photos.photos = {"AF1QipRed": RED}
    out = tmp_path / "board.png"
    cfg = config(tmp_path, athena.url, google_photos.album_url)
    assert run("--config", cfg, "run", "--preview", str(out), "--once") == 0
    with Image.open(out) as image:
        assert all(abs(a - b) <= 8 for a, b in zip(image.getpixel((160, 120)), RED, strict=True))
    assert (data / "photos" / "AF1QipRed.jpg").exists()


def test_can_start_on_athenas_status_when_there_are_photos(tmp_path, athena, google_photos, data):
    google_photos.photos = {"AF1QipRed": RED}
    out = tmp_path / "board.png"
    cfg = config(tmp_path, athena.url, google_photos.album_url)
    assert run("--config", cfg, "run", "--preview", str(out), "--once", "--page", "status") == 0
    with Image.open(out) as image:
        assert image.getpixel((152, 62)) == IDLE


def test_there_are_no_photos_to_start_on_without_an_album(tmp_path, athena, capsys):
    out = tmp_path / "board.png"
    assert run("--config", config(tmp_path, athena.url), "run", "--preview", str(out), "--page", "photos") == 2
    assert "set album_url in config.toml" in capsys.readouterr().err and not out.exists()


def test_check_says_what_is_in_the_album(tmp_path, athena, google_photos, capsys):
    google_photos.photos = {"AF1QipRed": RED, "AF1QipBlue": BLUE}
    cfg = config(tmp_path, athena.url, google_photos.album_url)
    assert run("--config", cfg, "check") == 0
    assert capsys.readouterr().out.splitlines()[-1] == 'The album "Summer" has 2 photos.'

    google_photos.more = True
    assert run("--config", cfg, "check") == 0
    assert capsys.readouterr().out.splitlines()[-1] == (
        'The album "Summer" has more photos than its page lists, so the slideshow only has the first 2 photos.'
    )


def test_check_explains_an_album_it_cannot_read(tmp_path, athena, google_photos, capsys):
    google_photos.answer = (404, b"Not found.")
    assert run("--config", config(tmp_path, athena.url, google_photos.album_url), "check") == 1
    said = capsys.readouterr()
    assert "Athena answered" in said.out  # Athena is still checked
    assert "Google Photos doesn't know album_url" in said.err


def test_check_says_what_athena_answered(tmp_path, athena, capsys):
    athena.set(state="working", step="Using fetch")
    assert run("--config", config(tmp_path, athena.url), "check") == 0
    assert capsys.readouterr().out == f"Athena answered from {athena.url}/v1/status. Athena is working: Using fetch.\n"


def test_check_explains_when_nothing_answers(tmp_path, capsys):
    url = f"http://127.0.0.1:{free_port()}"
    assert run("--config", config(tmp_path, url), "check") == 1
    err = capsys.readouterr().err
    assert f"No status from {url}/v1/status." in err and "Is Athena running" in err


def test_a_bad_config_stops_it_with_a_reason(tmp_path, capsys):
    path = tmp_path / "config.toml"
    path.write_text("leds = true\n")
    assert run("--config", str(path), "check") == 2
    assert "leds" in capsys.readouterr().err
