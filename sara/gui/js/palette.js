/* ==========================================================================
   palette.js -- Ctrl+K (or Cmd+K) command palette. Jump to a page, switch theme / mode, run a routine,
   toggle focus / mute, or type anything else and send it to Sara as a command.
   API calls (all existing): get_modes_status, apply_mode, list_routines, run_routine_now, stop_sara,
   plus SARA.sendCommand / SARA.wake / SARA.setFocus / SARA.setMute.
   Styles: style/palette.css.  Markup is built here (no index.html markup needed).
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA;
  const PAGES = [['home', 'Home'], ['chat', 'Chat'], ['notes', 'Notes & Reminders'], ['apps', 'Apps'], ['automation', 'Automation'], ['settings', 'Settings']];

  const overlay = document.createElement('div'); overlay.className = 'pal-overlay'; overlay.setAttribute('aria-hidden', 'true');
  overlay.innerHTML = '<div class="pal" role="dialog" aria-label="Command palette"><input class="pal-input" type="text" placeholder="Type a command, page, or ask Sara…" autocomplete="off" spellcheck="false"><div class="pal-list" role="listbox"></div><div class="pal-foot"><span>↑↓ move</span><span>Enter run</span><span>Esc close</span></div></div>';
  document.body.appendChild(overlay);
  const input = overlay.querySelector('.pal-input'), list = overlay.querySelector('.pal-list');
  let items = [], shown = [], sel = 0, dynamic = { modes: [], routines: [] };

  function score(q, text) {                       // 0 = no match; higher = better (prefix > substring > in-order letters)
    q = q.toLowerCase(); text = text.toLowerCase(); if (!q) return 1;
    if (text.startsWith(q)) return 100 - text.length * 0.1;
    const at = text.indexOf(q); if (at >= 0) return 60 - at;
    let i = 0; for (const ch of text) { if (ch === q[i]) i++; if (i === q.length) return 20; }
    return 0;
  }
  function build() {
    const out = [];
    PAGES.forEach((p) => out.push({ g: 'Go to', label: p[1], run: () => SARA.gotoPage(p[0]) }));
    out.push({ g: 'Sara', label: 'Wake Sara', run: () => SARA.wake && SARA.wake() });
    out.push({ g: 'Sara', label: 'Stop Sara', run: () => SARA.callApi('stop_sara') });
    out.push({ g: 'Sara', label: (SARA.state.focus ? 'Turn off' : 'Turn on') + ' focus mode', run: () => SARA.setFocus(!SARA.state.focus, true) });
    out.push({ g: 'Sara', label: (SARA.state.muted ? 'Unmute' : 'Mute') + ' Sara', run: () => SARA.setMute(!SARA.state.muted, true) });
    if (SARA.theme) SARA.theme.list.forEach((t) => out.push({ g: 'Theme', label: 'Theme: ' + t.name, hint: SARA.theme.current() === t.id ? 'active' : '', run: () => SARA.theme.apply(t.id) }));
    dynamic.modes.forEach((m) => out.push({ g: 'Mode', label: 'Mode: ' + m, run: async function () { const r = await SARA.callApi('apply_mode', m); if (r && r.ok) SARA.ok(r.message || 'Mode applied'); else SARA.fail('Could not apply that mode.'); } }));
    dynamic.routines.forEach((r) => out.push({ g: 'Routine', label: 'Run: ' + (r.label || r.name), run: async function () { const x = await SARA.callApi('run_routine_now', r.name); if (!x || !x.ok) SARA.fail('Routine could not start.'); } }));
    return out;
  }
  function render() {
    const q = input.value.trim();
    shown = items.map((it) => ({ it: it, s: score(q, it.label) })).filter((x) => x.s > 0).sort((a, b) => b.s - a.s).map((x) => x.it).slice(0, 40);
    if (q) shown.push({ g: 'Ask Sara', label: 'Ask Sara: ' + q, run: function () { SARA.gotoPage('chat'); SARA.sendCommand(q); } });
    sel = Math.min(sel, Math.max(0, shown.length - 1));
    if (!shown.length) { list.innerHTML = '<div class="pal-empty">Nothing matches.</div>'; return; }
    let html = '', lastG = '';
    shown.forEach(function (it, i) {
      if (it.g !== lastG) { html += '<div class="pal-group">' + SARA.escapeHtml(it.g) + '</div>'; lastG = it.g; }
      html += '<div class="pal-item' + (i === sel ? ' sel' : '') + '" role="option" data-i="' + i + '"><span>' + SARA.escapeHtml(it.label) + '</span>' + (it.hint ? '<small>' + SARA.escapeHtml(it.hint) + '</small>' : '') + '</div>';
    });
    list.innerHTML = html;
    const cur = list.querySelector('.sel'); if (cur && cur.scrollIntoView) cur.scrollIntoView({ block: 'nearest' });
  }
  async function loadDynamic() {
    const m = await SARA.callApi('get_modes_status'); dynamic.modes = (m && m.modes) || [];
    const r = await SARA.callApi('list_routines'); dynamic.routines = (r && r.data) || [];
    if (overlay.classList.contains('open')) { items = build(); render(); }
  }
  function open() {
    items = build(); input.value = ''; sel = 0; render();
    overlay.classList.add('open'); overlay.setAttribute('aria-hidden', 'false');
    setTimeout(() => input.focus(), 30); loadDynamic();
  }
  function close() { overlay.classList.remove('open'); overlay.setAttribute('aria-hidden', 'true'); input.blur(); }
  function runSel() { const it = shown[sel]; if (!it) return; close(); SARA.sound.tap(); try { it.run(); } catch (e) { console.error('[palette]', e); } }

  document.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) { e.preventDefault(); overlay.classList.contains('open') ? close() : open(); return; }
    if (!overlay.classList.contains('open')) return;
    if (e.key === 'Escape') { e.preventDefault(); close(); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(shown.length - 1, sel + 1); render(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(0, sel - 1); render(); }
    else if (e.key === 'Enter') { e.preventDefault(); runSel(); }
  });
  input.addEventListener('input', function () { sel = 0; render(); });
  overlay.addEventListener('mousedown', function (e) { if (e.target === overlay) close(); });
  list.addEventListener('mousemove', function (e) { const el = e.target.closest('.pal-item'); if (el && +el.dataset.i !== sel) { sel = +el.dataset.i; render(); } });
  list.addEventListener('click', function (e) { if (e.target.closest('.pal-item')) runSel(); });
  SARA.openPalette = open;
})();
