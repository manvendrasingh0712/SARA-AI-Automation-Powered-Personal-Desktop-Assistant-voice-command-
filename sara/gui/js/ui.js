/* ==========================================================================
   ui.js -- tiny shared UI helpers on the global `SARA` object: HTML escaping, time formatting,
   toasts, the generated sound set (Web Audio, no files; all respect SARA.sound.on), the eased boot overlay,
   and SARA.reduceMotion. NO backend calls here.
   Used by: every other js/*.js file.  Styles: style/layout.css (.toast), style/overlays.css (.boot-overlay).
   Loaded FIRST (creates window.SARA).
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA = window.SARA || {};
  SARA.state = { status: 'sleeping', assistantActive: true, focus: false, muted: false };

  /* OS "reduce motion" preference, kept live. JS-driven motion (typewriter, orb drift, spectrum, boot bar) checks this. */
  const rmq = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
  SARA.reduceMotion = !!(rmq && rmq.matches);
  if (rmq && rmq.addEventListener) rmq.addEventListener('change', function (e) { SARA.reduceMotion = e.matches; });

  /* ---- helpers ---- */
  SARA.$ = (id) => document.getElementById(id);
  // Schedule non-urgent DOM work (periodic re-renders, stat formatting) off the critical rendering
  // path, so it never competes with the orb/spectrum rAF loops for frame budget. Falls back to a
  // macrotask on browsers without requestIdleCallback (e.g. older WebKit inside pywebview).
  SARA.idle = function (fn) {
    if (typeof window.requestIdleCallback === 'function') window.requestIdleCallback(fn, { timeout: 1000 });
    else setTimeout(fn, 0);
  };
  SARA.escapeHtml = function (s) {
    const d = document.createElement('div');
    d.textContent = (s === null || s === undefined) ? '' : String(s);
    return d.innerHTML;
  };
  SARA.fmt12h = function (hhmm) {            // "18:30" -> "6:30 PM"
    if (!hhmm) return '';
    const p = String(hhmm).split(':');
    let h = parseInt(p[0], 10); const m = parseInt(p[1] || '0', 10);
    if (isNaN(h)) return String(hhmm);
    const ap = h >= 12 ? 'PM' : 'AM'; h = h % 12 || 12;
    return h + ':' + String(m).padStart(2, '0') + ' ' + ap;
  };
  SARA.fmtClockTime = function (date) {      // Date -> "6:04 PM"
    let h = date.getHours(); const m = String(date.getMinutes()).padStart(2, '0');
    const ap = h >= 12 ? 'PM' : 'AM'; h = h % 12 || 12;
    return h + ':' + m + ' ' + ap;
  };
  SARA.fmtDuration = function (sec) {        // 125 -> "2:05"
    sec = Math.max(0, Math.round(sec || 0));
    return Math.floor(sec / 60) + ':' + String(sec % 60).padStart(2, '0');
  };
  SARA.relTime = function (iso) {            // ISO -> "5m ago"
    if (!iso) return '';
    const then = new Date(iso).getTime();
    if (isNaN(then)) return String(iso);
    const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
    if (mins < 1) return 'just now';
    if (mins < 60) return mins + 'm ago';
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return hrs + 'h ago';
    return Math.round(hrs / 24) + 'd ago';
  };
  SARA.todayStr = function (offsetDays) {    // local "YYYY-MM-DD"
    const d = new Date(); d.setDate(d.getDate() + (offsetDays || 0));
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  };

  /* ---- toasts ---- */
  // opts.tone === 'notify' plays the notification chime; alert-type icons play the soft error tone.
  SARA.toast = function (iconClass, color, message, opts) {
    const stack = SARA.$('toastStack'); if (!stack) return;
    const t = document.createElement('div'); t.className = 'toast';
    const dot = document.createElement('span'); dot.className = 't-dot';
    dot.style.background = color || 'var(--core)'; dot.style.color = color || 'var(--core)';
    const msg = document.createElement('span'); msg.textContent = message == null ? '' : String(message);
    const x = document.createElement('button'); x.type = 'button'; x.className = 't-x'; x.textContent = '\u00d7'; x.setAttribute('aria-label', 'Dismiss');
    t.appendChild(dot); t.appendChild(msg); t.appendChild(x);
    if (opts && opts.sub) {
      const small = document.createElement('small'); small.className = 't-sub';
      small.textContent = String(opts.sub);
      small.style.opacity = '0.7'; small.style.fontSize = '11px'; small.style.display = 'block';
      t.appendChild(small);
    }
    stack.appendChild(t);
    if (/alert|error/i.test(iconClass || '')) SARA.sound.error();
    else if (opts && opts.tone === 'notify') SARA.sound.notify();
    while (stack.children.length > 4) stack.removeChild(stack.firstChild);
    let fadeTimer = 0, leaving = false;
    function dismiss() {                       // shared by the 4.5s auto-fade and the x button
      if (leaving) return; leaving = true; clearTimeout(fadeTimer);
      t.classList.add('leaving'); setTimeout(() => t.remove(), 240);
    }
    x.addEventListener('click', dismiss);
    fadeTimer = setTimeout(dismiss, 4500);
  };
  SARA.ok = (m) => SARA.toast('ti-check', '#3FD8C4', m);
  SARA.fail = (m) => SARA.toast('ti-alert-triangle', '#D97A6B', m);

  let hintShown = 0;
  SARA.proactiveHint = function () {         // one-time tip, max 3 times ever
    try {
      let c = parseInt(localStorage.getItem('sara_proactive_hint_count') || '0', 10) || 0;
      if (c >= 3 || hintShown) return;
      localStorage.setItem('sara_proactive_hint_count', String(c + 1)); hintShown = 1;
    } catch (e) { return; }
    SARA.toast('ti-bulb', '#8B6FD8', 'Tip: ask "why did you say that?" and Sara will explain.');
  };

  /* ---- sound blips (Web Audio, no files). Every sound goes through tone(), which returns silently when SARA.sound.on is false. ---- */
  let actx = null;
  SARA.sound = {
    on: (function () { try { return localStorage.getItem('sara_ui_sounds') !== 'off'; } catch (e) { return true; } })(),
    set(v) { this.on = !!v; try { localStorage.setItem('sara_ui_sounds', this.on ? 'on' : 'off'); } catch (e) {} },
    tone(freq, dur, type, vol, delay) {          // delay = seconds from now (lets one call schedule a small sequence)
      if (!this.on) return;
      try {
        actx = actx || new (window.AudioContext || window.webkitAudioContext)();
        if (actx.state === 'suspended') actx.resume();
        const d = dur || 0.05, v = vol || 0.04, t0 = actx.currentTime + (delay || 0);
        const o = actx.createOscillator(), g = actx.createGain();
        o.type = type || 'sine'; o.frequency.value = freq || 440;
        g.gain.setValueAtTime(0.0001, t0); g.gain.linearRampToValueAtTime(v, t0 + 0.008);
        g.gain.exponentialRampToValueAtTime(0.0001, t0 + d);
        o.connect(g); g.connect(actx.destination); o.start(t0); o.stop(t0 + d + 0.02);
      } catch (e) { /* audio unavailable -- ignore */ }
    },
    tap() { this.tone(500, 0.04, 'sine', 0.035); },
    toggle(on) { this.tone(on ? 720 : 340, 0.05, 'triangle', 0.03); },
    wake() { this.tone(520, 0.09, 'sine', 0.05); setTimeout(() => SARA.sound.tone(780, 0.12, 'sine', 0.05), 90); },
    // --- event sounds (each < 150ms, quiet) ---
    sent()     { this.tone(520, 0.05, 'sine', 0.028);     this.tone(700, 0.07, 'sine', 0.026, 0.04); },     // chat: message sent
    received() { this.tone(660, 0.06, 'triangle', 0.026); this.tone(500, 0.09, 'triangle', 0.022, 0.05); }, // chat: Sara replied
    added()    { this.tone(600, 0.05, 'sine', 0.03);      this.tone(900, 0.09, 'sine', 0.026, 0.04); },     // reminder added
    done()     { this.tone(660, 0.06, 'sine', 0.03);      this.tone(830, 0.09, 'sine', 0.028, 0.05); },     // reminder completed
    notify()   { this.tone(880, 0.12, 'sine', 0.028);     this.tone(1320, 0.09, 'sine', 0.01, 0.01); },     // notification arrived
    run()      { [440, 554, 660].forEach((f, i) => this.tone(f, 0.05, 'triangle', 0.026, i * 0.04)); },     // routine started
    saved()    { this.tone(520, 0.06, 'triangle', 0.028); this.tone(780, 0.09, 'triangle', 0.026, 0.05); }, // routine saved
    error()    { this.tone(240, 0.07, 'sine', 0.03);      this.tone(190, 0.08, 'sine', 0.028, 0.06); }      // something failed (soft, low)
  };

  /* ---- boot overlay (driven by 'boot_progress' + 'backend_ready' events) ----
     The percentage the backend sends is only the TARGET; the bar eases toward it every frame so it never jumps. */
  let boot = null, bootDone = false, bootTarget = 0, bootShown = 0;
  function ensureBoot() {
    if (boot || bootDone) return boot;
    const root = document.createElement('div'); root.className = 'boot-overlay';
    root.innerHTML = '<div class="boot-orb"></div><div class="boot-msg">Starting up…</div><div class="boot-track"><div class="boot-fill"></div></div>';
    document.body.appendChild(root);
    boot = { root, msg: root.querySelector('.boot-msg'), fill: root.querySelector('.boot-fill') };
    requestAnimationFrame(bootTick);
    return boot;
  }
  function bootTick() {
    if (!boot) return;
    const diff = bootTarget - bootShown;
    bootShown = (SARA.reduceMotion || Math.abs(diff) < 0.1) ? bootTarget : bootShown + diff * 0.08;
    boot.fill.style.width = bootShown.toFixed(2) + '%';
    if (bootTarget >= 100 && bootShown >= 99.5) { finishBoot(); return; }
    requestAnimationFrame(bootTick);
  }
  function finishBoot() {
    if (bootDone) return; bootDone = true;
    const o = boot; boot = null; if (!o) return;
    o.root.style.pointerEvents = 'none'; o.root.style.opacity = '0';
    setTimeout(() => o.root.remove(), 420);
  }
  SARA.bootOverlay = {
    update(message, percent) {
      if (bootDone) return;
      const o = ensureBoot(); if (!o) return;
      if (message) o.msg.textContent = message;
      const pct = Math.max(0, Math.min(100, Number(percent) || 0));
      if (pct > bootTarget) bootTarget = pct;                // never slide backwards
    },
    hide() {
      if (bootDone) return;
      if (!boot) { bootDone = true; return; }
      bootTarget = 100;                                       // let the bar finish its run, then fade out
      setTimeout(finishBoot, 1200);                           // safety net if animation frames are paused
    }
  };
})();