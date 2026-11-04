#!/usr/bin/env bash
# Installs the assistant for the current user on Debian/Ubuntu/Parrot (run as your normal user).
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
NAME="$(python3 -c "import sys; sys.path.insert(0,'$DIR'); from assist5 import config; print(config.APP_ID)")"

# remove a previous install under an old name (settings/keys are migrated automatically)
for old in kora void4; do
  if [ "$old" != "$NAME" ] && [ -f ~/.config/systemd/user/$old.service ]; then
    systemctl --user disable --now $old.service || true
    rm -f ~/.config/systemd/user/$old.service ~/.local/share/applications/$old.desktop
  fi
done

echo "==> Installing system tools (needs sudo)"
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip \
  pulseaudio-utils alsa-utils playerctl brightnessctl xdotool network-manager rfkill bluez \
  mpv espeak-ng libnotify-bin xdg-utils
sudo usermod -aG video "$USER" || true     # lets brightnessctl work without root

echo "==> Python environment"
python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install -q --upgrade pip
"$DIR/.venv/bin/pip" install -q -r "$DIR/requirements5.txt"

echo "==> Offline voice models (~100 MB: speech recognition + deep male voice)"
( cd "$DIR" && "$DIR/.venv/bin/python" -m assist5.voice_setup ) || echo "   (skipped: install later in Settings → Voice)"

if ! command -v ollama >/dev/null 2>&1 && apt-cache show ollama >/dev/null 2>&1; then
  echo "==> Installing Ollama from the configured Debian package repositories"
  sudo apt-get install -y ollama
fi
if command -v ollama >/dev/null 2>&1; then
  echo "==> Downloading the small offline chat model (~1.2 GB)"
  ollama pull qwen3.5:0.8b && "$DIR/.venv/bin/python" -c 'from assist5 import config; config.update(offline_llm=True)'
else
  echo "Ollama is unavailable from the configured repositories. Offline commands and voice are ready;"
  echo "install Ollama separately if you also want local free-form chat."
fi

echo "==> Autostart service + menu entry"
mkdir -p ~/.config/systemd/user ~/.local/share/applications
sed "s|__DIR__|$DIR|g" "$DIR/scripts/assistant.service" > ~/.config/systemd/user/$NAME.service
sed "s|__DIR__|$DIR|g; s|__NAME__|$NAME|g" "$DIR/scripts/assistant.desktop" > ~/.local/share/applications/$NAME.desktop
systemctl --user daemon-reload
systemctl --user enable --now "$NAME.service"

echo
echo "Done. Open http://127.0.0.1:5005  (or the '$NAME' icon in your app menu)."
echo "Log out and back in once so the 'video' group (brightness) takes effect."
