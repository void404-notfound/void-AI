"""Local web server + API for the UI. Secure by default:
 - listens on 127.0.0.1 only (LAN access is an explicit setting)
 - every /api call needs a logged-in session (password set at first run)
 - cookies are SameSite=Strict, POSTs need a custom header, Host header is checked
 - no CORS, no public tunnels
"""
import hashlib, hmac, io, os, re, secrets, time
from pathlib import Path
from flask import Flask, Response, jsonify, request, send_from_directory, session
from werkzeug.utils import secure_filename

from . import brain, config, memory, models, perms, scheduler, stt, tools, voice, voice_setup

UI_DIR = Path(__file__).parent / "ui"
app = Flask(__name__)
app.secret_key = config.session_secret()
app.config.update(MAX_CONTENT_LENGTH=12 * 1024 * 1024, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
                  PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 14)
_attempts = {}


# ── password ─────────────────────────────────────────────────
def _hash(pw, salt):
    return hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1).hex()


def _set_password(pw):
    salt = secrets.token_bytes(16)
    config.write_json(config.AUTH_FILE, {"salt": salt.hex(), "hash": _hash(pw, salt)}, private=True)


def _check_password(pw):
    a = config.read_json(config.AUTH_FILE, None)
    return bool(a) and hmac.compare_digest(_hash(pw, bytes.fromhex(a["salt"])), a["hash"])


def reset_password(pw):
    """Command-line recovery:  python -m assist5 --reset-password   (works while the server is stopped).
    Also rotates the session key so every browser that was logged in is signed out at next start."""
    if len(pw) < 6:
        raise ValueError("Password must be at least 6 characters.")
    _set_password(pw)
    (config.CONFIG_DIR / "session.key").unlink(missing_ok=True)


# ── guards ───────────────────────────────────────────────────
@app.before_request
def guard():
    if not config.get("allow_lan"):
        host = request.host.rsplit(":", 1)[0].strip("[]")
        if host not in ("127.0.0.1", "localhost", "::1"):
            return jsonify(error="bad host"), 400
    if request.path.startswith("/api/"):
        if request.method in ("POST", "DELETE") and request.headers.get("X-Requested-With") != "fetch":
            return jsonify(error="missing header"), 400
        if not request.path.startswith(("/api/state", "/api/auth/")) and not session.get("ok"):
            return jsonify(error="login required"), 401


@app.after_request
def headers(r):
    r.headers["Cache-Control"] = "no-store"
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; script-src 'self'; media-src 'self' blob:"
    r.headers["Permissions-Policy"] = "microphone=(self), camera=()"
    return r


@app.route("/")
def index():
    return send_from_directory(UI_DIR, "index.html")


@app.route("/<name>")
def ui_file(name):
    if name not in ("app.js", "style.css", "voice.js", "blackhole5.js"):
        return jsonify(error="not found"), 404
    return send_from_directory(UI_DIR, name)


# ── auth ─────────────────────────────────────────────────────
@app.route("/api/state")
def state():
    return jsonify(setup_done=config.get("setup_done"), authed=bool(session.get("ok")), name=config.APP_NAME)


def _throttled():
    ip, now = request.remote_addr, time.time()
    _attempts[ip] = [t for t in _attempts.get(ip, []) if now - t < 60]
    return len(_attempts[ip]) >= 6


@app.route("/api/auth/setup", methods=["POST"])
def auth_setup():
    if config.get("setup_done") or request.remote_addr not in ("127.0.0.1", "::1"):
        return jsonify(error="already set up"), 403
    d = request.get_json(silent=True) or {}
    if len(d.get("password", "")) < 6:
        return jsonify(error="Password must be at least 6 characters."), 400
    _set_password(d["password"])
    config.update(owner=str(d.get("owner", ""))[:40], city=str(d.get("city", ""))[:60])
    session.permanent, session["ok"] = True, True
    return jsonify(ok=True)


@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    if _throttled():
        return jsonify(error="Too many attempts. Wait a minute."), 429
    _attempts.setdefault(request.remote_addr, []).append(time.time())
    if _check_password((request.get_json(silent=True) or {}).get("password", "")):
        session.permanent, session["ok"] = True, True
        return jsonify(ok=True)
    return jsonify(error="Wrong password."), 401


@app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    session.clear()
    return jsonify(ok=True)


# ── data ─────────────────────────────────────────────────────
@app.route("/api/overview")
def overview():
    lv = perms.levels()
    keys = {p: config.has_key(p) for p in list(models.PROVIDERS) + list(models.EXTRA_KEYS)}
    s = config.settings()
    return jsonify(
        name=config.APP_NAME, settings={k: s[k] for k in ("owner", "city", "mode", "voice_enabled", "offline_llm",
                                                          "allow_lan", "provider_order", "telegram_contacts", "setup_done", "mic_enabled",
                                                          "listen_mode", "stt_engine", "tts_engine", "edge_voice")},
        online=models.online(), active_providers=models.usable_providers(),
        groups=[{"id": g, "label": v[0], "desc": v[1], "level": lv[g]} for g, v in perms.GROUPS.items()],
        always_allowed=perms.always_allowed_actions(),
        providers=[{"id": p, "label": v["label"], "url": v["url"], "note": v["note"],
                    "has_key": config.has_key(p), "model": models.model_for(p), "custom": v.get("note") == "custom",
                    "error": models.LAST_ERROR.get(p)} for p, v in models.all_providers().items()],
        has_any_key=any(config.has_key(p) for p in models.all_providers()),
        ollama_choices=models.OLLAMA_CHOICES,
        extras=[{"id": k, "label": v[0], "url": v[1], "note": v[2], "has_key": keys[k]} for k, v in models.EXTRA_KEYS.items()],
        history=memory.history()[-30:])


@app.route("/api/permissions", methods=["POST"])
def set_permissions():
    return jsonify(levels=perms.set_levels((request.get_json(silent=True) or {}).get("levels", {})))


@app.route("/api/settings", methods=["POST"])
def set_settings():
    d = request.get_json(silent=True) or {}
    upd = {}
    for k in ("owner", "city"):
        if k in d:
            upd[k] = str(d[k])[:60]
    for k in ("voice_enabled", "offline_llm", "allow_lan", "setup_done", "mic_enabled"):
        if k in d:
            upd[k] = bool(d[k])
    for k, allowed in (("listen_mode", ("push", "handsfree")), ("stt_engine", ("auto", "online", "local")),
                       ("tts_engine", ("auto", "online", "local")), ("edge_voice", tuple(voice.EDGE_VOICES))):
        if d.get(k) in allowed:
            upd[k] = d[k]
    if d.get("mode") in ("auto", "online", "offline"):
        upd["mode"] = d["mode"]
    if isinstance(d.get("provider_order"), list):
        known = models.all_providers()
        upd["provider_order"] = [p for p in d["provider_order"] if p in known]
    if isinstance(d.get("telegram_contacts"), dict):
        upd["telegram_contacts"] = {str(k).lower()[:30]: str(v)[:30] for k, v in d["telegram_contacts"].items()}
    if isinstance(d.get("models"), dict):
        known = models.all_providers()
        cur = config.get("models")
        for k, v in d["models"].items():
            if k in known and str(v).strip():
                cur[k] = str(v).strip()[:120]
                models.clear_failure(k)      # a changed model name invalidates any earlier 404 backoff
        upd["models"] = cur
    if d.get("ollama_model") in [c[0] for c in models.OLLAMA_CHOICES] or d.get("ollama_model") == config.get("ollama_model"):
        if "ollama_model" in d:
            upd["ollama_model"] = d["ollama_model"]
    config.update(**upd)
    return jsonify(ok=True)


@app.route("/api/keys", methods=["POST"])
def set_key():
    d = request.get_json(silent=True) or {}
    name = d.get("provider")
    if name in models.all_providers() or name in models.EXTRA_KEYS:
        config.set_key(name, str(d.get("key", ""))[:300])
        models.clear_failure(name)
        return jsonify(ok=True)
    return jsonify(error="unknown provider"), 400


@app.route("/api/providers/custom", methods=["POST"])
def add_custom_provider():
    """Any OpenAI-compatible API, so Void is never limited to one named model."""
    d = request.get_json(silent=True) or {}
    pid = re.sub(r"[^a-z0-9_-]", "", str(d.get("id", "")).lower().strip())[:30]
    label, base, model = str(d.get("label", "")).strip(), str(d.get("base", "")).strip(), str(d.get("model", "")).strip()
    if not pid or pid in models.PROVIDERS or pid in models.EXTRA_KEYS:
        return jsonify(error="Pick a short id that isn't already used (letters, numbers, - or _)."), 400
    if not label or not model:
        return jsonify(error="A label and a model name are required."), 400
    if not re.match(r"^https://", base):
        return jsonify(error="The base URL must start with https://"), 400
    config.add_custom_provider(pid, label, base.rstrip("/"), model)
    if d.get("key"):
        config.set_key(pid, str(d["key"])[:300])
    order = config.get("provider_order")
    if pid not in order:
        config.update(provider_order=order + [pid])
    return jsonify(ok=True, id=pid)


@app.route("/api/providers/custom/<pid>", methods=["DELETE"])
def delete_custom_provider(pid):
    if pid not in config.custom_providers():
        return jsonify(error="not found"), 404
    config.remove_custom_provider(pid)
    config.update(provider_order=[p for p in config.get("provider_order") if p != pid])
    models.clear_failure(pid)
    return jsonify(ok=True)


@app.route("/api/keys/test", methods=["POST"])
def key_test():
    pid = (request.get_json(silent=True) or {}).get("provider")
    if pid not in models.PROVIDERS:
        return jsonify(error="unknown provider"), 400
    ok, msg = models.test(pid)
    return jsonify(ok=ok, message=msg)


# ── voice ────────────────────────────────────────────────────
@app.route("/api/voice/status")
def voice_status():
    order, online = stt.plan(False)
    return jsonify(**voice_setup.status(), mic_enabled=config.get("mic_enabled"), voices=voice.EDGE_VOICES,
                   stt_online_provider=online, stt_order=order, tts_order=voice.engines())


@app.route("/api/voice/transcribe", methods=["POST"])
def voice_transcribe():
    if not config.get("mic_enabled"):
        return jsonify(error="The microphone is turned off. Enable it in Settings → Voice."), 403
    try:
        r = stt.transcribe(request.get_data(), continuous=request.args.get("continuous") == "1")
    except stt.VoiceError as e:
        return jsonify(error=str(e)), 503
    woke, command = stt.strip_wake(r["text"]) if r["text"] else (False, "")
    return jsonify(text=r["text"], engine=r["engine"], woke=woke, command=command)


@app.route("/api/voice/speak", methods=["POST"])
def voice_speak():
    try:
        data, mime, eng = voice.synthesize((request.get_json(silent=True) or {}).get("text", ""))
    except stt.VoiceError as e:
        return jsonify(error=str(e)), 503
    return Response(data, mimetype=mime, headers={"X-Voice-Engine": eng})


@app.route("/api/voice/install", methods=["POST"])
def voice_install():
    what = (request.get_json(silent=True) or {}).get("what")
    if what not in ("stt", "tts"):
        return jsonify(error="unknown"), 400
    return jsonify(started=voice_setup.start(what))


@app.route("/api/chat", methods=["POST"])
def chat():
    d = request.get_json(silent=True) or {}
    return jsonify(brain.handle(d.get("text", ""), attachment=d.get("attachment")))


@app.route("/api/upload", methods=["POST"])
def upload():
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return jsonify(error="Choose a file to attach."), 400
    filename = secure_filename(uploaded.filename)[:120]
    suffix = Path(filename).suffix.lower()
    if suffix not in {".txt", ".md", ".csv", ".json", ".log", ".py", ".sh", ".yaml", ".yml", ".pdf"}:
        return jsonify(error="Attach a text, code, data, or PDF file."), 415
    raw = uploaded.stream.read(4 * 1024 * 1024 + 1)
    if not raw:
        return jsonify(error="The selected file is empty."), 400
    if len(raw) > 4 * 1024 * 1024:
        return jsonify(error="Files must be 4 MB or smaller."), 413
    try:
        if suffix == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw), strict=False)
            if reader.is_encrypted:
                return jsonify(error="Password-protected PDFs are not supported."), 400
            text = "\n".join((page.extract_text() or "") for page in reader.pages[:20])
        else:
            text = raw.decode("utf-8-sig", errors="replace")
    except Exception as e:
        return jsonify(error=f"Couldn't read that file ({type(e).__name__})."), 400
    text = text.replace("\x00", "")
    if not text.strip():
        return jsonify(error="No readable text was found in the file."), 400
    return jsonify(filename=filename or "attachment", text=text[:12000], truncated=len(text) > 12000)


@app.route("/api/chat/clear", methods=["POST"])
def chat_clear():
    memory.clear()
    return jsonify(ok=True)


@app.route("/api/approvals")
def approvals():
    return jsonify(items=perms.pending())


@app.route("/api/approvals/<aid>", methods=["POST"])
def approval_decide(aid):
    d = request.get_json(silent=True) or {}
    return jsonify(ok=perms.decide(aid, d.get("approve"), always=bool(d.get("always"))))


@app.route("/api/permissions/actions/<fingerprint>", methods=["DELETE"])
def permission_action_revoke(fingerprint):
    if not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
        return jsonify(error="invalid action id"), 400
    return jsonify(ok=perms.revoke_always_allowed(fingerprint))


@app.route("/api/audit")
def audit():
    return jsonify(items=perms.audit_tail())


@app.route("/api/schedules")
def schedules():
    return jsonify(items=scheduler.load())


@app.route("/api/schedules/<sid>", methods=["DELETE"])
def schedule_delete(sid):
    return jsonify(ok=scheduler.remove(sid))
