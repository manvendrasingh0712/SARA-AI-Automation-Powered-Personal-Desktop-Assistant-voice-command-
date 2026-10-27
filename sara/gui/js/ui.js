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
  SARA.restRect = function (el) {
    const r = el.getBoundingClientRect();
    let dx = 0, dy = 0;
    for (let n = el; n && n !== document.body; n = n.parentElement) {
      const tf = window.getComputedStyle(n).transform;
      const m = tf && tf !== 'none' ? /^matrix\(([^)]+)\)$/.exec(tf) : null;
      if (m) { const v = m[1].split(','); dx += parseFloat(v[4]) || 0; dy += parseFloat(v[5]) || 0; }
    }
    return { left: r.left - dx, top: r.top - dy, width: r.width, height: r.height };
  };
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

  /* ---- toast icon glyphs (tabler-style names, prefix optional) ---- */
  const ICON_PATHS = {
    'alarm': '<circle cx="12" cy="13" r="7"/><path d="M12 10v3l2 2M5 4L3 6M19 4l2 2"/>',
    'alert-triangle': '<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9L2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
    'bell': '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10 21h4"/>',
    'check': '<path d="M5 12l5 5L20 7"/>',
    'battery': '<rect x="2" y="7" width="16" height="10" rx="2"/><path d="M22 11v2"/>',
    'calendar': '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 11h18"/>',
    'clock': '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    'info-circle': '<circle cx="12" cy="12" r="9"/><path d="M12 8h.01M11 12h1v4h1"/>'
  };
  SARA.iconSvg = function (name) {
    if (!name) return '';
    const p = ICON_PATHS[String(name).replace(/^ti-/, '')];
    return p ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' + p + '</svg>' : '';
  };

  /* ---- toasts ---- */
  // opts.tone === 'notify' plays the notification chime; alert-type icons play the soft error tone.
  function flipStack(stack, mutate) {
    const kids = Array.prototype.slice.call(stack.children);
    const before = kids.map(function (k) { return k.getBoundingClientRect().top; });
    mutate();
    if (SARA.reduceMotion) return;
    kids.forEach(function (k, i) {
      if (!k.isConnected || typeof k.animate !== 'function') return;
      const dy = before[i] - k.getBoundingClientRect().top;
      if (Math.abs(dy) < 1) return;
      k.animate([{ transform: 'translateY(' + dy.toFixed(1) + 'px)' }, { transform: 'translateY(0)' }], { duration: 260, easing: 'cubic-bezier(.22,1,.36,1)' });
    });
  }
  SARA.toast = function (iconClass, color, message, opts) {
    const stack = SARA.$('toastStack'); if (!stack) return;
    opts = opts || {};
    const t = document.createElement('div'); t.className = 'toast';
    let variant = opts.variant;
    if (!variant) {
      if (/alert|error/i.test(iconClass || '')) variant = 'error';
      else if (opts.tone === 'notify') variant = 'info';
      else variant = 'success';
    }
    t.classList.add('v-' + variant);
    t.setAttribute('role', variant === 'error' ? 'alert' : 'status');
    const accent = color || 'var(--core)';
    t.style.setProperty('--t-accent', accent);
    const svg = SARA.iconSvg(iconClass);
    let lead;
    if (svg) {
      lead = document.createElement('span');
      lead.className = 't-ico' + (/check/.test(String(iconClass || '')) ? ' is-check' : '');
      lead.innerHTML = svg; lead.style.color = accent; lead.setAttribute('aria-hidden', 'true');
    } else {
      lead = document.createElement('span'); lead.className = 't-dot';
      lead.style.background = accent; lead.style.color = accent;
    }
    const body = document.createElement('div'); body.className = 't-body';
    const hasTitle = opts.title != null && opts.title !== '';
    if (hasTitle) {
      const ti = document.createElement('span'); ti.className = 't-title'; ti.textContent = String(opts.title);
      body.appendChild(ti); t.classList.add('has-title');
    }
    const text = message == null ? '' : String(message);
    if (text || !hasTitle) {
      const msg = document.createElement('span'); msg.className = 't-msg'; msg.textContent = text;
      body.appendChild(msg);
    }
    if (opts.sub) {
      const small = document.createElement('small'); small.className = 't-sub'; small.textContent = String(opts.sub);
      body.appendChild(small);
    }
    if (opts.meta) {
      const meta = document.createElement('small'); meta.className = 't-meta'; meta.textContent = String(opts.meta);
      body.appendChild(meta);
    }
    const actCfg = opts.action || (typeof opts.undo === 'function' ? { label: 'Undo', onClick: opts.undo } : null);
    let act = null;
    if (actCfg && typeof actCfg.onClick === 'function') {
      act = document.createElement('button'); act.type = 'button'; act.className = 't-act';
      act.textContent = actCfg.label ? String(actCfg.label) : 'Undo';
    }
    const x = document.createElement('button'); x.type = 'button'; x.className = 't-x'; x.textContent = '\u00d7'; x.setAttribute('aria-label', 'Dismiss');
    const AUTO_MS = opts.duration > 0 ? opts.duration : (act ? 6500 : 4500);
    const bar = document.createElement('div'); bar.className = 't-bar'; bar.setAttribute('aria-hidden', 'true');
    bar.style.setProperty('--t-dur', AUTO_MS + 'ms');
    t.appendChild(lead); t.appendChild(body);
    if (act) t.appendChild(act);
    t.appendChild(x); t.appendChild(bar);
    flipStack(stack, function () {
      stack.appendChild(t);
      while (stack.children.length > 4) stack.removeChild(stack.firstChild);
    });
    if (/alert|error/i.test(iconClass || '')) SARA.sound.error();
    else if (opts.tone === 'notify') SARA.sound.notify();
    let fadeTimer = 0, leaving = false, paused = false, hov = false, foc = false, acted = false, startedAt = 0, remaining = AUTO_MS;
    function dismiss() {
      if (leaving) return; leaving = true; clearTimeout(fadeTimer);
      t.classList.add('leaving');
      setTimeout(function () { flipStack(stack, function () { t.remove(); }); }, 240);
    }
    function arm(ms) { clearTimeout(fadeTimer); remaining = ms; startedAt = performance.now(); fadeTimer = setTimeout(dismiss, ms); }
    function pause() {
      if (leaving || paused) return; paused = true; clearTimeout(fadeTimer);
      remaining = Math.max(0, remaining - (performance.now() - startedAt)); t.classList.add('paused');
    }
    function resume() {
      if (leaving || !paused) return; paused = false; t.classList.remove('paused');
      arm(Math.max(300, remaining));
    }
    function sync() { if (hov || foc) pause(); else resume(); }
    t.addEventListener('pointerenter', function () { hov = true; sync(); });
    t.addEventListener('pointerleave', function () { hov = false; sync(); });
    t.addEventListener('focusin', function () { foc = true; sync(); });
    t.addEventListener('focusout', function (e) { if (!t.contains(e.relatedTarget)) { foc = false; sync(); } });
    x.addEventListener('click', function (e) { e.stopPropagation(); dismiss(); });
    if (act) act.addEventListener('click', function (e) {
      e.stopPropagation();
      if (acted) return; acted = true; act.disabled = true;
      try { actCfg.onClick(e); } catch (err) { console.error('[toast action]', err); }
      dismiss();
    });
    arm(AUTO_MS);
  };
  SARA.ok = (m) => SARA.toast('ti-check', '#3FD8C4', m, { variant: 'success' });
  SARA.fail = (m) => SARA.toast('ti-alert-triangle', '#D97A6B', m, { variant: 'error' });

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