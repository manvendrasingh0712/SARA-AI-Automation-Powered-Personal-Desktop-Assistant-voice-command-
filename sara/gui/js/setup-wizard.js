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
    const bar = $('setupBar'), fill = $('setupBarFill');
    if (state !== 'running') { bar.classList.remove('show', 'indeterminate', 'determinate'); fill.style.width = ''; return; }
    bar.classList.add('show');
    const m = /(\d{1,3})\s*%/.exec(message || "");
    if (m) { bar.classList.remove('indeterminate'); bar.classList.add('determinate'); fill.style.width = Math.min(100, +m[1]) + '%'; }
    else if (!bar.classList.contains('determinate')) { bar.classList.add('indeterminate'); fill.style.width = ''; }
  }
  function render(s) {
    const list = $('setupChecklist');
    list.innerHTML = (s.all_ready ? '<div class="setup-allgood">Everything looks good.</div>' : '') +
      CHECKS.filter((c) => !c.show || c.show(s)).map(function (c) {
        const ok = !!c.ok(s), fix = c.fix ? c.fix(s) : null;
        return '<div class="setup-check ' + (ok ? 'ready' : 'error') + '"><div class="sc-icon">' + (ok ? '✓' : '!') + '</div>' +
          '<div class="sc-text"><b>' + esc(c.label(s)) + '</b><span>' + esc(c.detail(s)) + '</span></div>' +
          (!ok && fix ? '<button class="pill-btn small" data-fix="' + esc(fix) + '">' + esc(c.fixLabel) + '</button>' : '') + '</div>';
      }).join('');
    list.querySelectorAll('[data-fix]').forEach((b) => b.addEventListener('click', () => runFix(b.dataset.fix, b)));
    const go = $('setupContinue');
    go.disabled = !s.all_ready; go.textContent = s.all_ready ? 'Get started' : 'Fix the items above to continue';
  }
  async function refresh() {
    const s = await SARA.callApi('check_setup_status');
    if (s && !s.error) render(s);
  }
  async function runFix(action, btn) {
    btn.disabled = true; btn.textContent = 'Working…';
    const log = $('setupLog'); log.classList.add('show'); log.textContent = ''; setBar('running', '');
    const res = await SARA.callApi('run_setup_fix', action);
    if (res && res.ok === false && res.error) { log.textContent = res.error; setBar('idle'); refresh(); }
  }
  SARA.on('ev:setup_progress', function (action, state, message) {
    const log = $('setupLog'); log.classList.add('show'); log.textContent = message || ''; log.scrollTop = log.scrollHeight;
    setBar(state, message);
    if (state === 'done' || state === 'error') refresh();
  });

  SARA.showSetupWizard = function () { $('setupLog').classList.remove('show'); SARA.openOverlay('setupWizardOverlay'); refresh(); };
  async function dismiss() { await SARA.callApi('mark_setup_wizard_seen'); SARA.closeOverlay('setupWizardOverlay'); }
  $('setupRecheck').addEventListener('click', refresh);
  $('setupSkip').addEventListener('click', dismiss);
  $('setupContinue').addEventListener('click', function () { if (!$('setupContinue').disabled) dismiss(); });

  let checked = false, checkedReal = false;
  SARA.onBoot(async function () {           // once per boot; again only if the real bridge shows up late
    const real = SARA.isConnected();
    if (checked && (checkedReal || !real)) return;
    checked = true; if (real) checkedReal = true;
    const seen = await SARA.callApi('get_setup_wizard_seen');
    if (seen && seen.seen) return;
    SARA.showSetupWizard();
  });
})();
