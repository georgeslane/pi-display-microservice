#!/usr/bin/env bash
# What the git pre-commit hook runs (scripts/install-git-hooks.sh installs it): the check
# for secrets and personal data, then the tests. If either fails, nothing is committed.
#
#   bash scripts/pre-commit.sh
#
# The tests run on your working tree as it is, including changes you haven't staged.
# To skip all of this once: git commit --no-verify
set -euo pipefail

fail() {
  printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2
  exit 1
}

cd "$(git rev-parse --show-toplevel)"
bash scripts/check-secrets.sh pre-commit

# uv's installer puts it here, which a git app's PATH may not include.
PATH="$PATH:$HOME/.local/bin"
command -v uv >/dev/null 2>&1 || fail "uv isn't installed, so the tests can't run. See https://docs.astral.sh/uv/"
echo "Running the tests..." >&2
# --locked: like CI, stop if uv.lock is out of date with pyproject.toml, rather than change it.
uv run --locked python -m pytest -q || fail "The tests failed (above), so nothing was committed."
printf '\033[1;32m✓ The tests passed.\033[0m\n' >&2
