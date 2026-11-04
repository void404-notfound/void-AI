"""Downloads the offline voice models (Vosk speech recognition + a Piper voice, ~100 MB total).
Run by the installer, or from Settings → Voice, or:  python -m assist5.voice_setup"""
import os, re, subprocess, sys, threading, time, zipfile
import requests
from . import config, stt, voice

VOSK_URL = f"https://alphacephei.com/vosk/models/{stt.MODEL_NAME}.zip"
PIPER_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
PIPER_CHUNK_SIZE = 8 * 1024 * 1024
PROGRESS = {k: {"state": "idle", "pct": 0, "msg": ""} for k in ("stt", "tts")}
_threads = {}


def _set(key, **kw):
    PROGRESS[key].update(kw)


def _download(url, dest, key, lo, hi):
    with requests.get(url, stream=True, timeout=30) as r:
        r.raise_for_status()
        total, got = int(r.headers.get("content-length", 0)), 0
        tmp = str(dest) + ".part"
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
                got += len(chunk)
                if total:
                    _set(key, pct=int(lo + (hi - lo) * got / total))
        import os
        os.replace(tmp, dest)


def _piper_url(name, suffix):
    match = re.fullmatch(r"(?P<lang>[a-z]+_[A-Z]{2})-(?P<speaker>[^-]+)-(?P<quality>[^-]+)", name)
    if not match:
        raise ValueError(f"Unsupported Piper voice name: {name}")
    lang = match.group("lang")
    filename = f"{name}{suffix}"
    return f"{PIPER_BASE}/{lang.split('_', 1)[0]}/{lang}/{match.group('speaker')}/{match.group('quality')}/{filename}?download=true"


def _remote_size(url):
    try:
        response = requests.head(url, allow_redirects=True, timeout=(10, 25))
        response.raise_for_status()
        return int(response.headers.get("content-length") or response.headers.get("x-linked-size") or 0)
    except (requests.RequestException, ValueError):
        return 0


def _download_voice_file(url, dest, pct_start, pct_end):
    tmp = dest.with_name(dest.name + ".part")
    total_expected = _remote_size(url)
    if total_expected and dest.exists() and dest.stat().st_size == total_expected:
        return
    if tmp.exists() and total_expected and tmp.stat().st_size > total_expected:
        tmp.unlink()
    if tmp.exists() and total_expected and tmp.stat().st_size < total_expected:
        safe_size = tmp.stat().st_size - tmp.stat().st_size % PIPER_CHUNK_SIZE
        if safe_size != tmp.stat().st_size:
            with open(tmp, "r+b") as stream:
                stream.truncate(safe_size)
    if total_expected:
        offset = tmp.stat().st_size if tmp.exists() else 0
        stalled_retries = 0
        while offset < total_expected:
            end = min(offset + PIPER_CHUNK_SIZE, total_expected) - 1
            request_offset = offset
            try:
                with requests.get(url, headers={"Range": f"bytes={offset}-{end}"},
                                  stream=True, timeout=(15, 180)) as response:
                    response.raise_for_status()
                    if response.status_code != 206:
                        raise RuntimeError("voice host did not honor ranged download")
                    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("content-range", ""))
                    if not match or tuple(map(int, match.groups())) != (offset, end, total_expected):
                        raise RuntimeError("voice host returned an invalid content range")
                    with open(tmp, "ab" if offset else "wb") as stream:
                        for chunk in response.iter_content(1 << 16):
                            if chunk:
                                stream.write(chunk)
                                offset += len(chunk)
                                _set("tts", pct=min(pct_end, pct_start + int((pct_end - pct_start) * offset / total_expected)))
            except requests.RequestException:
                received = tmp.stat().st_size if tmp.exists() else 0
                if received <= request_offset:
                    stalled_retries += 1
                    if stalled_retries >= 3:
                        raise
                else:
                    offset = received
                    stalled_retries = 0
                continue
            if offset > end + 1:
                raise RuntimeError("voice host sent more bytes than requested")
            if offset == request_offset:
                raise RuntimeError("voice host returned an empty range")
            _set("tts", pct=min(pct_end, pct_start + int((pct_end - pct_start) * offset / total_expected)))
    else:
        with requests.get(url, stream=True, timeout=(15, 180)) as response:
            response.raise_for_status()
            expected = int(response.headers.get("content-length") or response.headers.get("x-linked-size") or 0)
            chunk_data = bytearray()
            for chunk in response.iter_content(1 << 16):
                if chunk:
                    chunk_data.extend(chunk)
        if not chunk_data or (expected and len(chunk_data) != expected):
            raise RuntimeError(f"incomplete voice download ({len(chunk_data)}/{expected} bytes)")
        with open(tmp, "wb") as stream:
            stream.write(chunk_data)
    if total_expected and tmp.stat().st_size != total_expected:
        raise RuntimeError(f"incomplete voice download ({tmp.stat().st_size}/{total_expected} bytes)")
    os.replace(tmp, dest)


def _official_download(name):
    """Safety net: let Piper's own documented downloader fetch the voice, so a changed Hugging Face URL
    layout can't break voice setup again."""
    voice.PIPER_DIR.mkdir(parents=True, exist_ok=True)
    p = subprocess.run([sys.executable, "-m", "piper.download_voices", name, "--download-dir", str(voice.PIPER_DIR)],
                       capture_output=True, timeout=1800)
    if p.returncode != 0:
        raise RuntimeError("Piper's own downloader failed: " + (p.stderr.decode(errors="replace").strip()[-150:] or f"exit code {p.returncode}"))


def install_stt():
    _set("stt", state="downloading", pct=1, msg="Downloading speech model…")
    try:
        base = stt.MODEL_DIR.parent
        base.mkdir(parents=True, exist_ok=True)
        zpath = base / "vosk.zip"
        _download(VOSK_URL, zpath, "stt", 1, 90)
        _set("stt", msg="Unpacking…", pct=92)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(base)
        zpath.unlink()
        if not (stt.MODEL_DIR / "am").exists():
            raise RuntimeError("model folder looks wrong after unpacking")
        _set("stt", state="done", pct=100, msg="Ready")
    except Exception as e:
        _set("stt", state="error", msg=f"{type(e).__name__}: {str(e)[:120]}")


def install_tts():
    """Download voice assets with size checks (resumable). If that fails, retry with Piper's own downloader."""
    name = config.get("piper_voice")
    _set("tts", state="downloading", pct=2, msg=f"Downloading voice ({name})…")
    model = voice.PIPER_DIR / f"{name}.onnx"
    metadata = voice.PIPER_DIR / f"{name}.onnx.json"
    try:
        voice.PIPER_DIR.mkdir(parents=True, exist_ok=True)
        try:
            _download_voice_file(_piper_url(name, ".onnx"), model, 2, 94)
            _download_voice_file(_piper_url(name, ".onnx.json"), metadata, 94, 99)
        except Exception as first_error:
            _set("tts", pct=50, msg="Retrying with Piper's own downloader…")
            try:
                _official_download(name)
            except Exception as second_error:
                raise RuntimeError(f"{type(first_error).__name__}: {str(first_error)[:70]}; {str(second_error)[:80]}")
        if not voice.piper_ready() or not metadata.exists() or metadata.stat().st_size == 0:
            raise RuntimeError("downloaded voice files are incomplete (is the 'piper-tts' package installed?)")
        _set("tts", state="done", pct=100, msg="Ready")
        voice._piper_unload()
        voice.prewarm()
    except Exception as e:
        _set("tts", state="error", msg=f"{type(e).__name__}: {str(e)[:150]}")


def start(what):
    fn = {"stt": install_stt, "tts": install_tts}[what]
    t = _threads.get(what)
    if t and t.is_alive():
        return False
    _threads[what] = threading.Thread(target=fn, daemon=True)
    _threads[what].start()
    return True


def status():
    import importlib.util
    return {"stt_ready": stt.local_ready(), "tts_ready": voice.piper_ready(),
            "vosk_pkg": importlib.util.find_spec("vosk") is not None,
            "piper_pkg": importlib.util.find_spec("piper") is not None,
            "edge_pkg": voice.edge_ready(), "espeak": bool(voice.espeak_bin()), "progress": PROGRESS}


if __name__ == "__main__":
    for key, fn in (("stt", install_stt), ("tts", install_tts)):
        if (key == "stt" and stt.local_ready()) or (key == "tts" and voice.piper_ready()):
            print(f"{key}: already installed"); continue
        print(f"{key}: downloading…"); fn(); print(f"{key}: {PROGRESS[key]['state']} {PROGRESS[key]['msg']}")
