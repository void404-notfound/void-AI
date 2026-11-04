"""Multi-provider AI router.

Most providers speak the OpenAI chat format, so one adapter covers Groq, Gemini,
OpenAI, OpenRouter, DeepSeek, Mistral and a local Ollama. Anthropic has its own.
The router tries providers in the user's order and skips any that fail.
Model names are defaults only: override them in Settings.
"""
import json, socket, time
import requests
from . import config

PROVIDERS = {
    "groq":       {"label": "Groq", "kind": "openai", "base": "https://api.groq.com/openai/v1",
                   "model": "openai/gpt-oss-120b", "url": "https://console.groq.com/keys", "note": "free tier, very fast"},
    "gemini":     {"label": "Google Gemini", "kind": "openai",
                   "base": "https://generativelanguage.googleapis.com/v1beta/openai",
                   "model": "gemini-3.5-flash", "url": "https://aistudio.google.com/apikey", "note": "free tier"},
    "openrouter": {"label": "OpenRouter", "kind": "openai", "base": "https://openrouter.ai/api/v1",
                   "model": "openrouter/auto", "url": "https://openrouter.ai/keys", "note": "one key, hundreds of models, some free"},
    "openai":     {"label": "OpenAI", "kind": "openai", "base": "https://api.openai.com/v1",
                   "model": "gpt-4o-mini", "url": "https://platform.openai.com/api-keys", "note": "paid"},
    "anthropic":  {"label": "Anthropic Claude", "kind": "anthropic", "base": "https://api.anthropic.com/v1",
                   "model": "claude-sonnet-5-5", "url": "https://console.anthropic.com/settings/keys", "note": "paid"},
    "deepseek":   {"label": "DeepSeek", "kind": "openai", "base": "https://api.deepseek.com/v1",
                   "model": "deepseek-chat", "url": "https://platform.deepseek.com/api_keys", "note": "cheap"},
    "mistral":    {"label": "Mistral", "kind": "openai", "base": "https://api.mistral.ai/v1",
                   "model": "mistral-small-latest", "url": "https://console.mistral.ai/api-keys", "note": "free tier"},
}
EXTRA_KEYS = {
    "openweather": ("OpenWeather (optional)", "https://openweathermap.org/api", "weather works without it"),
    "serpapi":     ("SerpAPI (optional)", "https://serpapi.com", "better web search; DuckDuckGo is used otherwise"),
    "telegram":    ("Telegram bot token", "https://t.me/BotFather", "needed to send messages"),
}


class AllModelsFailed(Exception):
    pass


LAST_ERROR = {}
FAIL_BACKOFF = 20     # seconds a provider is skipped after failing, before being retried automatically

# Small local models; the default is sized for this older CPU and limited free RAM.
OLLAMA_CHOICES = [
    ("qwen3.5:0.8b", "Qwen3.5 0.8B - better answers, about 1.2 GB download"),
    ("qwen2.5:0.5b", "Qwen2.5 0.5B - smallest option, about 0.5 GB download"),
]


def _explain(e, pid):
    s = str(e)
    if s[:3] in ("401", "403"):
        return "key rejected: check it was pasted correctly and is active"
    if s[:3] == "404":
        default = PROVIDERS.get(pid, {}).get("model")
        hint = f" (current default: {default})" if default and model_for(pid) != default else " (providers rename models often - check their docs for the current name)"
        return f"model '{model_for(pid)}' not found{hint}"
    if s[:3] == "429":
        return "rate limit reached: try again in a minute"
    if s[:3] in ("400", "402"):
        return "request refused: " + s[5:140]
    if isinstance(e, requests.exceptions.ConnectionError):
        return "can't reach the provider (network or DNS problem)"
    if isinstance(e, requests.exceptions.Timeout):
        return "timed out"
    return s[:140]


_online = {"t": 0.0, "v": True}
_fail = {}


def online():
    """Checked the same way Void actually talks to providers (via `requests`, so it honours any proxy
    the system or browser is configured with) rather than a raw socket, which can disagree with real
    connectivity on a proxied or restricted network."""
    if time.time() - _online["t"] < 6:
        return _online["v"]
    v = False
    try:
        r = requests.head("https://www.gstatic.com/generate_204", timeout=1.8)
        v = r.status_code < 500
    except requests.RequestException:
        for host in ("1.1.1.1", "8.8.8.8", "9.9.9.9"):
            try:
                socket.create_connection((host, 443), timeout=1.2).close()
                v = True
                break
            except OSError:
                continue
    _online.update(t=time.time(), v=v)
    return v


def all_providers():
    """Built-in providers plus any the user has added, so Void is never locked to one model or vendor."""
    out = dict(PROVIDERS)
    for pid, c in config.custom_providers().items():
        out[pid] = {"label": c["label"], "kind": "openai", "base": c["base"], "model": c["model"],
                    "url": "", "note": "custom"}
    return out


def model_for(pid):
    p = all_providers().get(pid, {})
    return config.get("models").get(pid) or p.get("model", "")


def usable_providers():
    known = all_providers()
    order = config.get("provider_order") + [p for p in known if p not in config.get("provider_order")]
    return [p for p in order if p in known and config.has_key(p) and time.time() - _fail.get(p, 0) > FAIL_BACKOFF]


def clear_failure(pid):
    """Call after the user edits a key or model name - an old failure shouldn't keep looking 'offline'."""
    _fail.pop(pid, None)
    LAST_ERROR.pop(pid, None)


def _openai(base, key, model, messages, tools, system):
    body = {"model": model, "messages": [{"role": "system", "content": system}] + messages,
            "temperature": 0.4, "max_tokens": 1024}
    if tools:
        body.update(tools=tools, tool_choice="auto")
    r = requests.post(base + "/chat/completions", json=body, timeout=30,
                      headers={"Authorization": f"Bearer {key}"} if key else {})
    if r.status_code >= 400:
        raise RuntimeError(f"{r.status_code}: {r.text[:200]}")
    msg = r.json()["choices"][0]["message"]
    out = {"role": "assistant", "content": msg.get("content") or ""}
    if msg.get("tool_calls"):
        out["tool_calls"] = [{"id": tc.get("id") or f"call_{i}", "type": "function", "function": tc["function"]}
                             for i, tc in enumerate(msg["tool_calls"])]
    return out


def _anthropic(base, key, model, messages, tools, system):
    msgs = []
    for m in messages:
        if m["role"] == "user":
            msgs.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant":
            blocks = [{"type": "text", "text": m["content"]}] if m.get("content") else []
            for tc in m.get("tool_calls", []):
                try:
                    inp = json.loads(tc["function"]["arguments"] or "{}")
                except ValueError:
                    inp = {}
                blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["function"]["name"], "input": inp})
            msgs.append({"role": "assistant", "content": blocks or "…"})
        elif m["role"] == "tool":
            blk = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
            if msgs and msgs[-1]["role"] == "user" and isinstance(msgs[-1]["content"], list):
                msgs[-1]["content"].append(blk)
            else:
                msgs.append({"role": "user", "content": [blk]})
    body = {"model": model, "max_tokens": 1024, "system": system, "messages": msgs}
    if tools:
        body["tools"] = [{"name": t["function"]["name"], "description": t["function"]["description"],
                          "input_schema": t["function"]["parameters"]} for t in tools]
    r = requests.post(base + "/messages", json=body, timeout=30,
                      headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
    if r.status_code >= 400:
        raise RuntimeError(f"{r.status_code}: {r.text[:200]}")
    text, calls = "", []
    for b in r.json().get("content", []):
        if b["type"] == "text":
            text += b["text"]
        elif b["type"] == "tool_use":
            calls.append({"id": b["id"], "type": "function",
                          "function": {"name": b["name"], "arguments": json.dumps(b["input"])}})
    out = {"role": "assistant", "content": text}
    if calls:
        out["tool_calls"] = calls
    return out


def complete(messages, tools, system):
    """Returns (assistant_message, provider_id). Raises AllModelsFailed."""
    errors, known = [], all_providers()
    for pid in usable_providers():
        spec = known[pid]
        fn = _anthropic if spec["kind"] == "anthropic" else _openai
        try:
            msg = fn(spec["base"], config.get_key(pid), model_for(pid), messages, tools, system)
            LAST_ERROR.pop(pid, None)
            return msg, pid
        except Exception as e:
            _fail[pid] = time.time()
            LAST_ERROR[pid] = _explain(e, pid)
            errors.append(f"{spec['label']}: {LAST_ERROR[pid]}")
    raise AllModelsFailed("; ".join(errors) or "no provider has an API key")


def test(pid):
    """Tiny live request so the user can see if a key (and model name) works. Returns (ok, message)."""
    spec = all_providers().get(pid)
    if not spec:
        return False, "unknown provider"
    if not config.has_key(pid):
        return False, "no key saved"
    fn = _anthropic if spec["kind"] == "anthropic" else _openai
    try:
        fn(spec["base"], config.get_key(pid), model_for(pid),
           [{"role": "user", "content": "Reply with the single word OK."}], None, "You are a connection test.")
        clear_failure(pid)
        return True, "Working ✓"
    except Exception as e:
        LAST_ERROR[pid] = _explain(e, pid)
        _fail[pid] = time.time()
        return False, LAST_ERROR[pid]


def ollama_chat(prompt, system):
    """Tiny local model, chit-chat only (no tools)."""
    try:
        r = requests.post("http://127.0.0.1:11434/v1/chat/completions", timeout=40, json={
            "model": config.get("ollama_model"), "max_tokens": 200,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]})
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return None
