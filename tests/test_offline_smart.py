"""Run:  python tests/test_offline_smart.py   (the broader offline 'mini AI' phrasing coverage)"""
import os, sys, tempfile
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(); os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from assist5 import offline

ok = fail = 0
def check(name, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print("  PASS", name)
    else: fail += 1; print("  FAIL", name, extra)

print("phrasings the precise regex rules never covered")
cases = {
    "crank the volume": ("change_volume", {"amount": 10}),
    "make my laptop louder": ("change_volume", {"amount": 10}),
    "turn the speakers down": ("change_volume", {"amount": -10}),
    "turn the sound down a touch": ("change_volume", {"amount": -10}),
    "silence it": ("set_mute", {"muted": True}),
    "bring the sound back on": ("set_mute", {"muted": False}),
    "it's too dark in here": ("change_brightness", {"amount": 15}),
    "get wifi back on": ("wifi_set", {"enabled": True}),
    "disconnect from the internet": ("wifi_set", {"enabled": False}),
    "am i connected to the internet right now": ("wifi_status", {}),
    "hold on a sec": ("media_control", {"action": "pause"}),
    "keep playing": ("media_control", {"action": "play"}),
    "play the next one": ("media_control", {"action": "next"}),
    "play the last one": ("media_control", {"action": "previous"}),
    "lock up": ("lock_screen", {}),
    "nap time for the computer": ("suspend", {}),
    "turn the computer off completely": ("shutdown", {}),
    "start it over please": ("restart", {}),
    "how's the computer doing": ("system_status", {}),
    "how much battery do i have left": ("system_status", {}),
    "tell me the time right now": ("current_time", {}),
    "fire up the calculator": ("open_app", {"name": "the calculator"}),
    "pull up firefox for me": ("open_app", {"name": "firefox"}),
    "get rid of spotify": ("close_app", {"name": "spotify"}),
    "shut discord down": ("close_app", {"name": "discord"}),   # trailing word must not leak into the name
}
for text, want in cases.items():
    check(repr(text), offline.route(text) == want, offline.route(text))

print("precision: the broad layer must not override exact, unambiguous commands")
check("'open firefox' still opens the app, not a vaguer guess", offline.route("open firefox") == ("open_app", {"name": "firefox"}))
check("'mute' (exact) still works", offline.route("mute") == ("set_mute", {"muted": True}))
check("'volume 40' (exact) still sets a precise number", offline.route("volume 40") == ("set_volume", {"level": 40}))

print("it doesn't hallucinate a command for unrelated chatter")
for junk in ("purple elephants dance sideways", "what's the meaning of life", "write me a poem about autumn"):
    check(f"no false match for {junk!r}", offline.route(junk) is None, offline.route(junk))
check("substring overlap does not trigger a system action", offline.route("program update") is None)

print("recognised-but-unsupported requests say so plainly, rather than mis-firing another tool")
check("'log off' is recognised but declined cleanly", offline.handle("log off") == "I understood that, but there's no way for me to do it yet.")

print(f"\n{ok} passed, {fail} failed"); sys.exit(1 if fail else 0)
