"""Settings, API-key storage and paths.

To rename the assistant, change APP_NAME below. Nothing else needs editing.
Everything user-specific lives under ~/.config/<name>/ and ~/.local/share/<name>/,
never inside the program folder.
"""
import json, os, secrets, threading
from pathlib import Path

APP_NAME = "Void5"
LEGACY_IDS = []
APP_ID = APP_NAME.lower().replace(" ", "-")

_home = Path.home()
_CFG_BASE = Path(os.environ.get("XDG_CONFIG_HOME", _home / ".config"))
_DATA_BASE = Path(os.environ.get("XDG_DATA_HOME", _home / ".local" / "share"))
CONFIG_DIR = _CFG_BASE / APP_ID
DATA_DIR = _DATA_BASE / APP_ID
for _old in LEGACY_IDS:
    for _base, _new in ((_CFG_BASE, CONFIG_DIR), (_DATA_BASE, DATA_DIR)):
        if _old != APP_ID and (_base / _old).exists() and not _new.exists():
            (_base / _old).rename(_new)
for _d in (CONFIG_DIR, DATA_DIR):
    _d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(_d, 0o700)
    except OSError:
        pass

SETTINGS_FILE = CONFIG_DIR / "settings.json"
KEYS_FILE = CONFIG_DIR / "keys.json"        # API keys, chmod 600
AUTH_FILE = CONFIG_DIR / "auth.json"        # password hash, chmod 600
_lock = threading.RLock()

DEFAULTS = {
    "owner": "",
    "city": "",
    "mode": "auto",                  # auto | online | offline
    "voice_enabled": True,           # speak replies; microphone capture remains off by default
    "mic_enabled": False,            # microphone voice commands: OFF until the user turns them on
    "listen_mode": "push",           # push = tap the mic | handsfree = say "Void, ..."
    "stt_engine": "local",           # auto | online | local
    "tts_engine": "local",           # auto | online | local
    "edge_voice": "en-GB-SoniaNeural",
    "edge_pitch": "+0Hz",
    "edge_rate": "+0%",
    "piper_voice": "en_GB-alba-medium",
    "stt_language": "en",
    "offline_llm": False,            # use a small local Ollama model for chit-chat when offline
    "ollama_model": "qwen3.5:0.8b",
    "allow_lan": False,              # False = only this computer can open the UI
    "port": 5005,
    "provider_order": ["groq", "gemini", "openrouter", "openai", "anthropic", "deepseek", "mistral"],
    "models": {},                    # per-provider model name overrides (any provider can use any model string)
    "custom_providers": {},          # user-added OpenAI-compatible providers: {id: {label, base, model}}
    "file_roots": [str(_home)],
    "telegram_contacts": {},         # {"mum": "123456789"}
    "setup_done": False,
}


def read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, data, private=False):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with _lock:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        if private:
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)


def settings():
    with _lock:
        return {**DEFAULTS, **read_json(SETTINGS_FILE, {})}


def get(key):
    return settings().get(key, DEFAULTS.get(key))


def update(**kw):
    with _lock:
        s = settings()
        s.update({k: v for k, v in kw.items() if k in DEFAULTS})
        write_json(SETTINGS_FILE, s)
        return s


# ── API keys ─────────────────────────────────────────────────
def get_key(name):
    env = os.environ.get(f"{name.upper()}_API_KEY")
    if env:
        return env
    return read_json(KEYS_FILE, {}).get(name, "")


def set_key(name, value):
    with _lock:
        keys = read_json(KEYS_FILE, {})
        if value:
            keys[name] = value.strip()
        else:
            keys.pop(name, None)
        write_json(KEYS_FILE, keys, private=True)


def _migrate_legacy_keys():
    if KEYS_FILE.exists():
        return
    for old_id in ("void", "void2", "void3", "void4", "kora", "ema"):
        old_file = _CFG_BASE / old_id / "keys.json"
        old_keys = read_json(old_file, {})
        if isinstance(old_keys, dict):
            keys = {str(k): str(v) for k, v in old_keys.items() if isinstance(v, str) and v.strip()}
            if keys:
                write_json(KEYS_FILE, keys, private=True)
                return


_migrate_legacy_keys()


def has_key(name):
    return bool(get_key(name))


# ── custom OpenAI-compatible providers (so Void isn't locked to one named model) ──
def custom_providers():
    return read_json(CONFIG_DIR / "custom_providers.json", {})


def add_custom_provider(pid, label, base, model):
    with _lock:
        c = custom_providers()
        c[pid] = {"label": label[:40], "base": base[:300], "model": model[:120]}
        write_json(CONFIG_DIR / "custom_providers.json", c)
        return c[pid]


def remove_custom_provider(pid):
    with _lock:
        c = custom_providers()
        c.pop(pid, None)
        write_json(CONFIG_DIR / "custom_providers.json", c)
        set_key(pid, "")


def session_secret():
    f = CONFIG_DIR / "session.key"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
        os.chmod(f, 0o600)
    return f.read_text().strip()
