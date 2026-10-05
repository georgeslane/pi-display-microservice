"""The git hooks: every commit is checked for secrets, then must pass the tests. Runs real commits in a
throwaway clone, with stand-ins for gitleaks and uv that record what they were asked to do."""

import shutil
import subprocess

import pytest
from test_scripts import RECORD, REPO

pytestmark = pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None, reason="needs git, bash")

STUBS = {
    "gitleaks": RECORD + '[ -z "$SECRET_FOUND" ]\n',  # finds nothing, unless SECRET_FOUND is set
    "uv": RECORD + '[ -z "$TESTS_FAIL" ]\n',  # "runs the tests", which pass unless TESTS_FAIL is set
}


@pytest.fixture
def clone(tmp_path):
    repo, stubs, home = tmp_path / "repo", tmp_path / "stubs", tmp_path / "home"
    for d in (repo, stubs, home):
        d.mkdir()
    for item in ["scripts", ".gitignore", ".gitleaks.toml"]:
        (shutil.copytree if (REPO / item).is_dir() else shutil.copy)(REPO / item, repo / item)
    for name, script in STUBS.items():
        (stubs / name).write_text(script)
        (stubs / name).chmod(0o755)
    log = tmp_path / "log"
    log.touch()
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "HOME": str(home),
        "STUB_LOG": str(log),
        "GIT_CONFIG_GLOBAL": "/dev/null",  # none of this machine's git settings, or hooks
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }

    def run(*command, **flags):
        flags = {name.upper(): "1" for name, on in flags.items() if on}
        return subprocess.run(command, cwd=repo, env={**env, **flags}, capture_output=True, text=True, timeout=60)

    def commit(**flags):
        (repo / "notes.txt").write_text("hello\n")
        run("git", "add", "notes.txt")
        result = run("git", "commit", "-q", "-m", "Add notes", **flags)
        committed = run("git", "rev-parse", "--verify", "--quiet", "HEAD").returncode == 0
        return result, committed, log.read_text().splitlines()

    assert run("git", "init", "-q").returncode == 0
    installed = run("bash", "scripts/install-git-hooks.sh")
    assert installed.returncode == 0, installed.stderr
    return commit


def test_a_commit_is_checked_for_secrets_then_tested(clone):
    result, committed, calls = clone()
    assert result.returncode == 0 and committed, result.stderr
    assert calls[0].startswith("gitleaks git") and "--staged" in calls[0]
    assert calls[1:] == ["uv run --locked python -m pytest -q"]
    assert "The tests passed" in result.stderr


def test_a_commit_that_fails_the_tests_is_stopped(clone):
    result, committed, _ = clone(tests_fail=True)
    assert result.returncode != 0 and not committed
    assert "The tests failed (above), so nothing was committed." in result.stderr


def test_a_commit_with_a_secret_is_stopped_before_the_tests(clone):
    result, committed, calls = clone(secret_found=True)
    assert result.returncode != 0 and not committed
    assert not any(call.startswith("uv") for call in calls)
