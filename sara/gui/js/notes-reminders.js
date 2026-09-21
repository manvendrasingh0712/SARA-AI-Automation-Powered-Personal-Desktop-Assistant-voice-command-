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
      html += '<div class="section-label group-label ' + g[0] + '">' + g[1] + '</div>' + items.map(function (r) {
        const showDate = g[0] === 'upcoming' || g[0] === 'overdue' || g[0] === 'done';
        return '<div class="row' + (r.done ? ' done' : '') + '"><span class="dot" data-id="' + r.id + '" title="Mark done"><svg viewBox="0 0 12 12"><path d="M2.5 6.5l2.5 2.5 4.5-5.5"/></svg></span>' +
          '<span class="r-time">' + SARA.escapeHtml(SARA.fmt12h(r.time)) + '</span>' +
          '<span class="r-text"><span class="r-t">' + SARA.escapeHtml(r.text) + '</span>' + (showDate ? ' <small style="color:var(--muted)">· ' + SARA.escapeHtml(shortDate(r.date)) + '</small>' : '') + '</span>' +
          '<button class="r-del" data-del="' + r.id + '" title="Delete">×</button></div>';
      }).join('');
    });
    $('reminderGroups').innerHTML = html || '<div class="empty">No reminders yet. Add one below, or say “remind me to…”.</div>';
  }
  async function loadReminders() {
    const res = await SARA.callApi('get_reminders');
    const list = (res && res.data) || [];
    renderReminders(list); SARA.emit('reminders', list);
  }
  $('reminderGroups').addEventListener('click', async function (e) {
    const dot = e.target.closest('.dot'), del = e.target.closest('[data-del]');
    if (dot) {
      const row = dot.closest('.row');
      if (row.classList.contains('completing')) return;                  // ignore double-clicks mid-animation
      const completing = !row.classList.contains('done');
      if (completing) { row.classList.add('completing'); SARA.sound.done(); } else SARA.sound.tap();
      const hold = (completing && !SARA.reduceMotion) ? sleep(520) : Promise.resolve();   // let the check-draw finish before the list re-renders
      await SARA.callApi('toggle_reminder', parseInt(dot.dataset.id, 10));
      await hold; loadReminders();
    }
    else if (del) { await SARA.callApi('delete_reminder', parseInt(del.dataset.del, 10)); loadReminders(); }
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
