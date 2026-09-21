/* ==========================================================================
   routine-builder.js -- the "New / Edit automation" modal (name, label, run-at time, ordered steps).
   API call: save_routine(name, label, steps, trigger_time). The backend validates every step and returns
   {ok:false, error} which is shown inside the modal.  Step shapes (unchanged from the old GUI):
     {type:'simple_action', key} | {type:'intent'|'skill', name, args}
   Exposes SARA.openRoutineBuilder(existingRoutineOrNull); emits 'routines:changed' after a save.
   Data: js/routine-actions.js.  Opened by: js/automation.js.  Styles: style/automation.css.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA, $ = SARA.$, esc = SARA.escapeHtml;
  const A = SARA.routineActions;
  let editingName = null, steps = [];

  function optgroups(selectedKey) {
    return A.groups.map((g) => '<optgroup label="' + esc(g.label) + '">' + g.keys.map((k) =>
      '<option value="' + esc(k) + '"' + (k === selectedKey ? ' selected' : '') + '>' + esc(A.labels[k] || k) + '</option>').join('') + '</optgroup>').join('');
  }
  function rowHtml(i, s) {
    const adv = s.type === 'intent' || s.type === 'skill';
    const argsVal = s._argsRaw != null ? s._argsRaw : (s.args ? JSON.stringify(s.args) : '');
    return '<div class="rt-step" data-idx="' + i + '"><div class="rt-line">' +
      '<select class="rt-mode"><option value="simple_action"' + (adv ? '' : ' selected') + '>Action</option><option value="advanced"' + (adv ? ' selected' : '') + '>Advanced…</option></select>' +
      (adv
        ? '<select class="rt-adv-type"><option value="intent"' + (s.type === 'intent' ? ' selected' : '') + '>intent</option><option value="skill"' + (s.type === 'skill' ? ' selected' : '') + '>skill</option></select>' +
          '<input type="text" class="rt-adv-name" placeholder="e.g. weather" value="' + esc(s.name || '') + '">'
        : '<select class="rt-action">' + optgroups(s.key) + '</select>') +
      '<button class="rt-x" data-rm title="Remove step">✕</button></div>' +
      (adv ? '<div class="rt-line"><input type="text" class="rt-adv-args" placeholder=\'Optional args as JSON, e.g. {"location":"Ajmer,IN"}\' value="' + esc(argsVal) + '"></div>' : '') + '</div>';
  }
  function render() {
    $('rtSteps').innerHTML = steps.length ? steps.map((s, i) => rowHtml(i, s)).join('') : '<div class="rt-empty">No steps yet — add at least one.</div>';
  }
  const idxOf = (el) => parseInt(el.closest('.rt-step').dataset.idx, 10);
  const defaultAction = () => ({ type: 'simple_action', key: A.groups[0].keys[0] });

  $('rtSteps').addEventListener('change', function (e) {
    const t = e.target, i = t.closest('.rt-step') ? idxOf(t) : -1; if (i < 0) return;
    if (t.classList.contains('rt-mode')) { steps[i] = t.value === 'advanced' ? { type: 'intent', name: '' } : defaultAction(); render(); }
    else if (t.classList.contains('rt-action')) steps[i] = { type: 'simple_action', key: t.value };
    else if (t.classList.contains('rt-adv-type')) steps[i].type = t.value;
  });
  $('rtSteps').addEventListener('input', function (e) {
    const t = e.target; if (!t.closest('.rt-step')) return; const i = idxOf(t);
    if (t.classList.contains('rt-adv-name')) steps[i].name = t.value;
    else if (t.classList.contains('rt-adv-args')) steps[i]._argsRaw = t.value;
  });
  $('rtSteps').addEventListener('click', function (e) {
    if (e.target.closest('[data-rm]')) { steps.splice(idxOf(e.target), 1); render(); }
  });
  $('rtAddStep').addEventListener('click', () => { steps.push(defaultAction()); render(); });
  $('rtCancel').addEventListener('click', () => SARA.closeOverlay('routineModal'));

  SARA.openRoutineBuilder = function (existing) {
    $('rtError').style.display = 'none';
    editingName = existing ? existing.name : null;
    $('rtTitle').textContent = existing ? 'Edit automation' : 'New automation';
    $('rtName').value = existing ? existing.name : ''; $('rtName').disabled = !!existing;
    $('rtLabel').value = existing ? (existing.label || '') : '';
    $('rtTime').value = existing ? (existing.trigger_time || '') : '';
    steps = existing ? (existing.steps || []).map((s) => Object.assign({}, s)) : [];
    render(); SARA.openOverlay('routineModal');
  };

  function showError(msg) { const el = $('rtError'); el.textContent = msg; el.style.display = 'block'; }
  $('rtSave').addEventListener('click', async function () {
    $('rtError').style.display = 'none';
    const name = $('rtName').value.trim(), label = $('rtLabel').value.trim(), time = $('rtTime').value || null;
    if (!name) return showError('Routine name is required.');
    if (!steps.length) return showError('Add at least one step.');
    const out = [];
    for (let i = 0; i < steps.length; i++) {
      const s = steps[i];
      if (s.type === 'simple_action') { out.push({ type: 'simple_action', key: s.key }); continue; }
      if (!s.name || !s.name.trim()) return showError('Step ' + (i + 1) + ': name is required.');
      let args = null;
      if (s._argsRaw && s._argsRaw.trim()) {
        try { args = JSON.parse(s._argsRaw); } catch (err) { return showError('Step ' + (i + 1) + ': args must be valid JSON.'); }
      } else if (s.args && s._argsRaw == null) { args = s.args; }
      out.push({ type: s.type, name: s.name.trim(), args: args });
    }
    const res = await SARA.callApi('save_routine', name, label, out, time);
    if (res && res.ok) { SARA.closeOverlay('routineModal'); SARA.sound.saved(); SARA.ok('Automation saved'); SARA.emit('routines:changed'); }
    else showError((res && res.error) || 'Could not save automation.');
  });
})();
