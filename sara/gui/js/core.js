/* ==========================================================================
   core.js -- THE BRIDGE. Everything that talks to Python goes through here.
   Owns: SARA.callApi (window.pywebview.api[name](...args), with bind-race retries and automatic
   fallback to js/mock.js in a plain browser), the Python->JS push handler window.saraEvent
   (re-broadcast as SARA.emit('ev:<kind>', ...args) for the page files), page navigation,
   the boot sequence (SARA.onBoot / onFirstBoot / every), and the small event bus (SARA.on / emit).
   Backend files: sara/gui/app/events.py (push side), engine.py (Api).  Started by js/main.js.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA;

  /* ---- tiny event bus (used for both backend pushes and page-to-page messages) ---- */
  const L = {};
  SARA.on = function (name, fn) { (L[name] = L[name] || []).push(fn); };
  SARA.emit = function (name) {
    const args = Array.prototype.slice.call(arguments, 1);
    (L[name] || []).slice().forEach(function (fn) {
      try { fn.apply(null, args); } catch (e) { console.error('[sara:' + name + ']', e); }
    });
  };

  /* ---- Python bridge ---- */
  const RETRY_ATTEMPTS = 5, RETRY_DELAY_MS = 200, GRACE_MS = 1500, T0 = performance.now();
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  SARA.isConnected = () => !!(window.pywebview && window.pywebview.api);

  SARA.callApi = async function (name) {
    const args = Array.prototype.slice.call(arguments, 1);
    for (let attempt = 0; attempt < RETRY_ATTEMPTS; attempt++) {
      const api = window.pywebview && window.pywebview.api;
      if (api && typeof api[name] === 'function') {
        try { return await api[name].apply(api, args); }
        catch (e) { console.error('[api]', name, e); return { ok: false }; }
      }
      // Plain browser (no pywebview object at all) and startup grace is over -> go straight to mock.
      if (!window.pywebview && performance.now() - T0 > GRACE_MS) break;
      if (attempt < RETRY_ATTEMPTS - 1) await sleep(RETRY_DELAY_MS);
    }
    if (!SARA.usingMock) {
      SARA.usingMock = true;
      console.warn('[api] backend bridge not connected -> using mock (' + name + ')');
      SARA.refreshConnection();
    }
    return SARA.mockApi(name, args);
  };

  let lastConnected = null;
  SARA.refreshConnection = function () {
    const connected = SARA.isConnected();
    const strip = SARA.$('offlineStrip');
    if (strip) strip.hidden = connected || (!SARA.usingMock && performance.now() - T0 < GRACE_MS);
    if (connected) SARA.usingMock = false;
    if (connected !== lastConnected) { lastConnected = connected; SARA.emit('connection', connected); }
  };

  /* Send a typed command exactly like the old GUI: dispatch + usage-analytics counter. */
  SARA.sendCommand = function (text) {
    text = (text || '').trim();
    if (!text) return Promise.resolve({ ok: false });
    SARA.callApi('record_command_usage', text);
    return SARA.callApi('send_text_command', text);
  };

  /* ---- Python -> JS pushes: window.saraEvent({kind, args}) ---- */
  window.saraEvent = function (payload) {
    try {
      const kind = payload && payload.kind, args = (payload && payload.args) || [];
      SARA.emit.apply(null, ['ev:' + kind].concat(args));
    } catch (e) { console.error('[saraEvent]', e); }
  };
  const PROACTIVE_ICONS = ['ti-battery-1', 'ti-alarm', 'ti-coffee', 'ti-flame'];
  SARA.on('ev:notification', function (icon, color, msg) {
    SARA.toast(icon, color, msg, { tone: 'notify' });
    if (PROACTIVE_ICONS.indexOf(icon) >= 0) SARA.proactiveHint();
  });
  SARA.on('ev:proactive_notification', function (icon, color, msg, trigger, reason) { SARA.toast(icon, color, msg, { tone: 'notify', sub: reason }); SARA.proactiveHint(); });
  SARA.on('ev:boot_progress', function (m, p) { SARA.bootOverlay.update(m, p); });
  SARA.on('ev:backend_ready', function () { SARA.refreshConnection(); SARA.bootOverlay.hide(); SARA.emit('backend_ready'); });

  /* ---- navigation ---- */
  SARA.gotoPage = function (name) {
    const page = SARA.$('page-' + name); if (!page) return;
    document.querySelectorAll('.page').forEach((p) => p.classList.toggle('active', p === page));
    document.querySelectorAll('.nav button').forEach((b) => b.classList.toggle('active', b.dataset.page === name));
    document.body.dataset.page = name; SARA.current = name;
    SARA.emit('page', name);
  };
  SARA.$('navBar').addEventListener('click', function (e) {
    const b = e.target.closest('button[data-page]'); if (!b) return;
    SARA.sound.tone(380, 0.035, 'sine', 0.022);
    SARA.gotoPage(b.dataset.page);
  });
  SARA.openOverlay = (id) => SARA.$(id).classList.add('open');
  SARA.closeOverlay = (id) => SARA.$(id).classList.remove('open');
  ['todaySheet', 'quickInputModal', 'routineModal'].forEach(function (id) {   // click backdrop / Esc closes
    SARA.$(id).addEventListener('click', function (e) { if (e.target === this) SARA.closeOverlay(id); });
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') ['todaySheet', 'quickInputModal', 'routineModal'].forEach(SARA.closeOverlay);
  });
  document.querySelectorAll('[data-mini]').forEach((el) => el.addEventListener('click', () => SARA.gotoPage('home')));

  /* ---- tiny accordion helper + toggle helper shared by settings files ---- */
  SARA.setToggle = function (el, on) {
    if (!el) return;
    el.classList.toggle('on', !!on); el.setAttribute('aria-checked', on ? 'true' : 'false');
  };
  SARA.bindToggle = function (el, handler) {   // click / Enter / Space -> handler(newState)
    if (!el) return;
    const act = function () { const on = !el.classList.contains('on'); SARA.setToggle(el, on); SARA.sound.toggle(on); handler(on, el); };
    el.addEventListener('click', act);
    el.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); act(); } });
  };

  /* ---- boot sequence (same guard logic as the old app.js: never double-start timers) ---- */
  const bootFns = [], firstFns = [], timers = [];
  let booted = false, realBooted = false;
  const run = (f) => { try { f(); } catch (e) { console.error('[boot]', e); } };
  SARA.onBoot = (f) => bootFns.push(f);          // runs on every boot (again when the real bridge appears late)
  SARA.onFirstBoot = (f) => firstFns.push(f);    // runs once
  SARA.every = (ms, f) => timers.push([ms, f]); // polling, skipped while window hidden
  SARA.boot = function () {
    const bridge = SARA.isConnected();
    if (realBooted) return;
    if (booted && !bridge) return;
    const first = !booted; booted = true; if (bridge) realBooted = true;
    bootFns.forEach(run);
    if (first) {
      firstFns.forEach(run);
      timers.forEach(function (t) { setInterval(function () { if (!document.hidden) run(t[1]); }, t[0]); });
      setInterval(SARA.refreshConnection, 2000);
      document.addEventListener('visibilitychange', function () { if (!document.hidden) SARA.emit('visible'); });
    }
    SARA.refreshConnection();
  };
})();
