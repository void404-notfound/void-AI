"use strict";
/* Microphone capture with voice-activity detection. Records a spoken phrase, converts it to 16 kHz mono
   16-bit WAV in the browser, and hands it to the app. No libraries; works the same for local (Vosk)
   and online (Whisper) recognition on the server. */
const Voice = (() => {
  const TARGET = 16000;
  let ctx = null, stream = null, proc = null, src = null, opts = {};
  let active = false, paused = false;
  let pre = [], buf = [], speaking = false, speechMs = 0, silentMs = 0, idleMs = 0, noise = 0.004;

  const rms = f => { let s = 0; for (let i = 0; i < f.length; i++) s += f[i] * f[i]; return Math.sqrt(s / f.length); };

  function resample(f32, from) {
    if (from === TARGET) return f32;
    const ratio = from / TARGET, n = Math.floor(f32.length / ratio), out = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const a = Math.floor(i * ratio), b = Math.min(f32.length, Math.floor((i + 1) * ratio));
      let s = 0; for (let j = a; j < b; j++) s += f32[j];
      out[i] = s / Math.max(1, b - a);
    }
    return out;
  }

  function encodeWav(frames, from) {
    const total = frames.reduce((n, f) => n + f.length, 0), all = new Float32Array(total);
    let o = 0; for (const f of frames) { all.set(f, o); o += f.length; }
    const pcm = resample(all, from), out = new ArrayBuffer(44 + pcm.length * 2), v = new DataView(out);
    const w = (off, s) => { for (let i = 0; i < s.length; i++) v.setUint8(off + i, s.charCodeAt(i)); };
    w(0, "RIFF"); v.setUint32(4, 36 + pcm.length * 2, true); w(8, "WAVE"); w(12, "fmt ");
    v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, TARGET, true); v.setUint32(28, TARGET * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
    w(36, "data"); v.setUint32(40, pcm.length * 2, true);
    for (let i = 0; i < pcm.length; i++) { const s = Math.max(-1, Math.min(1, pcm[i])); v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true); }
    return new Blob([out], { type: "audio/wav" });
  }

  function reset() { pre = []; buf = []; speaking = false; speechMs = silentMs = idleMs = 0; }

  function handle(f) {
    if (!active || paused) return;
    const ms = f.length / ctx.sampleRate * 1000, level = rms(f), thr = Math.max(0.012, noise * 3.5);
    if (!speaking) {
      noise = noise * 0.97 + level * 0.03;
      pre.push(f); if (pre.length > 4) pre.shift();
      idleMs += ms;
      if (level > thr) { speaking = true; buf = pre.slice(); speechMs = ms; silentMs = 0; opts.onSpeechStart && opts.onSpeechStart(); }
      else if (!opts.continuous && idleMs > 8000) stop("I didn't hear anything.");
      return;
    }
    buf.push(f);
    if (level > thr) { speechMs += ms; silentMs = 0; } else silentMs += ms;
    if (silentMs > 650 || speechMs + silentMs > 15000) finish();
  }

  function finish() {
    const frames = buf, ok = speechMs > 300, rate = ctx.sampleRate;
    reset();
    if (!ok) return;
    const blob = encodeWav(frames, rate);
    if (!opts.continuous) stop();
    opts.onUtterance && opts.onUtterance(blob);
  }

  async function start(o) {
    if (active) return;
    opts = o || {};
    stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 } });
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    if (ctx.state === "suspended") await ctx.resume();
    src = ctx.createMediaStreamSource(stream);
    proc = ctx.createScriptProcessor(4096, 1, 1);
    proc.onaudioprocess = e => handle(new Float32Array(e.inputBuffer.getChannelData(0)));
    src.connect(proc); proc.connect(ctx.destination);       // output stays silent (we never write to it)
    reset(); paused = false; active = true;
  }

  function stop(reason) {
    if (!active) return;
    active = false;
    try { proc.disconnect(); src.disconnect(); } catch (e) {}
    stream && stream.getTracks().forEach(t => t.stop());
    ctx && ctx.close().catch(() => {});
    reset();
    opts.onStop && opts.onStop();
    if (reason && opts.onError) opts.onError(reason);
  }

  // pause while the assistant speaks so it never hears itself
  const pause = () => { paused = true; reset(); };
  const resume = () => { reset(); paused = false; };

  return { start, stop, pause, resume, get active() { return active; }, _test: { encodeWav, resample } };
})();
if (typeof module !== "undefined") module.exports = Voice;
