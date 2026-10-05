from pathlib import Path

import pytest

from pi_display_microservice.config import Config, ConfigError, data_dir, load_config

REPO = Path(__file__).resolve().parents[1]


def test_runs_without_a_config_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert load_config() == Config()
    assert Config().athena_url == "http://127.0.0.1:8091"  # Athena's own default


def test_the_example_is_the_defaults():
    assert load_config(REPO / "config.example.toml") == Config()


def test_reads_settings(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('athena_url = "http://athena.local:9000"\ntoken = "abc"\nled = false\nwait_seconds = 10\n')
    cfg = load_config(path)
    assert (cfg.athena_url, cfg.token, cfg.led, cfg.wait_seconds) == ("http://athena.local:9000", "abc", False, 10.0)


def test_the_photo_page_is_only_there_if_you_give_an_album(tmp_path):
    assert Config().album_url == ""
    path = tmp_path / "config.toml"
    path.write_text('album_url = "https://photos.example/share/album?key=k3y"\n')
    assert load_config(path).album_url == "https://photos.example/share/album?key=k3y"


def test_photos_and_scores_are_kept_in_the_users_data_folder(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert data_dir() == tmp_path / ".local" / "share" / "pi-display-microservice"
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "elsewhere"))
    assert data_dir() == tmp_path / "elsewhere" / "pi-display-microservice"


@pytest.mark.parametrize(
    ("text", "says"),
    [
        ("leds = false", "doesn't know: leds"),
        ('led = "no"', "led should be a bool"),
        ("token = 7", "token should be a str"),
        ("wait_seconds = true", "wait_seconds should be a float"),
        ('athena_url = "127.0.0.1:8091"', "should start with http://"),
        ("led = ", "isn't valid TOML"),
    ],
)
def test_explains_what_is_wrong(tmp_path, text, says):
    path = tmp_path / "config.toml"
    path.write_text(text + "\n")
    with pytest.raises(ConfigError, match=says):
        load_config(path)


def test_a_config_file_you_name_must_exist(tmp_path):
    with pytest.raises(ConfigError, match="no config file"):
        load_config(tmp_path / "nope.toml")
