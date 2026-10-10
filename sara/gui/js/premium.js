/* ==========================================================================
   premium.js (v2, polished) -- 14 premium effects for the SARA GUI. Frontend only, NO backend calls, no dependencies.
   Styles: style/premium.css.  Load right BEFORE js/main.js.  Every effect is skipped under prefers-reduced-motion.
   v4: every effect now respects Settings > Visual quality (js/quality.js).
   v3: JS wheel-smoothing removed (native scroll is smoother); hover effects pause while dragging/scrolling.
   v2: the light-wipe page transition is removed; all easing is now time-based (identical feel on 60/90/120/144Hz);
       tilt follows the pointer tightly (no spring wobble); overlay "depth recede" no longer relies on a document-wide :has().

    1 Cursor aura            soft light that follows the pointer        2 Magnetic buttons     buttons lean toward the cursor
    3 3D tilt cards          tiles tilt in perspective under the cursor 4 Spark burst          particles on primary clicks
    5 Number roll            stats count smoothly to new values         6 Title decrypt        page titles resolve from scrambled glyphs
    7 (removed: native scrolling is smoother)                          8 Scroll progress      hairline progress bar on long pages
    9 Dust motes             faint drifting particles (self-pausing)   10 Depth recede         page sinks back behind sheets/modals
   11 Border beam on chat dock (CSS)                                   12 Theme circle reveal  theme change expands from the click
   13 Header condense        header gets an edge + smaller title       14 Edge pulse           screen edge glows softly on a toast
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA;
  if (!SARA || !document.body) return;
  const doc = document, root = doc.documentElement, body = doc.body;
  const reduce = !!SARA.reduceMotion;
  const coarse = !!(window.matchMedia && window.matchMedia('(pointer: coarse)').matches);
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const rnd = Math.random;
  const EASE = 'cubic-bezier(.16,1,.3,1)';
  /* Settings > Visual quality (js/quality.js): each effect checks whether the chosen tier allows it */
  const Q = (f) => !SARA.quality || SARA.quality.allows(f);
  /* frame-rate independent smoothing: k = fraction of the remaining distance covered per 16.7ms frame */
  const lerpK = (k, dt) => 1 - Math.pow(1 - k, dt / 16.667);
  const frameDt = (now, last) => (last ? clamp(now - last, 1, 50) : 16.667);

  function rgbOf(name, fb) {
    try { const v = SARA.theme && SARA.theme.rgb && SARA.theme.rgb(name); if (v && v.length === 3) return v; } catch (e) { /* fall through */ }
    return fb;
  }
  function make(cls, tag) { const n = doc.createElement(tag || 'div'); n.className = cls; n.setAttribute('aria-hidden', 'true'); return n; }

  if (reduce) return;   // everything below is motion

  /* ---- 1) cursor aura ---- */
  (function aura() {
    if (coarse) return;
    const el = make('pm-aura'); body.appendChild(el);
    let x = innerWidth / 2, y = innerHeight / 2, tx = x, ty = y, raf = 0, shown = false, last = 0;
    function tick(now) {
      raf = 0; const k = lerpK(0.14, frameDt(now, last)); last = now;
      x += (tx - x) * k; y += (ty - y) * k;
      el.style.transform = 'translate3d(' + (x - 260).toFixed(1) + 'px,' + (y - 260).toFixed(1) + 'px,0)';
      if (Math.abs(tx - x) > 0.4 || Math.abs(ty - y) > 0.4) raf = requestAnimationFrame(tick); else last = 0;
    }
    doc.addEventListener('pointermove', function (e) {
      if (e.pointerType === 'touch') return;
      if (!Q('aura')) { if (shown) { shown = false; el.classList.remove('on'); } return; }
      tx = e.clientX; ty = e.clientY;
      if (!shown) { shown = true; x = tx; y = ty; el.classList.add('on'); }
      if (!raf && !doc.hidden) raf = requestAnimationFrame(tick);
    }, { passive: true });
    root.addEventListener('mouseleave', function () { shown = false; el.classList.remove('on'); });
  })();

  /* ---- 2) magnetic buttons + 3) 3D tilt cards (one shared delegated pointermove) ---- */
  (function magneticAndTilt() {
    if (coarse) return;
    const MAG = '.pill-btn,.dock-btn,.qa-btn,.chip';
    const TILT = '.today-card,.next-card,.clock-card,.app-tile,.theme-card';
    let mEl = null, mx = 0, my = 0, mtx = 0, mty = 0, mraf = 0, mlast = 0;
    let tEl = null, tPending = null, traf = 0;

    function magLoop(now) {
      mraf = 0; const k = lerpK(0.2, frameDt(now, mlast)); mlast = now;
      mx += (mtx - mx) * k; my += (mty - my) * k;
      if (mEl) mEl.style.translate = mx.toFixed(2) + 'px ' + my.toFixed(2) + 'px';
      if (Math.abs(mtx - mx) > 0.05 || Math.abs(mty - my) > 0.05) mraf = requestAnimationFrame(magLoop);
      else { mlast = 0; if (!mtx && !mty && mEl) { mEl.style.translate = ''; mEl = null; mx = my = 0; } }
    }
    function magSet(el, e) {
      if (el !== mEl) { if (mEl) mEl.style.translate = ''; mEl = el; mx = my = 0; }
      if (!el) { mtx = mty = 0; }
      else {
        const r = el.getBoundingClientRect();
        mtx = clamp((e.clientX - (r.left + r.width / 2)) * 0.22, -7, 7);
        mty = clamp((e.clientY - (r.top + r.height / 2)) * 0.22, -5, 5);
      }
      if (!mraf && mEl) mraf = requestAnimationFrame(magLoop);
    }
    function tiltApply() {
      traf = 0; const p = tPending; if (!p || !p.el) return;
      p.el.style.transform = 'perspective(800px) rotateX(' + p.rx.toFixed(2) + 'deg) rotateY(' + p.ry.toFixed(2) + 'deg) translateY(-3px)';
    }
    function tiltRelease(el) { if (!el) return; el.classList.remove('pm-tilt'); el.style.transform = ''; }
    function tiltSet(el, e) {
      if (el !== tEl) { tiltRelease(tEl); tEl = el; if (el) el.classList.add('pm-tilt'); }
      if (!el) { tPending = null; return; }
      const r = el.getBoundingClientRect(); if (!r.width || !r.height) return;
      const max = el.matches('.today-card,.next-card,.clock-card') ? 3.2 : 6;
      const nx = (e.clientX - r.left) / r.width - 0.5, ny = (e.clientY - r.top) / r.height - 0.5;
      tPending = { el: el, rx: -ny * max * 2, ry: nx * max * 2 };
      if (!traf) traf = requestAnimationFrame(tiltApply);
    }
    doc.addEventListener('pointermove', function (e) {
      if (e.pointerType === 'touch') return;
      if (e.buttons) { magSet(null, null); tiltSet(null, null); return; }   // dragging something: no hover effects
      const t = e.target && e.target.closest ? e.target : null;
      const m = Q('magnetic') ? (t && t.closest(MAG)) : null; const c = Q('tilt') ? (t && t.closest(TILT)) : null;
      magSet(m && !m.disabled ? m : null, e);
      tiltSet(c, e);
    }, { passive: true });
    root.addEventListener('mouseleave', function () { magSet(null, null); tiltSet(null, null); });
  })();

  /* ---- 4) spark burst ---- */
  (function spark() {
    const SPARK = '.pill-btn.primary,.chip,.toggle,.theme-card';
    if (typeof body.animate !== 'function') return;
    doc.addEventListener('pointerdown', function (e) {
      if (!Q('spark')) return;
      const t = e.target && e.target.closest ? e.target.closest(SPARK) : null;
      if (!t || t.disabled) return;
      const col = 'rgb(' + rgbOf('--core-rgb', [0, 183, 255]).join(',') + ')', n = 9;
      for (let i = 0; i < n; i++) {
        const p = make('pm-spark'); p.style.left = e.clientX + 'px'; p.style.top = e.clientY + 'px'; p.style.background = col;
        body.appendChild(p);
        const a = (i / n) * Math.PI * 2 + rnd() * 0.5, d = 22 + rnd() * 24;
        const an = p.animate([
          { transform: 'translate(-50%,-50%) scale(1)', opacity: 0.9 },
          { transform: 'translate(calc(-50% + ' + (Math.cos(a) * d).toFixed(1) + 'px),calc(-50% + ' + (Math.sin(a) * d).toFixed(1) + 'px)) scale(.2)', opacity: 0 }
        ], { duration: 520 + rnd() * 200, easing: EASE });
        an.onfinish = function () { p.remove(); };
      }
    }, true);
  })();

  /* ---- 6) title decrypt on page change (the light-wipe is gone) ---- */
  (function titleDecrypt() {
    let first = true;
    const GL = 'ABCDEFGHJKLMNPQRSTUVWXYZ0123456789#%&*';
    function decrypt(el) {
      const fin = el.textContent; if (!fin || fin.length > 36 || el._pmDec) return;
      el._pmDec = true; el.style.minWidth = el.offsetWidth + 'px';
      const t0 = performance.now(), dur = 520; let last = fin;
      (function step(now) {
        const k = clamp((now - t0) / dur, 0, 1);
        if (el.textContent !== last && last !== fin) { el._pmDec = false; el.style.minWidth = ''; return; }   // someone else changed it
        const reveal = Math.floor(k * fin.length * 1.15); let out = '';
        for (let i = 0; i < fin.length; i++) out += (fin[i] === ' ' || i < reveal) ? fin[i] : GL[Math.floor(rnd() * GL.length)];
        last = k >= 1 ? fin : out; el.textContent = last;
        if (k < 1) requestAnimationFrame(step); else { el._pmDec = false; el.style.minWidth = ''; }
      })(t0);
    }
    SARA.on('page', function (name) {
      if (first) { first = false; return; }
      if (!Q('decrypt')) return;
      const pg = doc.getElementById('page-' + name);
      const title = pg && pg.querySelector('.page-header .page-title');
      if (title) decrypt(title);
    });
  })();

  /* ---- 5) number roll on settings stats ---- */
  (function numberRoll() {
    const host = doc.getElementById('page-settings'); if (!host || typeof MutationObserver !== 'function') return;
    const NUM = '.an-kpi b,.perf-num';
    function roll(el) {
      if (el.offsetParent === null) { el._pmVal = undefined; return; }
      const txt = el.textContent.trim(), m = /^(-?\d+(?:\.\d+)?)(.*)$/.exec(txt);
      if (!m) { el._pmVal = undefined; return; }
      const to = parseFloat(m[1]), from = el._pmVal === undefined ? to : el._pmVal, dec = (m[1].split('.')[1] || '').length, suf = m[2];
      el._pmVal = to; if (from === to) return;
      const tok = (el._pmTok = (el._pmTok || 0) + 1), t0 = performance.now(), dur = 700;
      (function step(now) {
        if (el._pmTok !== tok) return;
        const k = clamp((now - t0) / dur, 0, 1), e = 1 - Math.pow(1 - k, 4);
        el._pmWrite = (k >= 1 ? to : from + (to - from) * e).toFixed(dec) + suf; el.textContent = el._pmWrite;
        if (k < 1) requestAnimationFrame(step);
      })(t0);
    }
    new MutationObserver(function (muts) {
      for (let i = 0; i < muts.length; i++) {
        const t = muts[i].target, el = t.nodeType === 3 ? t.parentElement : t;
        if (!el || !el.matches || !el.matches(NUM)) continue;
        if (el._pmWrite !== undefined && el.textContent === el._pmWrite) continue;   // our own write
        roll(el);
      }
    }).observe(host, { childList: true, characterData: true, subtree: true });
  })();

  /* ---- 8) scroll progress + 13) header condense ---- */
  (function scrollFx() {
    const bar = make('pm-progress'); body.appendChild(bar);
    let pend = null, raf = 0;
    function apply() {
      raf = 0; const t = pend; if (!t) return;
      const max = t.scrollHeight - t.clientHeight, top = t.scrollTop;
      bar.style.transform = 'scaleX(' + (max > 0 ? clamp(top / max, 0, 1) : 0).toFixed(3) + ')';
      bar.classList.toggle('on', max > 0 && top > 12);
      const pg = t.closest('.page'); if (pg) pg.classList.toggle('pm-scrolled', top > 10);
    }
    doc.addEventListener('scroll', function (e) {
      const t = e.target; if (!t || !t.classList || !t.classList.contains('page-body')) return;
      pend = t; if (!raf) raf = requestAnimationFrame(apply);
    }, { capture: true, passive: true });
    SARA.on('page', function () { bar.classList.remove('on'); pend = null; });
  })();

  /* ---- 9) dust motes (self-pausing; removes itself if the machine can't keep 30fps) ---- */
  (function dust() {
    const cv = make('pm-dust', 'canvas'), ctx = cv.getContext && cv.getContext('2d'); if (!ctx) return;
    body.insertBefore(cv, body.firstChild);
    let cleared = false, W = 0, H = 0, dpr = 1, last = 0, slow = 0, raf = 0, col = rgbOf('--core-rgb', [0, 183, 255]), rt = 0;
    const P = [];
    function fit() { dpr = Math.min(window.devicePixelRatio || 1, 1.5); W = innerWidth; H = innerHeight; cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); }
    fit();
    for (let i = 0; i < 36; i++) P.push({ x: rnd() * W, y: rnd() * H, r: 0.6 + rnd() * 1.4, vx: (rnd() - 0.5) * 0.08, vy: -(0.03 + rnd() * 0.12), a: 0.1 + rnd() * 0.25, ph: rnd() * 6.28 });
    function stop() { cancelAnimationFrame(raf); cv.remove(); }
    function frame(t) {
      raf = requestAnimationFrame(frame);
      if (!Q('dust')) { if (!cleared) { ctx.clearRect(0, 0, W, H); cleared = true; } last = 0; return; }
      cleared = false;
      if (doc.hidden || root.classList.contains('is-scrolling') || root.classList.contains('is-dragging')) { last = 0; return; }
      const dt = t - last; if (dt < 33) return; last = t;
      if (dt > 70) { if (++slow > 40) { stop(); return; } } else if (slow > 0) slow--;
      ctx.clearRect(0, 0, W, H);
      const c = 'rgba(' + col[0] + ',' + col[1] + ',' + col[2] + ',', step = Math.min(dt, 70) / 16;
      for (let i = 0; i < P.length; i++) {
        const p = P[i];
        p.x += p.vx * step; p.y += p.vy * step;
        if (p.y < -4) { p.y = H + 4; p.x = rnd() * W; } if (p.x < -4) p.x = W + 4; else if (p.x > W + 4) p.x = -4;
        ctx.fillStyle = c + (p.a * (0.6 + 0.4 * Math.sin(t / 1500 + p.ph))).toFixed(3) + ')';
        ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, 6.2832); ctx.fill();
      }
    }
    raf = requestAnimationFrame(frame);
    window.addEventListener('resize', function () { clearTimeout(rt); rt = setTimeout(fit, 150); });
    SARA.on('theme', function () { setTimeout(function () { col = rgbOf('--core-rgb', col); }, 60); });
  })();

  /* ---- 10) depth recede: a body class (set here) drives the CSS, so no document-wide :has() is needed ---- */
  (function overlayState() {
    const els = doc.querySelectorAll('.overlay,.pal-overlay');
    if (!els.length || typeof MutationObserver !== 'function') return;
    const upd = function () { body.classList.toggle('pm-overlay-open', !!doc.querySelector('.overlay.open,.pal-overlay.open')); };
    const mo = new MutationObserver(upd);
    els.forEach(function (el) { mo.observe(el, { attributes: true, attributeFilter: ['class'] }); });
    upd();
  })();

  /* ---- 12) theme change: circular reveal from the click point (View Transitions API, graceful fallback) ---- */
  (function themeReveal() {
    if (typeof doc.startViewTransition !== 'function' || !SARA.theme || typeof SARA.theme.apply !== 'function') return;
    const orig = SARA.theme.apply; let px = innerWidth / 2, py = innerHeight / 2, down = -1e9;
    doc.addEventListener('pointerdown', function (e) { px = e.clientX; py = e.clientY; down = performance.now(); }, true);
    SARA.theme.apply = function () {
      const self = this, args = arguments;
      if (!Q('reveal') || doc.hidden || performance.now() - down > 900 || root.classList.contains('pm-vt')) return orig.apply(self, args);
      root.classList.add('pm-vt');
      root.style.setProperty('--vt-x', px + 'px'); root.style.setProperty('--vt-y', py + 'px');
      root.style.setProperty('--vt-r', Math.hypot(Math.max(px, innerWidth - px), Math.max(py, innerHeight - py)) + 'px');
      try {
        const vt = doc.startViewTransition(function () { orig.apply(self, args); });
        const done = function () { root.classList.remove('pm-vt'); };
        vt.finished.then(done, done);
      } catch (e) { root.classList.remove('pm-vt'); return orig.apply(self, args); }
    };
  })();

  /* ---- 14) edge pulse on every toast ---- */
  (function edgePulse() {
    const orig = SARA.toast; if (typeof orig !== 'function') return;
    const edge = make('pm-edge'); body.appendChild(edge);
    SARA.toast = function (icon, color) {
      try {
        if (typeof edge.animate === 'function') {
          edge.style.setProperty('--pm-edge', typeof color === 'string' && color ? color : 'var(--core)');
          edge.animate([{ opacity: 0 }, { opacity: 1, offset: 0.25 }, { opacity: 0 }], { duration: 900, easing: 'ease-out' });
        }
      } catch (e) { /* cosmetic only */ }
      return orig.apply(this, arguments);
    };
  })();
})();