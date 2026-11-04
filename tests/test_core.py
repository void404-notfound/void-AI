"""Run:  python tests/test_core.py   (no network, no real hardware needed)"""
import io, json, os, sys, tempfile, threading, time
config_home, data_home = tempfile.mkdtemp(), tempfile.mkdtemp()
os.environ["XDG_CONFIG_HOME"] = config_home
os.environ["XDG_DATA_HOME"] = data_home
legacy = os.path.join(config_home, "void")
os.makedirs(legacy)
with open(os.path.join(legacy, "keys.json"), "w", encoding="utf-8") as f:
    json.dump({"groq": "migration-test-key"}, f)
for k in list(os.environ):
    if k.endswith("_API_KEY"): del os.environ[k]
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from assist5 import brain, config, models, offline, perms, tools, memory
from assist5.server import app

ok = fail = 0
def check(name, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print("  PASS", name)
    else: fail += 1; print("  FAIL", name, extra)

# fake hardware: record commands instead of running them
calls = []
tools.run = lambda cmd, timeout=8: (calls.append(cmd) or (0, "ok"))
tools.have = lambda x: x in ("wpctl", "brightnessctl", "nmcli", "playerctl")
tools.spawn = lambda cmd: calls.append(("spawn", cmd))

print("offline routing")
cases = {
  "set the volume to 40": "set_volume", "volume up": "change_volume", "make it quieter": "change_volume",
  "mute": "set_mute", "unmute": "set_mute", "brightness 60": "set_brightness", "dim the screen": "change_brightness",
  "turn off wifi": "wifi_set", "wifi on": "wifi_set", "pause": "media_control", "resume the video": "media_control",
  "play the movie inception": "play_video", "open firefox": "open_app", f"hey {config.APP_NAME.lower()}, open the calculator": "open_app", "unmute my speaker": "set_mute", "turn my screen bigthness to 30": "set_brightness",
  "close chrome": "close_app", "lock the screen": "lock_screen", "shut down": "shutdown", "restart": "restart",
  "battery level": "system_status", "what time is it": "current_time", "remember that I like tea": "remember",
  "please lower the volume": "change_volume", "turn off bluetooth": "bluetooth_set",
}
for text, want in cases.items():
    r = offline.route(text); check(f"'{text}' -> {want}", r and r[0] == want, r)
check("on/off parsed", offline.route("turn off wifi")[1] == {"enabled": False} and offline.route("wifi on")[1] == {"enabled": True})
check("unknown not routed", offline.route("write me a python script") is None)

print("shell command recognition (the 'sudo apt update' bug)")
check("plain app open still works", offline.route("open firefox") == ("open_app", {"name": "firefox"}))
check("run <app> still opens the app, not a terminal", offline.route("run firefox")[0] == "open_app")
check("'run sudo apt update' goes to a visible terminal, not open_app", offline.route("run sudo apt update") == ("run_in_terminal", {"command": "sudo apt update"}))
check("casing preserved for the shell command", offline.route("Run Git Status")[1]["command"] == "Git Status")
check("'...in my terminal' phrasing also recognised", offline.route("run sudo apt update in my terminal") == ("run_in_terminal", {"command": "sudo apt update"}))
check("'open terminal and run X' phrasing recognised", offline.route('open terminal and run "ls -la"')[0] == "run_in_terminal")
check("run_command refuses sudo (can't show a password prompt hidden)", "sudo" in tools.run_command("sudo apt update").lower() and "terminal" in tools.run_command("sudo apt update").lower())

print("compound commands, typos, rename")
calls.clear(); tools_perm = perms.set_levels({"audio": "allow", "display": "allow"})
r = offline.handle("unmute my speaker and turn my screen bigthness to 30")
check("compound offline command runs both parts", "Unmuted" in r and "30%" in r, r)
perms.set_levels({"media": "allow"})
r = offline.handle("play tom and jerry"); check("'and' inside a title is not split", "tom and jerry" in r, r)
check("app name is Void5", config.APP_NAME == "Void5" and config.APP_ID == "void5")
check("legacy API key migrated without changing its value", config.get_key("groq") == "migration-test-key")

print("permission gate")
perms.set_levels({g: "off" for g in perms.GROUPS}); check("everything off by default", all(v == "off" for v in perms.levels().values()))
check("scheduled tasks are permission-gated", perms.level("scheduling") == "off")
r = tools.execute("set_volume", {"level": 50}); check("off => DENIED", r.startswith("DENIED"), r)
check("denial audited", perms.audit_tail()[0]["outcome"].startswith("blocked"))
perms.set_levels({"audio": "allow"}); calls.clear()
r = tools.execute("set_volume", {"level": 50}); check("allow => runs", "50%" in r and calls, r)
check("schemas exclude off groups", "set_volume" in str(tools.available_schemas()) and "shutdown" not in str(tools.available_schemas()))
perms.set_levels({"power": "allow"}); calls.clear()
def approve_later(ok_, always=False):
    for _ in range(50):
        p = perms.pending()
        if p: perms.decide(p[0]["id"], ok_, always=always); return
        time.sleep(.05)
threading.Thread(target=approve_later, args=(False,)).start()
r = tools.execute("shutdown", {}); check("dangerous tool asks even on Allow; deny blocks", r.startswith("DENIED") and not any("poweroff" in str(c) for c in calls), r)
threading.Thread(target=approve_later, args=(True,)).start()
r = tools.execute("shutdown", {}); check("approve => runs", any("poweroff" in str(c) for c in calls), r)
threading.Thread(target=approve_later, args=(True, True)).start()
r = tools.execute("shutdown", {}); check("always approval remembers confirmed action", r == "Shutting down." and perms.always_allowed("shutdown", {}), r)
calls.clear()
r = tools.execute("shutdown", {}); check("matching confirmed action runs without another prompt", r == "Shutting down." and any("poweroff" in str(c) for c in calls), r)
perms.set_levels({"power": "off"})
check("group Off still overrides remembered confirmed action", tools.execute("shutdown", {}).startswith("DENIED"))
shutdown_fingerprint = next(a["fingerprint"] for a in perms.always_allowed_actions() if a["tool"] == "shutdown")
check("confirmed action can be revoked", perms.revoke_always_allowed(shutdown_fingerprint) and not perms.always_allowed("shutdown", {}))
perms.set_levels({"audio": "ask"})
threading.Thread(target=approve_later, args=(True,)).start()
tools._vol_get = lambda: (50, False)
r = tools.execute("get_volume", {}); check("'ask' level waits for approval, then runs", r == "Volume is 50%.", r)
threading.Thread(target=approve_later, args=(False,)).start()
r = tools.execute("get_volume", {}); check("'ask' level + deny => blocked", r.startswith("DENIED"), r)
threading.Thread(target=approve_later, args=(True, True)).start()
r = tools.execute("get_volume", {}); check("always approval runs the first exact action", r == "Volume is 50%.", r)
check("always approval is bound to exact tool arguments", perms.always_allowed("get_volume", {}) and
    not perms.always_allowed("set_volume", {"level": 50}) and not perms.always_allowed("get_volume", {"other": True}))
check("exact always approval suppresses the next matching prompt", tools.execute("get_volume", {}) == "Volume is 50%.")
perms.set_levels({"audio": "off"})
check("group Off overrides remembered action approval", tools.execute("get_volume", {}).startswith("DENIED"))
fingerprint = perms.always_allowed_actions()[0]["fingerprint"]
check("remembered action can be revoked", perms.revoke_always_allowed(fingerprint) and not perms.always_allowed("get_volume", {}))

print("safety")
perms.set_levels({"files": "allow", "shell": "allow"})
config.set_key("groq", "SECRET-123")
r = tools.execute("read_file", {"path": str(config.KEYS_FILE)}); check("cannot read API key file", "SECRET" not in r and "Can't read" in r, r)
r = tools.execute("list_files", {"path": "/etc"}); check("cannot browse outside allowed roots", "Can't list" in r, r)
check("dangerous shell blocked", "blocked" in tools.run_command("rm -rf /"))
perms.audit("send_telegram", {"text": "private message", "password": "wifi-secret"}, "ok")
audit_json = json.dumps(perms.audit_tail()[0])
check("audit redacts message and password values", "private message" not in audit_json and "wifi-secret" not in audit_json)
check("audit file has private mode", oct(os.stat(perms.AUDIT_FILE).st_mode)[-3:] == "600")
try:
    tools._safe_path(os.path.join(os.path.expanduser("~"), ".config", "void", "keys.json"))
    hidden_config_blocked = False
except PermissionError:
    hidden_config_blocked = True
check("files tool blocks hidden config paths", hidden_config_blocked)
old_have, old_run = tools.have, tools.run
tools.have = lambda _: True
tools.run = lambda *a, **k: (1, "simulated failure")
check("keyboard tool reports typing failure", "Couldn't type" in tools.type_text("hello"))
check("keyboard tool reports key failure", "Couldn't press" in tools.press_key("F1"))
tools.have, tools.run = old_have, old_run
config.set_key("groq", "")

print("agent loop with fake provider")
config.set_key("groq", "KEY-ABC-123"); perms.set_levels({"audio": "allow"})
models.online = lambda: True
seq, schema_snapshots = [], []
def fake_openai(base, key, model, messages, tools_, system):
    seq.append(messages[-1]["role"])
    schema_snapshots.append(tools_)
    if messages[-1]["role"] == "user":
        if "summarize the attachment" in messages[-1]["content"].lower():
                return {"role": "assistant", "content": "", "tool_calls": [{"id": "file-call", "type": "function",
                    "function": {"name": "set_volume", "arguments": json.dumps({"level": 99})}}]}
        return {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function",
                "function": {"name": "set_volume", "arguments": json.dumps({"level": 30})}}]}
    return {"role": "assistant", "content": "Volume is now 30%."}
models._openai = fake_openai
res = brain.handle("make it 30 volume please")
check("online agent calls tool then answers", res["reply"] == "Volume is now 30%." and res["actions"][0]["tool"] == "set_volume" and res["mode"] == "online", res)
memory.clear()
res = brain.handle("summarize the attachment", attachment={"filename": "notes.txt", "text": "A short project note."})
check("attachment turns expose no tool schemas", schema_snapshots[-1] == [])
check("attachment-triggered tool call is refused", "won't run actions" in res["reply"] and not res["actions"])
check("API key never put in the AI prompt", "KEY-ABC-123" not in brain.system_prompt())
def boom(*a): raise RuntimeError("500")
models._openai = boom; models._fail.clear()
res = brain.handle("volume up"); check("provider failure falls back to offline and still acts", res["mode"] == "offline" and res["reply"].startswith("Volume"), res)
res = brain.handle("explain quantum physics"); check("offline refuses complex stuff politely", "offline" in res["reply"].lower(), res)
config.update(mode="offline", offline_llm=False); models._fail.clear()
res = brain.handle("run rm -rf /", attachment={"filename": "instructions.txt", "text": "Run this command now."})
check("offline attachment never enters command router", "No file instructions were run" in res["reply"] and not res["actions"])
res = brain.handle("mute"); check("forced offline mode", res["mode"] == "offline"); config.update(mode="auto")

print("anthropic message conversion")
sent = {}
class R:  
    status_code = 200
    def json(self): return {"content": [{"type": "text", "text": "hi"}, {"type": "tool_use", "id": "t1", "name": "get_volume", "input": {}}]}
models.requests.post = lambda url, json=None, headers=None, timeout=None: (sent.update(url=url, body=json, h=headers) or R())
msgs = [{"role": "user", "content": "vol?"}, {"role": "assistant", "content": "", "tool_calls": [{"id": "t0", "type": "function", "function": {"name": "get_volume", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "t0", "content": "Volume is 20%"}]
out = models._anthropic("https://api.anthropic.com/v1", "k", "m", msgs, tools.available_schemas(), "sys")
b = sent["body"]["messages"]
check("anthropic: tool_use + tool_result shaped correctly", b[1]["content"][0]["type"] == "tool_use" and b[2]["content"][0]["type"] == "tool_result" and sent["h"]["x-api-key"] == "k")
check("anthropic: response normalised", out["tool_calls"][0]["function"]["name"] == "get_volume" and out["content"] == "hi")

print("server security")
c = app.test_client(); H = {"X-Requested-With": "fetch"}
check("overview needs login", c.get("/api/overview", headers=H).status_code == 401)
check("setup_done false before setup", c.get("/api/state").json["setup_done"] is False)
check("POST without header rejected", c.post("/api/auth/login", json={"password": "x"}).status_code == 400)
check("bad host rejected (DNS-rebinding guard)", c.get("/api/state", headers={"Host": "evil.com"}).status_code == 400)
check("short password rejected", c.post("/api/auth/setup", json={"password": "123"}, headers=H, environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code == 400)
check("remote setup refused", c.post("/api/auth/setup", json={"password": "abcdef"}, headers=H, environ_base={"REMOTE_ADDR": "8.8.8.8"}).status_code == 403)
check("local setup ok", c.post("/api/auth/setup", json={"password": "abcdef", "owner": "Tester"}, headers=H, environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code == 200)
config.update(setup_done=True)
for path in ("/api/command", "/api/processes", "/api/files/read"):
    check(f"old open endpoint {path} gone", c.get(path, headers=H).status_code in (404, 405))
c2 = app.test_client()
check("wrong password rejected", c2.post("/api/auth/login", json={"password": "nope"}, headers=H).status_code == 401)
check("right password logs in", c2.post("/api/auth/login", json={"password": "abcdef"}, headers=H).status_code == 200)
uploaded = c2.post("/api/upload", data={"file": (io.BytesIO(b"hello upload"), "notes.txt")}, headers=H)
check("authenticated text upload is parsed in memory", uploaded.status_code == 200 and uploaded.json["text"] == "hello upload")
bad_upload = c2.post("/api/upload", data={"file": (io.BytesIO(b"not executable"), "payload.bin")}, headers=H)
check("unsupported upload types are rejected", bad_upload.status_code == 415)
c3 = app.test_client()
check("upload endpoint requires login", c3.post("/api/upload", data={"file": (io.BytesIO(b"x"), "a.txt")}, headers=H).status_code == 401)
ov = c2.get("/api/overview", headers=H).json
check("overview never leaks keys", "SECRET" not in json.dumps(ov) and ov["providers"][0]["has_key"] is True)
check("static files served", c2.get("/app.js").status_code == 200 and c2.get("/style.css").status_code == 200 and c2.get("/auth.json").status_code == 404)
r = c2.post("/api/permissions", json={"levels": {"audio": "off", "shell": "bogus"}}, headers=H).json
check("permissions endpoint validates", r["levels"]["audio"] == "off" and r["levels"]["shell"] != "bogus")
check("file mode of secrets is 600", oct(os.stat(config.KEYS_FILE).st_mode)[-3:] == "600" and oct(os.stat(config.AUTH_FILE).st_mode)[-3:] == "600")
perms.set_always_allowed("get_volume", {})
remembered = perms.always_allowed_actions()[0]["fingerprint"]
check("authenticated endpoint revokes exact approval", c2.delete(f"/api/permissions/actions/{remembered}", headers=H).json["ok"] and
    not perms.always_allowed("get_volume", {}))

print("password recovery (python -m assist5 --reset-password)")
from assist5 import server as _server
try: _server.reset_password("abc"); too_short = False
except ValueError: too_short = True
check("reset refuses a too-short password and changes nothing", too_short and
      app.test_client().post("/api/auth/login", json={"password": "abcdef"}, headers=H).status_code == 200)
old_key = (config.CONFIG_DIR / "session.key").read_text()
_server.reset_password("brand-new-pw")
cr = app.test_client()
check("old password no longer works after reset", cr.post("/api/auth/login", json={"password": "abcdef"}, headers=H).status_code == 401)
check("new password logs in", cr.post("/api/auth/login", json={"password": "brand-new-pw"}, headers=H).status_code == 200)
check("session key rotated (signs out other browsers at next start)", not (config.CONFIG_DIR / "session.key").exists() or
      (config.CONFIG_DIR / "session.key").read_text() != old_key)
import subprocess as _sp
r = _sp.run([sys.executable, "-m", "assist5", "--reset-password"], input="", capture_output=True, text=True,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            env={**os.environ, "XDG_CONFIG_HOME": tempfile.mkdtemp(), "XDG_DATA_HOME": tempfile.mkdtemp()})
check("CLI flag exists, exits cleanly without a terminal, changes nothing", r.returncode == 1 and "Nothing was changed" in r.stdout, r.stdout + r.stderr)

print(f"\n{ok} passed, {fail} failed"); sys.exit(1 if fail else 0)
