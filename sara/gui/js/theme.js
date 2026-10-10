/* ==========================================================================
   theme.js -- colour themes + accent hue.  The look itself lives in style/tokens.css
   (:root[data-theme="..."] blocks); this file only switches data-theme and, for the accent-hue slider,
   overrides --core-rgb on <html>.  Everything else (CSS, orb canvas, ambient glow) reads those variables.
   Persistence: SARA.prefs ('theme', 'accent_hue') -> backend DB, localStorage cache.
   Exposes: SARA.theme { list, current(), apply(id), setHue(0..360|null), rgb(varName) }; emits 'theme' (id).
   Settings UI (Appearance card): #themeFilters (category chips), #themeGrid (mini-UI cards), #hueSlider, #hueVal, #hueReset, #accentPv.
   Theme cards render a real mini-UI: each card carries data-theme-preview="id", and tokens.css matches that attribute too.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA, $ = SARA.$;
  const root = document.documentElement;

  /* colours live in style/tokens.css; an entry here is only id / label / category */
  const CATEGORIES = [
    { id: 'all', name: 'All' }, { id: 'signature', name: 'Signature' }, { id: 'futuristic', name: 'Futuristic' },
    { id: 'dark', name: 'Dark' }, { id: 'warm', name: 'Warm' }, { id: 'minimal', name: 'Minimal' }, { id: 'light', name: 'Light' }
  ];
  const LIST = [
    { id: 'sara',    name: 'Terrarium',  description: 'Signature',       category: 'signature',  tags: ['dark'] },
    { id: 'paper',   name: 'Paper',      description: 'Warm light',      category: 'light',      tags: ['minimal'] },
    { id: 'arctic',  name: 'Sky',        description: 'iOS blue',        category: 'light' },
    { id: 'emerald', name: 'Mint',       description: 'Fresh & airy',    category: 'light' },
    { id: 'rose',    name: 'Blush',      description: 'Soft pink',       category: 'light',      tags: ['warm'] },
    { id: 'violet',  name: 'Lilac',      description: 'Indigo glow',     category: 'light',      tags: ['futuristic'] },
    { id: 'slate',   name: 'Midnight',   description: 'iOS dark',        category: 'minimal',    tags: ['dark'] },
    { id: 'ember',   name: 'Ember',      description: 'Molten warm',     category: 'warm',       tags: ['dark'] },
    { id: 'cyber',   name: 'Cyber Blue', description: 'Electric',        category: 'futuristic', tags: ['dark'] },
    { id: 'aurora',  name: 'Aurora',     description: 'Northern lights', category: 'futuristic', tags: ['dark'] },
    { id: 'neon',    name: 'Neon Black', description: 'Electric blue',   category: 'futuristic', tags: ['dark'] },
    { id: 'ink',     name: 'Mono Ink',   description: 'Black & vermilion', category: 'minimal',  tags: ['dark'] },
    { id: 'oat',     name: 'Oat & Sage', description: 'Calm & matte',      category: 'light',    tags: ['minimal'] },
    { id: 'lime',    name: 'Graphite Lime', description: 'Quiet & bold',   category: 'minimal',  tags: ['dark'] }
  ];
  const ids = LIST.map((t) => t.id);
  const DANGER_GUARD = 30;                                   // deg: accent hue never lands this close to the theme's danger red
  let filter = 'all';

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

  let current = 'sara', hue = null, baseHue = 170;
  /* keep the accent away from the danger red so "accent" can never read as "error" (status colours themselves are never touched) */
  function guardHue(h) {
    const d = rgb('--danger-rgb', [217, 122, 107]), dh = rgbToHsl(d[0], d[1], d[2])[0];
    const diff = ((h - dh + 540) % 360) - 180;                     // signed distance -180..180
    if (Math.abs(diff) >= DANGER_GUARD) return h;
    return ((dh + (diff < 0 ? -DANGER_GUARD : DANGER_GUARD)) % 360 + 360) % 360;
  }
  function applyHue() {
    root.style.removeProperty('--core-rgb');                       // read the THEME's own accent first
    const base = rgb('--core-rgb', [63, 216, 196]), hsl = rgbToHsl(base[0], base[1], base[2]);
    baseHue = hsl[0];
    if (hue === null) return;
    hue = guardHue(hue);
    root.style.setProperty('--core-rgb', hslToRgb(hue, hsl[1], hsl[2]).join(' '));
  }
  function paintUi() {
    const g = $('themeGrid');
    if (g) g.querySelectorAll('.theme-card').forEach((b) => { const on = b.dataset.theme === current; b.classList.toggle('on', on); b.setAttribute('aria-checked', on ? 'true' : 'false'); });
    const s = $('hueSlider'), v = $('hueVal'), r = $('hueReset');
    if (s) s.value = String(Math.round(hue === null ? baseHue : hue));
    if (v) v.textContent = hue === null ? 'theme default' : Math.round(hue) + '°';
    if (r) r.disabled = hue === null;
  }
  function paintFilters() {
    const f = $('themeFilters'), g = $('themeGrid');
    if (f) f.querySelectorAll('.chip').forEach((c) => { const on = c.dataset.cat === filter; c.classList.toggle('on', on); c.setAttribute('aria-pressed', on ? 'true' : 'false'); });
    if (g) g.querySelectorAll('.theme-card').forEach((b) => { b.hidden = filter !== 'all' && (' ' + b.dataset.cat + ' ').indexOf(' ' + filter + ' ') < 0; });
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
    list: LIST, categories: CATEGORIES, rgb: rgb, _rgbToHsl: rgbToHsl, _hslToRgb: hslToRgb, _parse: parseTriplet,
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
    /* mini SARA UI per card: bar (logo + clock), ACTIVE pill, panel + accent block, palette dots -- all coloured by the card's own data-theme-preview tokens */
    grid.innerHTML = LIST.map((t) =>
      '<button type="button" class="theme-card" role="radio" aria-checked="false" data-theme="' + t.id + '" data-cat="' + [t.category].concat(t.tags || []).join(' ') + '" aria-label="' + t.name + ' theme, ' + t.description + '">' +
      '<span class="tc-pv" data-theme-preview="' + t.id + '" aria-hidden="true">' +
      '<span class="tc-bar"><b>SARA</b><em>12:42</em></span>' +
      '<span class="tc-active"><i></i>ACTIVE</span>' +
      '<span class="tc-row"><span class="tc-panel"><i></i><i></i></span><span class="tc-accent"></span></span>' +
      '<span class="tc-dots"><i class="p"></i><i class="s"></i><i class="h"></i></span>' +
      '</span>' +
      '<span class="tc-meta"><b>' + t.name + '</b><small>' + t.description + '</small></span>' +
      '<span class="tc-check" aria-hidden="true"><svg viewBox="0 0 16 16"><path d="M3.5 8.5l3 3 6-7"/></svg></span></button>').join('');
    grid.addEventListener('click', function (e) {
      const b = e.target.closest('.theme-card'); if (!b) return;
      SARA.sound.tap(); SARA.theme.apply(b.dataset.theme);
    });
    const fbar = $('themeFilters');
    if (fbar) {
      fbar.innerHTML = CATEGORIES.map((c) => '<button type="button" class="chip" data-cat="' + c.id + '" aria-pressed="false">' + c.name + '</button>').join('');
      fbar.addEventListener('click', function (e) {
        const c = e.target.closest('.chip'); if (!c) return;
        SARA.sound.tap(); filter = c.dataset.cat; paintFilters();
      });
    }
    paintFilters(); paintUi();
  }
  const slider = $('hueSlider');
  if (slider) {
    let timer = 0;
    slider.addEventListener('input', function () { hue = normHue(slider.value); commit(false); clearTimeout(timer); timer = setTimeout(() => SARA.prefs.set('accent_hue', Math.round(hue)), 250); });
  }
  const reset = $('hueReset');
  if (reset) reset.addEventListener('click', function () { SARA.sound.tap(); SARA.theme.setHue(null); });
})();