#!/usr/bin/env bash
# Update pi-display-microservice: pull the latest code, update its packages and restart it.
# Run it from the repo, as your normal user, over SSH:
#
#   bash scripts/update.sh
set -euo pipefail

main() {
  cd "$(dirname "${BASH_SOURCE[0]}")/.."
  export PATH="$HOME/.local/bin:$PATH"
  git pull --ff-only
  uv sync --no-dev
  sudo systemctl restart pi-display-microservice
  echo "Updated and restarted pi-display-microservice."
}

# Everything runs inside main(), which bash reads in full first, so `git pull` can
# safely change this file while it runs.
main "$@"
exit
