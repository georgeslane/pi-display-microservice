#!/usr/bin/env bash
# Update pi-display-microservice: pull the latest code, update its packages and restart it.
# Run it from the repo, as your normal user, over SSH:
#
#   bash scripts/update.sh
set -euo pipefail

main() {
  cd "$(dirname "${BASH_SOURCE[0]}")/.."
  export PATH="$HOME/.local/bin:$PATH"
  local before
  before="$(git rev-parse HEAD)"
  git pull --ff-only
  uv sync --no-dev
  sudo systemctl restart pi-display-microservice
  echo "Updated and restarted pi-display-microservice."
  # Hermes loads its plugins when it starts, so a change to the board's plugin waits for a
  # restart. That ends anything Hermes is doing, so it's left to you.
  if ! git diff --quiet "$before" HEAD -- hermes_plugin; then
    echo "The board's plugin in Hermes changed too. To load it, when Hermes isn't busy: hermes gateway restart"
  fi
}

# Everything runs inside main(), which bash reads in full first, so `git pull` can
# safely change this file while it runs.
main "$@"
exit
