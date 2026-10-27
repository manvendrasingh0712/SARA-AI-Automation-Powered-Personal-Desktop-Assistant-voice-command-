/* ==========================================================================
   a11y-plus.js -- accessibility + keyboard layer (frontend only; existing JS is not modified).
   KEYBOARD   "?" opens a shortcuts list. Alt+1..6 jump to Home / Chat / Notes / Apps / Automation / Settings, Alt+/ jumps to the chat box,
              Ctrl+Alt+0 resets the interface size. (Ctrl+K palette, Ctrl+F chat search, Ctrl+Z undo, / settings search already existed and are listed.)
   SCREEN READERS  a polite live region announces page changes, toasts, Sara's finished replies and "listening / thinking"; page changes also move
              focus to the page title when you navigate by keyboard. Missing names are filled in: switches, icon-only buttons, reminder checkboxes,
              delete / run / edit buttons, the orb canvas, nav (aria-current), main + page landmarks, dialogs (role, label, focus trap, focus return).
   SETTINGS   Settings > Accessibility: Interface size (beta), Higher contrast, Reduce motion, Strong focus outline, Screen-reader announcements.
   Stored on this device (localStorage). Styles: style/a11y-plus.css. Load AFTER js/settings.js (anywhere near the end is fine).
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA; if (!SARA) return;
  const doc = document, root = doc.documentElement, $ = SARA.$;
  const el = function (tag, cls, text) { const n = doc.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
  const clamp = function (v, a, b) { return Math.min(b, Math.max(a, v)); };
  const mem = {};
  const store = {
    get: function (k) { try { const v = localStorage.getItem(k); if (v !== null) return v; } catch (e) { /* blocked */ } return (k in mem) ? mem[k] : null; },
    set: function (k, v) { mem[k] = v; try { localStorage.setItem(k, v); } catch (e) { /* blocked */ } }
  };
  const osReduce = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);

  /* ====================================================================== preferences */
  const KEY = 'sara_a11y';
  const pref = { scale: 100, contrast: false, motion: false, sr: true, focus: false };
  try { const s = JSON.parse(store.get(KEY) || 'null'); if (s && typeof s === 'object') { if (typeof s.scale === 'number') pref.scale = clamp(Math.round(s.scale / 5) * 5, 85, 150); ['contrast', 'motion', 'focus'].forEach(function (k) { if (typeof s[k] === 'boolean') pref[k] = s[k]; }); if (typeof s.sr === 'boolean') pref.sr = s.sr; } } catch (e) { /* defaults */ }
  const save = function () { store.set(KEY, JSON.stringify(pref)); };
  function parseColor(v) {
    v = String(v || '').trim(); let m = /^#([0-9a-f]{6})$/i.exec(v); if (m) { const n = parseInt(m[1], 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
    m = /^#([0-9a-f]{3})$/i.exec(v); if (m) return m[1].split('').map(function (c) { return parseInt(c + c, 16); });
    m = /rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)/i.exec(v); return m ? [+m[1], +m[2], +m[3]] : null;
  }
  function updateLum() {
    const c = parseColor(getComputedStyle(root).getPropertyValue('--void')) || [5, 8, 12];
    const f = function (x) { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); };
    root.dataset.lum = (0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2])) < 0.4 ? 'dark' : 'light';
  }
  function applyPrefs() {
    root.dataset.contrast = pref.contrast ? 'high' : ''; if (!pref.contrast) delete root.dataset.contrast;
    root.dataset.focus = pref.focus ? 'strong' : ''; if (!pref.focus) delete root.dataset.focus;
    if (pref.motion || osReduce) { root.dataset.motion = 'reduce'; SARA.reduceMotion = true; } else delete root.dataset.motion;
    if (pref.scale !== 100) { root.style.zoom = String(pref.scale / 100); root.dataset.uiscale = String(pref.scale); } else { root.style.removeProperty('zoom'); delete root.dataset.uiscale; }
    updateLum();
  }
  SARA.on('theme', function () { setTimeout(updateLum, 30); });
  SARA.on('pref', function (k) { if (k === 'theme') setTimeout(updateLum, 30); });
  SARA.a11y = {
    get: function () { return Object.assign({}, pref); },
    set: function (p) {
      if (!p || typeof p !== 'object') return;
      if (typeof p.scale === 'number' && isFinite(p.scale)) pref.scale = clamp(Math.round(p.scale / 5) * 5, 85, 150);
      ['contrast', 'motion', 'focus', 'sr'].forEach(function (k) { if (typeof p[k] === 'boolean') pref[k] = p[k]; });
      save(); applyPrefs(); paintCard();
    }
  };

  /* ====================================================================== live region + announcements */
  const live = el('div', 'sr-only'); live.id = 'a11yLive'; live.setAttribute('aria-live', 'polite'); live.setAttribute('aria-atomic', 'true'); live.setAttribute('role', 'status'); doc.body.appendChild(live);
  let lastMsg = '', lastAt = 0;
  function announce(msg) {
    if (!pref.sr || !msg) return; msg = String(msg).replace(/\s+/g, ' ').trim().slice(0, 320); const now = Date.now();
    if (msg === lastMsg && now - lastAt < 900) return; lastMsg = msg; lastAt = now;
    live.textContent = ''; setTimeout(function () { live.textContent = msg; }, 40);
  }
  SARA.a11y.announce = announce;
  const origToast = SARA.toast;
  if (typeof origToast === 'function') SARA.toast = function (icon, color, msg) { try { announce(msg); } catch (e) { /* ignore */ } return origToast.apply(this, arguments); };
  const STATUS = { listening: 'Sara is listening', thinking: 'Sara is thinking' }; let lastStatusAt = 0;
  SARA.on('status', function (s) {
    const orb = $('org'); if (orb) orb.setAttribute('aria-label', 'Sara \u2014 ' + (s || 'idle'));
    if (STATUS[s] && Date.now() - lastStatusAt > 1500) { lastStatusAt = Date.now(); announce(STATUS[s]); }
  });
  const log = $('chatLog'), told = new WeakSet();
  if (log && typeof MutationObserver === 'function') {
    const check = function (m) {
      if (!m.classList || !m.classList.contains('msg') || !m.classList.contains('sara') || told.has(m)) return;
      if (['thinking', 'preview', 'typing', 'streaming'].some(function (c) { return m.classList.contains(c); })) return;
      const t = (m.querySelector('.msg-text') || m).textContent; if (!t || !t.trim()) return; told.add(m); announce('Sara: ' + t);
    };
    new MutationObserver(function (muts) { muts.forEach(function (mu) { if (mu.type === 'attributes') check(mu.target); else mu.addedNodes.forEach(function (n) { if (n.nodeType === 1) check(n); }); }); })
      .observe(log, { childList: true, attributes: true, attributeFilter: ['class'], subtree: true });
  }

  /* ====================================================================== input modality + page changes */
  let kb = false;
  doc.addEventListener('keydown', function (e) { if (['Tab', 'Enter', ' ', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Alt'].indexOf(e.key) >= 0) kb = true; }, true);
  doc.addEventListener('pointerdown', function () { kb = false; }, true);
  const nav = $('navBar');
  const pageTitle = function (name) { const p = $('page-' + name), t = p && p.querySelector('.page-title'); return t ? t.textContent.trim() : (name.charAt(0).toUpperCase() + name.slice(1)); };
  SARA.on('page', function (name) {
    if (nav) nav.querySelectorAll('button[data-page]').forEach(function (b) { if (b.dataset.page === name) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current'); });
    announce(pageTitle(name) + ' page');
    if (kb) { const p = $('page-' + name), t = p && p.querySelector('.page-title'); if (t) { t.setAttribute('tabindex', '-1'); t.focus({ preventScroll: true }); } }
  });

  /* ====================================================================== landmarks + missing names */
  const app = doc.querySelector('.app'); if (app && !app.getAttribute('role')) app.setAttribute('role', 'main');
  if (nav) { if (!nav.getAttribute('aria-label')) nav.setAttribute('aria-label', 'Main navigation'); const cur = nav.querySelector('button.active'); if (cur) cur.setAttribute('aria-current', 'page'); }
  doc.querySelectorAll('section.page').forEach(function (p) { if (!p.getAttribute('aria-label')) p.setAttribute('aria-label', pageTitle(p.id.replace('page-', ''))); });
  const orb = $('org'); if (orb) { orb.setAttribute('role', 'img'); orb.setAttribute('aria-label', 'Sara \u2014 idle'); }
  const nameOf = function (n) {
    const lb = n.getAttribute('aria-labelledby'); if (lb) { const t = lb.split(' ').map(function (i) { const x = $(i); return x ? x.textContent : ''; }).join(' ').trim(); if (t) return t; }
    return (n.getAttribute('aria-label') || n.textContent || n.getAttribute('title') || '').trim();
  };
  const ctxText = function (n, sel) { const r = n.closest(sel); const t = r && r.querySelector('.r-text, .r-t, .a-name, .n-title, .s-name, .qc-name, .hp-name'); return t ? t.textContent.trim() : ''; };
  function labelNode(n) {
    if (n.nodeType !== 1) return;
    const all = [n].concat(Array.prototype.slice.call(n.querySelectorAll('[role="switch"],button,[role="button"],input,select,textarea,.dot')));
    all.forEach(function (x) {
      if (x.dataset && x.dataset.a11y) return;
      if (x.matches('[role="switch"]')) { if (!nameOf(x)) { const t = ctxText(x, '.set-row,.info-row,.qc-tile,.hp-row,.cs-row,.auto-card'); if (t) x.setAttribute('aria-label', t); } return; }
      if (x.matches('.row .dot')) {
        const row = x.closest('.row'), t = ctxText(x, '.row'); x.setAttribute('role', 'checkbox'); x.setAttribute('aria-checked', row && row.classList.contains('done') ? 'true' : 'false');
        x.setAttribute('aria-label', (row && row.classList.contains('done') ? 'Done: ' : 'Mark done: ') + t); if (!x.hasAttribute('tabindex')) x.tabIndex = 0; x.dataset.a11y = '1'; return;
      }
      if (x.matches('button,[role="button"]')) {
        const nm = nameOf(x); if (nm && !/^[^\p{L}\p{N}]{1,2}$/u.test(nm)) return;   // symbol-only text such as × is not a name
        let label = '';
        if (x.hasAttribute('data-del')) label = 'Delete ' + (ctxText(x, '.row,.auto-card,.note-row2') || 'item');
        else if (x.hasAttribute('data-run')) label = 'Run ' + (ctxText(x, '.auto-card') || 'automation');
        else if (x.hasAttribute('data-edit')) label = 'Edit ' + (ctxText(x, '.auto-card') || 'item');
        else if (x.hasAttribute('data-rm')) label = 'Remove step';
        if (label) { x.setAttribute('aria-label', label); x.dataset.a11y = '1'; }
        return;
      }
      if (x.matches('input,select,textarea') && x.type !== 'hidden' && !x.getAttribute('aria-label') && !x.id) { const p = x.getAttribute('placeholder') || x.getAttribute('title'); if (p) x.setAttribute('aria-label', p); }
      else if (x.matches('input,select,textarea') && !x.getAttribute('aria-label') && !doc.querySelector('label[for="' + x.id + '"]')) { const p = x.getAttribute('placeholder') || x.getAttribute('title'); if (p) x.setAttribute('aria-label', p); }
    });
  }
  labelNode(doc.body);
  if (typeof MutationObserver === 'function') {
    let pending = new Set(), t = 0;
    new MutationObserver(function (muts) {
      muts.forEach(function (mu) { mu.addedNodes.forEach(function (n) { if (n.nodeType === 1) pending.add(n); }); });
      if (pending.size && !t) t = setTimeout(function () { const list = Array.from(pending); pending = new Set(); t = 0; list.forEach(function (n) { if (n.isConnected) labelNode(n); }); }, 120);
    }).observe(doc.body, { childList: true, subtree: true });
    // keep reminder checkboxes in sync when a row flips to done
    doc.addEventListener('click', function (e) { if (e.target.closest && e.target.closest('.row .dot')) setTimeout(function () { doc.querySelectorAll('.row .dot[data-a11y]').forEach(function (d) { const r = d.closest('.row'), done = r && r.classList.contains('done'); d.setAttribute('aria-checked', done ? 'true' : 'false'); }); }, 900); }, true);
  }

  /* ====================================================================== dialogs: role, label, focus trap, focus return */
  const FOC = 'a[href],button:not([disabled]),input:not([disabled]):not([type="hidden"]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
  const visible = function (n) { return !!(n.offsetWidth || n.offsetHeight || n.getClientRects().length); };
  const focusables = function (c) { return Array.prototype.filter.call(c.querySelectorAll(FOC), visible); };
  const opener = new Map();
  doc.querySelectorAll('.overlay').forEach(function (ov) {
    const dlg = ov.querySelector('.sheet,.modal') || ov; dlg.setAttribute('role', 'dialog'); dlg.setAttribute('aria-modal', 'true');
    const h = dlg.querySelector('.page-title,.m-title,h1,h2,h3'); if (h && h.textContent.trim()) dlg.setAttribute('aria-label', h.textContent.trim()); else if (!dlg.getAttribute('aria-label')) dlg.setAttribute('aria-label', 'Dialog');
    if (typeof MutationObserver !== 'function') return;
    new MutationObserver(function () {
      if (ov.classList.contains('open')) { opener.set(ov, doc.activeElement); setTimeout(function () { if (!ov.classList.contains('open')) return; const f = focusables(dlg)[0]; if (f) f.focus({ preventScroll: true }); else { dlg.setAttribute('tabindex', '-1'); dlg.focus({ preventScroll: true }); } }, 80); }
      else { const o = opener.get(ov); opener.delete(ov); if (o && o.isConnected && o.focus) o.focus({ preventScroll: true }); }
    }).observe(ov, { attributes: true, attributeFilter: ['class'] });
  });
  doc.addEventListener('keydown', function (e) {
    if (e.key !== 'Tab') return; const ov = doc.querySelector('.overlay.open'); if (!ov) return;
    const dlg = ov.querySelector('.sheet,.modal') || ov, f = focusables(dlg); if (!f.length) return;
    const first = f[0], last = f[f.length - 1], act = doc.activeElement;
    if (!dlg.contains(act)) { e.preventDefault(); first.focus(); }
    else if (e.shiftKey && act === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && act === last) { e.preventDefault(); first.focus(); }
  }, true);

  /* ====================================================================== shortcuts */
  const PAGES = ['home', 'chat', 'notes', 'apps', 'automation', 'settings'];
  const typing = function (t) { return !!t && (/^(input|textarea|select)$/i.test(t.tagName) || t.isContentEditable); };
  function go(name) { const b = nav && nav.querySelector('button[data-page="' + name + '"]'); if (b) b.click(); }
  const SHORTCUTS = [
    ['Everywhere', [[['?'], 'Show this list'], [['Ctrl', 'K'], 'Command palette'], [['Alt', '1\u20136'], 'Home, Chat, Notes, Apps, Automation, Settings'], [['Alt', '/'], 'Jump to the chat box'], [['Esc'], 'Close a dialog or sheet'], [['Ctrl', 'Alt', '0'], 'Reset interface size']]],
    ['Chat', [[['Enter'], 'Send'], [['\u2191'], 'Bring back your last message (empty box)'], [['Ctrl', 'F'], 'Search this conversation'], [['Enter'], 'Next match (in search)  \u00b7  Shift+Enter previous']]],
    ['Notes & reminders', [[['Enter'], 'Add the note / reminder'], [['Ctrl', 'Z'], 'Undo delete or done (for 6 seconds)']]],
    ['Settings', [[['/'], 'Search settings']]]
  ];
  let sdlg = null;
  function openShortcuts() {
    if (sdlg) return; const prev = doc.activeElement;
    const back = el('div', 'ax-back'), box = el('div', 'ax-dialog'); box.setAttribute('role', 'dialog'); box.setAttribute('aria-modal', 'true'); box.setAttribute('aria-label', 'Keyboard shortcuts');
    const head = el('div', 'ax-head'); head.appendChild(el('div', 'ax-title', 'Keyboard shortcuts')); const x = el('button', 'ax-x', '\u00d7'); x.type = 'button'; x.setAttribute('aria-label', 'Close'); head.appendChild(x); box.appendChild(head);
    const body = el('div', 'ax-body');
    SHORTCUTS.forEach(function (g) {
      const sec = el('section', 'ax-sec'); sec.appendChild(el('h3', 'ax-h', g[0]));
      g[1].forEach(function (r) { const row = el('div', 'ax-row'), keys = el('span', 'ax-keys'); r[0].forEach(function (k, i) { if (i) keys.appendChild(doc.createTextNode(' ')); keys.appendChild(el('kbd', '', k)); }); row.appendChild(keys); row.appendChild(el('span', 'ax-desc', r[1])); sec.appendChild(row); });
      body.appendChild(sec);
    });
    box.appendChild(body); back.appendChild(box); doc.body.appendChild(back); sdlg = back; requestAnimationFrame(function () { back.classList.add('open'); });
    const close = function () { if (!sdlg) return; doc.removeEventListener('keydown', onKey, true); back.classList.remove('open'); sdlg = null; setTimeout(function () { back.remove(); }, 240); if (prev && prev.focus) prev.focus({ preventScroll: true }); };
    const onKey = function (e) {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }
      else if (e.key === 'Tab') { const f = focusables(box); if (!f.length) return; const first = f[0], last = f[f.length - 1]; if (e.shiftKey && doc.activeElement === first) { e.preventDefault(); last.focus(); } else if (!e.shiftKey && doc.activeElement === last) { e.preventDefault(); first.focus(); } }
    };
    doc.addEventListener('keydown', onKey, true); x.addEventListener('click', close); back.addEventListener('pointerdown', function (e) { if (e.target === back) close(); }); x.focus();
  }
  SARA.a11y.shortcuts = openShortcuts;
  doc.addEventListener('keydown', function (e) {
    if (e.defaultPrevented) return;
    if (e.ctrlKey && e.altKey && e.key === '0') { e.preventDefault(); SARA.a11y.set({ scale: 100 }); announce('Interface size reset'); return; }
    if (e.altKey && !e.ctrlKey && !e.metaKey) {
      if (/^[1-6]$/.test(e.key)) { e.preventDefault(); go(PAGES[+e.key - 1]); return; }
      if (e.key === '/') { e.preventDefault(); go('chat'); setTimeout(function () { const i = $('chatInputField'); if (i) i.focus(); }, 160); return; }
    }
    if (e.key === '?' && !e.ctrlKey && !e.altKey && !e.metaKey && !typing(e.target) && !typing(doc.activeElement)) { e.preventDefault(); openShortcuts(); }
  });

  /* ====================================================================== Settings > Accessibility card */
  const body = $('accessBody'); let paintCard = function () { /* card not present */ };
  if (body) {
    const row = function (name, sub, id) {
      const r = el('div', 'set-row'), info = el('div', 'set-info'); info.appendChild(el('div', 's-name', name)); info.appendChild(el('div', 's-sub', sub)); r.appendChild(info);
      const t = el('div', 'toggle'); t.id = id; t.setAttribute('role', 'switch'); t.tabIndex = 0; t.setAttribute('aria-label', name); r.appendChild(t); body.appendChild(r); return t;
    };
    body.appendChild(el('div', 'section-label', 'Interface size (beta)'));
    const sz = el('div', 'ax-size'), sl = el('input'); sl.type = 'range'; sl.min = '85'; sl.max = '150'; sl.step = '5'; sl.id = 'uiScale'; sl.setAttribute('aria-label', 'Interface size');
    const val = el('span', 'ax-sizeval'), rst = el('button', 'pill-btn small', 'Reset'); rst.type = 'button'; sz.appendChild(sl); sz.appendChild(val); sz.appendChild(rst); body.appendChild(sz);
    body.appendChild(el('div', 'acc-note', 'Makes everything bigger or smaller. After a change you get 10 seconds to confirm it, so a size that does not fit your screen reverts by itself. Ctrl+Alt+0 resets it.'));
    const confirm = el('div', 'ax-confirm'); confirm.hidden = true; const cmsg = el('span', '', ''), keep = el('button', 'pill-btn primary small', 'Keep'), rev = el('button', 'pill-btn small', 'Revert');
    keep.type = 'button'; rev.type = 'button'; confirm.setAttribute('role', 'alertdialog'); confirm.setAttribute('aria-label', 'Keep this size?'); [cmsg, keep, rev].forEach(function (n) { confirm.appendChild(n); }); doc.body.appendChild(confirm);
    let prevScale = pref.scale, ctimer = 0, cleft = 0;
    const endConfirm = function () { clearInterval(ctimer); confirm.hidden = true; };
    function startConfirm(from) {
      prevScale = from; cleft = 10; confirm.hidden = false; cmsg.textContent = 'Keep this size? Reverting in ' + cleft + 's'; clearInterval(ctimer);
      ctimer = setInterval(function () { cleft--; if (cleft <= 0) { endConfirm(); SARA.a11y.set({ scale: prevScale }); announce('Size reverted'); } else cmsg.textContent = 'Keep this size? Reverting in ' + cleft + 's'; }, 1000);
      keep.focus({ preventScroll: true });
    }
    keep.addEventListener('click', endConfirm); rev.addEventListener('click', function () { endConfirm(); SARA.a11y.set({ scale: prevScale }); });
    sl.addEventListener('input', function () { val.textContent = sl.value + '%'; });
    sl.addEventListener('change', function () { const from = pref.scale, to = parseInt(sl.value, 10); if (to === from) return; SARA.a11y.set({ scale: to }); if (to !== 100) startConfirm(from); else endConfirm(); });
    rst.addEventListener('click', function () { endConfirm(); SARA.a11y.set({ scale: 100 }); });
    body.appendChild(el('div', 'section-label', 'Display & input'));
    const tContrast = row('Higher contrast', 'stronger text, solid panels, clearer borders and focus', 'axContrast');
    const tMotion = row('Reduce motion', osReduce ? 'on \u2014 your system asks apps to reduce motion' : 'turns off animations (fully applies after a restart)', 'axMotion');
    const tFocus = row('Strong focus outline', 'a thick, high-visibility ring around whatever has keyboard focus', 'axFocus');
    const tSr = row('Screen reader announcements', 'speaks page changes, notifications and Sara\u2019s replies', 'axSr');
    SARA.bindToggle(tContrast, function (on) { SARA.a11y.set({ contrast: on }); }); SARA.bindToggle(tMotion, function (on) { if (osReduce) { SARA.setToggle(tMotion, true); return; } SARA.a11y.set({ motion: on }); });
    SARA.bindToggle(tFocus, function (on) { SARA.a11y.set({ focus: on }); }); SARA.bindToggle(tSr, function (on) { SARA.a11y.set({ sr: on }); if (on) announce('Announcements on'); });
    const kr = el('div', 'btn-row'), kb2 = el('button', 'pill-btn', 'Keyboard shortcuts (?)'); kb2.type = 'button'; kb2.addEventListener('click', openShortcuts); kr.appendChild(kb2); body.appendChild(kr);
    paintCard = function () { sl.value = String(pref.scale); val.textContent = pref.scale + '%'; SARA.setToggle(tContrast, pref.contrast); SARA.setToggle(tMotion, pref.motion || osReduce); SARA.setToggle(tFocus, pref.focus); SARA.setToggle(tSr, pref.sr); };
  }
  applyPrefs(); paintCard();
})();