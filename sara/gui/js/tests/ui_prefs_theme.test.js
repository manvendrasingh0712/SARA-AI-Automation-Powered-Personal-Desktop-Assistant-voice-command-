/* ==========================================================================
   ui_prefs_theme.test.js -- dependency-free tests (same style as chat_state.test.js) for:
     ui.js helpers + toast icons | core.js SARA.every page gating | prefs.js (cache / backend / migration)
     | theme.js (theme switch, accent hue, colour maths).
   Runs the real files in a Node `vm` against a tiny fake DOM.
   RUN:  node sara/gui/js/tests/ui_prefs_theme.test.js      (exits non-zero on any failed assertion)
   ========================================================================== */
'use strict';
const fs = require('fs'), path = require('path'), vm = require('vm');
const JS = path.join(__dirname, '..');
let failed = 0, passed = 0;
function ok(cond, msg) { if (cond) passed++; else { failed++; console.error('  FAIL: ' + msg); } }
function eq(a, b, msg) { ok(JSON.stringify(a) === JSON.stringify(b), msg + '  (got ' + JSON.stringify(a) + ', want ' + JSON.stringify(b) + ')'); }
function section(n) { console.log(n); }

const THEME_CORE = { sara: '63 216 196', ember: '255 159 90', paper: '15 157 138' };
function makeEnv(opts) {
  opts = opts || {};
  const store = Object.assign({}, opts.local || {}), calls = [], intervals = [], stubs = {};
  function mkStyle() {
    const props = {};
    return { props, setProperty(k, v) { props[k] = String(v); }, removeProperty(k) { delete props[k]; }, getPropertyValue(k) { return props[k] || ''; } };
  }
  function el(tag) {
    const e = {
      tagName: tag, style: mkStyle(), dataset: {}, children: [], hidden: false, className: '', value: '',
      classList: { _s: new Set(), add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); }, toggle(c, f) { (f === undefined ? !this._s.has(c) : f) ? this._s.add(c) : this._s.delete(c); }, contains(c) { return this._s.has(c); } },
      addEventListener() {}, removeEventListener() {}, setAttribute() {}, appendChild(c) { this.children.push(c); return c; },
      querySelectorAll() { return []; }, querySelector() { return null; }, closest() { return null; },
      insertBefore() {}, remove() {}, focus() {}, blur() {}
    };
    let text = '';
    Object.defineProperty(e, 'textContent', { get() { return text; }, set(v) { text = String(v); } });
    Object.defineProperty(e, 'innerHTML', { get() { return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }, set(v) { text = String(v); } });
    return e;
  }
  const root = el('html');
  const doc = { hidden: false, body: el('body'), documentElement: root, createElement: el, addEventListener() {}, querySelectorAll() { return []; }, querySelector() { return null; },
    getElementById(id) { return stubs[id] || (stubs[id] = el('div')); } };
  const win = {
    document: doc, console, performance, setTimeout, clearTimeout,
    setInterval(fn, ms) { intervals.push({ fn, ms }); return intervals.length; }, clearInterval() {},
    matchMedia() { return { matches: false, addEventListener() {} }; }, addEventListener() {},
    requestAnimationFrame(f) { return setTimeout(f, 0); },
    localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
    getComputedStyle() {
      return { getPropertyValue(k) { const o = root.style.getPropertyValue(k); if (o) return ' ' + o; if (k === '--core-rgb') return ' ' + (THEME_CORE[root.dataset.theme || 'sara'] || THEME_CORE.sara); if (k === '--muted') return '#7A8B88'; return ''; } };
    },
    CSS: {}
  };
  win.window = win; win.localStorage = win.localStorage;
  const ctx = vm.createContext(Object.assign(win, { localStorage: win.localStorage }));
  win.pywebview = undefined;
  function load(name) { vm.runInContext(fs.readFileSync(path.join(JS, name), 'utf8'), ctx, { filename: name }); }
  win.SARA_API = function (n) { calls.push([].slice.call(arguments)); };
  return { win, ctx, load, store, calls, intervals, root, doc, stubs };
}
function bootEnv(files, backend) {
  const env = makeEnv(backend && backend.local ? { local: backend.local } : {});
  env.load('ui.js');
  const S = env.win.SARA;
  S.mockApi = function (name, args) { env.calls.push([name].concat(args)); return backend && backend.api ? backend.api(name, args) : { ok: true }; };
  S.callApi = async function (name) { const a = [].slice.call(arguments, 1); env.calls.push([name].concat(a)); return backend && backend.api ? backend.api(name, a) : { ok: true }; };
  (files || []).forEach((f) => env.load(f));
  // core.js defines the real SARA.callApi -- re-stub AFTER loading so calls are recorded / answered by the fake backend
  S.callApi = async function (name) { const a = [].slice.call(arguments, 1); env.calls.push([name].concat(a)); return backend && backend.api ? backend.api(name, a) : { ok: true }; };
  return env;
}

section('ui.js helpers');
(function () {
  const env = bootEnv([]), S = env.win.SARA;
  eq(S.fmt12h('18:30'), '6:30 PM', 'fmt12h evening'); eq(S.fmt12h('00:05'), '12:05 AM', 'fmt12h midnight');
  eq(S.fmtDuration(125), '2:05', 'fmtDuration'); eq(S.fmtDuration(-4), '0:00', 'fmtDuration negative');
  eq(S.escapeHtml('<b>&"x'), '&lt;b&gt;&amp;"x', 'escapeHtml escapes < > &');
  ok(S.iconSvg('ti-alarm').startsWith('<svg'), 'known toast icon renders svg');
  ok(S.iconSvg('ti-alert-triangle').length > 20 && S.iconSvg('alert-triangle') === S.iconSvg('ti-alert-triangle'), 'prefix optional');
  eq(S.iconSvg('ti-does-not-exist'), '', 'unknown icon -> empty (falls back to dot)');
  eq(S.iconSvg(undefined), '', 'undefined icon -> empty');
})();

section('core.js: SARA.every page gating');
(function () {
  const env = bootEnv(['core.js']), S = env.win.SARA;
  const hits = { any: 0, apps: 0 };
  S.every(1000, () => hits.any++);
  S.every(2000, () => hits.apps++, { pages: ['apps'] });
  S.boot();
  const byMs = (ms) => env.intervals.filter((i) => i.ms === ms)[0].fn;
  S.current = 'home'; byMs(1000)(); byMs(2000)();
  eq([hits.any, hits.apps], [1, 0], 'page-gated timer is skipped on another page');
  S.current = 'apps'; byMs(2000)();
  eq(hits.apps, 1, 'page-gated timer runs on its page');
  S.current = undefined; byMs(2000)();
  eq(hits.apps, 1, 'before any navigation the page counts as home');
  env.doc.hidden = true; S.current = 'apps'; byMs(1000)(); byMs(2000)();
  eq([hits.any, hits.apps], [1, 1], 'nothing runs while the window is hidden');
})();

section('prefs.js: backend is the source of truth, localStorage a cache');
(async function () {
  // 1) cache is used immediately
  let env = bootEnv(['core.js', 'prefs.js'], { local: { sara_pref_theme: 'ember' } });
  eq(env.win.SARA.prefs.get('theme', 'sara'), 'ember', 'cached value returned before backend answers');
  eq(env.win.SARA.prefs.get('accent_hue', 'x'), 'x', 'missing key -> fallback');
  // 2) legacy clock theme key is still read
  env = bootEnv(['core.js', 'prefs.js'], { local: { sara_clock_theme: 'aurora' } });
  eq(env.win.SARA.prefs.get('clock_theme', 'sara'), 'aurora', 'legacy localStorage clock theme is picked up');
  // 3) backend value wins + emits 'pref'
  env = bootEnv(['core.js', 'prefs.js'], { local: { sara_pref_theme: 'ember' }, api: (n) => n === 'get_ui_prefs' ? { ok: true, data: { theme: 'violet', accent_hue: '', clock_theme: '' } } : { ok: true } });
  const seen = []; env.win.SARA.on('pref', (k, v) => seen.push(k + '=' + v));
  await env.win.SARA.prefs.load();
  eq(env.win.SARA.prefs.get('theme', 'sara'), 'violet', 'backend value overrides cache');
  eq(env.store.sara_pref_theme, 'violet', 'cache updated from backend');
  ok(seen.indexOf('theme=violet') >= 0, "'pref' event emitted on change");
  // 4) migration: backend empty, cache has a value -> pushed up once
  env = bootEnv(['core.js', 'prefs.js'], { local: { sara_pref_theme: 'rose' }, api: (n) => n === 'get_ui_prefs' ? { ok: true, data: { theme: '', accent_hue: '', clock_theme: '' } } : { ok: true } });
  await env.win.SARA.prefs.load();
  ok(env.calls.some((c) => c[0] === 'set_ui_pref' && c[1] === 'theme' && c[2] === 'rose'), 'local-only value migrated to backend');
  ok(!env.calls.some((c) => c[0] === 'set_ui_pref' && c[1] === 'accent_hue'), 'nothing pushed for keys with no local value');
  // 5) set() writes cache + backend
  env = bootEnv(['core.js', 'prefs.js']);
  await env.win.SARA.prefs.set('accent_hue', 200);
  eq(env.store.sara_pref_accent_hue, '200', 'set() writes cache'); ok(env.calls.some((c) => c[0] === 'set_ui_pref' && c[1] === 'accent_hue' && c[2] === '200'), 'set() calls backend');
  // 6) backend failure -> cache still works
  env = bootEnv(['core.js', 'prefs.js'], { local: { sara_pref_theme: 'slate' }, api: () => ({ ok: false }) });
  await env.win.SARA.prefs.load();
  eq(env.win.SARA.prefs.get('theme', 'sara'), 'slate', 'backend failure keeps cached value');
})().then(themeTests).then(done).catch((e) => { console.error(e); process.exit(1); });

function themeTests() {
  section('theme.js');
  let env = bootEnv(['core.js', 'prefs.js', 'theme.js']), S = env.win.SARA, T = S.theme;
  eq(env.root.dataset.theme, 'sara', 'default theme applied on load');
  eq(T.list.map((t) => t.id), ['sara', 'paper', 'arctic', 'emerald', 'rose', 'violet', 'slate', 'ember', 'cyber', 'aurora'], 'theme list');
  ok(T.list.every((t) => t.name && t.description && T.categories.some((c) => c.id === t.category)), 'every theme has name, description, valid category');
  const emitted = []; S.on('theme', (id) => emitted.push(id));
  T.apply('paper'); eq(env.root.dataset.theme, 'paper', 'apply() sets data-theme'); eq(emitted, ['paper'], "'theme' event emitted");
  ok(env.calls.some((c) => c[0] === 'set_ui_pref' && c[1] === 'theme' && c[2] === 'paper'), 'apply() persists to backend');
  T.apply('nope'); eq(env.root.dataset.theme, 'sara', 'unknown theme falls back to sara');
  T.apply('ember', false); ok(!env.calls.some((c) => c[0] === 'set_ui_pref' && c[2] === 'ember'), 'apply(id,false) does not persist');
  // hue
  T.setHue(200); const c = T._parse(env.root.style.getPropertyValue('--core-rgb'));
  ok(c && c.length === 3, 'setHue writes --core-rgb triplet');
  const hsl = T._rgbToHsl(c[0], c[1], c[2]); ok(Math.abs(hsl[0] - 200) < 3, 'hue is ~200 deg (got ' + hsl[0].toFixed(1) + ')');
  const base = T._parse(THEME_CORE.ember), bhsl = T._rgbToHsl(base[0], base[1], base[2]);
  ok(Math.abs(hsl[1] - bhsl[1]) < 0.03 && Math.abs(hsl[2] - bhsl[2]) < 0.03, 'hue change keeps the theme accent saturation + lightness');
  T.setHue(null); eq(env.root.style.getPropertyValue('--core-rgb'), '', 'setHue(null) removes the override');
  T.setHue(-30); ok(T.hue() === 330, 'negative hue wraps to 330');
  // colour maths
  [[63, 216, 196], [255, 159, 90], [15, 157, 138], [0, 0, 0], [255, 255, 255]].forEach(function (rgb) {
    const h = T._rgbToHsl(rgb[0], rgb[1], rgb[2]), back = T._hslToRgb(h[0], h[1], h[2]);
    ok(back.every((v, i) => Math.abs(v - rgb[i]) <= 1), 'rgb->hsl->rgb round trip ' + rgb);
  });
  eq(T._parse(' 63 216 196'), [63, 216, 196], 'parse triplet with leading space'); eq(T._parse('nope'), null, 'parse garbage -> null');
  // cached theme + hue restored on load
  env = bootEnv(['core.js', 'prefs.js', 'theme.js'], { local: { sara_pref_theme: 'violet', sara_pref_accent_hue: '120' } });
  eq(env.root.dataset.theme, 'violet', 'cached theme restored on load'); ok(env.root.style.getPropertyValue('--core-rgb') !== '', 'cached hue restored on load');
  // backend answer switches theme
  env = bootEnv(['core.js', 'prefs.js', 'theme.js']);
  env.win.SARA.emit('pref', 'theme', 'slate'); eq(env.root.dataset.theme, 'slate', "'pref' event from backend re-applies theme");
}
function done() { console.log('\n' + passed + ' passed, ' + failed + ' failed'); process.exit(failed ? 1 : 0); }