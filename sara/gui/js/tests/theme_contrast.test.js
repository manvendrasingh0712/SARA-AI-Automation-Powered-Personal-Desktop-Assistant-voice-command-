/* ==========================================================================
   theme_contrast.test.js -- the contrast gate of the redesign spec (section 10.2), for every theme in style/core/tokens.css.
   Reads the theme blocks from the file, works out each WCAG contrast ratio, and fails naming the theme and the token.
     --text on --panel >= 7 | --text-soft on --panel >= 4.5 | --muted on --panel and on --void >= 4.5
     --on-core on --core >= 4.5 | --danger on --panel >= 4.5 (it is used for small text)
   The Custom theme is generated at run time and is not in the file: js/settings/settings-presets.js adjusts its own colours.
   Run:  node sara/gui/js/tests/theme_contrast.test.js        (--report prints every ratio)
   ========================================================================== */
'use strict';
const fs = require('fs'), path = require('path');
const css = fs.readFileSync(path.join(__dirname, '..', '..', 'style', 'core', 'tokens.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');

const THEMES = ['sara', 'paper', 'arctic', 'emerald', 'rose', 'violet', 'oat', 'slate', 'ink', 'lime', 'forest', 'ember', 'obsidian', 'aurora', 'neon', 'ocean', 'sunset'];

/* every block that names a theme, in file order; a plain ":root" block is the default every theme inherits from */
function blocks() {
  const out = [], re = /([^{}]+)\{([^{}]*)\}/g;
  let m;
  while ((m = re.exec(css))) out.push({ sel: m[1].trim(), body: m[2] });
  return out;
}
function decls(body) {
  const o = {};
  body.split(';').forEach(function (d) { const i = d.indexOf(':'); if (i > 0) o[d.slice(0, i).trim()] = d.slice(i + 1).trim(); });
  return o;
}
function tokensOf(id) {
  let t = {};
  blocks().forEach(function (b) {
    const parts = b.sel.split(',').map((s) => s.trim());
    const isDefault = parts.indexOf(':root') >= 0;
    const named = parts.indexOf(':root[data-theme="' + id + '"]') >= 0;
    if (isDefault || named) t = Object.assign(t, decls(b.body));
  });
  return t;
}
function rgbOf(v, name, theme) {
  v = String(v || '').trim();
  let m = /^#([0-9a-f]{3})$/i.exec(v);
  if (m) return m[1].split('').map((c) => parseInt(c + c, 16));
  m = /^#([0-9a-f]{6})$/i.exec(v);
  if (m) return [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16));
  m = /^(\d+)\s+(\d+)\s+(\d+)$/.exec(v);
  if (m) return [+m[1], +m[2], +m[3]];
  throw new Error(theme + ': cannot read ' + name + ' = "' + v + '"');
}
function lum(c) {
  const f = c.map(function (x) { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); });
  return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2];
}
function ratio(a, b) { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); }

const GATE = [['--text', '--panel', 7], ['--text-soft', '--panel', 4.5], ['--muted', '--panel', 4.5], ['--muted', '--void', 4.5], ['--on-core', '--core-rgb', 4.5], ['--danger', '--panel', 4.5]];

let passed = 0, failed = 0;
const report = process.argv.indexOf('--report') >= 0;
THEMES.forEach(function (id) {
  const t = tokensOf(id), line = [];
  GATE.forEach(function (g) {
    const r = ratio(rgbOf(t[g[0]], g[0], id), rgbOf(t[g[1]], g[1], id));
    line.push(g[0].slice(2) + '/' + g[1].slice(2).replace('-rgb', '') + ' ' + r.toFixed(2));
    if (r >= g[2]) passed++;
    else { failed++; console.error('  FAIL - ' + id + ': ' + g[0] + ' on ' + g[1] + ' = ' + r.toFixed(2) + ':1, needs ' + g[2] + ':1'); }
  });
  if (report) console.log(id.padEnd(9) + line.join(' | '));
});
console.log('\n' + passed + ' passed, ' + failed + ' failed');
process.exitCode = failed ? 1 : 0;
