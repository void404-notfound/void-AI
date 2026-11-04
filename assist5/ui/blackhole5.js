"use strict";
/* Black hole background (pure canvas, no libraries).

   Performance design: everything expensive (gradients, wide strokes, glow, star field) is drawn ONCE into two
   cached layers whenever the window size changes. Each animation frame only blits those layers and moves a few
   hundred tiny orbiting specks, so the per-frame cost is small even on old integrated graphics.
   The canvas is drawn at full resolution (no down-scaled canvas stretched by CSS, which gave hard, blocky edges).

     back layer  : space + stars + far half of the accretion disk + glow
     (specks behind the hole)
     front layer : black shadow + bright photon ring + near half of the disk (transparent elsewhere)
     (specks in front of the hole)                                                                          */
(() => {
  const canvas = document.createElement("canvas");
  canvas.id = "bh";
  canvas.setAttribute("aria-hidden", "true");
  document.body.prepend(canvas);
  const ctx = canvas.getContext("2d", { alpha: false });
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let mode = localStorage.getItem("fx") || (reducedMotion ? "reduced" : "full");
  let width = 0, height = 0, dpr = 1, frameId = 0, lastFrame = 0, time = 0, resizeTimer = 0;
  let back = null, front = null, geo = null, specks = [];

  const COLORS = ["#572039", "#91344a", "#cf5037", "#f18a43", "#ffc66d", "#fff0c8"];
  const BG = "#030408";

  function makeLayer(w, h) {
    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    const g = c.getContext("2d");
    g.scale(dpr, dpr);
    return [c, g];
  }

  // small deterministic generator so the star field doesn't reshuffle on every resize
  function rng(seed) { return () => (seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296; }

  function bandGradient(g, cx, cy, outer) {
    const band = g.createLinearGradient(cx - outer, cy, cx + outer, cy);
    band.addColorStop(0, "rgba(104,30,68,0.28)");
    band.addColorStop(0.28, "rgba(238,99,53,0.72)");
    band.addColorStop(0.5, "rgba(255,235,179,0.98)");
    band.addColorStop(0.72, "rgba(255,160,63,0.86)");
    band.addColorStop(1, "rgba(115,34,41,0.25)");
    return band;
  }

  function bake() {
    const reduced = mode === "reduced";
    width = innerWidth; height = innerHeight;
    dpr = reduced ? 1 : Math.min(devicePixelRatio || 1, 1.5);
    const pw = Math.max(1, Math.round(width * dpr)), ph = Math.max(1, Math.round(height * dpr));
    canvas.width = pw; canvas.height = ph;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const compact = width < 600;
    const radius = Math.min(width, height) * (compact ? 0.09 : 0.115);
    const cx = width * 0.70, cy = height * (compact ? 0.60 : 0.68);
    const outer = radius * (compact ? 2.9 : 3.3);
    geo = { radius, cx, cy, outer };

    // ---- back layer
    let g;
    [back, g] = makeLayer(pw, ph);
    g.fillStyle = BG;
    g.fillRect(0, 0, width, height);
    const rand = rng(7);
    g.fillStyle = "#bacce4";
    for (let i = 0; i < (reduced ? 70 : 140); i++) {
      g.globalAlpha = 0.12 + rand() * 0.38;
      const size = rand() < 0.12 ? 1.5 : 0.9;
      g.fillRect(rand() * width, rand() * height, size, size);
    }
    g.globalAlpha = 1;
    g.globalCompositeOperation = "lighter";
    const band = bandGradient(g, cx, cy, outer);
    g.globalAlpha = 0.9;
    g.strokeStyle = band;
    g.lineWidth = radius * 0.29;
    g.beginPath();
    g.ellipse(cx, cy, outer, radius * 0.58, 0, Math.PI, Math.PI * 2);   // far half of the disk
    g.stroke();
    const glow = g.createRadialGradient(cx, cy, radius * 0.8, cx, cy, radius * 1.45);
    glow.addColorStop(0, "rgba(255,231,182,0.64)");
    glow.addColorStop(0.25, "rgba(255,145,67,0.27)");
    glow.addColorStop(0.66, "rgba(200,63,42,0.08)");
    glow.addColorStop(1, "rgba(0,0,0,0)");
    g.globalAlpha = 1;
    g.fillStyle = glow;
    g.beginPath();
    g.arc(cx, cy, radius * 1.45, 0, Math.PI * 2);
    g.fill();

    // ---- front layer (transparent)
    [front, g] = makeLayer(pw, ph);
    g.fillStyle = "#010102";
    g.beginPath();
    g.arc(cx, cy, radius, 0, Math.PI * 2);                              // the shadow
    g.fill();
    g.strokeStyle = "rgba(255,237,201,0.95)";
    g.lineWidth = Math.max(1.2, radius * 0.018);
    g.beginPath();
    g.arc(cx, cy, radius * 1.04, 0, Math.PI * 2);                       // photon ring
    g.stroke();
    g.globalAlpha = 0.92;
    g.strokeStyle = band;
    g.lineWidth = radius * 0.29;
    g.beginPath();
    g.ellipse(cx, cy, outer, radius * 0.58, 0, 0, Math.PI);             // near half of the disk
    g.stroke();

    // ---- orbiting specks (the only thing that really moves)
    const r2 = rng(11);
    specks = Array.from({ length: reduced ? 220 : 420 }, () => {
      const rr = 1.25 + Math.pow(r2(), 1.6) * 3.35;
      const heat = 1 - (rr - 1.25) / 3.35;
      return {
        r: rr, a: r2() * Math.PI * 2, v: 0.012 / Math.pow(rr, 1.35),
        s: 0.9 + r2() * 1.6, y: (r2() - 0.5) * 0.14,
        c: Math.min(COLORS.length - 1, Math.floor(heat * COLORS.length)), x: 0, py: 0, fr: false
      };
    });
  }

  function draw(now) {
    frameId = 0;
    if (mode === "off" || document.hidden) return;
    const fps = mode === "reduced" ? 12 : 20;
    if (now - lastFrame < 1000 / fps - 2) { frameId = requestAnimationFrame(draw); return; }
    const dt = Math.min(100, now - (lastFrame || now)) / 16.7;
    lastFrame = now;
    time += dt;

    const { radius, cx, cy } = geo;
    // slow whole-scene drift, in whole pixels (fractional offsets would resample/blur the cached layers)
    const dx = Math.round(Math.sin(time * 0.0014) * width * 0.014);
    const dy = Math.round(Math.cos(time * 0.0011) * height * 0.008);

    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = 1;
    ctx.fillStyle = BG;
    ctx.fillRect(0, 0, width, height);
    ctx.drawImage(back, dx, dy, width, height);

    for (const p of specks) {
      p.a += p.v * dt;
      const sin = Math.sin(p.a);
      p.x = cx + dx + p.r * radius * Math.cos(p.a);
      p.py = cy + dy + (p.r * radius * sin + p.y * radius) * 0.28;
      p.fr = sin >= 0;
    }
    ctx.globalCompositeOperation = "lighter";
    drawSpecks(false);
    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = 1;                                // (specks leave a partial alpha set)
    ctx.drawImage(front, dx, dy, width, height);
    ctx.globalCompositeOperation = "lighter";
    drawSpecks(true);
    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = 1;
    frameId = requestAnimationFrame(draw);
  }

  function drawSpecks(isFront) {
    for (let c = 0; c < COLORS.length; c++) {          // one style change per colour, not per speck
      ctx.fillStyle = COLORS[c];
      ctx.globalAlpha = (isFront ? 0.3 : 0.24) + (c / (COLORS.length - 1)) * (isFront ? 0.62 : 0.58);
      for (const p of specks) if (p.c === c && p.fr === isFront) ctx.fillRect(p.x, p.py, p.s, p.s);
    }
  }

  function start() {
    if (!frameId && mode !== "off" && !document.hidden) frameId = requestAnimationFrame(draw);
  }

  function apply(nextMode) {
    mode = ["full", "reduced", "off"].includes(nextMode) ? nextMode : "full";
    try { localStorage.setItem("fx", mode); } catch (e) { /* private mode: not fatal */ }
    cancelAnimationFrame(frameId);
    frameId = 0;
    canvas.style.display = mode === "off" ? "none" : "block";
    if (mode !== "off") {
      bake();
      lastFrame = 0;
      start();
    }
  }

  window.setFx = apply;
  window.getFx = () => mode;
  if (window.__bhTest) window.__bhTest = { canvas, draw };   // test hook; inactive unless a test sets it first
  addEventListener("resize", () => {
    clearTimeout(resizeTimer);                          // re-bake once the window stops changing size
    resizeTimer = setTimeout(() => { if (mode !== "off") bake(); }, 150);
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      cancelAnimationFrame(frameId);
      frameId = 0;
    } else {
      lastFrame = 0;
      start();
    }
  });
  apply(mode);
})();
