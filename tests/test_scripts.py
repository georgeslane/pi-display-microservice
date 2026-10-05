"""Runs install.sh and update.sh with stand-ins for sudo, apt-get, uv, systemctl and friends,
which record what they were asked to do instead of changing the machine."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
OVERLAY = "dtoverlay=spi0-2cs,no_miso"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or os.geteuid() == 0, reason="needs bash, and install.sh refuses to run as root"
)

RECORD = '#!/bin/sh\necho "$(basename "$0") $*" >> "$STUB_LOG"\n'
STUBS = {
    "apt-get": RECORD,
    "curl": RECORD,
    "uv": RECORD,
    "raspi-config": RECORD,
    "getent": RECORD,
    "journalctl": RECORD,
    # sudo records the command; for `sudo tee [-a] FILE` it also does the writing, into the
    # sandbox: FILE itself if it's the boot config, otherwise a copy named after it.
    "sudo": RECORD
    + 'if [ "$1" = tee ]; then\n'
    + '  if [ "$2" = -a ]; then cat >> "$3"; else cat > "$STUB_FILES/$(basename "$2")"; fi\n'
    + "fi\n",
    # `systemctl cat` finds the old board only if OLD_BOARD is set; `is-active` fails if BOARD_FAILS is.
    "systemctl": RECORD
    + 'if [ "$1" = cat ]; then [ -n "$OLD_BOARD" ]; exit; fi\n'
    + 'if [ "$1" = is-active ]; then [ -z "$BOARD_FAILS" ]; exit; fi\n',
}


@pytest.fixture
def sandbox(tmp_path):
    repo = tmp_path / "pi-display-microservice"
    for item in ["scripts", "deploy", "config.example.toml"]:
        (shutil.copytree if (REPO / item).is_dir() else shutil.copy)(REPO / item, repo / item)
    stubs, git_stub, files, home = (tmp_path / d for d in ("stubs", "git-stub", "written", "home"))
    for d in (stubs, git_stub, files, home):
        d.mkdir()
    for path, script in [*((stubs / name, script) for name, script in STUBS.items()), (git_stub / "git", RECORD)]:
        path.write_text(script)
        path.chmod(0o755)
    boot = tmp_path / "config.txt"
    boot.write_text("[all]\ndtparam=spi=on\n")
    log = tmp_path / "log"
    log.touch()
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "HOME": str(home),
        "USER": "pi",
        "STUB_LOG": str(log),
        "STUB_FILES": str(files),
        "BOOT_CONFIG": str(boot),
        "SETTLE_SECONDS": "0",
        "GIT_CEILING_DIRECTORIES": str(tmp_path),
    }

    def run(script, *args, fake_git=False, **flags):
        path = f"{git_stub}:{env['PATH']}" if fake_git else env["PATH"]  # install.sh needs the real git
        result = subprocess.run(
            ["bash", f"scripts/{script}", *args],
            cwd=repo,
            env={**env, "PATH": path, **{name.upper(): "1" for name, on in flags.items() if on}},
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result, log.read_text().splitlines()

    return repo, files, boot, run


def test_first_install_frees_gpio_9_and_asks_for_a_reboot(sandbox):
    repo, files, boot, run = sandbox
    result, calls = run("install.sh")
    assert result.returncode == 0, result.stderr

    assert "uv sync --no-dev" in calls
    assert "sudo raspi-config nonint do_spi 0" in calls
    assert boot.read_text().endswith(f"\n[all]\n{OVERLAY}\n")
    assert {"sudo usermod -aG spi pi", "sudo usermod -aG gpio pi"} <= set(calls)
    assert "sudo systemctl enable pi-display-microservice" in calls
    # The pin change needs a reboot, so the board isn't started until then.
    assert not any("restart" in call for call in calls)
    assert "Reboot to finish" in result.stdout

    unit = (files / "pi-display-microservice.service").read_text()
    assert f"ExecStart={repo}/.venv/bin/pi-display-microservice run" in unit and "User=pi" in unit
    assert "@USER@" not in unit and "@REPO_DIR@" not in unit  # every placeholder filled in
    assert (repo / "config.toml").read_text() == (repo / "config.example.toml").read_text()
    assert (repo / "config.toml").stat().st_mode & 0o777 == 0o600


def test_with_gpio_9_already_free_it_starts_the_board(sandbox):
    repo, files, boot, run = sandbox
    boot.write_text(f"[all]\n{OVERLAY}\n")
    result, calls = run("install.sh")
    assert result.returncode == 0, result.stderr
    assert boot.read_text() == f"[all]\n{OVERLAY}\n"  # not added twice
    assert "sudo systemctl restart pi-display-microservice" in calls
    assert "systemctl is-active --quiet pi-display-microservice" in calls
    assert "The board is running" in result.stdout


def test_install_retires_the_board_that_was_part_of_pi_assistant(sandbox):
    _, _, _, run = sandbox
    result, calls = run("install.sh", old_board=True)
    assert result.returncode == 0, result.stderr
    assert "sudo systemctl disable --now pi-assistant-display" in calls
    assert "sudo rm -f /etc/systemd/system/pi-assistant-display.service" in calls


def test_install_shows_why_the_board_stopped(sandbox):
    _, _, boot, run = sandbox
    boot.write_text(f"{OVERLAY}\n")
    result, calls = run("install.sh", board_fails=True)
    assert result.returncode == 1
    assert "pi-display-microservice stopped" in result.stdout + result.stderr
    assert "journalctl -u pi-display-microservice -n 15 --no-pager" in calls


def test_reinstalling_keeps_your_config(sandbox):
    repo, _, _, run = sandbox
    (repo / "config.toml").write_text('token = "mine"\n')
    result, _ = run("install.sh")
    assert result.returncode == 0, result.stderr
    assert (repo / "config.toml").read_text() == 'token = "mine"\n'


def test_install_takes_no_options(sandbox):
    _, _, _, run = sandbox
    result, calls = run("install.sh", "--display")
    assert result.returncode == 2 and calls == []


def test_update(sandbox):
    _, _, _, run = sandbox
    result, calls = run("update.sh", fake_git=True)
    assert result.returncode == 0, result.stderr
    assert calls == ["git pull --ff-only", "uv sync --no-dev", "sudo systemctl restart pi-display-microservice"]
