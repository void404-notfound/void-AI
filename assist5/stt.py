"""Speech-to-text. Audio arrives from the browser as 16 kHz mono 16-bit WAV.

Online : Whisper large-v3-turbo on Groq (free tier) or whisper-1 on OpenAI - fast and accurate.
Local  : Vosk (small English model, ~40 MB) - runs fully offline on any CPU, no AVX2 needed.
"""
import difflib, importlib.util, io, json, threading, wave
import requests
from . import config, models


class VoiceError(Exception):
    pass


MODEL_NAME = "vosk-model-small-en-us-0.15"
MODEL_DIR = config.DATA_DIR / "models" / MODEL_NAME
ONLINE = {
    "groq": ("https://api.groq.com/openai/v1/audio/transcriptions", "whisper-large-v3-turbo"),
    "openai": ("https://api.openai.com/v1/audio/transcriptions", "whisper-1"),
}
HALLUCINATIONS = {"thank you", "thanks for watching", "you", "bye", "thanks"}   # Whisper on near-silence
_vosk = {"model": None}
_lock = threading.Lock()


def local_ready():
    return importlib.util.find_spec("vosk") is not None and (MODEL_DIR / "am").exists()


def online_provider():
    if not models.online():
        return None
    return next((p for p in ONLINE if config.has_key(p)), None)


def _check_wav(wav):
    try:
        with wave.open(io.BytesIO(wav)) as w:
            fmt = (w.getnchannels(), w.getsampwidth(), w.getframerate())
            secs = w.getnframes() / max(1, w.getframerate())
    except (wave.Error, EOFError):
        raise VoiceError("That wasn't valid audio.")
    if fmt != (1, 2, 16000):
        raise VoiceError("Audio must be 16 kHz mono.")
    return secs


def transcribe_local(wav):
    if importlib.util.find_spec("vosk") is None:
        raise VoiceError("The 'vosk' package isn't installed (pip install vosk).")
    if not (MODEL_DIR / "am").exists():
        raise VoiceError("Local voice model not installed. Open Settings → Voice → Install offline voice.")
    from vosk import KaldiRecognizer, Model, SetLogLevel
    with _lock:
        if _vosk["model"] is None:
            SetLogLevel(-1)
            _vosk["model"] = Model(str(MODEL_DIR))
        rec = KaldiRecognizer(_vosk["model"], 16000)
        with wave.open(io.BytesIO(wav)) as w:
            while True:
                chunk = w.readframes(4000)
                if not chunk:
                    break
                rec.AcceptWaveform(chunk)
        return json.loads(rec.FinalResult()).get("text", "").strip()


def transcribe_online(wav, pid):
    url, model = ONLINE[pid]
    try:
        r = requests.post(url, headers={"Authorization": f"Bearer {config.get_key(pid)}"}, timeout=30,
                          files={"file": ("speech.wav", wav, "audio/wav")},
                          data={"model": model, "response_format": "json", "temperature": "0",
                                "language": config.get("stt_language"),
                                "prompt": f"{config.APP_NAME}. Voice commands for a Linux computer: volume, brightness, "
                                          f"wifi, bluetooth, open apps, play video, lock, shutdown."})
    except requests.RequestException as e:
        raise VoiceError(f"Couldn't reach {pid} ({type(e).__name__}).")
    if r.status_code >= 400:
        raise VoiceError(f"{pid} speech recognition failed ({r.status_code}): {models._explain(RuntimeError(str(r.status_code) + ': ' + r.text[:100]), pid)}")
    return r.json().get("text", "").strip()


def strip_wake(text):
    """('Hey Void, open firefox') -> (True, 'open firefox')."""
    words = text.strip().split()
    i = 1 if words and words[0].lower().strip(",.!?") in ("hey", "ok", "okay", "yo", "hi", "hello") else 0
    if len(words) > i and difflib.SequenceMatcher(None, words[i].lower().strip(",.!?"), config.APP_NAME.lower()).ratio() >= 0.75:
        return True, " ".join(words[i + 1:]).strip(" ,.!?")
    return False, text.strip()


def plan(continuous=False):
    """Ordered engines to try. Hands-free + auto stays LOCAL so ambient audio is never uploaded."""
    pref, order = config.get("stt_engine"), []
    online = online_provider()
    if pref == "local" or (pref == "auto" and continuous):
        order = ["local"]
    elif pref == "online":
        order = ["online"] if online else []
    else:
        order = (["local"] if local_ready() else []) + (["online"] if online else [])
    return order, online


def transcribe(wav, continuous=False):
    secs = _check_wav(wav)
    if secs < 0.25:
        return {"text": "", "engine": None}
    order, online = plan(continuous)
    if not order:
        raise VoiceError("Online speech needs internet and a Groq or OpenAI key (Settings → Models)."
                         if config.get("stt_engine") == "online" else
                         "No speech engine available. Add a Groq key or install the offline voice (Settings → Voice).")
    errors = []
    for eng in order:
        try:
            text = transcribe_local(wav) if eng == "local" else transcribe_online(wav, online)
            if text.lower().strip(" .!?") in HALLUCINATIONS and secs < 2.5:
                text = ""
            return {"text": text, "engine": "local" if eng == "local" else online}
        except VoiceError as e:
            errors.append(str(e))
    raise VoiceError(" ".join(errors))
