"""The command line, against a stand-in for Athena (conftest.FakeAthena)."""

import pytest
from PIL import Image
from test_client import free_port

from pi_display_microservice.cli import main


def run(*args):
    with pytest.raises(SystemExit) as exited:
        main(list(args))
    return exited.value.code


def config(tmp_path, url):
    path = tmp_path / "config.toml"
    path.write_text(f'athena_url = "{url}"\n')
    return str(path)


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
