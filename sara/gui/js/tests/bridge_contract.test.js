/* ==========================================================================
   bridge_contract.test.js -- dependency-free tests (same style as
   chat_state.test.js / ui_prefs_theme.test.js) for js/core.js's Python
   bridge: SARA.callApi dispatch, bind-race handling, and the preview
   (js/mock.js) fallback boundary.

   RUN:  node sara/gui/js/tests/bridge_contract.test.js
   (exits non-zero on any failed assertion, for CI use)

   Regression cover for the Phase 1 audit findings D2 and D3:

   1. A CONNECTED bridge that does not carry the requested name must return
      an explicit {ok:false, error:'unknown_method'} -- it must NOT fall
      through to the preview mock. Falling through is what made the Music
      page's mute button (which called the nonexistent 'toggle_media_mute')
      look like a working control: mock.js answered ok({muted:true}) and the
      icon flipped while nothing was actually muted.

   2. js/mock.js must never be fetched while a bridge is connected. It is no
      longer in index.html's script list; core.js loads it on demand only
      when there is no bridge at all.

   3. window.pywebview.api exists as an EMPTY object from the moment
      pywebview injects its api.js, and is filled in one shot later. An empty
      api therefore means "still binding" (retry), NOT "method missing", and
      SARA.isConnected() must report false for it.
   ========================================================================== */
'use strict';
const fs = require('fs'), path = require('path'), vm = require('vm');
const JS = path.join(__dirname, '..');
function findSrc(root, name) {
  for (const e of fs.readdirSync(root, { withFileTypes: true })) {
    if (e.name === 'tests') continue;
    const p = path.join(root, e.name);
    if (e.isDirectory()) { const r = findSrc(p, name); if (r) return r; }
    else if (e.name === name) return p;
  }
  return null;
}

let failed = 0, passed = 0;
function ok(cond, msg) { if (cond) passed++; else { failed++; console.error('  FAIL: ' + msg); } }
function eq(a, b, msg) { ok(JSON.stringify(a) === JSON.stringify(b), msg + '  (got ' + JSON.stringify(a) + ', want ' + JSON.stringify(b) + ')'); }
function section(n) { console.log(n); }

/* ---- minimal fake DOM, enough for ui.js + core.js to load and run ---- */
function makeEnv() {
  const stubs = {}, scripts = [], errors = [];
  function mkStyle() {
    const props = {};
    return { props, setProperty(k, v) { props[k] = String(v); }, removeProperty(k) { delete props[k]; }, getPropertyValue(k) { return props[k] || ''; } };
  }
  function el(tag) {
    const e = {
      tagName: tag, style: mkStyle(), dataset: {}, children: [], hidden: false, className: '', value: '', src: '',
      classList: { _s: new Set(), add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); }, toggle(c, f) { (f === undefined ? !this._s.has(c) : f) ? this._s.add(c) : this._s.delete(c); }, contains(c) { return this._s.has(c); } },
      addEventListener() {}, removeEventListener() {}, setAttribute() {}, appendChild(c) { this.children.push(c); return c; },
      querySelectorAll() { return []; }, querySelector() { return null; }, closest() { return null; },
      insertBefore() {}, remove() {}, focus() {}, blur() {}
    };
    let text = '';
    Object.defineProperty(e, 'textContent', { get() { return text; }, set(v) { text = String(v); } });
    Object.defineProperty(e, 'innerHTML', { get() { return text; }, set(v) { text = String(v); } });
    return e;
  }
  const root = el('html');
  // head.appendChild is how core.js loads the preview mock on demand: record
  // every script it injects, then fire onload so the await resolves.
  const head = el('head');
  head.appendChild = function (node) {
    scripts.push(node.src);
    setTimeout(function () { if (node.onload) node.onload(); }, 0);
    return node;
  };
  const doc = {
    hidden: false, body: el('body'), head: head, documentElement: root, createElement: el,
    addEventListener() {}, querySelectorAll() { return []; }, querySelector() { return null; },
    getElementById(id) { return stubs[id] || (stubs[id] = el('div')); }
  };
  const fakeConsole = Object.assign({}, console, { error() { errors.push([].slice.call(arguments).join(' ')); }, warn() {} });
  const win = {
    document: doc, console: fakeConsole, performance, setTimeout, clearTimeout,
    setInterval() { return 0; }, clearInterval() {},
    matchMedia() { return { matches: false, addEventListener() {} }; }, addEventListener() {},
    requestAnimationFrame(f) { return setTimeout(f, 0); },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    getComputedStyle() { return { getPropertyValue() { return ''; } }; }, CSS: {}
  };
  win.window = win;
  const ctx = vm.createContext(win);
  win.pywebview = undefined;
  function load(name) { vm.runInContext(fs.readFileSync(findSrc(JS, name), 'utf8'), ctx, { filename: name }); }
  load('ui.js');
  load('core.js');
  return { win, S: win.SARA, scripts, errors, stubs };
}

/* ======================================================================= */
section('core.js: bridge-bound detection (empty api is NOT connected)');
(function () {
  const env = makeEnv(), S = env.S;
  env.win.pywebview = undefined;
  ok(S.isConnected() === false, 'no pywebview at all -> not connected');
  // pywebview injects `api: {}` first and fills it only at _createApi() time.
  env.win.pywebview = { api: {} };
  ok(S.isConnected() === false, 'empty api placeholder -> NOT connected (still binding)');
  env.win.pywebview = { api: { get_system_stats: function () { return {}; } } };
  ok(S.isConnected() === true, 'api carrying at least one function -> connected');
})();

section('core.js: SARA.callApi against a connected bridge');
(async function () {
  const env = makeEnv(), S = env.S;
  let seen = null;
  env.win.pywebview = { api: { get_display_name: function (a) { seen = a; return { ok: true, name: 'Khush' }; } } };

  const good = await S.callApi('get_display_name', 'arg1');
  eq(good, { ok: true, name: 'Khush' }, 'known method returns the backend result');
  eq(seen, 'arg1', 'arguments are forwarded to the backend method');

  // D3 regression: this is exactly the toggle_media_mute case.
  const miss = await S.callApi('toggle_media_mute');
  eq(miss, { ok: false, error: 'unknown_method', method: 'toggle_media_mute' },
    'connected bridge + missing name -> explicit failure, never a fabricated ok()');
  ok(env.scripts.length === 0, 'preview mock is NOT fetched while the bridge is connected');
  ok(env.errors.some((e) => e.indexOf('toggle_media_mute') >= 0), 'the missing method is reported to the console');
  ok(S.usingMock !== true, 'a missing method does not flip the app into preview mode');

  // A backend method that throws is also an explicit failure, not an ok().
  env.win.pywebview.api.boom = function () { throw new Error('backend blew up'); };
  const threw = await S.callApi('boom');
  eq(threw, { ok: false, error: 'call_failed', method: 'boom' }, 'a throwing backend method returns an explicit failure');

  // The 'api:missing' event lets any page surface the problem in the UI.
  let announced = null;
  S.on('api:missing', function (n) { announced = n; });
  await S.callApi('no_such_method_at_all');
  eq(announced, 'no_such_method_at_all', "SARA.emit('api:missing') fires with the offending name");
})();

section('core.js: preview fallback only when there is no bridge at all');
(async function () {
  const env = makeEnv(), S = env.S;
  env.win.pywebview = undefined;
  const res = await S.callApi('get_display_name');
  ok(env.scripts.length === 1 && /mock\.js$/.test(env.scripts[0]), 'js/mock.js is fetched on demand exactly once');
  ok(S.usingMock === true, 'no bridge -> preview mode is flagged');
  ok(res && typeof res === 'object', 'preview still answers the call');

  // Second call must reuse the already-loaded mock rather than re-injecting it.
  await S.callApi('get_system_stats');
  ok(env.scripts.length === 1, 'the preview mock is not re-injected on later calls');
})();

/* ---- mock.js itself: an unknown name must be loud, not ok() ---- */
section('mock.js: unknown names are explicit failures');
(function () {
  const src = fs.readFileSync(findSrc(JS, 'mock.js'), 'utf8');
  ok(!/default:\s*return ok\(\);/.test(src), "mock.js no longer has `default: return ok();`");
  ok(/default:/.test(src) && /unknown_method/.test(src), 'mock.js default branch returns an unknown_method failure');
})();

section('index.html: mock.js is not in the production script list');
(function () {
  const html = fs.readFileSync(path.join(JS, '..', 'index.html'), 'utf8');
  ok(!/mock\.js/.test(html), 'index.html does not load mock.js');
  const man = JSON.parse(fs.readFileSync(path.join(JS, '..', 'assets.json'), 'utf8'));
  ok(man.js.indexOf('js/core/core.js') >= 0, 'assets.json still loads js/core/core.js');
  ok(!man.js.some(function (p) { return /mock\.js$/.test(p); }), 'mock.js is not in the bundle');
})();

section('core.js: SARA.api hooks observe calls without changing them');
(async function () {
  const env = makeEnv(), S = env.S, log = [];
  env.win.pywebview = { api: {
    get_x: function (a) { return { ok: true, data: [a] }; },
    del_x: function () { return { ok: true }; },
    bad_x: function () { throw new Error('boom'); }
  } };
  S.api.before('del_x', function (args) { log.push('before:' + args[1]); return { snap: args[1] }; });
  S.api.after('del_x', function (res, args, ctx) { log.push('after:' + res.ok + ':' + ctx.snap); });
  S.api.after('get_x', function () { throw new Error('hook bug'); });
  S.api.after('get_x', function (res) { log.push('second:' + res.data[0]); });
  S.api.after('bad_x', function (res) { log.push('bad:' + (res && res.error)); });
  S.api.after('bad_x', function (res) { log.push('onError:' + (res && res.error)); }, true);

  const r1 = await S.callApi('del_x', 7); await new Promise(function (r) { setTimeout(r, 5); });
  eq(r1, { ok: true }, 'hooked call returns the backend result unchanged');
  eq(log.slice(0, 2), ['before:7', 'after:true:7'], 'before runs first and its value reaches after as ctx');
  const r2 = await S.callApi('get_x', 5); await new Promise(function (r) { setTimeout(r, 5); });
  eq(r2, { ok: true, data: [5] }, 'a throwing hook does not change the result');
  ok(log.indexOf('second:5') >= 0, 'a throwing hook does not stop the next hook');
  await S.callApi('bad_x'); await new Promise(function (r) { setTimeout(r, 5); });
  ok(log.indexOf('bad:call_failed') >= 0, 'after hooks see the explicit failure object');
  const raw = await S.api.raw('del_x', 9); await new Promise(function (r) { setTimeout(r, 5); });
  eq(raw, { ok: true }, 'SARA.api.raw still calls the backend');
  ok(log.indexOf('before:9') < 0, 'SARA.api.raw skips hooks');
})();

section('ui.js: SARA.onToast listeners run for every toast and cannot break it');
(function () {
  const env = makeEnv(), S = env.S; let got = null;
  S.onToast(function () { throw new Error('listener bug'); });
  S.onToast(function (icon, color, msg) { got = [icon, color, msg]; });
  S.toast('ti-x', '#fff', 'hello');
  eq(got, ['ti-x', '#fff', 'hello'], 'listener receives the toast arguments even after another listener threw');
})();

/* ---- results ---- */
setTimeout(function () {
  console.log('\n' + passed + ' passed, ' + failed + ' failed');
  process.exit(failed ? 1 : 0);
}, 2000);
