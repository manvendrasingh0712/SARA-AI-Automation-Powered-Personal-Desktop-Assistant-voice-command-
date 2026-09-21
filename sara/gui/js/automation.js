/* ==========================================================================
   automation.js -- Automation page: Modes (one-tap setting bundles) + Routines (saved step chains).
   API calls: get_modes_status, apply_mode | list_routines, get_routine, run_routine_now, delete_routine
   (save_routine lives in js/routine-builder.js).  Run replies stream into Chat via 'transcript' pushes.
   Styles: style/automation.css.  Markup: index.html #page-automation.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA, $ = SARA.$, esc = SARA.escapeHtml, A = SARA.routineActions;
  const cap = (s) => String(s).charAt(0).toUpperCase() + String(s).slice(1);

  /* ---- modes ---- */
  function renderModes(active, modes) {
    $('modeChips').innerHTML = (modes || []).map((m) =>
      '<span class="chip' + (m === active ? ' on' : '') + '" data-mode="' + esc(m) + '">' + esc(cap(m)) + '</span>').join('') || '<div class="empty" style="padding:4px;">Modes unavailable.</div>';
  }
  async function loadModes() {
    const res = await SARA.callApi('get_modes_status');
    renderModes(res && res.active_mode, (res && res.modes) || []);
  }
  $('modeChips').addEventListener('click', async function (e) {
    const chip = e.target.closest('[data-mode]'); if (!chip) return;
    SARA.sound.tap();
    const res = await SARA.callApi('apply_mode', chip.dataset.mode);
    if (res && res.ok) { SARA.ok(res.message || 'Mode applied'); loadModes(); }
    else SARA.fail((res && res.error) || 'Could not apply that mode.');
  });

  /* ---- routines ---- */
  const stepName = (s) => !s ? '?' : s.type === 'simple_action' ? (A.labels[s.key] || s.key) : s.type + ': ' + s.name;
  function renderRoutines(list) {
    $('routinesList').innerHTML = list.length ? list.map(function (r) {
      const n = (r.steps || []).length;
      const sub = n + ' step' + (n === 1 ? '' : 's') + ' · ' + (r.trigger_time ? 'runs at ' + SARA.fmt12h(r.trigger_time) : 'manual trigger');
      return '<div class="auto-card" data-name="' + esc(r.name) + '"><div class="auto-info"><div class="a-name">' + esc(r.label || r.name) +
        '</div><div class="a-sub" title="' + esc((r.steps || []).map(stepName).join(' → ')) + '">' + esc(sub) + '</div></div>' +
        '<div class="auto-actions"><button class="pill-btn small" data-run>Run</button><button class="pill-btn small" data-edit>Edit</button>' +
        '<button class="icon-x" data-del title="Delete">×</button></div></div>';
    }).join('') : '<div class="empty">No automations yet. Create one to chain several actions together.</div>';
  }
  async function loadRoutines() {
    const res = await SARA.callApi('list_routines');
    renderRoutines((res && res.data) || []);
  }
  $('routinesList').addEventListener('click', async function (e) {
    const card = e.target.closest('.auto-card'); if (!card) return;
    const name = card.dataset.name;
    if (e.target.closest('[data-run]')) {
      const btn = e.target.closest('[data-run]'); btn.disabled = true;
      const res = await SARA.callApi('run_routine_now', name);
      if (res && res.ok) { SARA.sound.run(); SARA.ok('Running — watch the Chat page'); } else SARA.fail((res && res.error) || 'Could not run routine');
      setTimeout(() => { btn.disabled = false; }, 1200);
    } else if (e.target.closest('[data-edit]')) {
      const res = await SARA.callApi('get_routine', name);
      if (res && res.ok && res.data) SARA.openRoutineBuilder(res.data); else SARA.fail((res && res.error) || 'Could not load routine');
    } else if (e.target.closest('[data-del]')) {
      if (!confirm('Delete automation "' + name + '"?')) return;
      const res = await SARA.callApi('delete_routine', name);
      if (res && res.ok) { SARA.ok('Automation deleted'); loadRoutines(); } else SARA.fail((res && res.error) || 'Could not delete');
    }
  });
  $('newRoutineBtn').addEventListener('click', () => SARA.openRoutineBuilder(null));
  SARA.on('routines:changed', loadRoutines);

  SARA.onBoot(function () { loadModes(); loadRoutines(); });
  SARA.on('page', function (p) { if (p === 'automation') { loadModes(); loadRoutines(); } });
})();
