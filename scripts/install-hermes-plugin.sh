#!/usr/bin/env bash
# Install the board's plugin into Hermes, so Hermes tells the board what it's doing
# (hermes_plugin/). Run it on the Pi, from this repo, as the user Hermes runs as:
#
#   bash scripts/install-hermes-plugin.sh
#
# The plugin is linked rather than copied, so scripts/update.sh keeps it up to date. It
# restarts Hermes' gateway to load the plugin, which ends anything Hermes is doing. Safe to
# re-run. To undo: hermes plugins disable pi-display-status, then delete
# ~/.hermes/plugins/pi-display-status and restart Hermes.
set -euo pipefail

NAME=pi-display-status
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGINS="${HERMES_HOME:-$HOME/.hermes}/plugins"
export PATH="$PATH:$HOME/.local/bin" # where Hermes' installer puts the hermes command

if (($#)); then
  echo "install-hermes-plugin.sh takes no options." >&2
  exit 2
fi
if ! command -v hermes >/dev/null 2>&1; then
  echo "Can't find the hermes command. Is Hermes installed, and are you the user it runs as?" >&2
  exit 1
fi
link="$PLUGINS/$NAME"
if [[ -e "$link" && ! -L "$link" ]]; then
  echo "$link already exists and isn't a link, so nothing was changed. Move it aside and run this again." >&2
  exit 1
fi

mkdir -p "$PLUGINS"
ln -sfn "$REPO_DIR/hermes_plugin" "$link"
echo "Linked $link to this repo's hermes_plugin."
hermes plugins enable "$NAME"
echo "Restarting Hermes' gateway, so it loads the plugin..."
hermes gateway restart
echo "Done. Check the board can hear Hermes: uv run pi-display-microservice check"
