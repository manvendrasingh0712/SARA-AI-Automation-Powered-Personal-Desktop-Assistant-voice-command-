/* ==========================================================================
   security.js -- Settings > Security card (protection mode, 7-day counters, recent events).
   Backend: sara/gui/app/security_api.py (get_security_summary, set_security_mode).
   Markup: index.html #grp-security.  Styles: style/security.css.  Dynamic text via textContent only.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA;
  SARA.security = SARA.security || {};
  const ARM_MS = 6000;
  const CHIP = {
    blocked: 'sec-danger', injection_detected: 'sec-danger', arg_rejected: 'sec-danger',
    confirm_asked: 'sec-warning', confirmed: 'sec-success'
  };
  const KIND_LABEL = {
    injection_detected: 'injection', blocked: 'blocked', confirm_asked: 'confirm asked',
    confirmed: 'confirmed', denied_by_user: 'denied by you', arg_rejected: 'bad argument', redacted: 'redacted'
  };
  let busy = false, armed = false, armTimer = 0, mode = 'standard';

  const isNum = (v) => typeof v === 'number' && isFinite(v);
  const numText = (v) => (isNum(v) ? String(v) : '-');
  function node(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function setNote(text) {
    const note = SARA.$('secNote');
    if (!note) return;
    note.hidden = !text;
    note.textContent = text || '';
  }
  function whenText(ts) {
    const ms = typeof ts === 'number' ? (ts < 1e12 ? ts * 1000 : ts) : Date.parse(ts);
    if (!isFinite(ms)) return '';
    return new Date(ms).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  }

  function arm(on) {
    armed = on;
    clearTimeout(armTimer);
    if (on) armTimer = setTimeout(function () { arm(false); }, ARM_MS);
    const warn = SARA.$('secWarn');
    if (warn) warn.hidden = !on;
    document.querySelectorAll('#secModes .sec-mode').forEach(function (b) {
      b.classList.toggle('sec-armed', on && b.dataset.mode === 'off');
    });
  }

  function paintModes() {
    document.querySelectorAll('#secModes .sec-mode').forEach(function (b) {
      b.setAttribute('aria-pressed', b.dataset.mode === mode ? 'true' : 'false');
    });
  }

  function renderEvents(list) {
    const box = SARA.$('secRecent');
    box.textContent = '';
    if (!list.length) { box.appendChild(node('div', 'recent-line sec-empty', 'No security events yet')); return; }
    list.slice(0, 10).forEach(function (e) {
      const line = node('div', 'recent-line sec-event');
      line.appendChild(node('span', 'sec-chip ' + (CHIP[e.kind] || 'sec-neutral'), KIND_LABEL[e.kind] || String(e.kind || '-')));
      line.appendChild(node('span', 'sec-tool', String(e.tool || e.source || '-')));
      const meta = [e.tool && e.source ? String(e.source) : '', isNum(e.tier) ? 'T' + e.tier : '', whenText(e.ts)]
        .filter(Boolean).join(' \u00b7 ');
      line.appendChild(node('span', 'sec-meta', meta));
      box.appendChild(line);
    });
  }

  function render(data) {
    const counts = data.counts || {}, byKind = counts.by_kind || {};
    if (typeof data.mode === 'string') mode = data.mode;
    paintModes();
    SARA.$('secBlocked').textContent = numText(counts.blocked);
    SARA.$('secInjections').textContent = numText(byKind.injection_detected || 0);
    SARA.$('secConfirms').textContent = numText(byKind.confirm_asked || 0);
    setNote('');
    renderEvents(Array.isArray(data.last_events) ? data.last_events : []);
  }

  function renderEmpty() {
    ['secBlocked', 'secInjections', 'secConfirms'].forEach(function (id) { SARA.$(id).textContent = '-'; });
    SARA.$('secRecent').textContent = '';
    setNote('Security data is not available.');
  }

  async function refresh() {
    if (busy || !SARA.$('secBlock')) return;
    busy = true;
    try {
      const s = await SARA.callApi('get_security_summary');
      if (!s || !s.ok || !s.data) { renderEmpty(); return; }
      render(s.data);
    } catch (e) {
      renderEmpty();
    } finally {
      busy = false;
    }
  }
  SARA.security.refresh = refresh;

  function choose(next) {
    if (next === 'off' && mode !== 'off' && !armed) { arm(true); return; }
    arm(false);
    if (next === mode) return;
    SARA.callApi('set_security_mode', next).then(function (r) {
      if (r && r.ok) mode = next;
      refresh();
    }).catch(function () { refresh(); });
  }

  document.querySelectorAll('#secModes .sec-mode').forEach(function (b) {
    b.addEventListener('click', function () { choose(b.dataset.mode); });
  });
  SARA.on('page', function (name) { if (name === 'settings') refresh(); else arm(false); });
  SARA.every(10000, refresh, { pages: ['settings'] });
})();