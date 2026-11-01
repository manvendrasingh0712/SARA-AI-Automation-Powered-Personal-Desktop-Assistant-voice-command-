/* ==========================================================================
   theme-extras.js -- two small theme features (frontend only, per device, localStorage with an in-memory fallback).
   F2  Hover preview: pointing at a theme card in Settings shows that theme on the whole app until the pointer leaves (nothing is saved or
       emitted; a click still applies it normally). Feature key 'hoverpreview' (off in the Low tier, switchable in Fine-tune effects).
   F11 Auto day / night: with the Settings toggle on, "Paper" is applied from 06:00 to 18:00 and the dark theme you had when you switched
       it on the rest of the day. Applied WITHOUT saving, so your saved theme is never overwritten. Picking a theme yourself turns it off.
   Uses SARA.theme.apply(id, false) / SARA.theme.current(). Markup: #autoTheme in index.html. Load AFTER js/core/theme.js and js/fx/quality.js.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA; if (!SARA || !SARA.theme) return;
  const doc = document, root = doc.documentElement, T = SARA.theme;
  const mem = {};
  const ls = {
    get: function (k) { try { const v = localStorage.getItem(k); if (v !== null) return v; } catch (e) { /* blocked */ } return (k in mem) ? mem[k] : null; },
    set: function (k, v) { mem[k] = v; try { localStorage.setItem(k, v); } catch (e) { /* blocked */ } }
  };
  const allows = function (f) { return !SARA.quality || SARA.quality.allows(f); };
  const ids = function () { return T.list.map(function (t) { return t.id; }); };

  /* ---------------------------------------------------------------- F11 auto day / night */
  const K_AUTO = 'sara_auto_theme', K_DARK = 'sara_auto_dark', DAY_FROM = 6, DAY_TO = 18;
  let auto = ls.get(K_AUTO) === '1', applying = false, lastInput = 0;
  ['pointerdown', 'keydown'].forEach(function (ev) { doc.addEventListener(ev, function () { lastInput = performance.now(); }, { capture: true, passive: true }); });

  const isDay = function (d) { const h = (d || new Date()).getHours(); return h >= DAY_FROM && h < DAY_TO; };
  function darkTheme() { const d = ls.get(K_DARK); return d && ids().indexOf(d) >= 0 && d !== 'paper' ? d : 'sara'; }
  function evaluate() {
    if (!auto) return;
    const want = isDay() ? 'paper' : darkTheme();
    if (T.current() === want) return;
    applying = true; try { T.apply(want, false); } finally { applying = false; }
  }
  function setAuto(on, quiet) {
    auto = !!on; ls.set(K_AUTO, auto ? '1' : '0');
    const t = doc.getElementById('autoTheme'); if (t && SARA.setToggle) SARA.setToggle(t, auto);
    if (auto) {
      const cur = T.current(); if (cur !== 'paper' && cur !== 'custom') ls.set(K_DARK, cur);   // remember the dark theme to come back to at night
      evaluate();
    }
    if (!quiet && SARA.toast) SARA.toast('ti-check', 'var(--core)', auto ? 'Auto day / night is on' : 'Auto day / night is off', { variant: 'info' });
  }
  const toggle = doc.getElementById('autoTheme');
  if (toggle && SARA.bindToggle) { SARA.setToggle(toggle, auto); SARA.bindToggle(toggle, function (on) { setAuto(on); }); }
  // a theme the user picks (a click or key within 1.5 s of the change) switches auto off; changes made by auto itself or by loading saved prefs do not
  SARA.on('theme', function () {
    if (!auto || applying) return;
    if (performance.now() - lastInput < 1500) { auto = false; ls.set(K_AUTO, '0'); if (toggle && SARA.setToggle) SARA.setToggle(toggle, false); }
    else setTimeout(evaluate, 0);                                       // e.g. saved prefs just loaded: re-assert the day / night choice
  });
  SARA.on('pref', function (k) { if (k === 'theme' && auto) setTimeout(evaluate, 0); });
  SARA.onBoot && SARA.onBoot(function () { evaluate(); });
  if (SARA.every) SARA.every(60000, evaluate);
  evaluate();
  SARA.autoTheme = { on: function () { return auto; }, set: setAuto, evaluate: evaluate, isDay: isDay };

  /* ---------------------------------------------------------------- F2 hover preview */
  const grid = doc.getElementById('themeGrid'); let timer = 0, previewing = '';
  function restore() { clearTimeout(timer); if (previewing) { previewing = ''; root.dataset.theme = T.current(); } }
  if (grid) {
    grid.addEventListener('pointerover', function (e) {
      if (e.pointerType === 'touch' || !allows('hoverpreview') || SARA.reduceMotion) return;
      const card = e.target.closest('[data-theme-id],.theme-card'); if (!card || !grid.contains(card)) return;
      const id = card.dataset.themeId || card.dataset.theme || card.getAttribute('data-id') || '';
      if (!id || id === 'custom' || ids().indexOf(id) < 0 || id === T.current()) return restore();
      clearTimeout(timer); timer = setTimeout(function () { previewing = id; root.dataset.theme = id; }, 140);
    });
    grid.addEventListener('pointerleave', restore);
    grid.addEventListener('pointerdown', restore);                       // a click applies the theme for real: drop the preview first
    doc.addEventListener('visibilitychange', function () { if (doc.hidden) restore(); });
    SARA.on('page', restore);
  }
})();
