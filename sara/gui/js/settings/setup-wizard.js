/* ==========================================================================
   setup-wizard.js -- first-run checklist (LLM backend, chat model, notes-search model, voice files) with
   one-click fixes.  Shown once on first launch; re-openable from Settings > System > "Re-run setup check".
   API calls: get_setup_wizard_seen, mark_setup_wizard_seen, check_setup_status, run_setup_fix.
   Push event: 'setup_progress' (action, state running|done|error, message) -> live log line + a progress bar
   (real % when the message contains one, e.g. an ollama pull; otherwise an indeterminate sweep).
   Exposes SARA.showSetupWizard.  Styles: style/overlays.css.  Markup: index.html #setupWizardOverlay.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA, $ = SARA.$, esc = SARA.escapeHtml;

  const CHECKS = [
    { key: 'llm', label: (s) => s.llm_backend === 'gemini' ? 'Gemini API key' : 'Ollama running',
      detail: (s) => s.llm_backend === 'gemini' ? (s.gemini_key_set ? 'Configured' : 'Set GEMINI_API_KEY in your .env') : (s.ollama_running ? 'Connected' : 'Not detected on this machine'),
      ok: (s) => s.llm_backend === 'gemini' ? s.gemini_key_set : s.ollama_running,
      fix: (s) => s.llm_backend === 'gemini' ? null : 'open_ollama_download', fixLabel: 'Get Ollama' },
    { key: 'model', label: (s) => 'Chat model (' + (s.llm_model_name || 'model') + ')',
      detail: (s) => s.llm_model_pulled ? 'Ready' : 'Needs to be downloaded once',
      ok: (s) => s.llm_model_pulled, show: (s) => s.llm_backend !== 'gemini', fix: () => 'pull_llm_model', fixLabel: 'Download' },
    { key: 'embed', label: (s) => 'Notes search model (' + (s.embedding_model_name || 'model') + ')',
      detail: (s) => s.embedding_model_pulled ? 'Ready' : (s.embedding_backend_error || 'Needed for "what do my notes say" questions'),
      ok: (s) => s.embedding_model_pulled, show: (s) => s.rag_enabled, fix: () => 'pull_embedding_model', fixLabel: 'Details' },
    { key: 'voice', label: () => 'Voice files',
      detail: (s) => (s.kokoro_model_present && s.kokoro_voices_present) ? 'Ready' : 'See BUILD.md to add the voice model files',
      ok: (s) => s.kokoro_model_present && s.kokoro_voices_present, fix: () => null }
  ];

  function setBar(state, message) {          // visual only
    const bar = $('setupBar'), fill = $('setupBarFill'), pct = $('swPct');
    if (state !== 'running') { bar.classList.remove('show', 'indeterminate', 'determinate'); fill.style.width = ''; if (pct) pct.textContent = ''; return; }
    bar.classList.add('show');
    const m = /(\d{1,3})\s*%/.exec(message || "");
    if (m) {
      const v = Math.min(100, +m[1]);
      bar.classList.remove('indeterminate'); bar.classList.add('determinate'); fill.style.width = v + '%';
      if (pct) pct.textContent = v + '%';
    }
    else if (!bar.classList.contains('determinate')) { bar.classList.add('indeterminate'); fill.style.width = ''; }
  }
  const FIX_KEY = { open_ollama_download: 'llm', pull_llm_model: 'model', pull_embedding_model: 'embed' };
  const ROW_BADGE = { checking: 'Checking…', ready: 'Ready', completed: 'Completed', attention: 'Needs attention', fixing: 'Fixing…', error: 'Error' };
  const rowState = {}, prevState = {}, running = {};
  let lastStatus = null, refreshTok = 0, revealNext = true, introTimer = 0, wakeTimer = 0, wizardFirst = true;
  const baseTitle = () => wizardFirst ? 'Initializing SARA' : 'Checking SARA';
  const isRunning = () => Object.keys(running).length > 0;
  function swapText(el, text) {
    if (!el || el._swT === text) return;
    el._swT = text; clearTimeout(el._sw);
    if (SARA.reduceMotion || !$('setupWizardOverlay').classList.contains('open')) { el.textContent = text; el.classList.remove('sw-swap'); return; }
    el.classList.add('sw-swap');
    el._sw = setTimeout(function () { el.textContent = text; el.classList.remove('sw-swap'); }, 140);
  }
  function setWizard(state, title, sub, summary) {
    const modal = $('setupModal'); if (!modal) return;
    if (modal.dataset.state !== state) modal.dataset.state = state;
    swapText($('swTitle'), title); swapText($('swSub'), sub);
    const sm = $('swSummary'); if (sm) sm.textContent = summary || '';
  }
  function renderChecking() {
    const list = $('setupChecklist');
    list.classList.remove('sw-reveal', 'rechecking');
    list.innerHTML = [0, 1, 2].map(function (i) {
      return '<div class="setup-check checking" style="--i:' + i + '"><div class="sc-icon"><span class="sc-glyph"></span></div><div class="sc-text"><span class="skel skel-line"></span></div><span class="sc-badge">' + ROW_BADGE.checking + '</span></div>';
    }).join('');
    setWizard('checking', baseTitle(), 'Checking each part of the setup…', '');
    const go = $('setupContinue'); go.disabled = true; go.textContent = 'Checking…';
  }
  function rowStateFor(c, ok) {
    const o = rowState[c.key];
    if (ok) return o && o.s === 'completed' ? 'completed' : 'ready';
    return o && (o.s === 'fixing' || o.s === 'error') ? o.s : 'attention';
  }
  function render(s) {
    lastStatus = s;
    const list = $('setupChecklist');
    const visible = CHECKS.filter((c) => !c.show || c.show(s));
    let readyN = 0, badN = 0, fixingN = 0;
    const rows = visible.map(function (c, i) {
      const ok = !!c.ok(s), st = rowStateFor(c, ok), fix = c.fix ? c.fix(s) : null, o = rowState[c.key];
      if (ok) readyN++; else badN++;
      if (st === 'fixing') fixingN++;
      const detail = st === 'fixing' ? 'In progress…' : (st === 'error' && o && o.msg ? o.msg : c.detail(s));
      const changed = !!prevState[c.key] && prevState[c.key] !== st;
      prevState[c.key] = st;
      const glyph = ok ? '✓' : (st === 'fixing' ? '' : '!');
      return '<div class="setup-check ' + (st === 'completed' ? 'ready completed' : st) + (changed ? ' sw-changed' : '') + '" style="--i:' + i + '">' +
        '<div class="sc-icon"><span class="sc-glyph">' + glyph + '</span></div>' +
        '<div class="sc-text"><b>' + esc(c.label(s)) + '</b><span>' + esc(detail) + '</span></div>' +
        '<span class="sc-badge">' + ROW_BADGE[st] + '</span>' +
        (!ok && fix ? '<button class="pill-btn small" data-fix="' + esc(fix) + '"' + (st === 'fixing' ? ' disabled' : '') + '>' + esc(st === 'fixing' ? 'Working…' : c.fixLabel) + '</button>' : '') + '</div>';
    }).join('');
    list.classList.remove('rechecking');
    list.classList.toggle('sw-reveal', revealNext && !SARA.reduceMotion); revealNext = false;
    list.innerHTML = rows;
    list.querySelectorAll('[data-fix]').forEach((b) => b.addEventListener('click', () => runFix(b.dataset.fix, b)));
    const tally = readyN + ' of ' + visible.length + ' checks ready';
    if (s.all_ready) setWizard('ready', 'SARA', badN ? 'Ready. Some optional items still need attention.' : 'Ready.', tally);
    else if (fixingN) setWizard('fixing', baseTitle(), 'Applying a fix…', tally);
    else setWizard('attention', baseTitle(), badN + (badN === 1 ? ' item needs' : ' items need') + ' attention.', tally);
    const go = $('setupContinue');
    go.disabled = !s.all_ready; go.textContent = s.all_ready ? 'Get started' : 'Fix the items above to continue';
  }
  function renderFailure(msg) {
    lastStatus = null;
    const list = $('setupChecklist');
    list.classList.remove('sw-reveal', 'rechecking');
    list.innerHTML = '<div class="setup-check error"><div class="sc-icon"><span class="sc-glyph">!</span></div><div class="sc-text"><b>Setup check</b><span>' + esc(msg) + '</span></div><span class="sc-badge">' + ROW_BADGE.error + '</span></div>';
    setWizard('attention', baseTitle(), 'The setup check could not finish. Try Re-check.', '');
    const go = $('setupContinue'); go.disabled = true; go.textContent = 'Fix the items above to continue';
  }
  async function refresh() {
    const tok = ++refreshTok;
    const list = $('setupChecklist');
    if (!lastStatus) renderChecking(); else list.classList.add('rechecking');
    if (!isRunning()) setBar('running', '');
    let s = null;
    try { s = await SARA.callApi('check_setup_status'); } catch (e) { s = { error: String(e) }; }
    if (tok !== refreshTok) return;
    if (!isRunning()) setBar('idle');
    if (s && !s.error) render(s); else renderFailure(s && s.error ? s.error : 'No response from the setup check.');
  }
  async function runFix(action, btn) {
    const key = FIX_KEY[action];
    if (key) rowState[key] = { s: 'fixing' };
    running[action] = 1;
    const log = $('setupLog'); log.classList.add('show'); log.textContent = ''; setBar('running', '');
    if (lastStatus) render(lastStatus);
    const res = await SARA.callApi('run_setup_fix', action);
    if (!res || !res.ok) {
      const msg = (res && res.error) || 'Could not start this fix.';
      delete running[action]; if (key) rowState[key] = { s: 'error', msg: msg };
      log.textContent = msg; setBar('idle'); refresh();
    } else if (!res.started) {
      delete running[action]; if (key) delete rowState[key];
      log.textContent = 'Opened in your browser. Come back and press Re-check when it is done.'; setBar('idle');
      if (lastStatus) render(lastStatus);
    }
  }
  SARA.on('ev:setup_progress', function (action, state, message) {
    const log = $('setupLog'); log.classList.add('show'); log.textContent = message || ''; log.scrollTop = log.scrollHeight;
    const key = FIX_KEY[action];
    if (state === 'running' && key && (!rowState[key] || rowState[key].s !== 'fixing')) {
      rowState[key] = { s: 'fixing' }; running[action] = 1;
      if (lastStatus) render(lastStatus);
    }
    setBar(state, message);
    if (state === 'done' || state === 'error') {
      delete running[action];
      if (key) rowState[key] = state === 'done' ? { s: 'completed' } : { s: 'error', msg: message || 'The fix did not finish.' };
      refresh();
    }
  });

  SARA.showSetupWizard = function (opts) {
    wizardFirst = !!(opts && opts.first);
    Object.keys(rowState).forEach(function (k) { if (rowState[k].s !== 'fixing') delete rowState[k]; });
    Object.keys(prevState).forEach(function (k) { delete prevState[k]; });
    lastStatus = null; revealNext = true;
    $('setupLog').classList.remove('show');
    const modal = $('setupModal');
    clearTimeout(introTimer); modal.classList.remove('sw-intro');
    if (!SARA.reduceMotion) {
      void modal.offsetWidth; modal.classList.add('sw-intro');
      introTimer = setTimeout(function () { modal.classList.remove('sw-intro'); }, 1400);
    }
    SARA.openOverlay('setupWizardOverlay'); refresh();
  };
  function wakeOrb() {
    if (SARA.reduceMotion || typeof SARA.setStatus !== 'function') return;
    if ((SARA.current || 'home') !== 'home' || SARA.state.status !== 'sleeping') return;
    SARA.setStatus('waking');
    clearTimeout(wakeTimer);
    wakeTimer = setTimeout(function () { if (SARA.state.status === 'waking') SARA.setStatus('sleeping'); }, 1500);
  }
  async function dismiss(wake) {
    await SARA.callApi('mark_setup_wizard_seen');
    SARA.closeOverlay('setupWizardOverlay');
    if (wake === true) wakeOrb();
  }
  $('setupRecheck').addEventListener('click', refresh);
  $('setupSkip').addEventListener('click', function () { dismiss(false); });
  $('setupContinue').addEventListener('click', function () { if (!$('setupContinue').disabled) dismiss(true); });

  let checked = false, checkedReal = false;
  SARA.onBoot(async function () {           // once per boot; again only if the real bridge shows up late
    const real = SARA.isConnected();
    if (checked && (checkedReal || !real)) return;
    checked = true; if (real) checkedReal = true;
    const seen = await SARA.callApi('get_setup_wizard_seen');
    if (seen && seen.seen) return;
    SARA.showSetupWizard({ first: true });
  });
})();
