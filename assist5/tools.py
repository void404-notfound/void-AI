"""Everything the assistant can DO on Linux.

Each tool declares a capability group (see perms.py). `execute()` is the only
way tools get run, and it enforces the user's permission settings.
"""
import configparser, datetime, fnmatch, glob, html, json, os, re, shlex, shutil
import socket, subprocess, time
from pathlib import Path
from urllib.parse import quote, unquote, urlparse, parse_qs

import psutil
import requests

from . import config, memory, perms

TOOLS = {}


class Tool:
    def __init__(self, name, desc, group, props, required, confirm, fn):
        self.name, self.desc, self.group = name, desc, group
        self.props, self.required, self.confirm, self.fn = props, required, confirm, fn

    def schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.desc,
            "parameters": {"type": "object", "properties": self.props, "required": self.required}}}


def tool(group, desc, props=None, required=None, confirm=False):
    def deco(fn):
        p = props or {}
        TOOLS[fn.__name__] = Tool(fn.__name__, desc, group, p,
                                  list(p) if required is None else required, confirm, fn)
        return fn
    return deco


def run(cmd, timeout=8):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or p.stderr).strip()
    except FileNotFoundError:
        return 127, f"{cmd[0]} is not installed"
    except subprocess.TimeoutExpired:
        return 124, "timed out"


def have(x):
    return shutil.which(x) is not None


def spawn(cmd):
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


INT = {"type": "integer"}
STR = {"type": "string"}
BOOL = {"type": "boolean"}


# ═════════════════════════ basic (always allowed) ═════════════════════════
@tool("basic", "Get the current date and time.")
def current_time():
    return datetime.datetime.now().strftime("It's %I:%M %p on %A, %d %B %Y.")


@tool("basic", "Remember a lasting fact about the user (preferences, names, routines).",
      {"fact": {**STR, "description": "The fact, in one short sentence"}})
def remember(fact):
    memory.add_fact(fact)
    return "Got it, I'll remember that."


@tool("basic", "Save a note for the user.", {"text": STR})
def add_note(text):
    f = config.DATA_DIR / "notes.txt"
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(f"[{time.strftime('%Y-%m-%d %H:%M')}] {text}\n")
    return "Note saved."


@tool("basic", "Read the user's saved notes.")
def read_notes():
    f = config.DATA_DIR / "notes.txt"
    return f.read_text(encoding="utf-8")[-3000:] if f.exists() else "No notes yet."


@tool("scheduling", "Schedule a task. 'action' is a plain-English instruction you will carry out at that time "
      "(e.g. 'turn wifi off'). days: daily, weekdays, weekend, or a weekday name.",
      {"label": STR, "time": {**STR, "description": "24h HH:MM"}, "action": STR,
       "days": {**STR, "description": "daily|weekdays|weekend|monday..sunday"}},
    required=["label", "time", "action"], confirm=True)
def schedule_add(label, time, action, days="daily"):
    from . import scheduler
    if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", time):
        return "Time must look like 07:30."
    s = scheduler.add(label, time, action, days)
    return f"Scheduled '{label}' at {s['time']} ({days})."


@tool("scheduling", "List scheduled tasks.")
def schedule_list():
    from . import scheduler
    items = scheduler.load()
    return "\n".join(f"{s['id']}: {s['label']} at {s['time']} ({s['days']}) -> {s['action']}"
                     for s in items) or "No scheduled tasks."


@tool("scheduling", "Delete a scheduled task by id.", {"id": STR}, confirm=True)
def schedule_remove(id):
    from . import scheduler
    return "Removed." if scheduler.remove(id) else "No task with that id."


# ═════════════════════════ audio ═════════════════════════
def _vol_get():
    if have("wpctl"):
        rc, out = run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
        m = re.search(r"([\d.]+)", out)
        if rc == 0 and m:
            return round(float(m.group(1)) * 100), "MUTED" in out
    if have("pactl"):
        rc, out = run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
        m = re.search(r"(\d+)%", out)
        _, mu = run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"])
        if rc == 0 and m:
            return int(m.group(1)), "yes" in mu.lower()
    if have("amixer"):
        rc, out = run(["amixer", "sget", "Master"])
        m = re.search(r"\[(\d+)%\]", out)
        if rc == 0 and m:
            return int(m.group(1)), "[off]" in out
    return None


def _vol_set(pct):
    pct = max(0, min(100, int(pct)))
    attempts = [["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", f"{pct / 100:.2f}"],
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{pct}%"],
                ["amixer", "-q", "sset", "Master", f"{pct}%"]]
    for cmd in attempts:
        if have(cmd[0]) and run(cmd)[0] == 0:
            return pct
    return None


@tool("audio", "Get the current speaker volume.")
def get_volume():
    v = _vol_get()
    return "I couldn't read the volume (is PulseAudio/PipeWire running?)." if v is None else \
        f"Volume is {v[0]}%{' (muted)' if v[1] else ''}."


@tool("audio", "Set the speaker volume to an exact percentage.",
      {"level": {**INT, "description": "0-100"}})
def set_volume(level):
    r = _vol_set(level)
    return "I couldn't change the volume." if r is None else f"Volume set to {r}%."


@tool("audio", "Raise or lower the volume by an amount (negative lowers).",
      {"amount": {**INT, "description": "e.g. 10 or -10"}})
def change_volume(amount):
    cur = _vol_get()
    if cur is None:
        return "I couldn't read the volume."
    r = _vol_set(cur[0] + int(amount))
    return "I couldn't change the volume." if r is None else f"Volume {r}%."


@tool("audio", "Mute or unmute the speakers.", {"muted": BOOL})
def set_mute(muted):
    v = "1" if muted else "0"
    for cmd in (["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", v],
                ["pactl", "set-sink-mute", "@DEFAULT_SINK@", v],
                ["amixer", "-q", "sset", "Master", "mute" if muted else "unmute"]):
        if have(cmd[0]) and run(cmd)[0] == 0:
            return "Muted." if muted else "Unmuted."
    return "I couldn't change mute."


# ═════════════════════════ display ═════════════════════════
@tool("display", "Set screen brightness to a percentage (minimum 5 to avoid a black screen).",
      {"level": {**INT, "description": "5-100"}})
def set_brightness(level):
    level = max(5, min(100, int(level)))
    if have("brightnessctl") and run(["brightnessctl", "-q", "set", f"{level}%"])[0] == 0:
        return f"Brightness set to {level}%."
    if have("xbacklight") and run(["xbacklight", "-set", str(level)])[0] == 0:
        return f"Brightness set to {level}%."
    return "I couldn't change brightness (install 'brightnessctl' and make sure you're in the 'video' group)."


@tool("display", "Raise or lower brightness by an amount (negative lowers).", {"amount": INT})
def change_brightness(amount):
    cur = None
    if have("brightnessctl"):
        rc, out = run(["brightnessctl", "-m"])
        m = re.search(r",(\d+)%", out)
        if rc == 0 and m:
            cur = int(m.group(1))
    if cur is None:
        return "I couldn't read the current brightness."
    return set_brightness(cur + int(amount))


# ═════════════════════════ network ═════════════════════════
@tool("network", "Turn Wi-Fi on or off.", {"enabled": BOOL})
def wifi_set(enabled):
    if have("nmcli") and run(["nmcli", "radio", "wifi", "on" if enabled else "off"])[0] == 0:
        return f"Wi-Fi turned {'on' if enabled else 'off'}."
    if have("rfkill") and run(["rfkill", "unblock" if enabled else "block", "wifi"])[0] == 0:
        return f"Wi-Fi turned {'on' if enabled else 'off'}."
    return "I couldn't change Wi-Fi (needs NetworkManager)."


@tool("network", "Say whether Wi-Fi is on and which network is connected.")
def wifi_status():
    rc, out = run(["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"])
    if rc != 0:
        return "I couldn't read Wi-Fi status."
    active = [l.split(":", 1)[1] for l in out.splitlines() if l.startswith("yes:")]
    return f"Connected to {active[0]}." if active else "Wi-Fi is not connected to a network."


@tool("network", "List nearby Wi-Fi networks.")
def wifi_list():
    rc, out = run(["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list"], 12)
    if rc != 0:
        return "I couldn't scan for networks."
    rows = [l.split(":") for l in out.splitlines() if l and not l.startswith(":")]
    return "\n".join(f"{r[0]} ({r[1]}%) {r[2] if len(r) > 2 else ''}" for r in rows[:12]) or "No networks found."


@tool("network", "Connect to a Wi-Fi network.", {"ssid": STR, "password": STR}, required=["ssid"], confirm=True)
def wifi_connect(ssid, password=""):
    cmd = ["nmcli", "dev", "wifi", "connect", ssid] + (["password", password] if password else [])
    rc, out = run(cmd, 25)
    return f"Connected to {ssid}." if rc == 0 else f"Couldn't connect: {out[:150]}"


@tool("network", "Turn Bluetooth on or off.", {"enabled": BOOL})
def bluetooth_set(enabled):
    if have("rfkill") and run(["rfkill", "unblock" if enabled else "block", "bluetooth"])[0] == 0:
        return f"Bluetooth turned {'on' if enabled else 'off'}."
    if have("bluetoothctl") and run(["bluetoothctl", "power", "on" if enabled else "off"])[0] == 0:
        return f"Bluetooth turned {'on' if enabled else 'off'}."
    return "I couldn't change Bluetooth."


# ═════════════════════════ media ═════════════════════════
MPV_SOCK = str(config.DATA_DIR / "mpv.sock")
VIDEO_EXT = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".flv", ".wmv", ".mpg"}


def _mpv(*cmd):
    try:
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(1.5)
        s.connect(MPV_SOCK)
        s.sendall((json.dumps({"command": list(cmd)}) + "\n").encode())
        data = s.recv(4096)
        s.close()
        return data
    except OSError:
        return None


@tool("media", "Control whatever is playing: play, pause, toggle, next, previous, stop.",
      {"action": {**STR, "enum": ["play", "pause", "toggle", "next", "previous", "stop"]}})
def media_control(action):
    pc = {"play": "play", "pause": "pause", "toggle": "play-pause", "next": "next",
          "previous": "previous", "stop": "stop"}[action]
    if have("playerctl") and run(["playerctl", pc])[0] == 0:
        return f"Done ({action})."
    mp = {"play": ("set_property", "pause", False), "pause": ("set_property", "pause", True),
          "toggle": ("cycle", "pause"), "next": ("playlist-next",),
          "previous": ("playlist-prev",), "stop": ("quit",)}[action]
    if _mpv(*mp):
        return f"Done ({action})."
    return "Nothing seems to be playing."


@tool("media", "Skip forward or back in the playing video by seconds (negative = back).", {"seconds": INT})
def media_seek(seconds):
    return "Seeked." if _mpv("seek", int(seconds)) else "No video is playing."


def _find_video(query):
    q = query.lower().strip()
    p = Path(os.path.expanduser(query))
    if p.is_file():
        return str(p)
    roots = [Path.home() / d for d in ("Videos", "Movies", "Downloads", "Desktop")]
    best = None
    for root in roots:
        for dirpath, dirs, files in os.walk(root):
            if dirpath[len(str(root)):].count(os.sep) >= 3:
                dirs[:] = []
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in files:
                if Path(f).suffix.lower() in VIDEO_EXT and (not q or all(w in f.lower() for w in q.split())):
                    best = best or os.path.join(dirpath, f)
    return best


@tool("media", "Play a video from the user's Videos/Downloads folders by (part of) its name.", {"query": STR})
def play_video(query):
    path = _find_video(query)
    if not path:
        return f"I couldn't find a video matching '{query}' in Videos, Downloads or Desktop."
    player = next((p for p in ("mpv", "vlc", "celluloid") if have(p)), None)
    if not player:
        return "No video player found. Install one with: sudo apt install mpv"
    spawn(["mpv", f"--input-ipc-server={MPV_SOCK}", "--force-window=yes", path] if player == "mpv" else [player, path])
    return f"Playing {os.path.basename(path)}."


# ═════════════════════════ apps ═════════════════════════
_APP_CACHE = {"t": 0, "v": []}
ALIASES = {"browser": ["firefox", "chromium", "brave", "chrome"], "web": ["firefox", "chromium", "brave", "chrome"],
           "terminal": ["terminal", "konsole", "xterm", "alacritty", "kitty"],
           "files": ["files", "thunar", "nautilus", "caja", "pcmanfm", "dolphin"],
           "file manager": ["files", "thunar", "nautilus", "caja", "pcmanfm", "dolphin"],
           "editor": ["text editor", "gedit", "pluma", "mousepad", "kate", "code"],
           "calculator": ["calculator", "galculator", "kcalc"], "music": ["spotify", "rhythmbox", "audacious"],
           "video": ["vlc", "mpv", "celluloid"], "settings": ["settings", "control center", "system settings"]}


def _desktop_entries():
    if time.time() - _APP_CACHE["t"] < 120 and _APP_CACHE["v"]:
        return _APP_CACHE["v"]
    dirs = ["/usr/share/applications", "/usr/local/share/applications", "/var/lib/flatpak/exports/share/applications",
            "/var/lib/snapd/desktop/applications", str(Path.home() / ".local/share/applications"),
            str(Path.home() / ".local/share/flatpak/exports/share/applications")]
    out = []
    for d in dirs:
        for path in glob.glob(d + "/*.desktop"):
            cp = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                cp.read(path, encoding="utf-8")
                e = cp["Desktop Entry"]
            except Exception:
                continue
            if e.get("Type") != "Application" or e.get("NoDisplay", "").lower() == "true" or "Exec" not in e:
                continue
            out.append({"name": e.get("Name", ""), "exec": e["Exec"], "id": Path(path).stem,
                        "terminal": e.get("Terminal", "").lower() == "true"})
    _APP_CACHE.update(t=time.time(), v=out)
    return out


def _match_app(name):
    q = name.lower().strip()
    entries = _desktop_entries()
    cands = ALIASES.get(q, []) + [q]
    for c in cands:
        for test in (lambda e: e["name"].lower() == c, lambda e: e["id"].lower() == c,
                     lambda e: e["name"].lower().startswith(c), lambda e: c in e["name"].lower()):
            for e in entries:
                if test(e):
                    return e
    return None


@tool("apps", "Open an application by name (e.g. 'firefox', 'files', 'calculator').", {"name": STR})
def open_app(name):
    e = _match_app(name)
    if e:
        cmd = shlex.split(re.sub(r"\s?%[a-zA-Z]", "", e["exec"]))
        if e["terminal"]:
            cmd = ["x-terminal-emulator", "-e"] + cmd
        try:
            spawn(cmd)
            return f"Opening {e['name']}."
        except OSError as ex:
            return f"Couldn't start {e['name']}: {ex}"
    if have(name):
        spawn([name])
        return f"Opening {name}."
    return f"I couldn't find an app called '{name}'."


@tool("apps", "List installed applications.")
def list_apps():
    return ", ".join(sorted({e["name"] for e in _desktop_entries()})[:80]) or "No apps found."


PROTECTED = {"systemd", "init", "xorg", "x", "sshd", "dbus-daemon", "networkmanager", "gdm", "lightdm", "sddm"}


@tool("apps", "Close a running application by name (asks it to quit politely).", {"name": STR}, confirm=True)
def close_app(name):
    q, me, n = name.lower().strip(), psutil.Process().username(), 0
    for p in psutil.process_iter(["name", "username", "pid"]):
        try:
            pn = (p.info["name"] or "").lower()
            if q in pn and pn not in PROTECTED and p.info["username"] == me and p.pid != os.getpid():
                p.terminate()
                n += 1
        except psutil.Error:
            pass
    return f"Closed {name}." if n else f"'{name}' isn't running."


@tool("apps", "Force-stop a process by PID.", {"pid": INT}, confirm=True)
def kill_process(pid):
    try:
        p = psutil.Process(int(pid))
        if p.username() != psutil.Process().username() or (p.name() or "").lower() in PROTECTED:
            return "I'm not allowed to stop that process."
        p.kill()
        return f"Stopped process {pid}."
    except psutil.Error as e:
        return f"Couldn't stop it: {e}"


# ═════════════════════════ input (operate other apps) ═════════════════════════
def _need_xdotool():
    return None if have("xdotool") else "Keyboard control needs xdotool (sudo apt install xdotool) on an X11 session."


@tool("input", "Type text into the currently focused window.", {"text": STR},
      confirm=lambda a: "\n" in a.get("text", ""))
def type_text(text):
    missing = _need_xdotool()
    if missing:
        return missing
    rc, out = run(["xdotool", "type", "--delay", "15", "--", text], 20)
    return "Typed." if rc == 0 else f"Couldn't type text: {out[:160]}"


@tool("input", "Press a key or combo in the focused window, e.g. 'ctrl+s', 'alt+Tab', 'Return', 'F11'.",
      {"keys": STR}, confirm=lambda a: bool(re.search(r"(?i)return|enter|delete|ctrl\+alt|super", a.get("keys", ""))))
def press_key(keys):
    missing = _need_xdotool()
    if missing:
        return missing
    rc, out = run(["xdotool", "key", keys])
    return "Done." if rc == 0 else f"Couldn't press that key: {out[:160]}"


@tool("input", "Bring a window to the front by (part of) its title.", {"title": STR})
def focus_window(title):
    n = _need_xdotool()
    if n:
        return n
    rc, out = run(["xdotool", "search", "--name", title])
    ids = out.split()
    if rc != 0 or not ids:
        return f"No window matching '{title}'."
    run(["xdotool", "windowactivate", ids[0]])
    return "Window focused."


# ═════════════════════════ power ═════════════════════════
@tool("power", "Lock the screen.")
def lock_screen():
    for cmd in (["loginctl", "lock-session"], ["mate-screensaver-command", "-l"], ["xdg-screensaver", "lock"],
                ["xflock4"], ["gnome-screensaver-command", "-l"], ["dm-tool", "lock"]):
        if have(cmd[0]) and run(cmd)[0] == 0:
            return "Screen locked."
    return "I couldn't lock the screen."


@tool("power", "Put the computer to sleep.", confirm=True)
def suspend():
    spawn(["systemctl", "suspend"])
    return "Going to sleep."


@tool("power", "Shut the computer down.", confirm=True)
def shutdown():
    spawn(["systemctl", "poweroff"])
    return "Shutting down."


@tool("power", "Restart the computer.", confirm=True)
def restart():
    spawn(["systemctl", "reboot"])
    return "Restarting."


# ═════════════════════════ system info ═════════════════════════
@tool("system_info", "Report CPU, memory, disk, battery and uptime.")
def system_status():
    parts = [f"CPU {psutil.cpu_percent(interval=0.4):.0f}%",
             f"RAM {psutil.virtual_memory().percent:.0f}% used",
             f"disk {psutil.disk_usage('/').percent:.0f}% full"]
    b = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    if b:
        parts.append(f"battery {b.percent:.0f}%{' (charging)' if b.power_plugged else ''}")
    up = int(time.time() - psutil.boot_time())
    parts.append(f"up {up // 3600}h {up % 3600 // 60}m")
    return ", ".join(parts) + "."


@tool("system_info", "List the programs using the most CPU/memory.", {"count": INT}, required=[])
def top_processes(count=5):
    procs = []
    for p in psutil.process_iter(["pid", "name", "memory_percent"]):
        try:
            procs.append((p.info["memory_percent"] or 0, p.info["pid"], p.info["name"]))
        except psutil.Error:
            pass
    procs.sort(reverse=True)
    return "\n".join(f"{n} (pid {pid}) {m:.1f}% RAM" for m, pid, n in procs[:max(1, min(int(count), 15))])


# ═════════════════════════ files ═════════════════════════
SENSITIVE = {".ssh", ".gnupg", ".aws", ".kube", ".password-store"}


def _safe_path(p):
    path = Path(os.path.expanduser(p)).resolve()
    roots = [Path(r).expanduser().resolve() for r in config.get("file_roots")]
    if not any(path == r or r in path.parents for r in roots):
        raise PermissionError("that location is outside the folders you've allowed")
    cfg = config.CONFIG_DIR.resolve()
    if path == cfg or cfg in path.parents or SENSITIVE & set(path.parts) or any(part.startswith(".") for part in path.parts):
        raise PermissionError("that location is protected")
    return path


@tool("files", "List the contents of a folder.", {"path": STR})
def list_files(path="~"):
    try:
        p = _safe_path(path)
        items = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        return "\n".join(("[dir] " if i.is_dir() else "") + i.name for i in items if not i.name.startswith("."))[:3000] or "(empty)"
    except (OSError, PermissionError) as e:
        return f"Can't list that: {e}"


@tool("files", "Read a text file (or PDF) and return its contents.", {"path": STR})
def read_file(path):
    try:
        p = _safe_path(path)
        if p.suffix.lower() == ".pdf":
            from pypdf import PdfReader
            return "\n".join((pg.extract_text() or "") for pg in PdfReader(str(p)).pages[:15])[:6000]
        return p.read_text(encoding="utf-8", errors="replace")[:6000]
    except ImportError:
        return "PDF support needs: pip install pypdf"
    except (OSError, PermissionError) as e:
        return f"Can't read that: {e}"


@tool("files", "Find files by name under a folder.", {"query": STR, "folder": STR}, required=["query"])
def search_files(query, folder="~"):
    try:
        root = _safe_path(folder)
    except (OSError, PermissionError) as e:
        return f"Can't search there: {e}"
    q, hits, seen = query.lower(), [], 0
    for dp, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SENSITIVE]
        seen += 1
        if seen > 4000 or len(hits) >= 20:
            break
        for f in files:
            if q in f.lower() or fnmatch.fnmatch(f.lower(), q):
                hits.append(os.path.join(dp, f))
    return "\n".join(hits) or "No matching files."


@tool("files", "Move a file to the trash.", {"path": STR}, confirm=True)
def trash_file(path):
    try:
        p = _safe_path(path)
    except (OSError, PermissionError) as e:
        return f"Can't do that: {e}"
    rc, out = run(["gio", "trash", str(p)])
    return "Moved to trash." if rc == 0 else f"Couldn't trash it: {out[:120]}"


# ═════════════════════════ web ═════════════════════════
def _strip(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


@tool("web", "Get current weather for a city (uses the saved city if none given).", {"city": STR}, required=[])
def weather(city=""):
    city = city or config.get("city")
    if not city:
        return "Which city? You can also save one in Settings."
    try:
        key = config.get_key("openweather")
        if key:
            d = requests.get("https://api.openweathermap.org/data/2.5/weather",
                             params={"q": city, "appid": key, "units": "metric"}, timeout=8).json()
            return f"{city}: {d['weather'][0]['description']}, {d['main']['temp']:.0f}°C " \
                   f"(feels {d['main']['feels_like']:.0f}°C), humidity {d['main']['humidity']}%."
        d = requests.get(f"https://wttr.in/{quote(city)}?format=j1", timeout=8).json()["current_condition"][0]
        return f"{city}: {d['weatherDesc'][0]['value']}, {d['temp_C']}°C (feels {d['FeelsLikeC']}°C), " \
               f"humidity {d['humidity']}%, wind {d['windspeedKmph']} km/h."
    except Exception as e:
        return f"I couldn't get the weather ({type(e).__name__})."


@tool("web", "Search the web and return the top results.", {"query": STR})
def web_search(query):
    try:
        key = config.get_key("serpapi")
        if key:
            r = requests.get("https://serpapi.com/search", params={"q": query, "api_key": key, "num": 5}, timeout=8).json()
            res = r.get("organic_results", [])[:5]
            if res:
                return "\n".join(f"- {x.get('title')}: {x.get('snippet', '')} ({x.get('link')})" for x in res)
        r = requests.post("https://html.duckduckgo.com/html/", data={"q": query}, timeout=8,
                          headers={"User-Agent": "Mozilla/5.0"})
        links = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r.text, re.S)
        snips = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', r.text, re.S)
        out = []
        for i, (href, title) in enumerate(links[:5]):
            real = parse_qs(urlparse(href).query).get("uddg", [href])[0]
            out.append(f"- {_strip(title)}: {_strip(snips[i]) if i < len(snips) else ''} ({unquote(real)})")
        return "\n".join(out) or "No results."
    except Exception as e:
        return f"Search failed ({type(e).__name__})."


# ═════════════════════════ messaging ═════════════════════════
@tool("messaging", "Send a Telegram message to a saved contact name or numeric chat id.",
      {"contact": STR, "text": STR}, confirm=True)
def send_telegram(contact, text):
    token = config.get_key("telegram")
    if not token:
        return "No Telegram bot token saved. Add one in Settings → Keys."
    chat = config.get("telegram_contacts").get(contact.lower(), contact)
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": text}, timeout=10)
        return f"Sent to {contact}." if r.ok else f"Telegram refused it: {r.text[:120]}"
    except requests.RequestException as e:
        return f"Couldn't reach Telegram ({type(e).__name__})."


# ═════════════════════════ shell ═════════════════════════
TERMINALS = ("gnome-terminal", "konsole", "xfce4-terminal", "mate-terminal", "tilix",
            "lxterminal", "x-terminal-emulator", "xterm", "alacritty", "kitty")
BLOCKED = [r"rm\s+(-\w+\s+)*-?\w*r\w*\s+(/|~|\*)", r"\bmkfs", r"\bdd\s+if=", r":\(\)\s*\{", r">\s*/dev/sd",
           r"chmod\s+-R\s+\d+\s+/\s*$", r"\bcurl\b.*\|\s*(sudo\s+)?(ba)?sh", r"\bwget\b.*\|\s*(sudo\s+)?(ba)?sh"]


@tool("shell", "Run a shell command in the background and return its output as text. Good for quick, "
      "non-interactive commands (checking a version, listing something). The user must approve every command. "
      "Don't use this for anything that needs 'sudo' or might ask a question - use run_in_terminal instead, "
      "because a hidden background command can't show a password prompt and will just hang until it times out.",
      {"command": STR}, confirm=True)
def run_command(command):
    if any(re.search(p, command) for p in BLOCKED):
        return "That command is blocked for safety."
    if re.search(r"(?<!\w)sudo(?!\w)", command):
        return "This needs sudo, so it can't run hidden in the background (you'd never see the password prompt). " \
               "Use run_in_terminal so it opens in a visible window instead."
    try:
        p = subprocess.run(["bash", "-lc", command], capture_output=True, text=True, timeout=30)
        out = (p.stdout + p.stderr).strip()
        return f"(exit {p.returncode})\n{out[:3000]}"
    except subprocess.TimeoutExpired:
        return "The command took longer than 30 seconds and was stopped."


@tool("shell", "Open a visible terminal window and run a command in it. Use this (not run_command) for anything "
      "needing 'sudo', a password, confirmation (like apt), or that the user should watch run. The user must "
      "approve every command.", {"command": STR}, confirm=True)
def run_in_terminal(command):
    if any(re.search(p, command) for p in BLOCKED):
        return "That command is blocked for safety."
    term = next((t for t in TERMINALS if have(t)), None)
    if not term:
        return "No terminal emulator found."
    inner = f"{command}; echo; echo '--- finished (exit code' $? ') · press Enter to close ---'; read"
    try:
        if term == "gnome-terminal":
            spawn([term, "--", "bash", "-lc", inner])
        elif term in ("xterm", "alacritty", "kitty"):
            spawn([term, "-e", "bash", "-c", inner])
        else:
            spawn([term, "-e", "bash", "-lc", inner])
        return f"Opened a terminal running: {command}"
    except OSError as e:
        return f"Couldn't open a terminal: {e}"


# ═════════════════════════ the gate ═════════════════════════
def available_schemas():
    """Only tools whose group the user has not switched off are shown to the AI."""
    return [t.schema() for t in TOOLS.values() if perms.level(t.group) != "off"]


def execute(name, args, source="user"):
    t = TOOLS.get(name)
    if not t:
        return f"Unknown tool '{name}'."
    args = args if isinstance(args, dict) else {}
    lvl = perms.level(t.group)
    if lvl == "off":
        perms.audit(name, args, "blocked: group off", source)
        label = perms.GROUPS[t.group][0]
        return f"DENIED: the '{label}' capability is turned off. Tell the user they can enable it in Settings → Permissions."
    remembered = perms.always_allowed(name, args)
    need = not remembered and (lvl == "ask" or (t.confirm(args) if callable(t.confirm) else t.confirm))
    if need and not perms.request_approval(name, t.desc, args):
        perms.audit(name, args, "declined by user", source)
        return "DENIED: the user did not approve this action."
    try:
        clean = {k: v for k, v in args.items() if k in t.props}
        out = str(t.fn(**clean))
        perms.audit(name, args, "ok", source)
        return out[:4000]
    except TypeError as e:
        perms.audit(name, args, f"bad arguments: {e}", source)
        return f"Bad arguments for {name}: {e}"
    except Exception as e:
        perms.audit(name, args, f"error: {e}", source)
        return f"{name} failed: {e}"
