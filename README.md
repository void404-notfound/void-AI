# Void — a Linux-native AI assistant

A cleaned-up, Linux-native rewrite of the Ema project. Rename it in one place:
`assistant/config.py` → `APP_NAME`.

## Quick start (Parrot / Debian / Ubuntu)
```bash
./scripts/install.sh          # installs tools, venv, autostart
# then open http://127.0.0.1:5000 and follow the first-run setup
```
Or without services: `pip install -r requirements.txt && python -m assistant`.

## What it can do
Volume, brightness, Wi-Fi/Bluetooth, play/pause/seek videos (mpv), open/close apps, type and press keys in
other apps (xdotool), lock/sleep/shutdown, files (read/search/trash), web search, weather, Telegram messages,
notes, scheduled tasks, shell commands (always asks). Add more by writing one decorated function in `tools.py`.

## Voice (microphone in, deep voice out)
* **Off by default.** Turn the microphone on in first-run setup or Settings → Voice. Your browser asks too.
* **Tap to talk** or **hands-free** ("Void, …"). Hands-free stays on the local engine so room audio is never uploaded.
* **Speech recognition:** online = Whisper on Groq/OpenAI (uses the same key); local = Vosk (offline, any CPU).
* **Voice:** online = Edge neural "Christopher" pitched down and slowed (a deep, cold male voice); local = Piper
  "ryan"; last resort = espeak-ng. Change the online voice in Settings → Voice. Press Esc to silence it.
* The installer downloads the offline models (~100 MB). Or: `python -m assistant.voice_setup`, or Settings → Voice.
* Audio is captured in the browser, so no PortAudio/ffmpeg is needed.

## Look
Pure-black "Void" theme with an animated black hole (accretion disk, lensing, infalling stars). Settings → General:
animation Full / Reduced / Off (use Reduced on older laptops), and an optional Light theme.

## Online vs offline
* **Online:** your AI models (Groq, Gemini, OpenRouter, OpenAI, Claude, DeepSeek, Mistral), tried in the order
  you set, with automatic fallback. The model chooses tools (real agent behaviour).
* **Offline:** no model needed. A fast command matcher handles everyday actions (volume, brightness, wifi,
  video pause/play, open apps, power, battery, time, notes). Optional: a tiny local Ollama model for chit-chat.

## Permissions
First-run setup asks, per capability: **Off / Ask / Allow**. Dangerous tools (shutdown, shell, messaging,
closing apps, trashing files) ask every time even under Allow. Only you can change permissions, in the UI;
the AI has no tool for it. Everything the AI does is logged (Settings → Activity).

## Security notes
* Listens on 127.0.0.1 only, password-protected. No public tunnel, no open CORS.
* API keys live in `~/.config/<name>/keys.json` (mode 600), never in the program folder.
* Web pages, files and tool output are treated as untrusted data by the AI.

## Layout
`assistant/tools.py` actions + permission gate · `brain.py` agent loop · `models.py` providers ·
`stt.py`/`voice.py`/`voice_setup.py` speech in/out · `offline.py` offline commands · `scheduler.py` timers · `server.py` API · `ui/` interface · `tests/test_core.py`

Tests: `python tests/test_core.py && python tests/test_voice.py`
