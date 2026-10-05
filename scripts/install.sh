#!/usr/bin/env bash
# Set up pi-display-microservice, Athena's status board, on a Raspberry Pi with a Pimoroni
# Display HAT Mini. Run it from this repo, as your normal user, over SSH:
#
#   bash scripts/install.sh
#
# It installs the screen's drivers, turns on SPI, lets you use the screen and its
# buttons, and starts the pi-display-microservice service. Safe to re-run: it skips what's
# already done and never overwrites your config.toml.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE=/etc/systemd/system/pi-display-microservice.service
OLD_SERVICE=pi-assistant-display # the board that used to be part of pi-assistant
BOOT_CONFIG="${BOOT_CONFIG:-/boot/firmware/config.txt}"
OVERLAY=dtoverlay=spi0-2cs,no_miso
SETTLE_SECONDS="${SETTLE_SECONDS:-3}" # how long the service must stay up to count as working

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!! %s\033[0m\n' "$*"; }

if (($#)); then
  echo "install.sh takes no options." >&2
  exit 2
fi
if [[ $EUID -eq 0 ]]; then
  echo "Run this as your normal user, not root (it uses sudo where needed)." >&2
  exit 1
fi
if [[ "$(uname -m)" != "aarch64" ]]; then
  warn "Expected a 64-bit Raspberry Pi OS (aarch64), found $(uname -m). Continuing anyway."
fi

step "Installing system packages"
sudo apt-get update -qq
# gcc and libc6-dev build the screen's SPI driver (spidev), which only comes as source.
sudo apt-get install -y -qq git curl ca-certificates gcc libc6-dev >/dev/null

step "Installing uv (Python package manager)"
if ! command -v uv >/dev/null 2>&1 && [[ ! -x "$HOME/.local/bin/uv" ]]; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"
uv --version

step "Installing the board and its drivers"
cd "$REPO_DIR"
uv sync --no-dev

step "Disabling git push (this clone only pulls updates)"
bash scripts/disable-git-push.sh

step "Creating config.toml"
if [[ ! -f config.toml ]]; then
  cp config.example.toml config.toml
  echo "Created config.toml. The defaults work with Athena's."
else
  echo "config.toml already exists; left it alone"
fi
chmod 600 config.toml # it can hold a token

step "Turning on SPI, and freeing GPIO 9 for the screen"
if command -v raspi-config >/dev/null 2>&1; then
  sudo raspi-config nonint do_spi 0 # turns SPI on straight away
else
  warn "raspi-config not found: turn on SPI yourself (dtparam=spi=on)"
fi
reboot=false
if [[ -f "$BOOT_CONFIG" ]]; then
  # The HAT uses GPIO 9, which SPI claims as MISO, to tell the screen pixels from
  # commands. Since kernel 6.18 a Pi 5 won't share a pin, so SPI is told to leave it
  # alone. The screen never sends data back, so MISO isn't needed.
  if ! grep -qE "^[[:space:]]*dtoverlay=spi0-2cs.*no_miso" "$BOOT_CONFIG"; then
    printf '\n[all]\n%s\n' "$OVERLAY" | sudo tee -a "$BOOT_CONFIG" >/dev/null
    echo "Added $OVERLAY to $BOOT_CONFIG"
    reboot=true
  fi
else
  warn "$BOOT_CONFIG not found. If the screen stays blank, add $OVERLAY to your boot config."
fi

step "Letting $USER use the screen and buttons"
for group in spi gpio; do
  if getent group "$group" >/dev/null; then
    sudo usermod -aG "$group" "$USER"
  fi
done

if systemctl cat "$OLD_SERVICE.service" >/dev/null 2>&1; then
  step "Retiring the old status board ($OLD_SERVICE)"
  sudo systemctl disable --now "$OLD_SERVICE" >/dev/null 2>&1 || true
  sudo rm -f "/etc/systemd/system/$OLD_SERVICE.service"
fi

step "Installing the pi-display-microservice service"
sed -e "s|@USER@|$USER|g" -e "s|@REPO_DIR@|$REPO_DIR|g" deploy/pi-display-microservice.service |
  sudo tee "$SERVICE" >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable pi-display-microservice >/dev/null 2>&1

if $reboot; then
  printf '\n\033[1;32mDone.\033[0m Reboot to finish: sudo reboot\n'
  echo "The pin change needs it. The board starts by itself afterwards."
  exit 0
fi
sudo systemctl restart pi-display-microservice
sleep "$SETTLE_SECONDS" # a problem with the screen stops it within a second or two
if ! systemctl is-active --quiet pi-display-microservice; then
  warn "pi-display-microservice stopped. Its log says why:"
  journalctl -u pi-display-microservice -n 15 --no-pager >&2 || true
  exit 1
fi
printf '\n\033[1;32mDone.\033[0m The board is running. It shows "offline" until it hears from Athena.\n'
echo "Check what it hears with: uv run pi-display-microservice check"
