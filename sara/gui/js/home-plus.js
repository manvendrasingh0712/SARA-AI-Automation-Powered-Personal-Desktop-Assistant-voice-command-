/* ==========================================================================
   home-plus.js -- Home screen "hero" upgrades. Frontend only; js/home.js is NOT modified.
   1 Orb presence   a light layer around the orb that reacts to Sara's real state: listening -> ripples that follow your voice level,
                    thinking/working -> a soft rotating light ring, speaking -> a halo that swells with her voice. (The canvas orb is untouched.)
   2 Sky light      the home page gets a real-time "daylight": a sun that crosses the sky by the clock, warm dawn / gold morning /
                    cool afternoon / amber dusk / moonlit night, with faint stars on High quality. data-tod on <html> = dawn|morning|afternoon|evening|night.
   3 Customize      "customize" link under the orb opens a panel: presets (Full / Focus / Calm) and switches for every widget. Saved on this device.
   Styles: style/home-plus.css. Honours Settings > Visual quality (js/quality.js) and reduced motion.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA; if (!SARA) return;
  const doc = document, root = doc.documentElement, $ = SARA.$;
  const home = $('page-home'), stage = $('stage'); if (!home || !stage) return;
  const el = function (tag, cls, text) { const n = doc.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
  const clamp = function (v, a, b) { return Math.min(b, Math.max(a, v)); };
  const mem = {};
  const store = {
    get: function (k) { try { const v = localStorage.getItem(k); if (v !== null) return v; } catch (e) { /* blocked */ } return (k in mem) ? mem[k] : null; },
    set: function (k, v) { mem[k] = v; try { localStorage.setItem(k, v); } catch (e) { /* blocked */ } }
  };

  /* ====================================================== 1) orb presence */
  const fx = el('div', 'orb-fx'); fx.setAttribute('aria-hidden', 'true');
  fx.appendChild(el('span', 'orb-halo'));
  for (let i = 0; i < 3; i++) fx.appendChild(el('span', 'orb-rip'));
  fx.appendChild(el('span', 'orb-sweep'));
  stage.insertBefore(fx, stage.firstChild);

  const MAP = { listening: 'listening', waking: 'listening', thinking: 'thinking', working: 'working', speaking: 'speaking' };
  let mode = 'idle', level = 0, shown = 0, lraf = 0, llast = 0;
  function setMode(next) {
    if (SARA.state && SARA.state.assistantActive === false) next = 'idle';
    if (next === mode) return; mode = next; stage.dataset.orb = mode;
    if (mode === 'listening' || mode === 'speaking') startLevel(); else { level = 0; }
  }
  stage.dataset.orb = 'idle';
  SARA.on('status', function (s) { setMode(MAP[s] || 'idle'); });
  SARA.on('assistant_active', function (a) { if (!a) setMode('idle'); });
  SARA.on('ev:audio_level', function (source, v) {
    const ok = (source === 'mic' && mode === 'listening') || (source === 'tts' && mode === 'speaking');
    level = ok ? clamp(Number(v) || 0, 0, 1) : 0;
    if (ok) startLevel();
  });
  function startLevel() { if (!lraf && !SARA.reduceMotion) lraf = requestAnimationFrame(levelStep); }
  function levelStep(now) {
    lraf = 0;
    const dt = llast ? clamp(now - llast, 1, 50) : 16.7; llast = now;
    level *= Math.pow(0.94, dt / 16.7);                                       // decays by itself: a stalled event stream can't freeze the halo
    shown += (level - shown) * (1 - Math.pow(1 - 0.22, dt / 16.7));
    fx.style.setProperty('--lvl', shown.toFixed(3));
    const active = (mode === 'listening' || mode === 'speaking') && !doc.hidden && (SARA.current || 'home') === 'home';
    if (active || shown > 0.01) lraf = requestAnimationFrame(levelStep); else { llast = 0; fx.style.setProperty('--lvl', '0'); }
  }
  doc.addEventListener('visibilitychange', function () { if (!doc.hidden) startLevel(); });

  /* ====================================================== 2) sky light */
  const sky = el('div', 'home-sky'); sky.setAttribute('aria-hidden', 'true'); home.insertBefore(sky, home.firstChild);
  function phaseOf(h) { return h >= 5 && h < 8 ? 'dawn' : h < 12 ? 'morning' : h < 17 ? 'afternoon' : h < 20 ? 'evening' : 'night'; }
  function skyNow() {
    const d = new Date(), h = d.getHours() + d.getMinutes() / 60, p = phaseOf(d.getHours());
    let x, y;
    if (h >= 6 && h < 19) { const f = (h - 6) / 13; x = 12 + 76 * f; y = 64 - 52 * Math.sin(Math.PI * f); }          // sun: left -> right over an arc
    else { const f = h >= 19 ? (h - 19) / 11 : (h + 5) / 11; x = 12 + 76 * f; y = 58 - 44 * Math.sin(Math.PI * clamp(f, 0, 1)); }   // moon
    root.dataset.tod = p; sky.dataset.tod = p;
    sky.style.setProperty('--sx', x.toFixed(1) + '%'); sky.style.setProperty('--sy', y.toFixed(1) + '%');
    SARA.tod = p;
  }
  skyNow(); setInterval(function () { if (!doc.hidden) skyNow(); }, 60000);
  doc.addEventListener('visibilitychange', function () { if (!doc.hidden) skyNow(); });

  /* ====================================================== 3) customize panel */
  const KEY = 'sara_home';
  const WIDGETS = [
    ['clock', 'Clock', 'time, date and day progress'], ['today', 'Weather', 'temperature and next item'], ['next', 'Next up', 'your next reminder or event'],
    ['greet', 'Greeting', 'your name, top right'], ['hint', 'Hint line', 'tips under the orb'],
    ['ring', 'Orbit ring', 'the thin ring around the orb'], ['fx', 'Orb presence', 'ripples, halo and light ring'], ['sky', 'Sky light', 'sun, moon and daylight colour']
  ];
  const PRESETS = {
    full:  { clock: 1, today: 1, next: 1, greet: 1, hint: 1, ring: 1, fx: 1, sky: 1 },
    focus: { clock: 1, today: 0, next: 1, greet: 0, hint: 0, ring: 0, fx: 1, sky: 0 },
    calm:  { clock: 0, today: 0, next: 0, greet: 0, hint: 0, ring: 1, fx: 1, sky: 1 }
  };
  let cfg = Object.assign({}, PRESETS.full);
  try { const saved = JSON.parse(store.get(KEY) || 'null'); if (saved && typeof saved === 'object') WIDGETS.forEach(function (w) { if (w[0] in saved) cfg[w[0]] = saved[w[0]] ? 1 : 0; }); } catch (e) { /* defaults */ }
  const TARGET = { clock: '.clock-card', today: '.today-card', next: '.next-card', greet: '.home-greet', hint: '.home-hint' };
  function applyCfg(animateKeys) {
    WIDGETS.forEach(function (w) { home.classList.toggle('hw-hide-' + w[0], !cfg[w[0]]); });
    (animateKeys || []).forEach(function (k) {
      const t = TARGET[k] && home.querySelector(TARGET[k]);
      if (t && cfg[k] && t.animate && !SARA.reduceMotion) t.animate([{ opacity: 0, scale: '.96' }, { opacity: 1, scale: '1' }], { duration: 480, easing: 'cubic-bezier(.16,1,.3,1)' });
    });
    store.set(KEY, JSON.stringify(cfg)); paintPanel();
  }

  const pop = el('div', 'home-pop'); pop.hidden = true; pop.setAttribute('role', 'dialog'); pop.setAttribute('aria-label', 'Customize home');
  const head = el('div', 'hp-head'); head.appendChild(el('span', 'hp-title', 'Customize home'));
  const closeBtn = el('button', 'hp-x', '\u00d7'); closeBtn.type = 'button'; closeBtn.setAttribute('aria-label', 'Close'); head.appendChild(closeBtn);
  const presets = el('div', 'hp-presets'); presets.setAttribute('role', 'radiogroup'); presets.setAttribute('aria-label', 'Layout presets');
  Object.keys(PRESETS).forEach(function (k) {
    const b = el('button', 'chip', k === 'full' ? 'Full' : k === 'focus' ? 'Focus' : 'Calm'); b.type = 'button'; b.dataset.preset = k; b.setAttribute('role', 'radio'); presets.appendChild(b);
  });
  const rows = el('div', 'hp-rows'), toggles = {};
  WIDGETS.forEach(function (w) {
    const row = el('div', 'hp-row'), info = el('div', 'hp-info');
    info.appendChild(el('div', 'hp-name', w[1])); info.appendChild(el('div', 'hp-sub', w[2]));
    const t = el('div', 'toggle'); t.setAttribute('role', 'switch'); t.tabIndex = 0; t.setAttribute('aria-label', w[1]);
    row.appendChild(info); row.appendChild(t); rows.appendChild(row); toggles[w[0]] = t;
    SARA.bindToggle(t, function (on) { cfg[w[0]] = on ? 1 : 0; applyCfg(on ? [w[0]] : []); });
  });
  const foot = el('div', 'hp-foot'), reset = el('button', 'link-btn', 'Reset to default'); reset.type = 'button'; foot.appendChild(reset);
  pop.appendChild(head); pop.appendChild(presets); pop.appendChild(rows); pop.appendChild(foot); home.appendChild(pop);

  function paintPanel() {
    WIDGETS.forEach(function (w) { SARA.setToggle(toggles[w[0]], !!cfg[w[0]]); });
    presets.querySelectorAll('.chip').forEach(function (c) {
      const p = PRESETS[c.dataset.preset], on = WIDGETS.every(function (w) { return !!p[w[0]] === !!cfg[w[0]]; });
      c.classList.toggle('on', on); c.setAttribute('aria-checked', on ? 'true' : 'false');
    });
  }
  presets.addEventListener('click', function (e) {
    const c = e.target.closest && e.target.closest('.chip'); if (!c) return;
    const before = Object.assign({}, cfg); cfg = Object.assign({}, PRESETS[c.dataset.preset]);
    applyCfg(WIDGETS.map(function (w) { return w[0]; }).filter(function (k) { return cfg[k] && !before[k]; }));
  });
  reset.addEventListener('click', function () { const before = Object.assign({}, cfg); cfg = Object.assign({}, PRESETS.full); applyCfg(Object.keys(cfg).filter(function (k) { return !before[k]; })); });

  const openBtn = el('button', 'link-btn', 'customize'); openBtn.type = 'button'; openBtn.id = 'homeCustomizeBtn'; openBtn.setAttribute('aria-haspopup', 'dialog');
  const actions = home.querySelector('.home-actions'); if (actions) actions.appendChild(openBtn);
  function openPop() { pop.hidden = false; requestAnimationFrame(function () { pop.classList.add('open'); }); openBtn.setAttribute('aria-expanded', 'true'); const f = pop.querySelector('.chip'); if (f) f.focus({ preventScroll: true }); }
  function closePop(refocus) {
    pop.classList.remove('open'); openBtn.setAttribute('aria-expanded', 'false');
    setTimeout(function () { if (!pop.classList.contains('open')) pop.hidden = true; }, 260);
    if (refocus) openBtn.focus({ preventScroll: true });
  }
  openBtn.addEventListener('click', function (e) { e.stopPropagation(); if (pop.hidden || !pop.classList.contains('open')) openPop(); else closePop(false); });
  closeBtn.addEventListener('click', function () { closePop(true); });
  doc.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !pop.hidden) { e.stopPropagation(); closePop(true); } }, true);
  doc.addEventListener('pointerdown', function (e) { if (!pop.hidden && !pop.contains(e.target) && e.target !== openBtn) closePop(false); }, true);
  SARA.on('page', function (p) { if (p !== 'home' && !pop.hidden) closePop(false); });
  applyCfg([]);
})();
