"""Run:  python tests/test_voice.py   (no network or audio hardware needed)"""
import http.server, io, json, os, socketserver, struct, subprocess, sys, tempfile, threading, time, wave, zipfile
import subprocess
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(); os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from assist5 import config, models, stt, voice, voice_setup, perms
from assist5.server import app

ok = fail = 0
def check(name, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print("  PASS", name)
    else: fail += 1; print("  FAIL", name, extra)

def make_wav(rate=16000, ch=1, secs=1.0):
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(ch); w.setsampwidth(2); w.setframerate(rate); w.writeframes(b"\x01\x02" * int(rate * secs * ch))
    return b.getvalue()

print("wake word")
check("'Hey Void, open firefox'", stt.strip_wake("Hey Void, open firefox") == (True, "open firefox"))
check("'void lock the screen'", stt.strip_wake("void lock the screen") == (True, "lock the screen"))
check("misheard 'avoid' still wakes", stt.strip_wake("avoid mute")[0] is True)
check("normal speech does not wake", stt.strip_wake("what a lovely day") == (False, "what a lovely day"))
check("bare wake word -> empty command", stt.strip_wake("hey void") == (True, ""))

print("audio validation")
check("good wav accepted", stt._check_wav(make_wav()) > 0.9)
for bad, label in ((make_wav(rate=44100), "44.1k rejected"), (make_wav(ch=2), "stereo rejected"), (b"not audio", "garbage rejected")):
    try: stt._check_wav(bad); check(label, False)
    except stt.VoiceError: check(label, True)

print("engine selection & privacy")
check("fresh v5 profile defaults speech engines to local", config.DEFAULTS["stt_engine"] == "local" and config.DEFAULTS["tts_engine"] == "local")
stt.local_ready = lambda: True
models.online = lambda: True
config.set_key("groq", "k"); config.update(stt_engine="auto")
check("push-to-talk + auto prefers local, keeps online fallback", stt.plan(False)[0] == ["local", "online"])
check("HANDS-FREE + auto stays local (room audio never uploaded)", stt.plan(True)[0] == ["local"])
config.update(stt_engine="online"); check("online-only", stt.plan(False)[0] == ["online"])
models.online = lambda: False
try: stt.transcribe(make_wav()); check("online-only offline raises", False)
except stt.VoiceError as e: check("online-only offline raises clear message", "internet" in str(e), str(e))
config.update(stt_engine="auto"); models.online = lambda: True
calls = []
stt.transcribe_online = lambda wav, pid: (_ for _ in ()).throw(stt.VoiceError("groq down"))
stt.transcribe_local = lambda wav: (calls.append("local") or "open firefox")
r = stt.transcribe(make_wav()); check("online fails -> falls back to local automatically", r == {"text": "open firefox", "engine": "local"}, r)
stt.transcribe_local = lambda wav: "thank you"; stt.transcribe_online = lambda w, p: "thank you"
check("Whisper silence hallucination dropped", stt.transcribe(make_wav(secs=1.0))["text"] == "")
check("tiny clips ignored", stt.transcribe(make_wav(secs=0.1))["text"] == "")

print("voice output")
_real_piper = voice._piper
voice.edge_ready = lambda: True; voice.piper_ready = lambda: True; voice.espeak_bin = lambda: "/usr/bin/espeak-ng"
models.online = lambda: True; config.update(tts_engine="auto")
check("auto: local voices first, online edge as fallback", voice.engines() == ["piper", "espeak", "edge"])
models.online = lambda: False; check("offline: piper then espeak", voice.engines() == ["piper", "espeak"])
config.update(tts_engine="local"); models.online = lambda: True; check("local-only never uses edge", "edge" not in voice.engines())
config.update(tts_engine="auto")
voice._piper = lambda t: (_ for _ in ()).throw(voice.VoiceError("piper broke"))
voice._espeak = lambda t: (b"RIFFxxxx", "audio/wav")
models.online = lambda: False
data, mime, eng = voice.synthesize("Hello **there** https://x.com"); check("engine failure falls through to espeak", eng == "espeak")
check("markdown/URLs stripped before speaking", voice.clean("**Hi** `x` https://a.b/c") == "Hi x link")
check("default voice is original British female profile", config.DEFAULTS["edge_voice"] == "en-GB-SoniaNeural" and config.DEFAULTS["piper_voice"] == "en_GB-alba-medium")
check("reply speech defaults on while microphone stays opt-in", config.DEFAULTS["voice_enabled"] is True and config.DEFAULTS["mic_enabled"] is False)
voice.espeak_bin = lambda: None; voice.piper_ready = lambda: False
try: voice.synthesize("hi"); check("no engine -> helpful error", False)
except voice.VoiceError as e: check("no engine -> helpful error", "Install" in str(e), str(e))

print("model download (vosk: against a local server; piper: official downloader, invoked via a stub)")
tmp = tempfile.mkdtemp()
with zipfile.ZipFile(os.path.join(tmp, "vosk.zip"), "w") as z: z.writestr(f"{stt.MODEL_NAME}/am/final.mdl", "x"); z.writestr(f"{stt.MODEL_NAME}/conf/mfcc.conf", "x")
class Q(http.server.SimpleHTTPRequestHandler):
    def __init__(s, *a, **k): super().__init__(*a, directory=tmp, **k)
    def log_message(s, *a): pass
srv = socketserver.TCPServer(("127.0.0.1", 0), Q); threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_address[1]
voice_setup.VOSK_URL = f"http://127.0.0.1:{port}/vosk.zip"
voice_setup.install_stt(); check("speech model downloaded + unpacked", voice_setup.PROGRESS["stt"]["state"] == "done" and (stt.MODEL_DIR / "am" / "final.mdl").exists(), voice_setup.PROGRESS["stt"])
voice_setup.VOSK_URL = f"http://127.0.0.1:{port}/missing.zip"; voice_setup.install_stt()
check("failed vosk download reports an error, no crash", voice_setup.PROGRESS["stt"]["state"] == "error")

# Piper: verify we call the officially documented downloader (piper.download_voices) with the right
# arguments, rather than guessing a Hugging Face URL ourselves (that was the actual cause of the bug).
voice_dir = os.path.join(tmp, "en", "en_GB", "alba", "medium")
os.makedirs(voice_dir, exist_ok=True)
model_bytes, metadata_bytes = b"m" * (voice_setup.PIPER_CHUNK_SIZE * 2 + 123), b'{"voice":"alba"}'
with open(os.path.join(voice_dir, "en_GB-alba-medium.onnx"), "wb") as f: f.write(model_bytes)
with open(os.path.join(voice_dir, "en_GB-alba-medium.onnx.json"), "wb") as f: f.write(metadata_bytes)
old_piper_base, old_piper_ready = voice_setup.PIPER_BASE, voice.piper_ready
old_head, old_get = voice_setup.requests.head, voice_setup.requests.get
voice_setup.PIPER_BASE = f"http://127.0.0.1:{port}"
voice.piper_ready = lambda: True
range_calls = []
class RangeResponse:
    def __init__(self, data, status=200, headers=None):
        self.data, self.status_code = data, status
        self.headers = headers or {"content-length": str(len(data))}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def raise_for_status(self):
        if self.status_code >= 400: raise voice_setup.requests.HTTPError(f"HTTP {self.status_code}")
    def iter_content(self, size):
        for offset in range(0, len(self.data), size): yield self.data[offset:offset + size]

def piper_data(url):
    return model_bytes if ".onnx?download=true" in url and not ".onnx.json" in url else metadata_bytes

def piper_head(url, **kwargs):
    if "/missing/" in url: return RangeResponse(b"", 404)
    return RangeResponse(b"", headers={"content-length": str(len(piper_data(url)))})

def piper_get(url, headers=None, **kwargs):
    if "/missing/" in url: return RangeResponse(b"", 404)
    data = piper_data(url)
    byte_range = (headers or {}).get("Range")
    if not byte_range: return RangeResponse(data)
    start, end = [int(value) for value in byte_range.removeprefix("bytes=").split("-")]
    range_calls.append((start, end))
    part = data[start:end + 1]
    return RangeResponse(part, 206, {"content-length": str(len(part)),
        "content-range": f"bytes {start}-{end}/{len(data)}"})

voice_setup.requests.head, voice_setup.requests.get = piper_head, piper_get
voice_setup.install_tts()
model_path = voice.PIPER_DIR / "en_GB-alba-medium.onnx"
check("Piper Alba voice downloads in verified ranges", voice_setup.PROGRESS["tts"]["state"] == "done" and
      model_path.read_bytes() == model_bytes and len(range_calls) >= 3)
model_path.unlink()
partial_path = model_path.with_name(model_path.name + ".part")
partial_path.write_bytes(model_bytes[:voice_setup.PIPER_CHUNK_SIZE + 100])
range_calls.clear()
voice_setup.install_tts()
check("interrupted Piper download resumes at the last verified chunk",
    voice_setup.PROGRESS["tts"]["state"] == "done" and model_path.read_bytes() == model_bytes and
    range_calls and range_calls[0][0] == voice_setup.PIPER_CHUNK_SIZE)
model_path.write_bytes(model_bytes[:10])
voice_setup.install_tts()
check("truncated non-empty voice model is repaired", voice_setup.PROGRESS["tts"]["state"] == "done" and model_path.read_bytes() == model_bytes)
voice_setup.PIPER_BASE = f"http://127.0.0.1:{port}/missing"
real_official = voice_setup._official_download
voice_setup._official_download = lambda n: (_ for _ in ()).throw(RuntimeError("stubbed: no official downloader here"))
voice_setup.install_tts()
check("failed Piper download reports an error", voice_setup.PROGRESS["tts"]["state"] == "error")
voice_setup._official_download = real_official
voice_setup.PIPER_BASE, voice.piper_ready = old_piper_base, old_piper_ready
voice_setup.requests.head, voice_setup.requests.get = old_head, old_get
srv.shutdown()

print("official-downloader fallback (so a changed Hugging Face URL layout can't break voice setup again)")
import requests as _rq
def _down(*a, **k): raise _rq.ConnectionError("host unreachable")
voice_setup.requests.head, voice_setup.requests.get = _down, _down
voice.piper_ready = lambda: True
voice_setup.PIPER_BASE = "http://127.0.0.1:9/nope"
mp = voice.PIPER_DIR / "en_GB-alba-medium.onnx"
for f in (mp, mp.with_name(mp.name + ".json"), mp.with_name(mp.name + ".part")):
    f.exists() and f.unlink()
seen = []
def fake_run(cmd, **kw):
    seen.append(cmd)
    if "piper.download_voices" in cmd:
        mp.write_bytes(b"model"); mp.with_name(mp.name + ".json").write_bytes(b"{}")
    return subprocess.CompletedProcess(cmd, 0, b"", b"")
real_run, voice_setup.subprocess.run = voice_setup.subprocess.run, fake_run
voice_setup.install_tts()
check("manual download fails -> Piper's own downloader is used and succeeds", voice_setup.PROGRESS["tts"]["state"] == "done" and
      seen and "piper.download_voices" in seen[0] and "en_GB-alba-medium" in seen[0] and "--download-dir" in seen[0], voice_setup.PROGRESS["tts"])
for f in (mp, mp.with_name(mp.name + ".json")): f.unlink()
voice_setup.subprocess.run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, b"", b"no network")
voice_setup.install_tts()
check("both routes fail -> clear error, no crash", voice_setup.PROGRESS["tts"]["state"] == "error" and "own downloader" in voice_setup.PROGRESS["tts"]["msg"], voice_setup.PROGRESS["tts"])
voice_setup.subprocess.run = real_run

print("voice model loads ONCE, not on every reply (the real cause of slow speech)")
stubdir = tempfile.mkdtemp(); os.makedirs(os.path.join(stubdir, "piper"))
open(os.path.join(stubdir, "piper", "__init__.py"), "w").write('''
LOADS = []
class PiperVoice:
    @classmethod
    def load(cls, path, *a, **k):
        LOADS.append(path); return cls()
    def synthesize_wav(self, text, wav):
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(22050); wav.writeframes(b"\\x00\\x01" * 2000)
''')
sys.path.insert(0, stubdir)
import importlib; importlib.invalidate_caches(); import piper as _piper_stub
mp.write_bytes(b"model"); voice._piper_unload()
voice._piper = _real_piper
outs = [voice._piper(f"reply {i}") for i in range(4)]
check("four replies -> the model was loaded exactly once", len(_piper_stub.LOADS) == 1, _piper_stub.LOADS)
check("each reply is valid WAV audio", all(m == "audio/wav" and d[:4] == b"RIFF" and len(d) > 44 for d, m in outs))
other = voice.PIPER_DIR / "en_GB-other-medium.onnx"; other.write_bytes(b"model2"); config.update(piper_voice="en_GB-other-medium")
voice._piper("hi"); check("switching the voice setting loads the new model", len(_piper_stub.LOADS) == 2 and _piper_stub.LOADS[-1].endswith("other-medium.onnx"))
config.update(piper_voice="en_GB-alba-medium")
voice._piper_unload(); _piper_stub.LOADS.clear(); config.update(tts_engine="local")
voice.prewarm()
for _ in range(40):
    if _piper_stub.LOADS: break
    time.sleep(0.05)
check("startup prewarm loads the voice in the background", len(_piper_stub.LOADS) == 1)
voice._piper("first spoken reply"); check("...and the first reply then reuses it (no second load)", len(_piper_stub.LOADS) == 1)
real_in, real_sub = voice._piper_inprocess, voice._piper_subprocess
voice._piper_inprocess = lambda t: (_ for _ in ()).throw(AttributeError("old piper has no synthesize_wav"))
voice._piper_subprocess = lambda t, m: b"RIFF-from-subprocess"
check("old piper API -> falls back to the subprocess route instead of failing", voice._piper("x") == (b"RIFF-from-subprocess", "audio/wav"))
voice._piper_inprocess, voice._piper_subprocess = real_in, real_sub
sys.path.remove(stubdir); mp.unlink(); other.unlink(); voice.piper_ready = lambda: False

print("endpoints")
H = {"X-Requested-With": "fetch"}
c = app.test_client()
c.post("/api/auth/setup", json={"password": "abcdef"}, headers=H, environ_base={"REMOTE_ADDR": "127.0.0.1"}); config.update(setup_done=True)
wav = make_wav()
stt.local_ready = lambda: True; stt.transcribe_local = lambda w: "hey void turn the volume down"; stt.transcribe_online = lambda w, p: ""
config.update(mic_enabled=False, stt_engine="local")
r = c.post("/api/voice/transcribe", data=wav, headers={**H, "Content-Type": "audio/wav"}); check("mic OFF => server refuses audio (403)", r.status_code == 403)
config.update(mic_enabled=True)
r = c.post("/api/voice/transcribe", data=wav, headers={**H, "Content-Type": "audio/wav"})
check("mic ON => transcript + wake parsed", r.status_code == 200 and r.json["woke"] and r.json["command"] == "turn the volume down", r.json)
r = c.post("/api/voice/transcribe", data=b"junk", headers={**H, "Content-Type": "audio/wav"}); check("junk audio => clean 503 error", r.status_code == 503 and "error" in r.json)
voice.synthesize = lambda t: (b"RIFF", "audio/wav", "espeak")
r = c.post("/api/voice/speak", json={"text": "hi"}, headers=H); check("speak returns audio", r.status_code == 200 and r.mimetype == "audio/wav" and r.headers["X-Voice-Engine"] == "espeak")
c2 = app.test_client()
check("voice endpoints need login", all(c2.post(p, data=b"x", headers=H).status_code == 401 for p in ("/api/voice/transcribe", "/api/voice/speak", "/api/voice/install")))
check("CSP allows only own scripts + blob audio; mic limited to self", "script-src 'self'" in r.headers["Content-Security-Policy"] and "media-src 'self' blob:" in r.headers["Content-Security-Policy"] and "microphone=(self)" in r.headers["Permissions-Policy"])
r = c.get("/api/voice/status"); check("status reports readiness", r.status_code == 200 and "stt_ready" in r.json and "Sonia" in json.dumps(r.json["voices"]))
r = c.post("/api/settings", json={"edge_voice": "evil-voice", "listen_mode": "weird", "stt_engine": "local"}, headers=H); check("settings reject unknown values", config.get("edge_voice") == config.DEFAULTS["edge_voice"] and config.get("listen_mode") == "push")
check("browser JS files served", all(c.get(f"/{f}").status_code == 200 for f in ("voice.js", "blackhole5.js", "app.js", "style.css")))

print("browser WAV encoder (node)")
js = r'''
const V = require(process.argv[2]); const {encodeWav, resample} = V._test;
const rate = 48000, f = new Float32Array(rate); for (let i = 0; i < rate; i++) f[i] = 0.5 * Math.sin(2*Math.PI*440*i/rate);
(async () => { const blob = encodeWav([f.slice(0, 24000), f.slice(24000)], rate); const b = Buffer.from(await blob.arrayBuffer());
console.log(JSON.stringify({riff: b.toString("ascii",0,4), wave: b.toString("ascii",8,12), ch: b.readUInt16LE(22), rate: b.readUInt32LE(24), bits: b.readUInt16LE(34), bytes: b.length - 44, peak: Math.max(...Array.from({length: 200}, (_, i) => b.readInt16LE(44 + i*2)))})); })();'''
open(os.path.join(tmp, "t.js"), "w").write(js)
o = subprocess.run(["node", os.path.join(tmp, "t.js"), os.path.join(ROOT, "assist5/ui/voice.js")], capture_output=True, text=True)
try: j = json.loads(o.stdout)
except ValueError: j = {}; print(o.stderr[-300:])
check("WAV header: RIFF/WAVE, mono, 16 kHz, 16-bit", j.get("riff") == "RIFF" and j.get("wave") == "WAVE" and j.get("ch") == 1 and j.get("rate") == 16000 and j.get("bits") == 16, j)
check("48 kHz -> 16 kHz resample gives 1 s = 32000 bytes", j.get("bytes") == 32000, j)
check("audio level preserved (not silent / not clipped)", 8000 < j.get("peak", 0) < 17000, j)

print(f"\n{ok} passed, {fail} failed"); sys.exit(1 if fail else 0)
