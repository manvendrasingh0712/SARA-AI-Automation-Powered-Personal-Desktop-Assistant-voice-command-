/* ==========================================================================
   theme.js -- colour themes + accent hue.  The look itself lives in style/tokens.css
   (:root[data-theme="..."] blocks); this file only switches data-theme and, for the accent-hue slider,
   overrides --core-rgb on <html>.  Everything else (CSS, orb canvas, ambient glow) reads those variables.
   Persistence: SARA.prefs ('theme', 'accent_hue') -> backend DB, localStorage cache.
   Exposes: SARA.theme { list, current(), apply(id), setHue(0..360|null), rgb(varName) }; emits 'theme' (id).
   Settings UI: #themeGrid (swatches), #hueSlider, #hueReset, #hueVal in index.html (Appearance card).
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA, $ = SARA.$;
  const root = document.documentElement;

  const LIST = [
    { id: 'sara',   name: 'Terrarium', dots: ['#080C0D', '#3FD8C4', '#8B6FD8', '#E8C08F'] },
    { id: 'ember',  name: 'Ember',     dots: ['#0D0A09', '#FF9F5A', '#C97BD8', '#F2D28B'] },
    { id: 'violet', name: 'Violet',    dots: ['#0A0812', '#9D8CFF', '#5AC8FA', '#FF9BD2'] },
    { id: 'slate',  name: 'Slate',     dots: ['#0B0E13', '#8AB4FF', '#B39DFF', '#E6EDF7'] },
    { id: 'rose',   name: 'Rose',      dots: ['#0E080B', '#FF7EA8', '#9B7CFF', '#FFD3A8'] },
    { id: 'paper',  name: 'Paper',     dots: ['#F4F1EA', '#0F9D8A', '#6B54C9', '#B5763A'] }
  ];
  const ids = LIST.map((t) => t.id);

  /* ---- colour helpers (pure) ---- */
  function rgbToHsl(r, g, b) {
    r /= 255; g /= 255; b /= 255;
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2; let h = 0, s = 0;
    if (mx !== mn) {
      const d = mx - mn; s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
      h = mx === r ? (g - b) / d + (g < b ? 6 : 0) : mx === g ? (b - r) / d + 2 : (r - g) / d + 4; h *= 60;
    }
    return [h, s, l];
  }
  function hslToRgb(h, s, l) {
    h = ((h % 360) + 360) % 360 / 360;
    if (s === 0) { const v = Math.round(l * 255); return [v, v, v]; }
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s, p = 2 * l - q;
    const f = (t) => { t = (t + 1) % 1; return t < 1 / 6 ? p + (q - p) * 6 * t : t < 1 / 2 ? q : t < 2 / 3 ? p + (q - p) * (2 / 3 - t) * 6 : p; };
    return [f(h + 1 / 3), f(h), f(h - 1 / 3)].map((v) => Math.round(v * 255));
  }
  function parseTriplet(s) { const a = String(s || '').trim().split(/[\s,]+/).map(Number); return a.length === 3 && a.every((n) => !isNaN(n)) ? a : null; }

  /* current value of a "r g b" token, e.g. SARA.theme.rgb('--core-rgb') -> [63,216,196] */
  function rgb(varName, fallback) {
    return parseTriplet(getComputedStyle(root).getPropertyValue(varName)) || fallback || [128, 128, 128];
  }

  let current = 'sara', hue = null;
  function applyHue() {
    if (hue === null) { root.style.removeProperty('--core-rgb'); return; }
    root.style.removeProperty('--core-rgb');                       // read the THEME's own accent first
    const base = rgb('--core-rgb', [63, 216, 196]), hsl = rgbToHsl(base[0], base[1], base[2]);
    root.style.setProperty('--core-rgb', hslToRgb(hue, hsl[1], hsl[2]).join(' '));
  }
  function paintUi() {
    const g = $('themeGrid');
    if (g) g.querySelectorAll('.theme-swatch').forEach((b) => b.classList.toggle('on', b.dataset.theme === current));
    const s = $('hueSlider'), v = $('hueVal');
    if (s && hue !== null) s.value = String(Math.round(hue));
    if (v) v.textContent = hue === null ? 'theme default' : Math.round(hue) + '°';
  }
  function animateSwitch() {
    if (SARA.reduceMotion) return;
    root.classList.add('theme-anim'); clearTimeout(animateSwitch._t);
    animateSwitch._t = setTimeout(() => root.classList.remove('theme-anim'), 650);
  }
  function commit(animate) {
    if (animate) animateSwitch();
    root.dataset.theme = current; applyHue(); paintUi();
    SARA.emit('theme', current);
  }
  function normId(id) { return ids.indexOf(id) >= 0 ? id : 'sara'; }
  function normHue(v) { if (v === null || v === undefined || v === '') return null; const n = Number(v); return isNaN(n) ? null : ((n % 360) + 360) % 360; }

  SARA.theme = {
    list: LIST, rgb: rgb, _rgbToHsl: rgbToHsl, _hslToRgb: hslToRgb, _parse: parseTriplet,
    current: () => current,
    hue: () => hue,
    apply: function (id, persist) {
      current = normId(id); commit(true);
      if (persist !== false) SARA.prefs.set('theme', current);
    },
    setHue: function (v, persist) {
      hue = normHue(v); commit(false);
      if (persist !== false) SARA.prefs.set('accent_hue', hue === null ? '' : Math.round(hue));
    }
  };

  /* first paint: from the cache (the inline <script> in <head> already set data-theme; this also handles the hue) */
  current = normId(SARA.prefs.get('theme', 'sara')); hue = normHue(SARA.prefs.get('accent_hue', ''));
  commit(false);
  /* when the backend answers (SARA.prefs.load) re-apply if it differs */
  SARA.on('pref', function (k, v) {
    if (k === 'theme' && normId(v) !== current) { current = normId(v); commit(true); }
    if (k === 'accent_hue' && normHue(v) !== hue) { hue = normHue(v); commit(false); }
  });

  /* ---- Settings > Appearance ---- */
  const grid = $('themeGrid');
  if (grid) {
    grid.innerHTML = LIST.map((t) =>
      '<button type="button" class="theme-swatch" data-theme="' + t.id + '" aria-label="' + t.name + ' theme"><span class="ts-dots">' +
      t.dots.map((c) => '<i style="background:' + c + '"></i>').join('') + '</span>' + t.name + '</button>').join('');
    grid.addEventListener('click', function (e) {
      const b = e.target.closest('.theme-swatch'); if (!b) return;
      SARA.sound.tap(); SARA.theme.apply(b.dataset.theme);
    });
    paintUi();
  }
  const slider = $('hueSlider');
  if (slider) {
    let timer = 0;
    slider.addEventListener('input', function () { hue = normHue(slider.value); commit(false); clearTimeout(timer); timer = setTimeout(() => SARA.prefs.set('accent_hue', Math.round(hue)), 250); });
  }
  const reset = $('hueReset');
  if (reset) reset.addEventListener('click', function () { SARA.sound.tap(); SARA.theme.setHue(null); });
})();
