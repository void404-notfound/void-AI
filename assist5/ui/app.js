"use strict";
const $ = (s, r = document) => r.querySelector(s);
const root = $("#root");
document.documentElement.dataset.theme = localStorage.getItem("theme") || "void";
let NAME = "Assistant", OV = null, busy = false, approvalOpen = null, refreshStatus = () => {};

function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c != null) e.append(c.nodeType ? c : document.createTextNode(c));
  return e;
}

async function api(path, body, method) {
  const r = await fetch("/api/" + path, {
    method: method || (body !== undefined ? "POST" : "GET"),
    headers: { "Content-Type": "application/json", "X-Requested-With": "fetch" },
    body: body !== undefined ? JSON.stringify(body) : undefined, credentials: "same-origin"
  });
  let data = {}; try { data = await r.json(); } catch (e) {}
  if (r.status === 401 && path !== "auth/login") start();
  return { ok: r.ok, data };
}

function seg(options, value, onchange) {
  const wrap = h("div", { class: "seg" });
  options.forEach(([v, label]) => wrap.append(h("button", {
    class: (v === value ? "on " : "") + v, type: "button",
    onclick: () => { [...wrap.children].forEach(b => b.classList.remove("on")); wrap.children[options.findIndex(o => o[0] === v)].classList.add("on"); onchange(v); }
  }, label)));
  return wrap;
}
function toggle(on, onchange) {
  const t = h("div", { class: "toggle" + (on ? " on" : ""), role: "switch", onclick: () => { t.classList.toggle("on"); onchange(t.classList.contains("on")); } });
  return t;
}
const overlay = (...kids) => { const s = h("div", { class: "scrim" }, ...kids); document.body.append(s); return s; };

// ───────────────────────── start ─────────────────────────
async function start() {
  const st = (await api("state")).data;
  NAME = st.name; document.title = NAME;
  root.replaceChildren();
  if (!st.setup_done) return wizard();
  if (!st.authed) return login();
  OV = (await api("overview")).data;
  app();
}

function login() {
  const pw = h("input", { class: "field", type: "password", placeholder: "Password", autofocus: true });
  const err = h("div", { class: "err" });
  const go = async () => {
    const r = await api("auth/login", { password: pw.value });
    if (r.ok) start(); else err.textContent = r.data.error || "Login failed";
  };
  pw.addEventListener("keydown", e => e.key === "Enter" && go());
  root.replaceChildren(h("div", { class: "scrim" }, h("div", { class: "card glass" },
    h("h2", {}, `Welcome back`), h("p", { class: "sub" }, `Unlock ${NAME}`), pw, err,
    h("div", { class: "actions" }, h("button", { class: "btn", onclick: go }, "Unlock")))));
}

// ───────────────────────── setup wizard ─────────────────────────
const RECOMMENDED = { audio: "allow", display: "allow", media: "allow", apps: "allow", system_info: "allow", web: "allow",
  network: "ask", files: "ask", input: "ask", messaging: "ask", power: "ask", scheduling: "ask", shell: "off" };

async function wizard(step = 0) {
  const card = h("div", { class: "card glass" });
  root.replaceChildren(h("div", { class: "scrim" }, card));
  const nav = (back, next, label = "Continue") => h("div", { class: "actions" },
    back ? h("button", { class: "btn ghost", onclick: back }, "Back") : null, h("button", { class: "btn", onclick: next }, label));

  if (step === 0) {
    const owner = h("input", { class: "field", placeholder: "Your name" });
    const city = h("input", { class: "field", placeholder: "Your city (for weather, optional)" });
    const pw = h("input", { class: "field", type: "password", placeholder: "Choose a password (6+ characters)" });
    const err = h("div", { class: "err" });
    card.append(h("h2", {}, `Hi, I'm ${NAME} 👋`),
      h("p", { class: "sub" }, "I can help run this computer. First, a few quick details. Nothing leaves your machine except the AI requests you enable."),
      h("div", { class: "stack" }, owner, city, pw), err,
      nav(null, async () => {
        const r = await api("auth/setup", { password: pw.value, owner: owner.value, city: city.value });
        if (r.ok) wizard(1); else err.textContent = r.data.error || "Setup failed";
      }));
  }
  if (step === 1) {
    OV = (await api("overview")).data;
    const chosen = Object.fromEntries(OV.groups.map(g => [g.id, g.level]));
    const list = h("div", { class: "stack" });
    const draw = () => { list.replaceChildren(...OV.groups.map(g => h("div", { class: "row" },
      h("div", { class: "t" }, h("b", {}, g.label), h("span", {}, g.desc)),
      seg([["off", "Off"], ["ask", "Ask"], ["allow", "Allow"]], chosen[g.id], v => chosen[g.id] = v)))); };
    draw();
    let micOn = false, speakOn = true;
    const voiceRows = h("div", { class: "stack", style: "margin-top:12px" },
      h("div", { class: "row" }, h("div", { class: "t" }, h("b", {}, "Microphone"), h("span", {}, "Talk to me with your voice. Your browser will also ask permission.")), toggle(false, v => micOn = v)),
      h("div", { class: "row" }, h("div", { class: "t" }, h("b", {}, "Speak replies"), h("span", {}, "I answer out loud in a clear British female voice")), toggle(true, v => speakOn = v)));
    card.append(h("h2", {}, "What may I do?"),
      h("p", { class: "sub" }, "You decide. Off = never. Ask = I need your tap each time. Allow = I can do it on my own. Risky actions (shutdown, messages, terminal) always ask. You can change this any time in Settings."),
      h("button", { class: "chip", onclick: () => { Object.assign(chosen, RECOMMENDED); draw(); } }, "✨ Use recommended"),
      h("div", { style: "height:10px" }), list, voiceRows,
      nav(() => wizard(0), async () => { await api("permissions", { levels: chosen }); await api("settings", { mic_enabled: micOn, voice_enabled: speakOn }); wizard(2); }));
  }
  if (step === 2) {
    OV = (await api("overview")).data;
    const inputs = {};
    const rows = OV.providers.map(p => { inputs[p.id] = h("input", { class: "field", type: "password", placeholder: p.has_key ? "Saved ✓ (type to replace)" : "Paste API key" });
      return h("div", { class: "stack" }, h("div", {}, h("b", {}, p.label + " "), h("small", { class: "hint" }, p.note + " · "), h("a", { href: p.url, target: "_blank", rel: "noopener" }, "get a key")), inputs[p.id]); });
    card.append(h("h2", {}, "Connect AI models"),
      h("p", { class: "sub" }, "Add one or more keys. One is enough (Groq and Gemini have free tiers). With no key I still work offline for basic tasks."),
      h("div", { class: "stack", style: "gap:16px" }, rows),
      nav(() => wizard(1), async () => {
        for (const [id, el] of Object.entries(inputs)) if (el.value.trim()) await api("keys", { provider: id, key: el.value.trim() });
        await api("settings", { setup_done: true }); start();
      }, "Finish"));
  }
}

// ───────────────────────── main app ─────────────────────────
function app() {
  const log = h("div", { id: "log" });
  const input = h("input", { placeholder: `Ask ${NAME} anything…`, autocomplete: "off", autofocus: true });
  const fileInput = h("input", { type: "file", accept: ".txt,.md,.csv,.json,.log,.py,.sh,.yaml,.yml,.pdf", style: "display:none" });
  const sendBtn = h("button", { class: "send", title: "Send", onclick: () => send() }, "➤");
  const attachBtn = h("button", { class: "mic attach", title: "Attach a text or PDF file", onclick: () => fileInput.click() }, "📎");
  const micBtn = h("button", { class: "mic", title: "Talk (click to start, click again to stop)", onclick: () => micClick() }, "🎙");
  const attachmentLabel = h("span", { class: "attachment-label" });
  const pill = h("div", { class: "pill" });
  const chips = ["Battery?", "Volume 50", "Turn off wifi", "What's the weather?", "Open browser"];
  root.replaceChildren(h("div", { class: "shell" },
    h("div", { class: "top glass" }, h("h1", {}, NAME), pill, h("button", { class: "iconbtn", title: "Settings", onclick: () => settings() }, "⚙")),
    log,
    h("div", { class: "chips" }, chips.map(c => h("button", { class: "chip", onclick: () => { input.value = c; send(); } }, c))),
    h("div", { class: "composer glass" }, input, fileInput, attachmentLabel, attachBtn, micBtn, sendBtn)));
  input.addEventListener("keydown", e => e.key === "Enter" && send());

  const refreshPill = async () => {
    OV = (await api("overview")).data;
    const on = OV.online && OV.settings.mode !== "offline" && OV.active_providers.length;
    const why = !OV.online ? "No internet · offline mode" : OV.settings.mode === "offline" ? "Offline mode (set by you)"
      : !OV.has_any_key ? "Offline · add an API key in Settings" : "AI unreachable · offline mode (see Settings → Models)";
    pill.replaceChildren(h("span", { class: "dot" + (on ? "" : " off") }), on ? `Online · ${OV.active_providers[0]}` : why);
  };
  refreshStatus = refreshPill;
  refreshPill(); setInterval(refreshPill, 20000);

  const add = (who, text, meta) => {
    log.querySelector(".empty")?.remove();
    const m = h("div", { class: "msg " + who }, text);
    if (meta?.length) m.append(h("div", { class: "meta" }, meta.map(x => h("span", { class: "tag" }, x))));
    log.append(m); log.scrollTop = log.scrollHeight; return m;
  };
  let attachment = null;
  fileInput.addEventListener("change", async () => {
    const file = fileInput.files && fileInput.files[0];
    fileInput.value = "";
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    try {
      const r = await fetch("/api/upload", { method: "POST", credentials: "same-origin",
        headers: { "X-Requested-With": "fetch" }, body: form });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || "Upload failed.");
      attachment = d;
      attachmentLabel.textContent = d.filename;
      attachBtn.classList.add("live");
      add("bot", `Attached ${d.filename}. Ask a question and send it to analyze the file.`);
    } catch (e) {
      add("bot", `⚠ ${e.message}`);
    }
  });
  if (!OV.history.length) log.append(h("div", { class: "empty" }, h("h2", {}, `Hi${OV.settings.owner ? ", " + OV.settings.owner : ""} 👋`), h("div", {}, "What would you like to do?")));
  OV.history.forEach(m => add(m.role === "user" ? "user" : "bot", m.content));

  async function send(forced, viaVoice) {
    const text = (forced ?? input.value).trim(); if ((!text && !attachment) || busy) return;
    const prompt = text || "Summarize the attached file.";
    const attached = attachment;
    if (forced === undefined) input.value = "";
    attachment = null; attachmentLabel.textContent = ""; attachBtn.classList.remove("live");
    busy = true; sendBtn.disabled = true; stopSpeaking(); add("user", attached ? `${prompt}\n📎 ${attached.filename}` : prompt);
    const typing = h("div", { class: "msg bot typing" }, h("i"), h("i"), h("i")); log.append(typing); log.scrollTop = log.scrollHeight;
    const r = await api("chat", { text: prompt, attachment: attached ? { filename: attached.filename, text: attached.text } : undefined }); typing.remove();
    const d = r.data;
    add("bot", d.reply || d.error || "Something went wrong.",
      [...(d.actions || []).map(a => "⚡ " + a.tool), d.provider ? d.provider : (d.mode === "offline" ? "offline" : ""), d.note ? "⚠ " + d.note : ""].filter(Boolean));
    busy = false; sendBtn.disabled = false; refreshPill();
    if (d.reply && (viaVoice || OV.settings.voice_enabled)) await speak(d.reply);
    input.focus();
  }

  // ── voice: speak replies ──
  let audioEl = null;
  function stopSpeaking() { if (audioEl) { audioEl.pause(); audioEl.dispatchEvent(new Event("ended")); } }
  async function speak(text) {
    stopSpeaking(); Voice.pause();
    try {
      const r = await fetch("/api/voice/speak", { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-Requested-With": "fetch" }, body: JSON.stringify({ text }) });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || "Voice failed");
      const url = URL.createObjectURL(await r.blob());
      audioEl = new Audio(url);
      await new Promise(res => { audioEl.onended = audioEl.onerror = res; audioEl.play().catch(res); });
      URL.revokeObjectURL(url);
    } catch (e) { add("bot", "⚠ " + e.message); }
    audioEl = null; Voice.resume();
  }

  // ── voice: listen ──
  let awakeUntil = 0;
  const refreshMic = () => { micBtn.classList.toggle("live", Voice.active); micBtn.title = Voice.active ? "Listening… click to stop" : "Talk"; };
  const micError = e => e && e.name === "NotAllowedError" ? "The browser blocked the microphone. Click the lock icon in the address bar and allow it."
    : e && e.name === "NotFoundError" ? "No microphone found." : "Microphone error: " + (e && e.message || e);
  async function micClick() {
    stopSpeaking();
    if (!OV.settings.mic_enabled) { add("bot", "The microphone is off. Turn it on in Settings → Voice."); settings("Voice"); return; }
    if (Voice.active) { Voice.stop(); return; }
    const hf = OV.settings.listen_mode === "handsfree";
    try {
      await Voice.start({ continuous: hf, onStop: refreshMic, onError: m => add("bot", "⚠ " + m), onUtterance: wav => heard(wav, hf) });
      if (hf) add("bot", `Listening. Say "${NAME}, …" and then your command.`);
    } catch (e) { add("bot", "⚠ " + micError(e)); }
    refreshMic();
  }
  async function heard(wav, hf) {
    micBtn.classList.add("busy");
    let d = {}, ok = false;
    try {
      const r = await fetch("/api/voice/transcribe" + (hf ? "?continuous=1" : ""), { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "audio/wav", "X-Requested-With": "fetch" }, body: wav });
      ok = r.ok; d = await r.json().catch(() => ({}));
    } catch (e) { d = { error: "Couldn't reach the assistant." }; }
    micBtn.classList.remove("busy");
    if (!ok) { add("bot", "⚠ " + (d.error || "Voice recognition failed.")); if (hf) Voice.stop(); return; }
    if (!d.text) return;
    if (!hf) return send(d.text, true);
    if (!d.woke && Date.now() > awakeUntil) return;          // not addressed to me: ignore
    awakeUntil = Date.now() + 12000;
    const cmd = d.woke ? d.command : d.text;
    return cmd ? send(cmd, true) : speak("Yes.");
  }
  addEventListener("keydown", e => e.key === "Escape" && stopSpeaking());
  pollApprovals();
}

// ───────────────────────── approvals ─────────────────────────
async function pollApprovals() {
  try {
    const items = (await api("approvals")).data.items || [];
    if (items.length && !approvalOpen) {
      const it = items[0];
      const done = async (ok, always = false) => {
        approvalOpen.remove(); approvalOpen = null;
        await api("approvals/" + it.id, { approve: ok, always });
      };
      approvalOpen = overlay(h("div", { class: "card glass" },
        h("h2", {}, `${NAME} wants to act`), h("p", { class: "sub" }, it.description),
        h("pre", { class: "args" }, it.tool + "\n" + JSON.stringify(it.args, null, 2)),
        h("div", { class: "actions" }, h("button", { class: "btn ghost", onclick: () => done(false) }, "Deny"),
          h("button", { class: "btn ghost", onclick: () => done(true) }, "Allow once"),
          h("button", { class: "btn", onclick: () => done(true, true) }, "Always allow this exact action"))));
    }
  } catch (e) {}
  setTimeout(pollApprovals, 1200);
}

// ───────────────────────── settings ─────────────────────────
function settings(first) {
  const sheet = h("div", { class: "sheet glass" }), body = h("div", { class: "body" });
  const tabs = ["Permissions", "Models", "Voice", "General", "Activity"];
  const tabBar = h("div", { class: "tabs" });
  const show = async name => {
    [...tabBar.children].forEach(b => b.classList.toggle("on", b.textContent === name));
    OV = (await api("overview")).data; body.replaceChildren(...await panels[name]());
  };
  tabs.forEach(t => tabBar.append(h("button", { class: "tab", onclick: () => show(t) }, t)));
  sheet.append(h("header", {}, h("h2", {}, "Settings"), h("button", { class: "iconbtn", onclick: () => sheet.remove() }, "✕")), tabBar, body);
  document.body.append(sheet); show(first || "Permissions");
}

const panels = {
  async Permissions() {
    const alwaysRows = (OV.always_allowed || []).map(item => h("div", { class: "row" },
      h("div", { class: "t" }, h("b", {}, item.tool), h("span", {}, `Exact action · ${item.fingerprint.slice(0, 10)}`)),
      h("button", { class: "btn ghost", onclick: async () => {
        await api("permissions/actions/" + item.fingerprint, undefined, "DELETE"); settingsRefresh();
      } }, "Revoke")));
    return [h("small", { class: "hint" }, "Only you can change these. I can't."),
      ...OV.groups.map(g => h("div", { class: "row" }, h("div", { class: "t" }, h("b", {}, g.label), h("span", {}, g.desc)),
        seg([["off", "Off"], ["ask", "Ask"], ["allow", "Allow"]], g.level, v => api("permissions", { levels: { [g.id]: v } })))),
      h("b", {}, "Always-approved exact actions"),
      ...(alwaysRows.length ? alwaysRows : [h("small", { class: "hint" }, "None")])];
  },
  async Models() {
    const order = OV.settings.provider_order.filter(id => OV.providers.some(p => p.id === id));
    const byId = Object.fromEntries(OV.providers.map(p => [p.id, p]));
    const sorted = [...order.map(id => byId[id]), ...OV.providers.filter(p => !order.includes(p.id))];
    const rows = sorted.map((p, i) => {
      const keyField = h("input", { class: "field", type: "password", placeholder: p.has_key ? "Saved ✓ (type to replace)" : "Paste API key" });
      const modelField = h("input", { class: "field", value: p.model, placeholder: "model name" });
      const status = h("small", { class: p.error ? "err" : "hint" }, p.error ? "⚠ " + p.error : "");
      const save = h("button", { class: "btn", onclick: async () => {
        if (keyField.value.trim()) await api("keys", { provider: p.id, key: keyField.value.trim() });
        if (modelField.value.trim() && modelField.value.trim() !== p.model) await api("settings", { models: { [p.id]: modelField.value.trim() } });
        keyField.value = ""; settingsRefresh(); } }, "Save");
      const test = h("button", { class: "btn ghost", onclick: async () => { status.className = "hint"; status.textContent = "Testing…";
        const r = (await api("keys/test", { provider: p.id })).data; status.className = r.ok ? "hint" : "err"; status.textContent = (r.ok ? "" : "⚠ ") + r.message; } }, "Test");
      const useFirst = i === 0 ? null : h("button", { class: "chip", title: "Try this one first", onclick: async () => {
        await api("settings", { provider_order: [p.id, ...order.filter(id => id !== p.id)] }); settingsRefresh(); } }, "Use first ↑");
      const del = p.custom ? h("button", { class: "chip", onclick: async () => { await api("providers/custom/" + p.id, undefined, "DELETE"); settingsRefresh(); } }, "Remove") : null;
      return h("div", { class: "row", style: "flex-direction:column;align-items:stretch" },
        h("div", { style: "display:flex;justify-content:space-between;align-items:baseline" },
          h("div", {}, h("b", {}, (i === 0 ? "① " : "") + p.label + " "), !p.custom && h("small", { class: "hint" }, p.note + " · "),
            p.url && h("a", { href: p.url, target: "_blank", rel: "noopener" }, "get key")),
          h("div", { style: "display:flex;gap:6px" }, useFirst, del)),
        h("small", { class: "hint" }, "Model"), modelField,
        h("div", { style: "display:flex;gap:8px" }, keyField, save, test), status);
    });
    const cid = h("input", { class: "field", placeholder: "short id, e.g. together" });
    const clabel = h("input", { class: "field", placeholder: "Display name, e.g. Together AI" });
    const cbase = h("input", { class: "field", placeholder: "https://api.together.xyz/v1" });
    const cmodel = h("input", { class: "field", placeholder: "model name" });
    const ckey = h("input", { class: "field", type: "password", placeholder: "API key" });
    const cerr = h("div", { class: "err" });
    const addBtn = h("button", { class: "btn", onclick: async () => {
      const r = await api("providers/custom", { id: cid.value.trim(), label: clabel.value.trim(), base: cbase.value.trim(), model: cmodel.value.trim(), key: ckey.value.trim() });
      if (r.ok) settingsRefresh(); else cerr.textContent = r.data.error || "Couldn't add that provider."; } }, "Add provider");
    return [h("small", { class: "hint" }, "① is tried first; I fall back automatically if one fails. Any provider's model name can be changed - Void isn't locked to one."), ...rows,
      h("b", {}, "Add any other OpenAI-compatible provider"),
      h("div", { class: "stack" }, cid, clabel, cbase, cmodel, ckey, cerr, addBtn)];
  },
  async General() {
    const s = OV.settings;
    const owner = h("input", { class: "field", value: s.owner, placeholder: "Your name" });
    const city = h("input", { class: "field", value: s.city, placeholder: "City" });
    const contacts = h("textarea", { class: "field", rows: 3, placeholder: "mum=123456789" }, Object.entries(s.telegram_contacts).map(([k, v]) => `${k}=${v}`).join("\n"));
    const save = h("button", { class: "btn", onclick: async () => {
      const tc = {}; contacts.value.split("\n").forEach(l => { const [k, v] = l.split("="); if (k && v) tc[k.trim()] = v.trim(); });
      await api("settings", { owner: owner.value, city: city.value, telegram_contacts: tc }); save.textContent = "Saved ✓"; } }, "Save");
    return [
      h("div", { class: "row" }, h("div", { class: "t" }, h("b", {}, "Theme"), h("span", {}, "Void is pure black")),
        seg([["void", "Void"], ["light", "Light"]], localStorage.getItem("theme") || "void", v => { localStorage.setItem("theme", v); document.documentElement.dataset.theme = v; })),
      h("div", { class: "row" }, h("div", { class: "t" }, h("b", {}, "Black hole animation"), h("span", {}, "Reduced or Off saves battery on older laptops")),
        seg([["full", "Full"], ["reduced", "Reduced"], ["off", "Off"]], getFx(), v => setFx(v))),
      h("div", { class: "row" }, h("div", { class: "t" }, h("b", {}, "Mode"), h("span", {}, "Auto uses AI when online")),
        seg([["auto", "Auto"], ["online", "Online"], ["offline", "Offline"]], s.mode, v => {
          s.mode = v; OV.settings.mode = v;
          api("settings", { mode: v }).then(refreshStatus);
        })),
      h("div", { class: "row" }, h("div", { class: "t" }, h("b", {}, "Speak replies"), h("span", {}, "Local female voice; microphone stays off until enabled")), toggle(s.voice_enabled, v => {
        s.voice_enabled = v; OV.settings.voice_enabled = v; api("settings", { voice_enabled: v });
      })),
      h("div", { class: "row" }, h("div", { class: "t" }, h("b", {}, "Small local model"), h("span", {}, "Chit-chat when offline (needs Ollama)")), toggle(s.offline_llm, v => {
        s.offline_llm = v; OV.settings.offline_llm = v; api("settings", { offline_llm: v });
      })),
      h("small", { class: "hint" }, "Connect Ollama locally: install Ollama, run `ollama pull qwen3.5:0.8b`, and keep it running. Void connects at 127.0.0.1:11434."),
      h("small", { class: "hint" }, "Qwen3.5 0.8B downloads about 1.2 GB and typically needs roughly 2-3 GB RAM for short chats. It runs on CPU on this laptop; expect sustained high CPU use and slower generation. The 0.5B choice is smaller, but less capable."),
      h("small", { class: "hint" }, "Choose a model tag that you have pulled with Ollama."),
      h("select", { class: "field", onchange: e => api("settings", { ollama_model: e.target.value }) },
        OV.ollama_choices.map(([id, label]) => h("option", { value: id, selected: id === s.ollama_model ? "" : null }, label))),
      h("div", { class: "row" }, h("div", { class: "t" }, h("b", {}, "Allow other devices on my network"), h("span", {}, "Takes effect after restart. Keep off unless needed")), toggle(s.allow_lan, v => api("settings", { allow_lan: v }))),
      owner, city, h("small", { class: "hint" }, "Telegram contacts (name=chat id, one per line)"), contacts, save,
      h("div", { class: "actions" }, h("button", { class: "btn ghost", onclick: async () => { await api("chat/clear", {}); location.reload(); } }, "Clear chat"),
        h("button", { class: "btn ghost", onclick: async () => { await api("auth/logout", {}); location.reload(); } }, "Lock"))];
  },
  async Voice() {
    const st = (await api("voice/status")).data, s = OV.settings;
    const stt = h("small", { class: "hint" }), tts = h("small", { class: "hint" }), bars = h("div", { class: "stack" });
    const line = () => {
      stt.textContent = `Recognition → online: ${st.stt_online_provider ? st.stt_online_provider + " (Whisper)" : "needs a Groq or OpenAI key"} · local: ${st.stt_ready ? "ready" : st.vosk_pkg ? "model not downloaded" : "vosk not installed"}`;
      tts.textContent = `Voice → online: ${st.edge_pkg ? "Edge neural" : "edge-tts not installed"} · local: ${st.tts_ready ? "Piper ready" : st.piper_pkg ? "voice not downloaded" : "piper not installed"}${st.espeak ? " · espeak fallback" : ""}`;
    }; line();
    const install = h("button", { class: "btn ghost", onclick: async () => {
      install.disabled = true;
      await api("voice/install", { what: "stt" }); await api("voice/install", { what: "tts" });
      const poll = async () => {
        const p = (await api("voice/status")).data;
        bars.replaceChildren(...["stt", "tts"].map(k => h("div", {}, h("small", { class: p.progress[k].state === "error" ? "err" : "hint" },
          (k === "stt" ? "Speech model: " : "Voice model: ") + (p.progress[k].msg || p.progress[k].state)), h("div", { class: "bar" }, h("i", { style: `width:${p.progress[k].pct}%` })))));
        if (["downloading"].includes(p.progress.stt.state) || p.progress.tts.state === "downloading") setTimeout(poll, 1200);
        else { install.disabled = false; Object.assign(st, p); line(); }
      }; poll(); } }, "Download offline voice (~100 MB)");
    const voices = h("select", { class: "field", onchange: e => api("settings", { edge_voice: e.target.value }) },
      Object.entries(st.voices).map(([k, v]) => h("option", { value: k, selected: k === s.edge_voice ? "" : null }, v)));
    const test = h("button", { class: "btn ghost", onclick: async () => {
      test.textContent = "Speaking…";
      const r = await fetch("/api/voice/speak", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-Requested-With": "fetch" }, body: JSON.stringify({ text: `I am ${NAME}. Systems online.` }) });
      if (r.ok) { const a = new Audio(URL.createObjectURL(await r.blob())); a.play(); test.textContent = "Test voice"; }
      else { test.textContent = "Test voice"; alert((await r.json()).error); } } }, "Test voice");
    return [
      h("div", { class: "row" }, h("div", { class: "t" }, h("b", {}, "Microphone voice commands"), h("span", {}, "Off until you turn it on. The browser also asks.")), toggle(s.mic_enabled, v => { s.mic_enabled = v; api("settings", { mic_enabled: v }); })),
      h("div", { class: "row" }, h("div", { class: "t" }, h("b", {}, "Listening"), h("span", {}, `Tap to talk, or hands-free: say "${NAME}, …"`)),
        seg([["push", "Tap"], ["handsfree", "Hands-free"]], s.listen_mode, v => { s.listen_mode = v; api("settings", { listen_mode: v }); })),
      h("div", { class: "row" }, h("div", { class: "t" }, h("b", {}, "Speech recognition"), h("span", {}, "Hands-free + Auto stays local, so room audio is never uploaded")),
        seg([["auto", "Auto"], ["online", "Online"], ["local", "Local"]], s.stt_engine, v => {
          s.stt_engine = v; OV.settings.stt_engine = v; api("settings", { stt_engine: v });
        })),
      h("div", { class: "row" }, h("div", { class: "t" }, h("b", {}, "Speak every reply"), h("span", {}, "Replies to spoken commands are always spoken")), toggle(s.voice_enabled, v => { s.voice_enabled = v; api("settings", { voice_enabled: v }); })),
      h("div", { class: "row" }, h("div", { class: "t" }, h("b", {}, "Voice output"), h("span", {}, "Online = Edge neural · Local = Piper")),
        seg([["auto", "Auto"], ["online", "Online"], ["local", "Local"]], s.tts_engine, v => {
          s.tts_engine = v; OV.settings.tts_engine = v; api("settings", { tts_engine: v });
        })),
      h("small", { class: "hint" }, "Online voice"), voices, stt, tts, bars,
      h("div", { class: "actions" }, test, install)];
  },
  async Activity() {
    const a = (await api("audit")).data.items || [], sch = (await api("schedules")).data.items || [];
    return [h("b", {}, "Scheduled tasks"),
      ...(sch.length ? sch.map(s => h("div", { class: "row" }, h("div", { class: "t" }, h("b", {}, s.label), h("span", {}, `${s.time} · ${s.days} · ${s.action}`)),
        h("button", { class: "chip", onclick: async () => { await api("schedules/" + s.id, undefined, "DELETE"); settingsRefresh(); } }, "Delete"))) : [h("small", { class: "hint" }, "None. Ask me to schedule something.")]),
      h("b", {}, "Everything I've done"),
      ...(a.length ? a.map(x => h("div", { class: "log-row" }, h("b", {}, x.tool), ` ${x.t} · ${x.outcome}`)) : [h("small", { class: "hint" }, "Nothing yet.")])];
  }
};
function settingsRefresh() { const t = document.querySelector(".tab.on"); t && t.click(); }

start();
