"""Conversation history and remembered facts (kept on disk, per user)."""
import threading, time
from . import config

FILE = config.DATA_DIR / "memory.json"
_lock = threading.RLock()
MAX_HISTORY, MAX_FACTS = 40, 100


def _load():
    d = config.read_json(FILE, {})
    return {"history": d.get("history", []), "facts": d.get("facts", [])}


def add(role, text):
    with _lock:
        d = _load()
        d["history"].append({"role": role, "content": text, "t": int(time.time())})
        d["history"] = d["history"][-MAX_HISTORY:]
        config.write_json(FILE, d, private=True)


def recent(n=12):
    with _lock:
        h = [{"role": m["role"], "content": m["content"]} for m in _load()["history"][-n:]]
    while h and h[0]["role"] != "user":      # providers need a user message first
        h.pop(0)
    return h


def history():
    with _lock:
        return _load()["history"]


def facts():
    with _lock:
        return _load()["facts"]


def add_fact(text):
    with _lock:
        d = _load()
        text = text.strip()[:300]
        if text and text not in d["facts"]:
            d["facts"] = (d["facts"] + [text])[-MAX_FACTS:]
            config.write_json(FILE, d, private=True)


def clear():
    with _lock:
        d = _load()
        d["history"] = []
        config.write_json(FILE, d, private=True)
