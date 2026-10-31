/* ==========================================================================
   notes-reminders.js -- the combined Notes + Reminders page (two tabs).
   API calls: get_notes, save_note, get_notes_status | get_reminders, add_reminder, toggle_reminder, delete_reminder.
   Publishes SARA.emit('reminders', list) so js/today.js can show today's reminders in the schedule.
   Polish: completing a reminder plays a checkmark-draw + strikethrough (class .completing) before the list refreshes;
   added/completed sounds.  Styles: style/notes-reminders.css (+ tabs/rows in components.css).  Markup: index.html #page-notes.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA, $ = SARA.$;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  /* ---- tabs ---- */
  document.querySelectorAll('.tab').forEach(function (tab) {
    tab.addEventListener('click', function () {
      SARA.sound.tap();
      document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t === tab));
      document.querySelectorAll('.pane').forEach((p) => p.classList.toggle('active', p.id === 'pane-' + tab.dataset.tab));
    });
  });

  /* ---- notes ---- */
  function noteWhen(ts) {
    if (!ts) return '';
    const d = new Date(ts);
    return isNaN(d.getTime()) ? String(ts) : SARA.relTime(d.toISOString());
  }
  async function loadNotes() {
    const res = await SARA.callApi('get_notes');
    const list = (res && res.data) || [];
    $('notesList').innerHTML = list.length ? list.map(function (n) {
      const lines = String(n.text || '').split('\n');
      const title = lines[0].slice(0, 80);
      const snip = lines.slice(1).join('\n').trim() || (lines[0].length > 80 ? lines[0].slice(80) : '');
      return '<div class="note-row2"><div class="n-head"><span class="n-title">' + SARA.escapeHtml(title) + '</span><span class="n-time">' +
        SARA.escapeHtml(noteWhen(n.timestamp)) + '</span></div>' + (snip ? '<div class="n-snip">' + SARA.escapeHtml(snip) + '</div>' : '') + '</div>';
    }).join('') : '<div class="empty">No notes yet. Write one above, or say “take a note…”.</div>';
  }
  async function loadNotesStatus() {
    const el = $('notesIndexStatus');
    const s = await SARA.callApi('get_notes_status');
    if (!s || !s.ok || !s.enabled) { el.textContent = "Notes search isn't enabled right now."; return; }
    if (!s.count) { el.textContent = 'No notes indexed yet.'; return; }
    el.textContent = s.count + ' note' + (s.count === 1 ? '' : 's') + ' indexed · last synced: ' + (s.last_synced ? SARA.relTime(s.last_synced) : 'never');
  }
  async function saveNote() {
    const input = $('newNoteText'), text = input.value.trim(); if (!text) return;
    const res = await SARA.callApi('save_note', text);
    if (res && res.ok) { input.value = ''; SARA.ok('Note saved'); loadNotes(); loadNotesStatus(); }
    else SARA.fail('Could not save note');
  }
  $('saveNoteBtn').addEventListener('click', saveNote);
  $('syncNotesBtn').addEventListener('click', async function () {
    const b = this; b.disabled = true;
    const res = await SARA.callApi('sync_notes_now');
    b.disabled = false;
    if (res && res.ok) { SARA.ok('Notes sync started'); loadNotesStatus(); }
    else SARA.fail((res && res.message) || "Couldn't sync notes.");
  });
  $('newNoteText').addEventListener('keydown', (e) => { if (e.key === 'Enter') saveNote(); });

  /* ---- reminders ---- */
  const GROUPS = [['overdue', 'Overdue'], ['today', 'Today'], ['tomorrow', 'Tomorrow'], ['upcoming', 'Upcoming'], ['done', 'Done']];
  function groupOf(r, today, tomorrow) {
    if (r.done) return 'done';
    if (r.date < today) return 'overdue';
    if (r.date === today) return 'today';
    return r.date === tomorrow ? 'tomorrow' : 'upcoming';
  }
  function shortDate(s) {
    const d = new Date(s + 'T00:00:00');
    return isNaN(d.getTime()) ? s : d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  }
  function renderReminders(list) {
    const today = SARA.todayStr(0), tomorrow = SARA.todayStr(1), buckets = {};
    list.forEach((r) => (buckets[groupOf(r, today, tomorrow)] = buckets[groupOf(r, today, tomorrow)] || []).push(r));
    let html = '';
    GROUPS.forEach(function (g) {
      const items = (buckets[g[0]] || []).sort((a, b) => (a.date + (a.time || '')).localeCompare(b.date + (b.time || '')));
      if (!items.length) return;
      html += '<div class="section-label group-label ' + g[0] + '" data-key="g:' + g[0] + '">' + g[1] + '</div>' + items.map(function (r) {
        const showDate = g[0] === 'upcoming' || g[0] === 'overdue' || g[0] === 'done';
        return '<div class="row' + (r.done ? ' done' : '') + '" data-key="r:' + r.id + '"><span class="dot" data-id="' + r.id + '" title="Mark done"><svg viewBox="0 0 12 12"><path d="M2.5 6.5l2.5 2.5 4.5-5.5"/></svg></span>' +
          '<span class="r-time">' + SARA.escapeHtml(SARA.fmt12h(r.time)) + '</span>' +
          '<span class="r-text"><span class="r-t">' + SARA.escapeHtml(r.text) + '</span>' + (showDate ? ' <small style="color:var(--muted)">· ' + SARA.escapeHtml(shortDate(r.date)) + '</small>' : '') + '</span>' +
          '<button class="r-del" data-del="' + r.id + '" title="Delete">×</button></div>';
      }).join('');
    });
    const out = html || '<div class="empty">No reminders yet. Add one below, or say “remind me to…”.</div>';
    const apply = function () { $('reminderGroups').innerHTML = out; };
    if (SARA.motion && SARA.motion.flip) SARA.motion.flip($('reminderGroups'), apply); else apply();   // rows glide to their new place (js/motion.js)
  }
  /* A row animation (check-draw + collapse) owns the list until it ends: a refresh that lands mid-animation
     (60s poll, 'visible', a page revisit) is parked and applied right after, so the row can never vanish half-way. */
  let rowBusy = 0, refreshQueued = false;
  async function loadReminders() {
    if (rowBusy) { refreshQueued = true; return; }
    const res = await SARA.callApi('get_reminders');
    if (rowBusy) { refreshQueued = true; return; }           // an animation started while the call was in flight
    const list = (res && res.data) || [];
    renderReminders(list); SARA.emit('reminders', list);
  }
  function endRowBusy() { if (--rowBusy <= 0) { rowBusy = 0; refreshQueued = false; loadReminders(); } }
  $('reminderGroups').addEventListener('click', async function (e) {
    const dot = e.target.closest('.dot'), del = e.target.closest('[data-del]');
    if (dot) {
      const row = dot.closest('.row');
      if (row.classList.contains('completing')) return;                  // ignore double-clicks mid-animation
      const completing = !row.classList.contains('done');
      if (completing) { row.classList.add('completing'); SARA.sound.done(); } else SARA.sound.tap();
      rowBusy++;
      try {
        const call = SARA.callApi('toggle_reminder', parseInt(dot.dataset.id, 10));
        if (completing && !SARA.reduceMotion) { await sleep(520); await SARA.motion.collapse(row); }   // check-draw + strike, then dim + collapse
        await call;
      } finally { endRowBusy(); }
    }
    else if (del) {
      const row = del.closest('.row');
      if (row && row.classList.contains('removing')) return;
      rowBusy++;
      try {
        if (row) row.classList.add('removing');
        const call = SARA.callApi('delete_reminder', parseInt(del.dataset.del, 10));
        if (row) await SARA.motion.collapse(row);
        await call;
      } finally { endRowBusy(); }
    }
  });
  function resetReminderForm() {
    $('newReminderText').value = '';
    $('newReminderDate').value = SARA.todayStr(0);
    const h = (new Date().getHours() + 1) % 24;
    $('newReminderTime').value = String(h).padStart(2, '0') + ':00';
  }
  async function addReminder() {
    const text = $('newReminderText').value.trim(), date = $('newReminderDate').value, time = $('newReminderTime').value;
    if (!text) { $('newReminderText').focus(); return; }
    if (!date || !time) { SARA.fail('Pick a date and time first.'); return; }
    const res = await SARA.callApi('add_reminder', date, time, text);
    if (res && res.ok) { SARA.sound.added(); SARA.ok('Reminder saved'); resetReminderForm(); loadReminders(); }
    else SARA.fail('Could not save reminder');
  }
  $('addReminderBtn').addEventListener('click', addReminder);
  $('newReminderText').addEventListener('keydown', (e) => { if (e.key === 'Enter') addReminder(); });

  resetReminderForm();
  SARA.onBoot(function () { loadReminders(); loadNotes(); loadNotesStatus(); });
  SARA.on('visible', function () { loadReminders(); loadNotes(); loadNotesStatus(); });
  SARA.every(60 * 1000, function () { loadReminders(); loadNotes(); loadNotesStatus(); });
})();
;

/* ######################################################################
   Merged from notes-plus.js (formerly a separate file layered on this one)
   ###################################################################### */
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
    trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/>',
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
  let remCache = [], noteCache = [];
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
    const res = await SARA.api.raw('add_reminder', r.date, r.time, r.text);
    if (res && res.ok && r.done && res.id) await SARA.api.raw('toggle_reminder', res.id);
    if (res && res.ok) { SARA.ok('Reminder restored'); SARA.emit('visible'); } else SARA.fail('Could not restore that reminder.');
  }
  /* snapshot BEFORE the call: the backend (or mock) may change the very object we hold */
  function snapshot(name) {
    return function (args) {
      const found = remCache.find(function (r) { return r.id === args[1]; });
      return found ? { item: Object.assign({}, found), completing: name === 'toggle_reminder' && !found.done } : { item: null, completing: false };
    };
  }
  SARA.api.before('delete_reminder', snapshot('delete_reminder'));
  SARA.api.before('toggle_reminder', snapshot('toggle_reminder'));
  SARA.api.after('get_reminders', function (r) { if (r && r.data) remCache = r.data; });
  SARA.api.after('get_notes', function (r) { if (r && r.data) noteCache = r.data; });
  SARA.api.after('delete_reminder', function (res, args, snap) {
    if (res && res.ok && snap && snap.item) showUndo('Reminder deleted', function () { return restore(snap.item); });
  });
  SARA.api.after('toggle_reminder', function (res, args, snap) {
    const id = args[1];
    if (res && res.ok && snap && snap.completing) showUndo('Marked done', function () { return SARA.api.raw('toggle_reminder', id).then(function () { SARA.emit('visible'); }); });
  });

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
  function applyParse() {
    if (cur && !ignored) { rtext.value = cur.text; rdate.value = cur.date; rtime.value = cur.time; }
    let n = 0; const t = setInterval(function () { refresh(); if (!rtext.value || ++n > 20) clearInterval(t); }, 150);   // the form is cleared after the save completes
  }
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
        const pb = btn('n-act n-pin', 'Pin note', 'pin'), cb = btn('n-act n-copy', 'Copy note', 'copy'), db = btn('n-act n-del', 'Delete note', 'trash');
        acts.appendChild(grip); acts.appendChild(pb); acts.appendChild(cb); acts.appendChild(db); if (head) head.appendChild(acts); else row.appendChild(acts);
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
    const del = e.target.closest('.n-del');
    if (del) { deleteNote(row); return; }
    const cp = e.target.closest('.n-copy');
    if (cp) {
      const done = function (ok) { if (ok) SARA.ok('Note copied'); else SARA.fail('Could not copy.'); };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(row._text).then(function () { done(true); }, function () { done(false); });
      else { const a = el('textarea'); a.value = row._text; a.style.cssText = 'position:fixed;left:-9999px;opacity:0'; doc.body.appendChild(a); a.select(); let ok = false; try { ok = doc.execCommand('copy'); } catch (err) { ok = false; } a.remove(); done(ok); }
    }
  });
  /* Delete one note (backend: delete_note). The id shifts after a delete, so the text goes along as a guard;
     Undo re-saves the same text (it comes back as the newest note). */
  async function deleteNote(row) {
    const n = row._note || {}; if (n.id === undefined || n.id === null) { SARA.fail('Could not delete that note.'); return; }
    const res = await SARA.callApi('delete_note', n.id, row._text);
    if (!res || !res.ok) { SARA.fail((res && res.message) || 'Could not delete that note.'); SARA.emit('visible'); return; }
    SARA.sound.tap(); const pi = pins.indexOf(row._key); if (pi >= 0) { pins.splice(pi, 1); savePins(); }
    row.remove(); SARA.emit('visible');
    showUndo('Note deleted', async function () {
      const r = await SARA.api.raw('save_note', res.text || row._text);
      if (r && r.ok) { SARA.ok('Note restored'); SARA.emit('visible'); } else SARA.fail('Could not restore that note.');
    });
  }
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