/* ==========================================================================
   empty-plus.js -- one consistent look for EMPTY and ERROR states (frontend only; no existing JS is modified).
   * Empty lists (notes, reminders, automations, Today sheets) get a small line illustration, a title, the original hint text and a one-tap action.
   * If loading notes / reminders / automations FAILS (the bridge answers {ok:false}), the list shows an error card with "Try again" instead of a
     misleading "No notes yet". When the backend is not connected the card says so (preview mode) and the top strip gets a Retry link.
   Styles: style/empty-plus.css. Load AFTER js/notes-reminders.js, js/automation.js and js/today.js.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA; if (!SARA) return;
  const doc = document, $ = SARA.$;
  const el = function (tag, cls, text) { const n = doc.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
  const ART = {
    note: '<rect x="14" y="10" width="34" height="44" rx="5"/><path d="M22 22h18M22 30h18M22 38h10"/><path class="a" d="M44 44l8-8 4 4-8 8-5 1z"/>',
    bell: '<path d="M20 42V30a12 12 0 0 1 24 0v12l4 4H16z"/><path class="a" d="M28 52a4 4 0 0 0 8 0"/><path d="M32 14v-3"/>',
    flow: '<circle cx="14" cy="32" r="6"/><rect class="a" x="26" y="26" width="12" height="12" rx="3"/><circle cx="50" cy="32" r="6"/><path d="M20 32h6M38 32h6"/>',
    check: '<circle cx="32" cy="32" r="20"/><path class="a" d="M22 33l7 7 13-14"/>',
    cloud: '<path d="M20 44h26a10 10 0 0 0 1-20 14 14 0 0 0-26-3 11 11 0 0 0-1 23z"/><path class="a" d="M12 12l40 40"/>',
    alert: '<path d="M32 10l24 42H8z"/><path class="a" d="M32 28v12M32 46h.01"/>'
  };
  function art(kind) {
    const NS = 'http://www.w3.org/2000/svg', s = doc.createElementNS(NS, 'svg'); s.setAttribute('viewBox', '0 0 64 64'); s.setAttribute('class', 'es-svg'); s.setAttribute('aria-hidden', 'true');
    const g = doc.createElementNS(NS, 'g'); g.innerHTML = ART[kind] || ART.note; while (g.firstChild) s.appendChild(g.firstChild); return s;   // static markup only
  }
  function state(o) {
    const n = el('div', 'es' + (o.compact ? ' compact' : '') + (o.error ? ' error' : '')); n.dataset.es = '1'; n.setAttribute('role', o.error ? 'alert' : 'status');
    n.appendChild(art(o.art));
    const tx = el('div', 'es-tx'); tx.appendChild(el('div', 'es-title', o.title)); if (o.text) tx.appendChild(el('div', 'es-text', o.text));
    if (o.action) { const b = el('button', 'pill-btn small' + (o.error ? '' : ' primary'), o.action.label); b.type = 'button'; b.addEventListener('click', o.action.run); tx.appendChild(b); }
    n.appendChild(tx); return n;
  }
  const focus = function (id) { return function () { const i = $(id); if (i) { i.scrollIntoView && i.scrollIntoView({ block: 'center' }); i.focus(); } }; };

  /* ---------------------------------------------------------------- empty lists */
  const KINDS = [
    ['#notesList', { art: 'note', title: 'No notes yet', action: { label: 'Write a note', run: focus('newNoteText') } }],
    ['#reminderGroups', { art: 'bell', title: 'No reminders', action: { label: 'Add a reminder', run: focus('newReminderText') } }],
    ['#routinesList', { art: 'flow', title: 'No automations yet', action: { label: 'Create one', run: function () { const t = doc.querySelector('.rt-tpl-row'); if (t) t.scrollIntoView({ block: 'center', behavior: 'smooth' }); else { const b = $('newRoutineBtn'); if (b) b.click(); } } } }]
  ];
  function decorate(e) {
    if (e.dataset.es || e.closest('[data-es]')) return;
    const text = e.textContent.trim(); let o = null;
    for (let i = 0; i < KINDS.length; i++) if (e.closest(KINDS[i][0])) { o = Object.assign({}, KINDS[i][1]); break; }
    if (!o && e.closest('.sheet')) o = { art: 'check', title: text, compact: true };
    if (!o) return;
    if (!o.compact) o.text = text;
    e.replaceWith(state(o));
  }
  function scan(n) { if (n.nodeType !== 1) return; if (n.classList.contains('empty')) decorate(n); n.querySelectorAll && n.querySelectorAll('.empty').forEach(decorate); }
  scan(doc.body);
  new MutationObserver(function (muts) { muts.forEach(function (m) { m.addedNodes.forEach(scan); }); }).observe(doc.body, { childList: true, subtree: true });

  /* ---------------------------------------------------------------- failed loads -> error card */
  const WATCH = { get_reminders: ['#reminderGroups', 'your reminders'], get_notes: ['#notesList', 'your notes'], list_routines: ['#routinesList', 'your automations'] };
  const origCall = SARA.callApi;
  SARA.callApi = function (name) {
    const p = origCall.apply(this, arguments), w = WATCH[name];
    if (w && p && p.then) p.then(function (r) { if (!r || r.ok === false) setTimeout(function () { showError(w[0], w[1]); }, 0); }, function () { setTimeout(function () { showError(w[0], w[1]); }, 0); });
    return p;
  };
  function showError(sel, what) {
    const c = doc.querySelector(sel); if (!c) return;
    const off = !SARA.isConnected();
    c.textContent = '';
    c.appendChild(state({ error: true, art: off ? 'cloud' : 'alert', title: off ? 'Can\u2019t reach Sara right now' : 'Couldn\u2019t load ' + what,
      text: off ? 'The app is in preview mode \u2014 the Sara backend isn\u2019t connected, so ' + what + ' can\u2019t be shown.' : 'Something went wrong while loading. Your data is not lost.',
      action: { label: 'Try again', run: function () { SARA.refreshConnection(); SARA.emit('visible'); } } }));
  }

  /* ---------------------------------------------------------------- offline strip: Retry */
  const strip = $('offlineStrip');
  if (strip) {
    const r = el('button', 'link-btn es-retry', 'Retry'); r.type = 'button'; r.addEventListener('click', function () { SARA.usingMock = false; SARA.refreshConnection(); SARA.emit('visible'); });
    strip.appendChild(doc.createTextNode(' ')); strip.appendChild(r);
  }
})();
