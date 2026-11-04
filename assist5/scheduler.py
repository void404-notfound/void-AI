"""Scheduled tasks and a light system monitor (replaces Ema's autonomy engine).

Scheduled actions are plain-English instructions run through the normal brain,
so they obey the same permission settings as everything else.
"""
import datetime, threading, time, uuid
import psutil
from . import config, tools

FILE = config.DATA_DIR / "schedules.json"
_lock = threading.RLock()


def load():
    return config.read_json(FILE, [])


def add(label, time_str, action, days="daily"):
    h, m = time_str.split(":")
    entry = {"id": uuid.uuid4().hex[:6], "label": label[:60], "time": f"{int(h):02d}:{m}",
             "action": action[:300], "days": (days or "daily").lower(), "last_run": None}
    with _lock:
        items = load() + [entry]
        config.write_json(FILE, items)
    return entry


def remove(sid):
    with _lock:
        items = load()
        kept = [s for s in items if s["id"] != sid]
        config.write_json(FILE, kept)
        return len(kept) < len(items)


def _day_ok(days):
    today = datetime.datetime.now().strftime("%A").lower()
    return (days == "daily" or (days == "weekdays" and today not in ("saturday", "sunday"))
            or (days == "weekend" and today in ("saturday", "sunday")) or days == today)


def notify(msg):
    if tools.have("notify-send"):
        tools.run(["notify-send", config.APP_NAME, msg])


def _run_due():
    from . import brain
    now = datetime.datetime.now()
    hhmm, today = now.strftime("%H:%M"), now.strftime("%Y-%m-%d")
    with _lock:
        items = load()
        due = [s for s in items if s["time"] == hhmm and s["last_run"] != today and _day_ok(s["days"])]
        for s in due:
            s["last_run"] = today
        if due:
            config.write_json(FILE, items)
    for s in due:
        def job(s=s):
            r = brain.handle(s["action"], source="schedule")
            notify(f"{s['label']}: {r['reply'][:120]}")
        threading.Thread(target=job, daemon=True).start()


def _loop():
    last_alert = 0
    while True:
        try:
            _run_due()
            b = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
            if b and not b.power_plugged and b.percent <= 15 and time.time() - last_alert > 900:
                notify(f"Battery is at {b.percent:.0f}%. Plug in soon.")
                last_alert = time.time()
        except Exception:
            pass
        time.sleep(20)


def start():
    threading.Thread(target=_loop, daemon=True, name="scheduler").start()
