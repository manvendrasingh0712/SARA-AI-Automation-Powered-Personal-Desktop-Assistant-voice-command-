/* ==========================================================================
   motion.js -- cross-page motion polish. NO backend calls, only reacts to things that really happened
   (SARA 'status' / 'page' events, real pointer input, real data changes). Every effect is transform/opacity
   based where possible, cleans up after itself, and is skipped under reduced-motion.
   1) Wake shockwave: thin teal ring expanding from the orb when Sara really wakes (status sleeping -> waking/listening).
   8) Scroll edge fade: soft top/bottom fade on scrolling lists, driven by the real scroll position.
   13/14) Shared list helpers: M.flip (rows glide to their new spot on add/remove/regroup) and M.collapse (row dims + collapses).
   17) Slider value bubble: small live value above the thumb while a slider is dragged.
   18) Sheet spring + drag-down-to-dismiss for the Today / Clock sheets (backdrop blur stays a fixed value -- no blur ramp).
   19) Nav proximity scale: bottom-nav buttons near the cursor grow slightly (max 1.12), dock style.
   20) Time-of-day tint: the ambient glow blobs drift with the clock (never the UI accent).
   11) Border beam: one light lap around a Home card's border when it really receives a new update/notification.
   Loaded right after js/fx.js.  Styles: style/motion.css.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA, $ = SARA.$;
  const M = SARA.motion = SARA.motion || {};
  const raf = window.requestAnimationFrame.bind(window);

  /* ---- 1) wake shockwave ----
     Trigger is the REAL status transition only: previous status must be 'sleeping' and the new one 'waking' or
     'listening'. A tap on the orb also lands here (SARA.wake sets 'waking' optimistically); the backend's follow-up
     'waking'/'listening' pushes are not a transition out of sleeping, so the ring never fires twice for one wake.
     The ring is an overlay div inside #stage -- the orb canvas (js/home.js) is never touched. */
  const WAKE_RING_MS = 600;
  let prevStatus = SARA.state.status || 'sleeping';
  function wakeShockwave() {
    const stage = $('stage');
    if (!stage || SARA.reduceMotion || document.hidden || (SARA.current || 'home') !== 'home') return;
    const old = stage.querySelectorAll('.wake-ring');
    for (let i = 0; i < old.length; i++) old[i].remove();            // a rapid re-wake replaces the ring instead of stacking
    const ring = document.createElement('div');
    ring.className = 'wake-ring'; ring.setAttribute('aria-hidden', 'true');
    let done = false;
    const cleanup = function () { if (done) return; done = true; clearTimeout(safety); ring.remove(); };
    const safety = setTimeout(cleanup, WAKE_RING_MS + 300);          // animationend can be skipped (hidden tab) -- never leave the div behind
    ring.addEventListener('animationend', cleanup);
    ring.addEventListener('animationcancel', cleanup);
    stage.appendChild(ring);
  }
  SARA.on('status', function (state) {
    const was = prevStatus; prevStatus = state;
    if (was === 'sleeping' && (state === 'waking' || state === 'listening')) wakeShockwave();
  });

  /* ---- 8) scroll edge fade ----
     M.edgeFade(el) keeps --fade-t / --fade-b (0..EDGE_PX) in step with the element's real scroll position; the CSS mask
     (.edge-on) is only active while one of them is non-zero. Recomputed (rAF-throttled, writes only on change) on scroll,
     on size change, when the list's content changes, and when a page becomes visible (hidden pages measure 0). */
  const EDGE_PX = 24, edgeUpdaters = [];
  M.edgeFade = function (el) {
    if (!el || el._edgeFade) return;
    el._edgeFade = true; el.classList.add('edge-fade');
    let pending = 0, lastT = -1, lastB = -1;
    function update() {
      pending = 0;
      const max = el.scrollHeight - el.clientHeight;
      const t = max > 1 ? Math.round(Math.max(0, Math.min(EDGE_PX, el.scrollTop))) : 0;
      const b = max > 1 ? Math.round(Math.max(0, Math.min(EDGE_PX, max - el.scrollTop))) : 0;
      if (t === lastT && b === lastB) return;
      lastT = t; lastB = b;
      el.style.setProperty('--fade-t', t + 'px'); el.style.setProperty('--fade-b', b + 'px');
      el.classList.toggle('edge-on', t > 0 || b > 0);
    }
    const queue = function () { if (!pending) pending = raf(update); };
    el.addEventListener('scroll', queue, { passive: true });
    if (window.MutationObserver) new MutationObserver(queue).observe(el, { childList: true, subtree: true });
    if (window.ResizeObserver) new ResizeObserver(queue).observe(el);
    edgeUpdaters.push(queue); queue();
  };
  document.querySelectorAll('.page-body, .chat-log, .skills-scroll, .pv-recent').forEach(M.edgeFade);
  SARA.on('page', function () { edgeUpdaters.forEach(function (q) { q(); }); });

  /* ---- 11) border beam ----
     M.beam(card): a light travels once around the card's border, then the class is removed. One card at a time (a new
     beam replaces the running one). Uses @property + conic-gradient where the engine supports it (WebView2/Chromium);
     otherwise a plain one-shot glow. Only fired for REAL changes: a notification / proactive notification (their notice
     lives behind the Today card), a weather_update whose data differs from the last one, or a changed "next" item. */
  const BEAM_MS = 1200, BEAM_SUPPORTED = !!(window.CSS && window.CSS.registerProperty);
  let beamCard = null, beamCls = '', beamTimer = 0;
  function endBeam() {
    clearTimeout(beamTimer); beamTimer = 0;
    if (beamCard) { beamCard.classList.remove('beam-on', 'beam-glow'); beamCard.removeEventListener('animationend', onBeamEnd); }
    beamCard = null;
  }
  function onBeamEnd(e) { if (e.target === beamCard && /^beam/.test(e.animationName)) endBeam(); }
  M.beam = function (card) {
    if (!card || SARA.reduceMotion || document.hidden || card.hidden || !card.offsetWidth) return;   // only cards that are on screen
    endBeam();
    beamCard = card; beamCls = BEAM_SUPPORTED ? 'beam-on' : 'beam-glow';
    void card.offsetWidth;                                   // restart cleanly even if the same card was beamed a moment ago
    card.classList.add(beamCls);
    card.addEventListener('animationend', onBeamEnd);
    beamTimer = setTimeout(endBeam, BEAM_MS + 250);          // safety net if animationend never arrives
  };
  const onHome = () => (SARA.current || 'home') === 'home';
  // Startup loads (reminders, calendar, weather arrive separately) reshuffle the cards' content a few times; those are
  // initial data, not "updates". Data-change beams stay quiet until things have settled (real notifications never wait).
  const BEAM_SETTLE_MS = 5000; let settledAt = Date.now() + BEAM_SETTLE_MS;
  SARA.on('backend_ready', function () { settledAt = Date.now() + BEAM_SETTLE_MS; });
  const settled = () => Date.now() >= settledAt;
  ['ev:notification', 'ev:proactive_notification'].forEach(function (name) {
    SARA.on(name, function () { if (onHome()) M.beam($('todayCard')); });
  });
  let lastWeatherKey = null;
  SARA.on('ev:weather_update', function (data) {
    let key = ''; try { key = JSON.stringify(data); } catch (e) {}
    const changed = lastWeatherKey !== null && key !== lastWeatherKey;
    lastWeatherKey = key;
    if (changed && settled() && onHome()) M.beam($('todayCard'));
  });
  let lastNextKey = '';                                      // '' = nothing shown yet: the first item to appear (e.g. at startup) is a baseline, not an "update"
  SARA.on('nextItem', function (item) {
    const key = item ? String(item.time) + '|' + String(item.text) : '';
    const changed = !!lastNextKey && !!key && key !== lastNextKey;
    lastNextKey = key;
    if (changed && settled() && onHome()) M.beam($('nextCard'));
  });

  /* ---- 13/14) shared list helpers (one place, so completing and add/remove never fight each other) ----
     M.flip(container, mutate): FLIP. Rows are identified by data-key (id-based, stable across the innerHTML rebuild).
       Positions are measured before mutate() (which re-renders), then every surviving element animates from its old
       spot to its new one with transform only; brand-new keys fade/slide in. The very first render (no previous keys)
       and anything hidden/reduced-motion just render. Elements that did not move are left alone.
     M.collapse(el): dims the row and collapses its height, resolves when finished (immediately if motion is off). */
  const FLIP_MS = 300;
  M.flip = function (container, mutate) {
    const can = container && !SARA.reduceMotion && !document.hidden && container.offsetParent !== null;
    const before = {};
    if (can) container.querySelectorAll('[data-key]').forEach(function (el) { before[el.dataset.key] = el.getBoundingClientRect().top; });
    const had = Object.keys(before).length > 0;
    mutate();
    if (!can || !had || !container.animate) return;
    container.querySelectorAll('[data-key]').forEach(function (el) {
      if (!el.animate) return;
      const key = el.dataset.key, old = before[key];
      if (old === undefined) {
        el.animate([{ opacity: 0, transform: 'translateY(-6px)' }, { opacity: 1, transform: 'none' }], { duration: FLIP_MS, easing: 'cubic-bezier(.22,1,.36,1)' });
        return;
      }
      const dy = old - el.getBoundingClientRect().top;
      if (Math.abs(dy) < 1) return;
      el.animate([{ transform: 'translateY(' + dy + 'px)' }, { transform: 'none' }], { duration: FLIP_MS, easing: 'cubic-bezier(.22,1,.36,1)' });
    });
  };
  M.collapse = function (el) {
    return new Promise(function (resolve) {
      if (!el || SARA.reduceMotion || document.hidden || !el.animate || !el.offsetHeight) { resolve(); return; }
      const cs = getComputedStyle(el), h = el.offsetHeight;
      el.style.overflow = 'hidden';
      let settled = false;
      const done = function () { if (settled) return; settled = true; resolve(); };
      const a = el.animate([
        { height: h + 'px', opacity: 1, paddingTop: cs.paddingTop, paddingBottom: cs.paddingBottom, borderBottomWidth: cs.borderBottomWidth },
        { height: h + 'px', opacity: 0.35, paddingTop: cs.paddingTop, paddingBottom: cs.paddingBottom, borderBottomWidth: cs.borderBottomWidth, offset: 0.35 },
        { height: '0px', opacity: 0, paddingTop: '0px', paddingBottom: '0px', borderBottomWidth: '0px' }
      ], { duration: 300, easing: 'cubic-bezier(.4,0,.2,1)', fill: 'forwards' });
      a.onfinish = done; a.oncancel = done;
      setTimeout(done, 550);
    });
  };

  /* ---- 17) slider value bubble ----
     One bubble per .slider-wrap slider (mic sensitivity, speech speed). Follows the thumb from the real input value
     (thumb centre = half a thumb + pct of the remaining track), shown on pointerdown / keyboard change, hidden on
     release (or ~0.9s after the last keyboard step). Only opacity/transform are animated; left is a plain write. ---- */
  document.querySelectorAll('.slider-wrap input[type=range]').forEach(function (input) {
    const wrap = input.parentElement; if (!wrap) return;
    const bubble = document.createElement('span');
    bubble.className = 'slider-bubble'; bubble.setAttribute('aria-hidden', 'true'); wrap.appendChild(bubble);
    let hideTimer = 0, dragging = false;
    const THUMB = 13;
    function place() {
      const min = Number(input.min) || 0, max = Number(input.max) || 100, span = max - min || 1;
      const pct = Math.max(0, Math.min(1, (Number(input.value) - min) / span));
      bubble.textContent = input.value;
      bubble.style.left = (input.offsetLeft + THUMB / 2 + (input.offsetWidth - THUMB) * pct).toFixed(1) + 'px';
    }
    const show = function () { clearTimeout(hideTimer); place(); bubble.classList.add('show'); };
    const hide = function () { clearTimeout(hideTimer); dragging = false; bubble.classList.remove('show'); };
    input.addEventListener('pointerdown', function () { dragging = true; show(); });
    input.addEventListener('input', function () {
      show();
      if (!dragging) hideTimer = setTimeout(hide, 900);      // keyboard / wheel: no pointerup to wait for
    });
    ['pointerup', 'pointercancel', 'blur'].forEach(function (n) { input.addEventListener(n, hide); });
    window.addEventListener('pointerup', function () { if (dragging) hide(); });   // released outside the slider
    SARA.on('page', hide);
  });

  /* ---- 18) sheet drag-to-dismiss ----
     The spring open/close is pure CSS (style/motion.css). Here: pull a sheet down to close it. Drag starts only after a
     6px mostly-vertical downward move, only when the sheet is scrolled to the top, and never from inputs/buttons/sliders.
     While dragging the sheet follows the pointer (transform) and the backdrop fades (opacity); on release it either
     flies off and closes (past ~110px or a quick flick) or springs back. Backdrop blur is deliberately NOT animated. ---- */
  ['todaySheet', 'clockSheet'].forEach(function (id) {
    const overlay = $(id), sheet = overlay && overlay.querySelector('.sheet'); if (!sheet) return;
    const NO_DRAG = 'input,button,select,textarea,a,[role="switch"],[data-nodrag]';
    let startY = 0, startX = 0, dy = 0, pid = null, active = false, t0 = 0, lastY = 0, lastT = 0, vel = 0;
    function reset() { overlay.classList.remove('sheet-dragging'); sheet.style.transform = ''; sheet.style.transition = ''; overlay.style.opacity = ''; }
    sheet.addEventListener('pointerdown', function (e) {
      if (SARA.reduceMotion || e.button > 0 || !overlay.classList.contains('open')) return;
      if (e.target.closest(NO_DRAG) || sheet.scrollTop > 0) return;
      pid = e.pointerId; startY = lastY = e.clientY; startX = e.clientX; dy = 0; vel = 0; active = false; t0 = lastT = performance.now();
    });
    sheet.addEventListener('pointermove', function (e) {
      if (pid !== e.pointerId) return;
      const my = e.clientY - startY, mx = e.clientX - startX;
      if (!active) {
        if (my < 0 || Math.abs(mx) > Math.abs(my)) { if (Math.abs(mx) > 10 || my < -6) pid = null; return; }   // sideways / upward = not a dismiss gesture
        if (my < 6) return;
        active = true; overlay.classList.add('sheet-dragging');
        try { sheet.setPointerCapture(pid); } catch (err) {}
      }
      const now = performance.now();
      vel = (e.clientY - lastY) / Math.max(1, now - lastT); lastY = e.clientY; lastT = now;
      dy = Math.max(0, my);
      sheet.style.transform = 'translateY(' + dy.toFixed(1) + 'px) scale(' + (1 - Math.min(dy, 400) / 4000).toFixed(4) + ')';
      overlay.style.opacity = String(1 - Math.min(0.7, dy / 380));
    });
    function finish(e, cancelled) {
      if (pid !== e.pointerId) return;
      const was = active; pid = null; active = false;
      try { sheet.releasePointerCapture(e.pointerId); } catch (err) {}
      if (!was) return;
      if (!cancelled && (dy > 110 || vel > 0.7)) {                       // dismiss: fly the rest of the way down, then close
        sheet.style.transition = 'transform .24s cubic-bezier(.4,0,1,1)';
        sheet.style.transform = 'translateY(' + Math.max(dy, 60) + 'px) translateY(40vh) scale(.96)';
        overlay.style.transition = 'opacity .24s ease'; overlay.style.opacity = '0';
        SARA.closeOverlay(id);
        setTimeout(function () { overlay.style.transition = ''; reset(); }, 300);
      } else {                                                            // spring back
        overlay.classList.remove('sheet-dragging');
        sheet.style.transition = 'transform .42s var(--ease-spring,cubic-bezier(.34,1.56,.64,1))';
        sheet.style.transform = ''; overlay.style.opacity = '';
        setTimeout(function () { sheet.style.transition = ''; }, 450);
      }
    }
    sheet.addEventListener('pointerup', function (e) { finish(e, false); });
    sheet.addEventListener('pointercancel', function (e) { finish(e, true); });
  });

  /* ---- 19) nav proximity scale ----
     Distance from the pointer to each button's centre (x only -- the nav is a single row) -> scale 1..1.12 via the
     --prox custom property (CSS turns it into transform: scale, so it is compositor-only). rAF-throttled; mouse/pen
     only; off under reduced-motion. The sliding underline (js/fx.js) positions itself from offsetLeft/offsetWidth,
     which transforms never change, so it stays exactly under its button -- no re-measure needed. Button centres are
     cached and re-measured on resize / page change (not every move), since the nav can scroll horizontally. ---- */
  (function () {
    const nav = $('navBar'); if (!nav) return;
    const buttons = Array.prototype.slice.call(nav.querySelectorAll('button[data-page]'));
    const MAX = 0.12, SIGMA = 100;
    let centers = [], mx = 0, frame = 0, inside = false;
    function measure() {
      centers = buttons.map(function (b) { const r = b.getBoundingClientRect(); return r.left + r.width / 2; });
    }
    function paint() {
      frame = 0;
      for (let i = 0; i < buttons.length; i++) {
        const dist = (mx - centers[i]) / SIGMA, k = inside && !SARA.reduceMotion ? Math.exp(-dist * dist) : 0;
        const eased = k;                     // smoothstep: soft shoulders, no visible kink
        buttons[i].style.setProperty('--prox', (1 + MAX * eased).toFixed(3));
      }
    }
    const queue = function () { if (!frame) frame = raf(paint); };
    nav.addEventListener('pointerenter', function (e) { if (e.pointerType === 'touch') return; inside = true; measure(); mx = e.clientX; queue(); });
    nav.addEventListener('pointermove', function (e) { if (e.pointerType === 'touch' || !inside) return; mx = e.clientX; queue(); }, { passive: true });
    nav.addEventListener('pointerleave', function () { inside = false; queue(); });
    nav.addEventListener('scroll', function () { if (inside) { measure(); queue(); } }, { passive: true });
    window.addEventListener('resize', function () { if (inside) { measure(); queue(); } });
  })();

  /* ---- 19b) nav tooltip ---- */
  (function () {
    const nav = $('navBar'); if (!nav) return;
    const TIPS = { home: 'Orb and today', chat: 'Talk or type to Sara', notes: 'Notes and reminders', apps: 'Apps and music', automation: 'Routines and automation', settings: 'Settings' };
    const tip = document.createElement('div');
    tip.className = 'nav-tip'; tip.id = 'navTip'; tip.setAttribute('role', 'tooltip'); tip.setAttribute('aria-hidden', 'true');
    document.body.appendChild(tip);
    let timer = 0, current = null;
    function hide() { clearTimeout(timer); timer = 0; current = null; tip.classList.remove('show'); }
    function show(btn) {
      const text = TIPS[btn.dataset.page]; if (!text || !btn.isConnected) return;
      tip.textContent = text;
      const sc = btn.dataset.shortcut;
      if (sc) { const k = document.createElement('kbd'); k.textContent = sc; tip.appendChild(k); btn.setAttribute('aria-keyshortcuts', sc); }
      const r = btn.getBoundingClientRect();
      tip.style.left = Math.round(r.left + r.width / 2) + 'px'; tip.style.top = Math.round(r.top) + 'px';
      tip.classList.add('show');
    }
    function arm(btn, delay) { if (btn === current) return; hide(); current = btn; timer = setTimeout(function () { show(btn); }, delay); }
    nav.addEventListener('pointerover', function (e) {
      if (e.pointerType === 'touch') return;
      const b = e.target.closest('button[data-page]'); if (b) arm(b, 420);
    });
    nav.addEventListener('pointerleave', hide);
    nav.addEventListener('pointerdown', hide);
    nav.addEventListener('focusin', function (e) { const b = e.target.closest('button[data-page]'); if (b && b.matches(':focus-visible')) arm(b, 0); });
    nav.addEventListener('focusout', hide);
    nav.addEventListener('scroll', hide, { passive: true });
    SARA.on('page', hide);
    window.addEventListener('blur', hide);
  })();

  /* ---- 20) time-of-day tint ----
     Anchors are [hour, blob1 (teal), blob2 (violet), blob3 (amber)] as rgb; the current colours are linearly
     interpolated between the two surrounding anchors from the REAL local time. Applied once now, then every ~17 min
     (SARA.every skips ticks while the window is hidden and the next visible tick catches up). The 20s CSS transition
     (style/ambient.css) is enabled only after first paint so the initial colour is set instantly, not faded in. ---- */
  const TINT = [
    [0,  [48, 92, 200],  [86, 78, 205],  [64, 88, 165]],    // night: deep blue-void
    [5,  [48, 92, 200],  [86, 78, 205],  [64, 88, 165]],
    [8,  [92, 222, 192], [122, 128, 222],[242, 200, 138]],  // morning: warm teal
    [12, [63, 216, 196], [139, 111, 216],[232, 192, 143]],  // day: the neutral base look
    [16, [63, 216, 196], [139, 111, 216],[232, 192, 143]],
    [19, [222, 168, 108],[198, 108, 142],[242, 148, 78]],   // evening: amber
    [22, [48, 92, 200],  [86, 78, 205],  [64, 88, 165]],
    [24, [48, 92, 200],  [86, 78, 205],  [64, 88, 165]]
  ];
  const lerp3 = (a, b, k) => a.map((v, i) => Math.round(v + (b[i] - v) * k));
  function tintAt(hour) {
    let i = 0; while (i < TINT.length - 2 && hour >= TINT[i + 1][0]) i++;
    const a = TINT[i], b = TINT[i + 1], k = Math.max(0, Math.min(1, (hour - a[0]) / (b[0] - a[0] || 1)));
    return [lerp3(a[1], b[1], k), lerp3(a[2], b[2], k), lerp3(a[3], b[3], k)];
  }
  M.applyTint = function (date) {
    const d = date || new Date(), c = tintAt(d.getHours() + d.getMinutes() / 60), root = document.documentElement;
    for (let i = 0; i < 3; i++) root.style.setProperty('--amb-' + (i + 1), 'rgb(' + c[i].join(',') + ')');
  };
  M.applyTint();
  raf(function () { raf(function () { document.documentElement.classList.add('tint-ready'); }); });
  SARA.every(1000 * 1000, function () { M.applyTint(); });

})();
