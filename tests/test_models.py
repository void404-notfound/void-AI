"""Run:  python tests/test_models.py   (no network needed)"""
import os, sys, tempfile, time
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(); os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from assist5 import config, models
from assist5.server import app

ok = fail = 0
def check(name, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print("  PASS", name)
    else: fail += 1; print("  FAIL", name, extra)

print("current model defaults (the actual bug the user hit)")
check("Groq default is not the retired llama-3.3-70b-versatile", models.PROVIDERS["groq"]["model"] != "llama-3.3-70b-versatile")
check("Gemini default is not the retired gemini-2.5-flash", models.PROVIDERS["gemini"]["model"] != "gemini-2.5-flash")

print("online() uses the same stack as real provider calls (respects proxies), not a raw socket")
class FakeResp:
    status_code = 204
models.requests.head = lambda url, timeout: FakeResp()
models._online["t"] = 0
check("a reachable HTTPS check -> online", models.online() is True)
def boom(url, timeout): raise models.requests.exceptions.ConnectionError("blocked")
models.requests.head = boom
models._online["t"] = 0
models.socket.create_connection = lambda addr, timeout: (_ for _ in ()).throw(OSError())
check("HTTPS blocked AND sockets blocked -> offline", models.online() is False)
calls = []
def sock_ok(addr, timeout):
    calls.append(addr)
    if addr[0] != "9.9.9.9":
        raise OSError()
    class S:
        def close(self): pass
    return S()
models.socket.create_connection = sock_ok
models._online["t"] = 0
check("HTTPS blocked but a DNS host reachable -> falls back to online, tries multiple hosts", models.online() is True and len(calls) >= 2)

print("editable models: a provider is never locked to one hardcoded name")
config.set_key("groq", "k1")
check("default model before any override", models.model_for("groq") == models.PROVIDERS["groq"]["model"])
config.update(models={"groq": "some-other-model-someone-released-tomorrow"})
check("overridden model is used instead", models.model_for("groq") == "some-other-model-someone-released-tomorrow")

print("a changed key or model name clears the old failure/backoff immediately")
models._fail["groq"] = time.time()
models.LAST_ERROR["groq"] = "model not found"
check("failure recorded", "groq" in models._fail and "groq" in models.LAST_ERROR)
models.clear_failure("groq")
check("clear_failure wipes both", "groq" not in models._fail and "groq" not in models.LAST_ERROR)

H = {"X-Requested-With": "fetch"}
c = app.test_client()
c.post("/api/auth/setup", json={"password": "abcdef"}, headers=H); config.update(setup_done=True)
models._fail["groq"] = time.time(); models.LAST_ERROR["groq"] = "model not found"
r = c.post("/api/settings", json={"models": {"groq": "openai/gpt-oss-120b"}}, headers=H)
check("saving a new model name via settings clears the backoff", r.status_code == 200 and "groq" not in models._fail)
models._fail["groq"] = time.time()
c.post("/api/keys", json={"provider": "groq", "key": "new-key"}, headers=H)
check("saving a new key also clears the backoff", "groq" not in models._fail)

print("custom OpenAI-compatible providers: not locked to the built-in list")
r = c.post("/api/providers/custom", json={"id": "together", "label": "Together AI", "base": "https://api.together.xyz/v1", "model": "meta-llama/Llama-3-70b", "key": "tk"}, headers=H)
check("add a custom provider", r.status_code == 200 and r.json["ok"], r.get_data(as_text=True))
check("it's usable like any other provider", "together" in models.all_providers() and config.has_key("together") and models.model_for("together") == "meta-llama/Llama-3-70b")
check("it joins the try-order automatically", "together" in config.get("provider_order"))
r = c.post("/api/providers/custom", json={"id": "groq", "label": "x", "base": "https://a.com", "model": "m"}, headers=H)
check("can't shadow a built-in provider id", r.status_code == 400)
r = c.post("/api/providers/custom", json={"id": "bad one!", "label": "x", "base": "https://a.com", "model": "m"}, headers=H)
check("id gets sanitised/rejected for bad characters", r.status_code == 400 or r.json.get("id") == "badone")
r = c.post("/api/providers/custom", json={"id": "insecure", "label": "x", "base": "http://not-https.com", "model": "m"}, headers=H)
check("non-https base URL rejected", r.status_code == 400)
ov = c.get("/api/overview", headers=H).json
check("overview lists the custom provider and marks it custom", any(p["id"] == "together" and p["custom"] for p in ov["providers"]))
r = c.delete("/api/providers/custom/together", headers=H)
check("delete a custom provider", r.status_code == 200 and "together" not in models.all_providers())
check("its key is gone too", not config.has_key("together"))

print("Ollama model choices: a real, documented recommendation, not a guess")
check("default small model is in the offered list", config.get("ollama_model") in [c[0] for c in models.OLLAMA_CHOICES])
check("at least one lighter fallback is offered for very tight RAM", len(models.OLLAMA_CHOICES) >= 2)
lighter_model = models.OLLAMA_CHOICES[-1][0]
r = c.post("/api/settings", json={"ollama_model": lighter_model}, headers=H)
check("can switch to the lighter model", config.get("ollama_model") == lighter_model)
r = c.post("/api/settings", json={"ollama_model": "some-made-up-70b-model"}, headers=H)
check("can't set an arbitrary huge model that would blow the RAM budget", config.get("ollama_model") != "some-made-up-70b-model")

print(f"\n{ok} passed, {fail} failed"); sys.exit(1 if fail else 0)
