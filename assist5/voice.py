"""Text-to-speech with a clear, warm British female voice.

Online : Microsoft Edge neural voice (edge-tts), default 'Sonia'.
Local  : Piper neural voice (en_GB-alba-medium), fully offline and light on CPU.
Last   : espeak-ng female voice, available without model downloads.
"""
import asyncio, importlib.util, io, os, re, shutil, subprocess, sys, tempfile, threading, wave
from . import config, models, tools
from .stt import VoiceError

PIPER_DIR = config.DATA_DIR / "models" / "piper"
EDGE_VOICES = {"en-GB-SoniaNeural": "Sonia: British, clear (default)",
               "en-GB-LibbyNeural": "Libby: British, warm",
               "en-US-AriaNeural": "Aria: American, expressive",
               "en-US-JennyNeural": "Jenny: American, conversational"}
_lock = threading.Lock()
_piper_voice, _piper_key = None, None      # the loaded voice model, kept in memory between replies
_piper_load_lock = threading.Lock()
_piper_run_lock = threading.Lock()         # one synthesis at a time on the shared model


def clean(text):
    text = re.sub(r"https?://\S+", "link", text)
    text = re.sub(r"[*_`#>|~]", "", text)
    return re.sub(r"\s+", " ", text).strip()[:700]


def edge_ready():
    return importlib.util.find_spec("edge_tts") is not None


def piper_ready():
    return importlib.util.find_spec("piper") is not None and (PIPER_DIR / f"{config.get('piper_voice')}.onnx").exists()


def espeak_bin():
    return shutil.which("espeak-ng") or shutil.which("espeak")


def _edge(text):
    import edge_tts
    out = tempfile.mktemp(suffix=".mp3")
    try:
        async def go():
            await asyncio.wait_for(edge_tts.Communicate(text, config.get("edge_voice"), rate=config.get("edge_rate"),
                                                        pitch=config.get("edge_pitch")).save(out), 20)
        asyncio.run(go())
        with open(out, "rb") as f:
            return f.read(), "audio/mpeg"
    except Exception as e:
        raise VoiceError(f"Online voice failed ({type(e).__name__}: {str(e)[:80]}).")
    finally:
        if os.path.exists(out):
            os.remove(out)


def _piper_load():
    """Load the Piper voice model ONCE and keep it in memory.

    Loading takes seconds (Piper's docs say ~2.5-5.5 s). Launching `python -m piper` for every reply
    paid that cost each time; this pays it once per voice, and prewarm() does it at startup."""
    global _piper_voice, _piper_key
    model = PIPER_DIR / f"{config.get('piper_voice')}.onnx"
    with _piper_load_lock:
        if _piper_voice is None or _piper_key != str(model):
            from piper import PiperVoice
            _piper_voice, _piper_key = PiperVoice.load(str(model)), str(model)
        return _piper_voice


def _piper_unload():
    global _piper_voice, _piper_key
    with _piper_load_lock:
        _piper_voice = _piper_key = None


def prewarm():
    """Load the local voice in the background so the first spoken reply isn't slow."""
    def job():
        try:
            if config.get("tts_engine") in ("auto", "local") and piper_ready():
                _piper_load()
        except Exception:
            pass          # not fatal: the first real reply will load it (or fall back) and report any error
    threading.Thread(target=job, daemon=True).start()


def _piper_inprocess(text):
    voice_model = _piper_load()
    buf = io.BytesIO()
    with _piper_run_lock, wave.open(buf, "wb") as w:
        voice_model.synthesize_wav(text, w)
    data = buf.getvalue()
    if len(data) <= 44:
        raise VoiceError("Piper produced no audio.")
    return data


def _piper_subprocess(text, model):
    """Slow path (reloads the model on every call). Only used if the in-process API is unavailable."""
    out = tempfile.mktemp(suffix=".wav")
    try:
        # Text as a `--` argument (not piped to stdin) is the form Piper's own docs use and is stable
        # across versions; piping stdin has been flaky on some releases.
        p = subprocess.run([sys.executable, "-m", "piper", "-m", str(model), "-f", out, "--", text],
                           capture_output=True, timeout=40)
        if p.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
            raise VoiceError("Piper failed: " + (p.stderr.decode(errors="replace")[-200:] or f"exit code {p.returncode}"))
        with open(out, "rb") as f:
            return f.read()
    except subprocess.TimeoutExpired:
        raise VoiceError("Piper timed out.")
    finally:
        if os.path.exists(out):
            os.remove(out)


def _piper(text):
    model = PIPER_DIR / f"{config.get('piper_voice')}.onnx"
    if not model.exists():
        raise VoiceError(f"Voice model '{model.name}' isn't downloaded yet (Settings → Voice).")
    try:
        return _piper_inprocess(text), "audio/wav"
    except VoiceError:
        raise
    except Exception:          # e.g. an older piper without synthesize_wav, or a corrupt cached model
        _piper_unload()
        return _piper_subprocess(text, model), "audio/wav"


def _espeak(text):
    p = subprocess.run([espeak_bin(), "-v", "en-gb+f3", "-p", "48", "-s", "160", "--stdout", text],
                       capture_output=True, timeout=30)
    if p.returncode != 0 or not p.stdout:
        raise VoiceError("espeak failed.")
    return p.stdout, "audio/wav"


def engines():
    """Ordered list of usable engines for the current settings."""
    pref, order = config.get("tts_engine"), []
    if pref in ("auto", "local"):
        if piper_ready():
            order.append("piper")
        if espeak_bin():
            order.append("espeak")
    if pref in ("auto", "online") and edge_ready() and models.online():
        order.append("edge")
    return order


def synthesize(text):
    """Returns (audio_bytes, mime_type, engine). Raises VoiceError with a helpful reason."""
    text = clean(text)
    if not text:
        raise VoiceError("Nothing to say.")
    order = engines()
    if not order:
        raise VoiceError("No voice engine available. Install the offline voice (Settings → Voice) or connect to the internet.")
    errors = []
    for eng in order:
        try:
            data, mime = {"edge": _edge, "piper": _piper, "espeak": _espeak}[eng](text)
            return data, mime, eng
        except (VoiceError, OSError, subprocess.SubprocessError) as e:
            errors.append(str(e))
    raise VoiceError(" ".join(errors))


def speak_local(text):
    """Speak through this computer's speakers (used for scheduled tasks when the browser isn't involved)."""
    def job():
        with _lock:
            try:
                data, mime, _ = synthesize(text)
            except VoiceError:
                return
            f = tempfile.mktemp(suffix=".mp3" if "mpeg" in mime else ".wav")
            with open(f, "wb") as fh:
                fh.write(data)
            for cmd in (["mpv", "--no-video", "--really-quiet", f], ["paplay", f], ["aplay", "-q", f], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", f]):
                if tools.have(cmd[0]) and tools.run(cmd, 90)[0] == 0:
                    break
            os.path.exists(f) and os.remove(f)
    threading.Thread(target=job, daemon=True).start()
