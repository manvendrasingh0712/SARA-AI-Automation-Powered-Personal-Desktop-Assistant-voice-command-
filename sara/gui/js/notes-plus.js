/* ==========================================================================
   notes-plus.js -- Notes & Reminders upgrades layered on js/notes-reminders.js (that file is NOT modified). Frontend only.
   1 Smart reminders  type "kal 5 baje dentist" / "friday 3pm standup" / "in 30 minutes tea": the date + time pickers fill themselves, a preview
                      chip shows what was understood (with AM/PM swap and x to ignore), and Add saves the CLEAN text. Quick chips set common times.
   2 Undo             deleting a reminder or ticking it done shows an Undo bar for 6 s (Ctrl+Z works too). Undo re-adds / re-opens it.
   3 Notes            #tags in a note become clickable chips + a tag filter, a search box, and Pin (pinned notes float to the top and can be dragged
                      to reorder). Copy button per note. Pins are stored on this device. Reminders stay sorted by time, so there is nothing to reorder.
   Needs js/nlparse.js loaded before this file. Styles: style/notes-plus.css.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA, NL = window.SaraNL; if (!SARA || !NL) return;
  const doc = document, $ = SARA.$;
  const rtext = $('newReminderText'), rdate = $('newReminderDate'), rtime = $('newReminderTime'), radd = $('addReminderBtn'), nlist = $('notesList');
  if (!rtext || !rdate || !rtime || !radd || !nlist) return;
  const el = function (tag, cls, text) { const n = doc.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
  const NS = 'http://www.w3.org/2000/svg';
  const ICON = {
    spark: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>', x: '<path d="M6 6l12 12M18 6L6 18"/>',
    pin: '<path d="M9 4h6l-1 6 3 3v1.5H7V13l3-3z"/><path d="M12 14.5V21"/>', copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>',
    grip: '<circle cx="9" cy="6" r="1.2"/><circle cx="15" cy="6" r="1.2"/><circle cx="9" cy="12" r="1.2"/><circle cx="15" cy="12" r="1.2"/><circle cx="9" cy="18" r="1.2"/><circle cx="15" cy="18" r="1.2"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>', undo: '<path d="M9 7L4 12l5 5"/><path d="M4 12h10a6 6 0 0 1 0 12"/>', swap: '<path d="M7 4L3 8l4 4M3 8h14M17 20l4-4-4-4M21 16H7"/>'
  };
  function svg(name) { const s = doc.createElementNS(NS, 'svg'); s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('aria-hidden', 'true'); const g = doc.createElementNS(NS, 'g'); g.innerHTML = ICON[name]; while (g.firstChild) s.appendChild(g.firstChild); return s; }   // static icon paths only
  function btn(cls, label, icon) { const b = el('button', cls); b.type = 'button'; b.title = label; b.setAttribute('aria-label', label); b.appendChild(svg(icon)); return b; }
  const mem = {};
  const store = {
    get: function (k) { try { const v = localStorage.getItem(k); if (v !== null) return v; } catch (e) { /* blocked */ } return (k in mem) ? mem[k] : null; },
    set: function (k, v) { mem[k] = v; try { localStorage.setItem(k, v); } catch (e) { /* blocked */ } }
  };
  const pad = function (n) { return String(n).padStart(2, '0'); };
  const ymd = function (d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); };
  const hm = function (d) { return pad(d.getHours()) + ':' + pad(d.getMinutes()); };

  /* ------------------------------------------------------------------ caches + undo (wrap callApi) */
  let remCache = [], noteCache = []; const origCall = SARA.callApi;
  const bar = el('div', 'undo-bar'); bar.hidden = true; bar.setAttribute('role', 'status'); bar.setAttribute('aria-live', 'polite');
  const bmsg = el('span', 'undo-msg'), bbtn = btn('undo-btn', 'Undo', 'undo'); bbtn.appendChild(el('span', '', 'Undo')); const bprog = el('i', 'undo-prog');
  bar.appendChild(bmsg); bar.appendChild(bbtn); bar.appendChild(bprog); doc.body.appendChild(bar);
  let utimer = 0, ufn = null;
  function hideUndo() { clearTimeout(utimer); ufn = null; bar.classList.remove('show'); setTimeout(function () { if (!bar.classList.contains('show')) bar.hidden = true; }, 260); }
  function showUndo(msg, fn) {
    clearTimeout(utimer); ufn = fn; bmsg.textContent = msg; bar.hidden = false;
    bprog.style.animation = 'none'; void bprog.offsetWidth; bprog.style.animation = '';
    requestAnimationFrame(function () { bar.classList.add('show'); }); utimer = setTimeout(hideUndo, 6000);
  }
  async function runUndo() { const f = ufn; hideUndo(); if (f) { try { await f(); } catch (e) { SARA.fail('Could not undo that.'); } } }
  bbtn.addEventListener('click', runUndo);
  doc.addEventListener('keydown', function (e) {
    if (!(e.ctrlKey || e.metaKey) || (e.key !== 'z' && e.key !== 'Z') || !ufn) return;
    const t = e.target; if (t && /^(input|textarea|select)$/i.test(t.tagName)) return;
    e.preventDefault(); runUndo();
  });
  async function restore(r) {
    const res = await origCall.call(SARA, 'add_reminder', r.date, r.time, r.text);
    if (res && res.ok && r.done && res.id) await origCall.call(SARA, 'toggle_reminder', res.id);
    if (res && res.ok) { SARA.ok('Reminder restored'); SARA.emit('visible'); } else SARA.fail('Could not restore that reminder.');
  }
  SARA.callApi = function (name) {
    const args = arguments, p = origCall.apply(this, args); if (!p || !p.then) return p;
    if (name === 'get_reminders') p.then(function (r) { if (r && r.data) remCache = r.data; }, function () { /* ignore */ });
    else if (name === 'get_notes') p.then(function (r) { if (r && r.data) noteCache = r.data; }, function () { /* ignore */ });
    else if (name === 'delete_reminder') {
      const item = remCache.find(function (r) { return r.id === args[1]; });
      p.then(function (res) { if (res && res.ok && item) showUndo('Reminder deleted', function () { return restore(item); }); }, function () { /* ignore */ });
    } else if (name === 'toggle_reminder') {
      const id = args[1], item = remCache.find(function (r) { return r.id === id; }), completing = item && !item.done;
      p.then(function (res) { if (res && res.ok && completing) showUndo('Marked done', function () { return origCall.call(SARA, 'toggle_reminder', id).then(function () { SARA.emit('visible'); }); }); }, function () { /* ignore */ });
    }
    return p;
  };

  /* ------------------------------------------------------------------ smart reminder input */
  const smart = el('div', 'rm-smart'); smart.hidden = true;
  const sIc = el('span', 'rm-smart-ic'); sIc.appendChild(svg('spark'));
  const sMain = el('div', 'rm-smart-main'), sWhen = el('div', 'rm-smart-when'), sTxt = el('div', 'rm-smart-txt'); sMain.appendChild(sWhen); sMain.appendChild(sTxt);
  const sSwap = el('button', 'rm-smart-btn'); sSwap.type = 'button'; sSwap.title = 'Swap AM / PM'; sSwap.appendChild(svg('swap')); sSwap.appendChild(el('span', '', 'AM/PM'));
  const sX = btn('rm-smart-btn rm-smart-x', 'Ignore (use the pickers)', 'x');
  [sIc, sMain, sSwap, sX].forEach(function (n) { smart.appendChild(n); });
  const quick = el('div', 'rm-quick'); quick.setAttribute('role', 'group'); quick.setAttribute('aria-label', 'Quick times');
  const form = rtext.closest('.rem-add') || rtext.parentNode; form.parentNode.insertBefore(quick, form); form.parentNode.insertBefore(smart, form);
  const QUICK = [
    ['In 10 min', function () { return new Date(Date.now() + 600000); }], ['In 1 hour', function () { return new Date(Date.now() + 3600000); }],
    ['Tonight 8 PM', function () { const d = new Date(); d.setHours(20, 0, 0, 0); if (d <= new Date()) d.setDate(d.getDate() + 1); return d; }],
    ['Tomorrow 9 AM', function () { const d = new Date(); d.setDate(d.getDate() + 1); d.setHours(9, 0, 0, 0); return d; }]
  ];
  QUICK.forEach(function (q) {
    const b = el('button', 'chip', q[0]); b.type = 'button';
    b.addEventListener('click', function () { const d = q[1](); rdate.value = ymd(d); rtime.value = hm(d); manual = true; [rdate, rtime].forEach(function (i) { i.classList.remove('rm-flash'); void i.offsetWidth; i.classList.add('rm-flash'); }); rtext.focus(); });
    quick.appendChild(b);
  });
  let cur = null, ignored = false, manual = false, lastText = '';
  function showSmart() {
    if (!cur) { smart.classList.remove('show'); setTimeout(function () { if (!cur) smart.hidden = true; }, 200); return; }
    smart.hidden = false; requestAnimationFrame(function () { smart.classList.add('show'); });
    sWhen.textContent = cur.display; sTxt.textContent = cur.text; sSwap.hidden = !cur.assumed || manual;
  }
  function refresh() {
    const v = rtext.value;
    if (v !== lastText) { ignored = false; manual = false; lastText = v; }
    if (!v.trim() || ignored) { cur = null; showSmart(); return; }
    cur = NL.parseWhen(v, new Date()); if (cur && !manual) { rdate.value = cur.date; rtime.value = cur.time; }
    showSmart();
  }
  rtext.addEventListener('input', refresh);
  [rdate, rtime].forEach(function (i) {
    i.addEventListener('change', function () { manual = true; if (cur && rdate.value && rtime.value) { cur.date = rdate.value; cur.time = rtime.value; cur.display = NL.label(cur.date, cur.time, new Date()); cur.assumed = false; showSmart(); } });
  });
  sSwap.addEventListener('click', function () {
    if (!cur) return; let h = parseInt(cur.time.slice(0, 2), 10); h = h < 12 ? h + 12 : h - 12; cur.time = pad(h) + ':' + cur.time.slice(3);
    cur.display = NL.label(cur.date, cur.time, new Date()); cur.assumed = false; rtime.value = cur.time; manual = true; showSmart();
  });
  sX.addEventListener('click', function () { ignored = true; cur = null; showSmart(); rtext.focus(); });
  function applyParse() { if (cur && !ignored) { rtext.value = cur.text; rdate.value = cur.date; rtime.value = cur.time; } setTimeout(function () { lastText = ''; refresh(); }, 400); }
  form.addEventListener('keydown', function (e) { if (e.key === 'Enter' && e.target === rtext) applyParse(); }, true);   // capture: runs BEFORE notes-reminders.js reads the field
  form.addEventListener('click', function (e) { if (e.target.closest && e.target.closest('#addReminderBtn')) applyParse(); }, true);
  rtext.placeholder = 'Add a reminder\u2026  try \u201ckal 5 baje dentist\u201d or \u201cfriday 3pm standup\u201d';

  /* ------------------------------------------------------------------ notes: tags, search, pin, reorder */
  const PINKEY = 'sara_note_pins';
  let pins = []; try { pins = JSON.parse(store.get(PINKEY) || '[]') || []; } catch (e) { pins = []; }
  const savePins = function () { store.set(PINKEY, JSON.stringify(pins)); };
  const keyOf = function (n) { return String(n.id != null ? n.id : (n.timestamp || '') + '|' + String(n.text || '').slice(0, 40)); };
  function tagsOf(text) { const out = [], re = /(^|\s)#([\p{L}\p{N}_-]{2,24})/gu; let m; while ((m = re.exec(text))) { const t = m[2].toLowerCase(); if (out.indexOf(t) < 0) out.push(t); } return out; }
  const tools = el('div', 'nt-tools'); tools.hidden = true;
  const sbox = el('div', 'nt-search'); sbox.appendChild(svg('search')); const sin = el('input'); sin.type = 'search'; sin.placeholder = 'Search notes or #tags'; sin.autocomplete = 'off'; sin.setAttribute('aria-label', 'Search notes'); sbox.appendChild(sin);
  const tagbar = el('div', 'nt-tags'), count = el('div', 'nt-count'); tools.appendChild(sbox); tools.appendChild(tagbar); tools.appendChild(count);
  nlist.parentNode.insertBefore(tools, nlist);
  const nin = $('newNoteText'); if (nin) nin.placeholder = 'Write a note\u2026 (use #tags to organise)';
  let q = '', tag = '', busy = false;
  const rowsOf = function () { return Array.prototype.slice.call(nlist.querySelectorAll('.note-row2')); };
  function matches(r) {
    const t = r._text.toLowerCase();
    if (tag && r._tags.indexOf(tag) < 0) return false;
    if (!q) return true;
    if (q.charAt(0) === '#') return r._tags.some(function (x) { return x.indexOf(q.slice(1)) === 0; });
    return t.indexOf(q) >= 0;
  }
  function paintTags() {
    const counts = {}; noteCache.forEach(function (n) { tagsOf(n.text || '').forEach(function (t) { counts[t] = (counts[t] || 0) + 1; }); });
    const names = Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a] || a.localeCompare(b); }).slice(0, 12);
    tagbar.textContent = '';
    names.forEach(function (t) { const b = el('button', 'chip' + (tag === t ? ' on' : ''), '#' + t + ' ' + counts[t]); b.type = 'button'; b.dataset.tag = t; tagbar.appendChild(b); });
    tagbar.hidden = !names.length;
  }
  function view(animate) {
    const rows = rowsOf(); tools.hidden = !rows.length; if (!rows.length) return;
    const pinned = pins.map(function (k) { return rows.find(function (r) { return r._key === k; }); }).filter(Boolean);
    const order = pinned.concat(rows.filter(function (r) { return pinned.indexOf(r) < 0; }));
    let shown = 0;
    order.forEach(function (r) { r.classList.toggle('pinned', pinned.indexOf(r) >= 0); const ok = matches(r); r.classList.toggle('n-hide', !ok); if (ok) shown++; });
    if (order.some(function (r, i) { return r !== rows[i]; })) {
      const before = new Map(rows.map(function (r) { return [r, r.getBoundingClientRect().top]; }));
      order.forEach(function (r) { nlist.appendChild(r); });
      if (animate && !SARA.reduceMotion) order.forEach(function (r) { const dy = before.get(r) - r.getBoundingClientRect().top; if (Math.abs(dy) > 1 && r.animate && !r.classList.contains('n-hide')) r.animate([{ transform: 'translateY(' + dy + 'px)' }, { transform: 'none' }], { duration: 340, easing: 'cubic-bezier(.16,1,.3,1)' }); });
    }
    count.textContent = (q || tag) ? (shown ? shown + ' of ' + rows.length + ' notes' : 'No notes match') : rows.length + ' note' + (rows.length === 1 ? '' : 's') + (pinned.length ? ' \u00b7 ' + pinned.length + ' pinned' : '');
    paintTags();
  }
  function decorate() {
    if (busy) return; busy = true;
    try {
      rowsOf().forEach(function (row, i) {
        if (row.dataset.npl) return; row.dataset.npl = '1';
        const n = noteCache[i] || { text: (row.querySelector('.n-title') || {}).textContent || '' };
        row._note = n; row._key = keyOf(n); row._text = String(n.text || ''); row._tags = tagsOf(row._text);
        const head = row.querySelector('.n-head'), acts = el('span', 'n-acts');
        const grip = el('span', 'n-grip'); grip.title = 'Drag to reorder pinned notes'; grip.appendChild(svg('grip'));
        const pb = btn('n-act n-pin', 'Pin note', 'pin'), cb = btn('n-act n-copy', 'Copy note', 'copy');
        acts.appendChild(grip); acts.appendChild(pb); acts.appendChild(cb); if (head) head.appendChild(acts); else row.appendChild(acts);
        if (row._tags.length) { const tg = el('div', 'n-tags'); row._tags.forEach(function (t) { const b = el('button', 'n-tag', '#' + t); b.type = 'button'; b.dataset.tag = t; tg.appendChild(b); }); row.appendChild(tg); }
      });
      view(false);
    } finally { busy = false; }
  }
  new MutationObserver(function () { if (!busy) decorate(); }).observe(nlist, { childList: true });
  sin.addEventListener('input', function () { q = sin.value.trim().toLowerCase(); view(false); });
  tagbar.addEventListener('click', function (e) { const b = e.target.closest && e.target.closest('[data-tag]'); if (!b) return; tag = (tag === b.dataset.tag) ? '' : b.dataset.tag; view(false); });
  nlist.addEventListener('click', function (e) {
    const row = e.target.closest && e.target.closest('.note-row2'); if (!row) return;
    const t = e.target.closest('.n-tag'); if (t) { tag = (tag === t.dataset.tag) ? '' : t.dataset.tag; view(false); return; }
    const pin = e.target.closest('.n-pin');
    if (pin) { const i = pins.indexOf(row._key); if (i >= 0) pins.splice(i, 1); else pins.push(row._key); savePins(); SARA.sound.tap(); view(true); return; }
    const cp = e.target.closest('.n-copy');
    if (cp) {
      const done = function (ok) { if (ok) SARA.ok('Note copied'); else SARA.fail('Could not copy.'); };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(row._text).then(function () { done(true); }, function () { done(false); });
      else { const a = el('textarea'); a.value = row._text; a.style.cssText = 'position:fixed;left:-9999px;opacity:0'; doc.body.appendChild(a); a.select(); let ok = false; try { ok = doc.execCommand('copy'); } catch (err) { ok = false; } a.remove(); done(ok); }
    }
  });
  let dragKey = null;
  nlist.addEventListener('pointerdown', function (e) { const g = e.target.closest && e.target.closest('.n-grip'); if (g) g.closest('.note-row2').draggable = true; });
  window.addEventListener('pointerup', function () { if (!dragKey) rowsOf().forEach(function (r) { r.draggable = false; }); });
  nlist.addEventListener('dragstart', function (e) { const row = e.target.closest && e.target.closest('.note-row2'); if (!row || !row.classList.contains('pinned')) return; dragKey = row._key; row.classList.add('dragging'); try { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', dragKey); } catch (err) { /* ignore */ } });
  const clearMarks = function () { rowsOf().forEach(function (r) { r.classList.remove('drop-before', 'drop-after'); }); };
  nlist.addEventListener('dragover', function (e) {
    if (!dragKey) return; const row = e.target.closest && e.target.closest('.note-row2'); if (!row || !row.classList.contains('pinned')) return;
    e.preventDefault(); clearMarks(); const r = row.getBoundingClientRect(), after = e.clientY > r.top + r.height / 2; row._after = after; row.classList.add(after ? 'drop-after' : 'drop-before');
  });
  nlist.addEventListener('drop', function (e) {
    const row = e.target.closest && e.target.closest('.note-row2'); if (!dragKey || !row || !row.classList.contains('pinned')) return; e.preventDefault();
    const from = pins.indexOf(dragKey); let to = pins.indexOf(row._key) + (row._after ? 1 : 0); if (from < 0 || to < 0) return; if (from < to) to--;
    pins.splice(to, 0, pins.splice(from, 1)[0]); savePins(); dragKey = null; clearMarks(); view(true);
  });
  nlist.addEventListener('dragend', function () { dragKey = null; clearMarks(); rowsOf().forEach(function (r) { r.draggable = false; r.classList.remove('dragging'); }); });
  decorate(); refresh();
})();
