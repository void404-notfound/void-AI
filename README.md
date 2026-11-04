# Void5 — a Linux-native AI assistant

Versioned desktop assistant prototype. Rename the displayed name in `assist5/config.py`.

## Quick start (Parrot / Debian / Ubuntu)
```bash
./scripts/install.sh          # installs tools, offline voice models, and autostart
# then open http://127.0.0.1:5005 and follow the first-run setup
```
Or without services: `pip install -r requirements5.txt && python -m assist5`.

## What it can do
Volume, brightness, Wi-Fi/Bluetooth, play/pause/seek videos (mpv), open/close apps, type and press keys in
other apps (xdotool), lock/sleep/shutdown, files (read/search/trash), web search, weather, Telegram messages,
notes, scheduled tasks, shell commands (always asks), and text/PDF attachments for analysis. Add tools by writing one decorated function in `assist5/tools.py`.

## File attachments
Use the paperclip button to attach `.txt`, `.md`, `.csv`, `.json`, `.log`, `.py`, `.sh`, `.yaml`, `.yml`, or PDF
files (maximum 4 MB; PDF extraction is limited to 20 pages and text to 12,000 characters). Files are parsed in
memory and not saved. When you send the message, their text goes to the selected online provider or local Ollama.
Without either model available, Void explains that it cannot analyze the file; uploaded instructions are never
run as commands.

## Voice (microphone in, local female voice out)
* **Microphone off by default.** Turn it on in setup or Settings → Voice. Your browser asks too.
* **Tap to talk** or **hands-free** ("Void5, …"). Auto prefers local Vosk recognition; hands-free Auto never uploads ambient audio.
* **Speech recognition:** local = Vosk (offline); online = Whisper on Groq/OpenAI when explicitly selected.
* **Voice:** local = Piper "Alba" (British female), with a female espeak-ng fallback. Online Edge TTS offers
  Sonia, Libby, Aria, and Jenny. These are standard voices with a similar broad tone, not an imitation of a character.
* Setup downloads offline voice models (~100 MB). Or run `python -m assist5.voice_setup` or use Settings → Voice.
* Audio is captured in the browser, so no PortAudio/ffmpeg is needed.
* The Piper voice is loaded into memory **once** (and pre-warmed at startup) instead of reloading from disk for
  every reply, which was the main cause of slow speech. If the direct download of a voice fails, setup retries
  with Piper's own downloader (`python -m piper.download_voices`).

## Forgot your password?
Run `python -m assist5 --reset-password` (stop the assistant first). It asks for a new password twice, stores
only a salted hash, and signs out every other browser at the next start. Nothing else (settings, keys, notes) is touched.

## Look
Pure-black "Void5" theme with an animated black hole. The expensive parts (glow, accretion disk, stars) are
drawn once into cached layers at full resolution; each frame only moves a few hundred orbiting specks, at a capped
frame rate, and it pauses while the tab is hidden. Settings → General offers Full / Reduced / Off and an optional
Light theme.

## Online vs offline
* **Online:** your AI models - Groq, Gemini, OpenRouter, OpenAI, Claude, DeepSeek, Mistral, or any other
  OpenAI-compatible API you add yourself (Settings → Models → "Add any other provider") - tried in the order
  you set, with automatic fallback. No provider is locked to one fixed model name: every model field is
  editable, so you can point it at whatever a provider calls its latest model. The model chooses tools
  (real agent behaviour).
* **Offline:** volume, brightness, Wi-Fi/Bluetooth, media, apps, power, system status, time, and notes use
  deterministic intent rules; no language model is needed for those actions. Matching is local and fast.
* **Optional local chat:** install Ollama, then run `ollama pull qwen3.5:0.8b`; leave the Ollama service running.
  Void connects to its local API at `127.0.0.1:11434`. Select the same pulled tag and enable Small local model
  in Settings → General. No API key is needed.
* `qwen3.5:0.8b` downloads about 1.2 GB and typically needs roughly 2-3 GB RAM for short chats, including runtime
  overhead. It runs on CPU on the i5-2540M, so expect sustained high CPU use and slower generation. The
  `qwen2.5:0.5b` option downloads about 0.5 GB and is lighter but less capable. Actual memory and speed vary
  with context length, runtime, and other apps; these are estimates, not guarantees.
* The installer pulls the local chat model when Ollama is available from the configured apt repositories. If
  not, offline commands and voice still work; install Ollama separately to enable local free-form chat.
* **Telling them apart:** the status pill always says why it's in the mode it's in (no key, no internet, or
  a specific provider error), checked the same way Void actually talks to providers (so it won't disagree
  with your browser about whether you're online).

## Permissions
First-run setup asks, per capability: **Off / Ask / Allow**. A confirmation can allow once or remember that exact
tool-and-arguments combination; it does not authorize different arguments. Remembered approvals are listed and
revocable in Settings → Permissions. Group **Off** always blocks actions. Only you can change permissions; the AI
has no tool for it. Everything the AI does is logged (Settings → Activity).

## Security notes
* Listens on 127.0.0.1 only, password-protected. No public tunnel, no open CORS.
* API keys live in `~/.config/void5/keys.json` (mode 600), never in the program folder. On first startup, v5
  copies existing keys from a Void/Void2/Void3/Void4/Kora/EMA config folder only when the v5 key store does not exist.
  Values are never written into source files or printed by the migration.
* Conversation/action logs and config/data directories are private to the current user. Audit arguments redact
  message content, typed text, shell commands, passwords, and API-key fields.
* Attachments are held in memory only. They are sent for analysis only when you submit a chat; the active model
  choice determines whether that means your configured online provider or local Ollama.
* Web pages, files and tool output are treated as untrusted data by the AI.

## Layout
`assist5/tools.py` actions + permission gate · `brain.py` agent loop · `models.py` providers ·
`stt.py`/`voice.py`/`voice_setup.py` speech in/out · `offline.py` offline commands · `scheduler.py` timers · `server.py` API · `ui/` interface · `tests/test_core.py`

Tests: `python tests/test_core.py && python tests/test_offline_smart.py && python tests/test_models.py && python tests/test_voice.py`
