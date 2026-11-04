"""Permission levels, per-action approvals and the audit log.

Every tool belongs to a capability group. Each group is set by the USER to:
  off   - the AI cannot use it at all
  ask   - the AI must get an approval click for every single action
  allow - the AI may act freely (tools flagged `confirm` still ask)
The AI has no tool that can change these settings.
"""
import hashlib, json, os, threading, time, uuid
from . import config

GROUPS = {
    "system_info": ("System info", "Read CPU, RAM, battery, disk and running programs"),
    "audio":       ("Sound", "Change the volume and mute"),
    "display":     ("Screen", "Change screen brightness"),
    "network":     ("Wi-Fi & Bluetooth", "Turn them on/off, list and join networks"),
    "media":       ("Media", "Play, pause and skip; play videos with the media player"),
    "apps":        ("Apps", "Open and close programs"),
    "input":       ("Keyboard control", "Type text and press keys to operate other apps"),
    "files":       ("Files", "Browse, read and search files in the folders you allow"),
    "web":         ("Web", "Search the web and fetch weather"),
    "messaging":   ("Messaging", "Send messages on your behalf (Telegram)"),
    "power":       ("Power", "Lock, sleep, restart, shut down"),
    "shell":       ("Terminal", "Run shell commands. The most powerful; always asks first"),
    "scheduling":  ("Scheduled tasks", "Create or remove tasks that run later"),
}
LEVELS = ("off", "ask", "allow")
PERMS_FILE = config.CONFIG_DIR / "permissions.json"
AUDIT_FILE = config.DATA_DIR / "audit.log"
APPROVAL_TIMEOUT = 90


def levels():
    saved = config.read_json(PERMS_FILE, {})
    return {g: saved.get(g, "off") if saved.get(g) in LEVELS else "off" for g in GROUPS}


def level(group):
    return "allow" if group == "basic" else levels().get(group, "off")


def set_levels(new):
    cur = levels()
    for g, l in new.items():
        if g in GROUPS and l in LEVELS:
            cur[g] = l
    saved = config.read_json(PERMS_FILE, {})
    config.write_json(PERMS_FILE, {**saved, **cur}, private=True)
    return cur


def _action_fingerprint(tool_name, args):
    encoded = json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(f"{tool_name}\0{encoded}".encode("utf-8")).hexdigest()


def always_allowed(tool_name, args):
    fingerprint = _action_fingerprint(tool_name, args)
    return any(item.get("fingerprint") == fingerprint for item in config.read_json(PERMS_FILE, {}).get("always_actions", []))


def always_allowed_actions():
    items = config.read_json(PERMS_FILE, {}).get("always_actions", [])
    return [{"tool": item.get("tool", ""), "fingerprint": item.get("fingerprint", "")}
            for item in items if isinstance(item, dict) and item.get("tool") and item.get("fingerprint")]


def set_always_allowed(tool_name, args, allowed=True):
    saved = config.read_json(PERMS_FILE, {})
    fingerprint = _action_fingerprint(tool_name, args)
    items = [item for item in saved.get("always_actions", [])
             if isinstance(item, dict) and item.get("fingerprint") != fingerprint]
    if allowed:
        items.append({"tool": tool_name, "fingerprint": fingerprint})
    saved["always_actions"] = items
    config.write_json(PERMS_FILE, saved, private=True)
    return fingerprint


def revoke_always_allowed(fingerprint):
    saved = config.read_json(PERMS_FILE, {})
    items = saved.get("always_actions", [])
    kept = [item for item in items if not isinstance(item, dict) or item.get("fingerprint") != fingerprint]
    if len(kept) == len(items):
        return False
    saved["always_actions"] = kept
    config.write_json(PERMS_FILE, saved, private=True)
    return True


# ── audit ────────────────────────────────────────────────────
def audit(tool, args, outcome, source="user"):
    private_fields = {"password", "token", "key", "text", "command", "fact"}
    safe_args = {k: "[redacted]" if k.lower() in private_fields else v for k, v in args.items()}
    rec = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "tool": tool, "args": safe_args,
           "outcome": outcome, "source": source}
    try:
        fd = os.open(AUDIT_FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False)[:1500] + "\n")
    except OSError:
        pass


def audit_tail(n=60):
    try:
        with open(AUDIT_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()[-n:]
        return [json.loads(l) for l in reversed(lines) if l.strip()]
    except (OSError, ValueError):
        return []


# ── approvals ────────────────────────────────────────────────
_pending = {}
_plock = threading.Lock()


def request_approval(tool_name, description, args):
    """Blocks until the user answers (or the request times out => denied)."""
    aid = uuid.uuid4().hex[:8]
    item = {"id": aid, "tool": tool_name, "description": description, "args": args,
            "event": threading.Event(), "approved": False, "t": time.time()}
    with _plock:
        _pending[aid] = item
    item["event"].wait(APPROVAL_TIMEOUT)
    with _plock:
        _pending.pop(aid, None)
    return item["approved"]


def pending():
    with _plock:
        return [{k: ({name: "[hidden]" if name.lower() in {"password", "token", "key"} else value
                      for name, value in v.items()} if k == "args" else v)
                 for k, v in i.items() if k != "event"} for i in _pending.values()]


def decide(aid, approved, always=False):
    with _plock:
        item = _pending.get(aid)
        if not item:
            return False
        item["approved"] = bool(approved)
        if approved and always:
            set_always_allowed(item["tool"], item["args"])
        item["event"].set()
    return True
