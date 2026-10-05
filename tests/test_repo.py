"""What keeps this repo safe to share: settings and personal details never reach git."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_settings_and_personal_details_stay_out_of_git(tmp_path):
    shutil.copy(REPO / ".gitignore", tmp_path / ".gitignore")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    paths = ["config.toml", ".personal-blocklist", ".venv/bin/python", "config.example.toml", "docs/board.png"]
    result = subprocess.run(
        ["git", "-c", "core.excludesFile=/dev/null", "check-ignore", "--no-index", *paths],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.stdout.split() == ["config.toml", ".personal-blocklist", ".venv/bin/python"]


def test_scripts_are_valid_bash():
    for script in sorted((REPO / "scripts").glob("*.sh")):
        assert subprocess.run(["bash", "-n", str(script)]).returncode == 0, script.name


@pytest.mark.skipif(shutil.which("gitleaks") is None, reason="needs gitleaks")
def test_an_albums_share_link_counts_as_a_secret(tmp_path):
    # Put together here, so this file doesn't hold one itself.
    links = [
        "https://photos.app.goo.gl/" + "Xy7Qw3Er5Ty9Ui1Op",
        "https://photos.google.com/share/" + "AF1QipExampleAlbum123" + "?key=" + "ExampleKey456",
    ]
    text = "".join(f"My album: {link}\n" for link in links) + "Not a link: https://photos.app.goo.gl/…\n"
    (tmp_path / "notes.md").write_text(text)
    result = subprocess.run(
        ["gitleaks", "dir", str(tmp_path), "--config", str(REPO / ".gitleaks.toml")]
        + ["--no-banner", "--report-format", "json", "--report-path", "-"],
        capture_output=True,
        text=True,
    )
    assert [finding["RuleID"] for finding in json.loads(result.stdout)] == ["google-photos-share-link"] * 2
