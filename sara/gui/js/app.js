/* ══════════════════════════════════════════════════════════════════
   SARA AI — frontend logic
   Talks to sara/gui/app.py's `Api` class through window.pywebview.api.*
   Falls back to an in-memory mock if opened outside pywebview (e.g. a
   plain browser preview) so the UI is still fully explorable.
   ══════════════════════════════════════════════════════════════════ */

const TOAST_ICON_COLOR = {
  'ti-alert-triangle': { emoji: '⚠️', color: '#f87171' },
  'ti-database': { emoji: '🗄️', color: '#8b5cf6' },
  'ti-check': { emoji: '✅', color: '#34d399' },
};

// ── mock backend (only used when window.pywebview is absent) ───────
const _mockState = {
  reminders: [
    { id: 1, date: '2026-07-14', time: '10:00', text: 'Maths Assignment', done: false },
    { id: 2, date: '2026-05-14', time: '16:00', text: 'Project Meeting', done: false },
    { id: 3, date: '2026-05-14', time: '20:00', text: 'Call with Parul', done: false },
  ],
  notes: [],
  mediaActive: false, mediaPlaying: false, mediaPos: 0, mediaDur: 222,
};
let _mockRemId = 100;

function mockApi(name, args) {
  switch (name) {
    case 'get_system_stats':
      return {
        cpu: Math.round(10 + Math.random() * 40), ram: Math.round(30 + Math.random() * 30),
        disk: 52, disk_total_gb: 512, disk_used_gb: 198,
        net_down_mbps: (Math.random() * 20).toFixed(1) * 1, net_up_mbps: (Math.random() * 4).toFixed(1) * 1
      };
    case 'get_memory_stats':
      return { ok: true, pct: 34, exchange_count: 7, max_exchanges: 20, approx_mb: 0.42 };
    case 'get_share_card_data':
      return { ok: true, streak: 5, total_messages: 42, days_used: 12, proactive_nudges: 3, sara_name: 'Sara' };
    case 'get_proactive_stats':
      return { ok: true, total: 3, by_trigger: { battery: 1, reminder: 1, idle_break: 1, streak: 0 }, recent: [] };
    case 'get_setup_wizard_seen':
      return { seen: true };
    case 'mark_setup_wizard_seen':
      return { ok: true };
    case 'check_setup_status':
      return {
        llm_backend: 'ollama', gemini_key_set: null,
        ollama_installed: true, ollama_running: true,
        llm_model_pulled: true, llm_model_name: 'qwen3:',
        rag_enabled: true, embedding_model_pulled: true, embedding_model_name: 'nomic-embed-text',
        kokoro_model_present: true, kokoro_voices_present: true,
        all_ready: true
      };
    case 'run_setup_fix':
      return { ok: true, started: args[0] };
    case 'get_weather':
      return { ok: true, data: { ok: true, city: 'Ajmer', temp: 31, temp_max: 35, temp_min: 26, condition: 'Clear', description: 'Clear Sky' } };
    case 'get_reminders':
      return { ok: true, data: _mockState.reminders };
    case 'add_reminder': {
      const r = { id: ++_mockRemId, date: args[0], time: args[1], text: args[2], done: false };
      _mockState.reminders.push(r);
      return { ok: true, id: r.id };
    }
    case 'delete_reminder':
      _mockState.reminders = _mockState.reminders.filter(r => r.id !== args[0]);
      return { ok: true };
    case 'toggle_reminder':
      _mockState.reminders.forEach(r => { if (r.id === args[0]) r.done = !r.done; });
      return { ok: true };
    case 'get_notes':
      return { ok: true, data: _mockState.notes };
    case 'save_note': {
      const n = { id: _mockState.notes.length + 1, text: args[0], timestamp: new Date().toLocaleString() };
      _mockState.notes.unshift(n);
      return { ok: true, message: 'Note saved.', id: n.id };
    }
    case 'get_media_status':
      if (!_mockState.mediaActive) return { ok: true, active: false };
      return {
        ok: true, active: true, title: 'Midnight City (preview)', artist: 'M83',
        status: _mockState.mediaPlaying ? 'playing' : 'paused',
        position_sec: _mockState.mediaPos, duration_sec: _mockState.mediaDur
      };
    case 'toggle_music_playback':
      _mockState.mediaActive = true; _mockState.mediaPlaying = !!args[0];
      return { ok: true };
    case 'stop_music':
      _mockState.mediaActive = false; _mockState.mediaPlaying = false; _mockState.mediaPos = 0;
      return { ok: true, message: 'Stopped.' };
    case 'skip_next_track': case 'skip_previous_track':
      _mockState.mediaPos = 0;
      return { ok: true };
    case 'seek_media':
      _mockState.mediaPos = args[0];
      return { ok: true };
    case 'toggle_shuffle':
      return { ok: true, shuffle: !!args[0] };
    case 'cycle_repeat_mode':
      return { ok: true };
    case 'send_text_command':
      // Mirrors the REAL backend's Api.send_text_command(), which pushes
      // the user's own transcript immediately, then the reply later —
      // so preview mode behaves identically to a real connection instead
      // of relying on the caller to render its own optimistic bubble.
      window.saraEvent({ kind: 'transcript', args: ['user', args[0]] });
      window.saraEvent({ kind: 'status', args: ['thinking'] });
      setTimeout(() => {
        window.saraEvent({ kind: 'status', args: ['speaking'] });
        const echoText = '(preview mode, no backend connected) Got it: ' + args[0];
        window.saraEvent({ kind: 'transcript', args: ['sara', echoText] });
        if (String(args[0]).includes('play')) { _mockState.mediaActive = true; _mockState.mediaPlaying = true; }
        setTimeout(() => window.saraEvent({ kind: 'status', args: ['sleeping'] }), 1200);
      }, 500);
      return { ok: true };
    case 'wake_now':
      window.saraEvent({ kind: 'status', args: ['waking'] });
      setTimeout(() => window.saraEvent({ kind: 'status', args: ['listening'] }), 400);
      setTimeout(() => window.saraEvent({ kind: 'status', args: ['sleeping'] }), 4000);
      return { ok: true };
    case 'stop_sara':
      window.saraEvent({ kind: 'status', args: ['sleeping'] });
      return { ok: true };
    case 'set_mute': case 'set_focus_mode': case 'update_setting':
    case 'set_mic_sensitivity': case 'set_speech_speed': case 'toggle_wifi':
    case 'set_language': case 'run_action': case 'set_assistant_active':
      return { ok: true };
    case 'get_assistant_active':
      return { ok: true, active: true };
    case 'get_ui_settings':
      return { ok: true, data: {} };
    case 'get_notes_status':
      return { ok: true, enabled: true, count: 3, last_synced: new Date(Date.now() - 3600000).toISOString() };
    case 'get_skills_list':
      return {
        ok: true, data: [
          { name: 'daily_briefing', intent: 'daily_briefing', description: 'Weather + reminders + a headline, spoken as one summary', enabled: true, status: 'loaded' },
          { name: 'notes_qa', intent: 'notes_qa', description: 'Answers questions from your class notes via the RAG vector store', enabled: true, status: 'loaded' },
          { name: 'broken_skill', intent: null, description: null, enabled: true, status: 'error', error: "missing one of ('INTENT_NAME', 'PATTERNS', 'handle')" },
        ]
      };
    case 'set_skill_enabled':
      return { ok: true };
    case 'export_memory':
      setTimeout(() => window.saraEvent({ kind: 'notification', args: ['ti-database', '#8b5cf6', 'Memory export completed (preview mode)'] }), 700);
      return { ok: true, path: 'memory_export.json' };
    default:
      return { ok: true };
  }
}

// BUGFIX: pywebview's js_api bridge (Windows/WinForms host) can finish
// binding its method stubs asynchronously, slightly after
// 'pywebviewready' fires -- so a specific method can appear undefined
// for the first call or two even though window.pywebview.api itself
// already exists and the Python Api object genuinely has that method
// (confirmed via the '[Api] N methods exposed...' startup print).
// Instead of treating one missing method as permanent and falling back
// to mock forever, this retries a few times with a short delay first --
// only falling back to mock if the method is STILL missing after that,
// which now means it's genuinely absent, not just not-yet-bound.
const _API_RETRY_ATTEMPTS = 5;
const _API_RETRY_DELAY_MS = 200;

function _sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

async function callApi(name, ...args) {
  for (let attempt = 0; attempt < _API_RETRY_ATTEMPTS; attempt++) {
    if (window.pywebview && window.pywebview.api && typeof window.pywebview.api[name] === 'function') {
      try {
        return await window.pywebview.api[name](...args);
      } catch (e) {
        console.error('[api]', name, e);
        return { ok: false };
      }
    }
    if (attempt < _API_RETRY_ATTEMPTS - 1) {
      await _sleep(_API_RETRY_DELAY_MS);
    }
  }

  // Still missing after retries -> genuinely not bound. Same diagnostic
  // logging as before, now with the retry count so it's clear this
  // isn't just first-call timing.
  if (!window.pywebview) {
    console.warn(`[api] '${name}' -> mock after ${_API_RETRY_ATTEMPTS} retries: window.pywebview is undefined (not running inside the pywebview desktop window, or it hasn't injected yet).`);
  } else if (!window.pywebview.api) {
    console.warn(`[api] '${name}' -> mock after ${_API_RETRY_ATTEMPTS} retries: window.pywebview.api is undefined (js_api didn't bind).`);
  } else {
    console.warn(`[api] '${name}' -> mock after ${_API_RETRY_ATTEMPTS} retries: window.pywebview.api.${name} is not a function (genuinely missing, not just a binding race).`);
  }
  return mockApi(name, args);
}

// ── push events from Python ──────────────────────────────────────
window.saraEvent = function (payload) {
  try {
    const kind = payload.kind, args = payload.args || [];
    if (kind === 'transcript') { appendChatMessage(args[0], args[1]); playTone(args[0] === 'user' ? 520 : 400, .05, 'sine', .03); }
    else if (kind === 'status') applySaraStatus(args[0]);
    else if (kind === 'footer') applyFooterText(args[0]);
    else if (kind === 'notification') { showToast(args[0], args[1], args[2]); maybeShowProactiveHint(args[0]); }
    else if (kind === 'proactive_notification') { showToast(args[0], args[1], args[2]); maybeShowProactiveHint(); }
    else if (kind === 'weather_update') renderWeather(args[0]);
    else if (kind === 'export_done') showToast('ti-database', '#34d399', 'Memory export completed');
    else if (kind === 'export_error') showToast('ti-alert-triangle', '#f87171', 'Memory export failed');
    else if (kind === 'backend_ready') { /* Small race: page loaded before the Python js_api binds in some renderers */ refreshStatusBar(); }
    else if (kind === 'setup_progress') handleSetupProgress(args[0], args[1], args[2]);
    // 'boot_progress' events exist in the backend push protocol but this
    // design has no boot-splash screen to drive — intentionally ignored,
    // matching window.saraEvent's silent-ignore behavior for unknown kinds.
  } catch (e) { console.error('[saraEvent]', e); }
};

// ── toasts ────────────────────────────────────────────────────────
function showToast(iconClass, color, message) {
  const stack = document.getElementById('toastStack');
  const t = document.createElement('div');
  t.className = 'toast';
  const meta = TOAST_ICON_COLOR[iconClass] || { emoji: '🔔', color: color || '#8b5cf6' };
  t.innerHTML = `<div class="dot" style="background:${color || meta.color}"></div><p>${message}</p>`;
  stack.appendChild(t);
  playTone(660, .06, 'triangle', .025);
  setTimeout(() => { t.style.opacity = '0'; t.style.transition = 'opacity .3s'; setTimeout(() => t.remove(), 300); }, 5000);
}

// ── Proactive Engine hint (pehli 3 baar dikhega) ─────────────────
// Do call patterns hain: kind==='notification' icon ke saath call karta
// hai (tab sirf proactive-icon wali notifications par hint dikhao), aur
// kind==='proactive_notification' bina arg ke call karta hai (yeh khud
// hamesha proactive hoti hai, icon-check ki zaroorat nahi).
const PROACTIVE_HINT_ICONS = ['ti-battery-1', 'ti-alarm', 'ti-coffee', 'ti-flame'];
const PROACTIVE_HINT_MAX_SHOWS = 3;

function maybeShowProactiveHint(iconClass) {
  if (iconClass !== undefined && !PROACTIVE_HINT_ICONS.includes(iconClass)) return;
  try {
    let count = parseInt(localStorage.getItem('sara_proactive_hint_count') || '0', 10);
    if (isNaN(count)) count = 0;
    if (count >= PROACTIVE_HINT_MAX_SHOWS) return;
    localStorage.setItem('sara_proactive_hint_count', String(count + 1));
  } catch (e) {
    return; // storage fail ho to bas skip — cosmetic feature hai, crash nahi hona chahiye
  }
  showProactiveHint();
}

function showProactiveHint() {
  const stack = document.getElementById('toastStack');
  if (!stack) return;
  const t = document.createElement('div');
  t.className = 'toast';
  t.innerHTML = `<div class="dot" style="background:#a78bfa"></div><p>Tip: aap "kyu bola?" pooch sakte ho, main wajah bata dungi.</p><button class="proactive-hint-close">×</button>`;
  stack.appendChild(t);
  const remove = () => { t.style.opacity = '0'; t.style.transition = 'opacity .3s'; setTimeout(() => t.remove(), 300); };
  t.querySelector('.proactive-hint-close').addEventListener('click', remove);
  setTimeout(remove, 4000);
}

/* ══════════════════════════════════════════════════════════════════
   Sound effects — small Web Audio blips, no external files needed
   ══════════════════════════════════════════════════════════════════ */
let soundsOn = localStorage.getItem('sara_ui_sounds') !== 'off';
let _actx = null;
function playTone(freq = 440, dur = 0.05, type = 'sine', vol = 0.04) {
  if (!soundsOn) return;
  try {
    if (!_actx) _actx = new (window.AudioContext || window.webkitAudioContext)();
    if (_actx.state === 'suspended') _actx.resume();
    const osc = _actx.createOscillator();
    const gain = _actx.createGain();
    osc.type = type; osc.frequency.value = freq;
    gain.gain.value = vol;
    gain.gain.exponentialRampToValueAtTime(0.0001, _actx.currentTime + dur);
    osc.connect(gain); gain.connect(_actx.destination);
    osc.start(); osc.stop(_actx.currentTime + dur);
  } catch (e) { /* audio not available — silently skip */ }
}
function tapSound() { playTone(500, .04, 'sine', .035); }
function navSound() { playTone(380, .035, 'sine', .022); }
function toggleSound(on) { playTone(on ? 720 : 340, .05, 'triangle', .03); }
function wakeChime() { playTone(520, .09, 'sine', .05); setTimeout(() => playTone(780, .12, 'sine', .05), 90); }

document.getElementById('soundBtn').addEventListener('click', (e) => {
  soundsOn = !soundsOn;
  localStorage.setItem('sara_ui_sounds', soundsOn ? 'on' : 'off');
  e.currentTarget.classList.toggle('active', soundsOn);
  document.getElementById('settingSounds').classList.toggle('on', soundsOn);
  callApi('update_setting', 'sound_effects', soundsOn);
  if (soundsOn) tapSound();
});
document.getElementById('soundBtn').classList.toggle('active', soundsOn);
document.getElementById('settingSounds').classList.toggle('on', soundsOn);

// ── generic click-ripple + tap sound on interactive elements ───────
function attachRipple(el) {
  el.addEventListener('click', function (e) {
    tapSound();
    const rect = el.getBoundingClientRect();
    const span = document.createElement('span');
    const size = Math.max(rect.width, rect.height) * 1.2;
    span.className = 'btn-ripple';
    span.style.width = span.style.height = size + 'px';
    span.style.left = (e.clientX - rect.left - size / 2) + 'px';
    span.style.top = (e.clientY - rect.top - size / 2) + 'px';
    el.appendChild(span);
    setTimeout(() => span.remove(), 600);
  });
}
document.querySelectorAll('.btn, .qa-card, .qt-btn, .app-tile, .icon-btn, .lang-opt, .pp-controls button, .mic-btn, .send-btn').forEach(attachRipple);

// ── mock media auto-progress (preview mode only) ────────────────────
setInterval(() => {
  if (!window.pywebview && _mockState.mediaActive && _mockState.mediaPlaying) {
    _mockState.mediaPos = (_mockState.mediaPos + 1) % _mockState.mediaDur;
  }
}, 1000);

// ── navigation ───────────────────────────────────────────────────
document.querySelectorAll('#navList li').forEach(li => {
  li.addEventListener('click', () => { navSound(); gotoPage(li.dataset.page); });
});
document.querySelectorAll('[data-goto]').forEach(el => {
  el.addEventListener('click', () => gotoPage(el.dataset.goto));
});
document.getElementById('viewAllReminders').addEventListener('click', () => gotoPage('reminders'));
document.getElementById('qtMoreBtn').addEventListener('click', () => gotoPage('apps'));
document.getElementById('viewAllTools').addEventListener('click', () => gotoPage('apps'));

function gotoPage(page) {
  document.querySelectorAll('#navList li').forEach(li => li.classList.toggle('active', li.dataset.page === page));
  document.querySelectorAll('.page').forEach(p => p.classList.toggle('active', p.id === 'page-' + page));
}

// ── greeting ─────────────────────────────────────────────────────
function setGreeting() {
  const h = new Date().getHours();
  const word = h < 12 ? 'morning' : h < 17 ? 'afternoon' : h < 21 ? 'evening' : 'night';
  document.getElementById('greetingText').childNodes[0].textContent = `Good ${word}, `;
  const saved = localStorage.getItem('sara_display_name');
  document.getElementById('greetingName').textContent = saved || 'friend';
  document.getElementById('displayNameInput').value = saved || '';
}
document.getElementById('displayNameInput').addEventListener('change', (e) => {
  const v = e.target.value.trim();
  if (v) { localStorage.setItem('sara_display_name', v); }
  else { localStorage.removeItem('sara_display_name'); }
  setGreeting();
});
setGreeting();

// ── titlebar controls ────────────────────────────────────────────
document.getElementById('btnMin').addEventListener('click', () => callApi('minimize_window'));
document.getElementById('btnMax').addEventListener('click', () => callApi('toggle_maximize'));
document.getElementById('btnClose').addEventListener('click', () => callApi('close_window'));

let muted = false, focusOn = false;
function setMuted(v) {
  muted = v;
  document.getElementById('muteBtn').classList.toggle('active', muted);
  document.getElementById('settingMute').classList.toggle('on', muted);
  callApi('set_mute', muted);
  toggleSound(muted);
}
function setFocus(v) {
  focusOn = v;
  document.getElementById('focusBtn').classList.toggle('active', focusOn);
  document.getElementById('settingFocus').classList.toggle('on', focusOn);
  document.getElementById('qaFocus').classList.toggle('active', focusOn);
  callApi('set_focus_mode', focusOn);
  toggleSound(focusOn);
}
document.getElementById('muteBtn').addEventListener('click', () => setMuted(!muted));
document.getElementById('focusBtn').addEventListener('click', () => setFocus(!focusOn));
document.getElementById('settingMute').addEventListener('click', () => setMuted(!muted));
document.getElementById('settingFocus').addEventListener('click', () => setFocus(!focusOn));
document.getElementById('qaFocus').addEventListener('click', () => setFocus(!focusOn));

document.getElementById('stopSaraBtn').addEventListener('click', async () => {
  tapSound();
  applySaraStatus('sleeping'); // optimistic — real backend also pushes this
  await callApi('stop_sara');
});

// ── waveform bars (titlebar) ─────────────────────────────────────
const wf = document.getElementById('waveform');
for (let i = 0; i < 28; i++) {
  const s = document.createElement('span');
  s.style.animationDelay = (Math.random() * 1.1).toFixed(2) + 's';
  wf.appendChild(s);
}

/* ══════════════════════════════════════════════════════════════════
   Deep-space orb — star field + ripple engine, shared by Home + Voice
   ══════════════════════════════════════════════════════════════════ */
function buildStarfield(container, count) {
  for (let i = 0; i < count; i++) {
    const s = document.createElement('i');
    const size = (Math.random() * 1.6 + 0.5).toFixed(1);
    s.style.width = s.style.height = size + 'px';
    s.style.left = (Math.random() * 100).toFixed(1) + '%';
    s.style.top = (Math.random() * 100).toFixed(1) + '%';
    s.style.opacity = (Math.random() * 0.7 + 0.2).toFixed(2);
    container.appendChild(s);
  }
}
buildStarfield(document.getElementById('orbStars'), 55);
buildStarfield(document.getElementById('orbStars2'), 65);

function spawnRipple(container) {
  const r = document.createElement('div');
  r.className = 'ripple';
  container.appendChild(r);
  setTimeout(() => r.remove(), 1750);
}

let rippleTimer = null;
function setOrbListening(wrapEl, rippleEl, isListening) {
  wrapEl.classList.toggle('listening', isListening);
  if (isListening) {
    spawnRipple(rippleEl); spawnRipple(rippleEl);
    if (rippleTimer) clearInterval(rippleTimer);
    rippleTimer = setInterval(() => spawnRipple(rippleEl), 850);
  } else if (rippleTimer) {
    clearInterval(rippleTimer); rippleTimer = null;
  }
}

const STATUS_LABELS = {
  sleeping: 'Listening for the wake word',
  waking: 'Waking up…',
  listening: 'Listening…',
  thinking: 'Thinking…',
  speaking: 'Speaking…'
};
const VOICE_STATUS_LABELS = {
  sleeping: 'Tap to speak',
  waking: 'Waking up…',
  listening: 'Listening…',
  thinking: 'Thinking…',
  speaking: 'Speaking…'
};
let currentSaraStatus = 'sleeping';
let assistantActive = true;

// Real backend status ("sleeping"/"waking"/"listening"/"thinking"/"speaking")
// drives the orb visuals directly — replaces a purely-cosmetic fixed
// timeout so the UI always reflects what Sara is actually doing.
function applySaraStatus(state) {
  currentSaraStatus = state;
  const active = (state === 'waking' || state === 'listening' || state === 'speaking');
  wf.classList.toggle('idle', !active);
  document.getElementById('chatMicBtn').classList.toggle('listening', state === 'listening' || state === 'waking');
  setOrbListening(document.getElementById('orbWrap'), document.getElementById('orbRipples'), active);
  setOrbListening(document.getElementById('orbWrap2'), document.getElementById('orbRipples2'), active);
  const label = assistantActive
    ? (STATUS_LABELS[state] || STATUS_LABELS.sleeping)
    : 'Paused — Sara will not respond to the wake word';
  document.getElementById('orbStatus').textContent = label;
  document.getElementById('voiceStatus').textContent = assistantActive
    ? (VOICE_STATUS_LABELS[state] || VOICE_STATUS_LABELS.sleeping)
    : 'Paused';
}

// Real backend footer text ("Say 'sara' to wake me...", "Listening...",
// "Didn't catch that — still listening... (Xs to sleep)") drives the hint
// line under the orb, on both Home and Voice Command pages. Suppressed
// while explicitly paused so it can't stomp the clearer "Paused" hint set
// by renderAssistantState() below.
function applyFooterText(text) {
  if (!assistantActive) return;
  const hintHome = document.getElementById('orbHint');
  const hintVoice = document.getElementById('voiceHint');
  if (hintHome) hintHome.textContent = text;
  if (hintVoice) hintVoice.textContent = text;
}

function doWake() {
  wakeChime();
  applySaraStatus('waking'); // optimistic instant feedback; real push confirms/continues
  callApi('wake_now');
}
document.getElementById('chatMicBtn').addEventListener('click', doWake);
document.getElementById('orbBtn').addEventListener('click', doWake);
document.getElementById('orbBtn2').addEventListener('click', doWake);
document.getElementById('wakeBtn').addEventListener('click', doWake);

// ── backend connection + assistant active/paused (Home page) ────────
let backendConnected = false;

function refreshStatusBar() {
  const statusEl = document.getElementById('onlineStatus');
  const textEl = document.getElementById('onlineStatusText');
  const banner = document.getElementById('backendBanner');
  backendConnected = !!(window.pywebview && window.pywebview.api);
  if (banner) banner.classList.toggle('show', !backendConnected);
  statusEl.classList.toggle('offline', !backendConnected);
  statusEl.classList.toggle('paused', backendConnected && !assistantActive);
  if (!backendConnected) textEl.textContent = 'SARA is offline — preview mode';
  else if (!assistantActive) textEl.textContent = 'SARA is paused';
  else textEl.textContent = 'SARA is online';
}

function renderAssistantState() {
  document.getElementById('orbWrap').classList.toggle('paused', !assistantActive);
  document.getElementById('orbWrap2').classList.toggle('paused', !assistantActive);
  document.getElementById('orbHint').textContent = assistantActive
    ? 'Say "Sara", tap the orb, or press Wake below.'
    : 'Press Resume when you want Sara listening again.';
  const voiceHintEl = document.getElementById('voiceHint');
  if (voiceHintEl) {
    voiceHintEl.textContent = assistantActive
      ? "Sara will respond out loud once she hears you"
      : 'Press Resume on the Home page when you want Sara listening again.';
  }
  document.getElementById('pauseBtnLabel').textContent = assistantActive ? 'Pause Listening' : 'Resume Listening';
  applySaraStatus(currentSaraStatus);
  refreshStatusBar();
}
document.getElementById('pauseBtn').addEventListener('click', async () => {
  assistantActive = !assistantActive;
  await callApi('set_assistant_active', assistantActive);
  toggleSound(assistantActive);
  renderAssistantState();
});
async function loadAssistantState() {
  const res = await callApi('get_assistant_active');
  if (res && typeof res.active === 'boolean') { assistantActive = res.active; renderAssistantState(); }
}

// quick action cards
document.querySelectorAll('[data-action]').forEach(el => {
  el.addEventListener('click', () => callApi('run_action', el.dataset.action));
});
document.querySelectorAll('[data-cmd]').forEach(el => {
  el.addEventListener('click', () => { gotoPage('chat'); callApi('send_text_command', el.dataset.cmd); });
});
async function doWifiToggle() {
  const res = await callApi('toggle_wifi');
  showToast(res && res.ok ? 'ti-check' : 'ti-alert-triangle', res && res.ok ? '#34d399' : '#f87171', (res && res.message) || 'Wi-Fi toggle attempted');
}
document.getElementById('qaWifi').addEventListener('click', doWifiToggle);
document.getElementById('qtWifiBtn').addEventListener('click', doWifiToggle);
document.getElementById('wifiToggleBtn').addEventListener('click', doWifiToggle);

// ── chat ─────────────────────────────────────────────────────────
function appendChatMessage(role, text) {
  const log = document.getElementById('chatLog');
  const div = document.createElement('div');
  div.className = 'msg ' + (role === 'user' ? 'user' : 'sara');
  div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;

  // Mirror into the Voice Command page's live transcript too, so that
  // page shows real conversation instead of staying permanently empty.
  const vt = document.getElementById('voiceTranscript');
  if (vt) {
    const vdiv = document.createElement('div');
    vdiv.className = 'msg ' + (role === 'user' ? 'user' : 'sara');
    vdiv.textContent = text;
    vt.appendChild(vdiv);
    vt.scrollTop = vt.scrollHeight;
    while (vt.children.length > 20) vt.removeChild(vt.firstChild);
  }
}
function sendChat() {
  const input = document.getElementById('chatInput');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  // NOTE: no local optimistic append here — send_text_command() (both the
  // real backend and the preview-mode mock) immediately pushes the user's
  // own transcript back via window.saraEvent, which is what actually
  // renders it. Appending it here too used to make every typed message
  // show up twice in a row.
  callApi('send_text_command', text);
}
document.getElementById('chatSendBtn').addEventListener('click', sendChat);
document.getElementById('chatInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') sendChat(); });

// ── web search page ──────────────────────────────────────────────
function doSearch() {
  const input = document.getElementById('searchInput');
  const q = input.value.trim();
  if (!q) return;
  const log = document.getElementById('searchLog');
  const div = document.createElement('div');
  div.className = 'msg user';
  div.textContent = 'Search: ' + q;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  callApi('send_text_command', 'search the web for ' + q);
  input.value = '';
}
document.getElementById('searchBtn').addEventListener('click', doSearch);
document.getElementById('searchInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') doSearch(); });

// ── reminders ────────────────────────────────────────────────────
function fmtDate(d) {
  if (!d) return '';
  const parts = d.split('-');
  if (parts.length !== 3) return d;
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return `${parseInt(parts[2])} ${months[parseInt(parts[1]) - 1]}`;
}
function to12h(t) {
  if (!t) return '';
  let [h, m] = t.split(':').map(Number);
  const ap = h >= 12 ? 'PM' : 'AM';
  h = h % 12 || 12;
  return `${h}:${String(m).padStart(2, '0')} ${ap}`;
}
const dotColors = ['var(--blue)', 'var(--green)', 'var(--amber)', 'var(--pink)'];

async function loadReminders() {
  const res = await callApi('get_reminders');
  const data = (res && res.data) || [];
  renderReminders(data);
  renderSideReminders(data);
  renderAutomation(data);
}
function renderReminders(list) {
  const wrap = document.getElementById('reminderList');
  wrap.innerHTML = '';
  if (!list.length) {
    wrap.innerHTML = `<div class="empty"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/></svg><h4>No reminders yet</h4><p>Add one above, or just tell Sara "remind me to…"</p></div>`;
    return;
  }
  list.forEach(r => {
    const item = document.createElement('div');
    item.className = 'rem-item';
    item.innerHTML = `
      <div class="rem-check ${r.done ? 'done' : ''}" data-id="${r.id}">${r.done ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M20 6 9 17l-5-5"/></svg>' : ''}</div>
      <div class="rem-body ${r.done ? 'done' : ''}">
        <b>${escapeHtml(r.text)}</b>
        <span>${fmtDate(r.date)}${r.time ? ', ' + to12h(r.time) : ''}</span>
      </div>
      <button class="rem-del" data-del="${r.id}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/></svg></button>
    `;
    wrap.appendChild(item);
  });
  wrap.querySelectorAll('.rem-check').forEach(el => {
    el.addEventListener('click', async () => { tapSound(); await callApi('toggle_reminder', parseInt(el.dataset.id)); loadReminders(); });
  });
  wrap.querySelectorAll('[data-del]').forEach(el => {
    el.addEventListener('click', async () => { await callApi('delete_reminder', parseInt(el.dataset.del)); loadReminders(); });
  });
}
function renderSideReminders(list) {
  const wrap = document.getElementById('sideReminders');
  const active = list.filter(r => !r.done).slice(0, 3);
  if (!active.length) {
    wrap.innerHTML = `<div class="empty" style="padding:14px 6px;"><p>No reminders yet.</p></div>`;
    return;
  }
  wrap.innerHTML = active.map((r, i) => `
    <div class="rlist-item">
      <div class="rlist-dot" style="background:${dotColors[i % dotColors.length]}"></div>
      <div><b>${escapeHtml(r.text)}</b><span>${fmtDate(r.date)}${r.time ? ', ' + to12h(r.time) : ''}</span></div>
    </div>`).join('');
}
function renderAutomation(list) {
  const wrap = document.getElementById('automationList');
  const active = list.filter(r => !r.done);
  if (!active.length) {
    wrap.innerHTML = `<div class="empty" style="padding:20px 6px;"><p>Nothing scheduled right now.</p></div>`;
    return;
  }
  wrap.innerHTML = active.map(r => `
    <div class="rem-item">
      <div class="qa-icon" style="width:30px;height:30px;"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px;"><path d="m13 2-9 12h6l-1 8 9-12h-6l1-8Z"/></svg></div>
      <div class="rem-body"><b>${escapeHtml(r.text)}</b><span>Triggers ${fmtDate(r.date)}${r.time ? ', ' + to12h(r.time) : ''}</span></div>
    </div>`).join('');
}
function escapeHtml(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

document.getElementById('addReminderBtn').addEventListener('click', () => {
  document.getElementById('remText').value = '';
  document.getElementById('remDate').value = new Date().toISOString().slice(0, 10);
  document.getElementById('remTime').value = '09:00';
  document.getElementById('reminderModal').classList.add('open');
});
document.getElementById('remCancel').addEventListener('click', () => document.getElementById('reminderModal').classList.remove('open'));
document.getElementById('remSave').addEventListener('click', async () => {
  const text = document.getElementById('remText').value.trim();
  const date = document.getElementById('remDate').value;
  const time = document.getElementById('remTime').value;
  if (!text || !date || !time) return;
  await callApi('add_reminder', date, time, text);
  document.getElementById('reminderModal').classList.remove('open');
  showToast('ti-check', '#34d399', 'Reminder saved');
  loadReminders();
});

// ── notes ────────────────────────────────────────────────────────
async function loadNotes() {
  const res = await callApi('get_notes');
  const list = (res && res.data) || [];
  const wrap = document.getElementById('notesList');
  if (!list.length) {
    wrap.innerHTML = `<div class="empty"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M4 19.5V4.8c0-1 .8-1.8 1.8-1.8h9.6L20 7.5v12c0 1-.8 1.8-1.8 1.8H5.8A1.8 1.8 0 0 1 4 19.5Z"/></svg><h4>No notes yet</h4><p>Write something above and it'll show up here.</p></div>`;
    return;
  }
  wrap.innerHTML = list.map(n => `
    <div class="note-item">
      <div class="qa-icon" style="width:30px;height:30px;"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px;"><path d="M4 19.5V4.8c0-1 .8-1.8 1.8-1.8h9.6L20 7.5v12c0 1-.8 1.8-1.8 1.8H5.8A1.8 1.8 0 0 1 4 19.5Z"/></svg></div>
      <div class="note-body">${escapeHtml(n.text)}<div class="note-time">${escapeHtml(n.timestamp || '')}</div></div>
    </div>`).join('');
}
document.getElementById('saveNoteBtn').addEventListener('click', async () => {
  const ta = document.getElementById('noteInput');
  const text = ta.value.trim();
  if (!text) return;
  const res = await callApi('save_note', text);
  if (res && res.ok) { ta.value = ''; showToast('ti-check', '#34d399', 'Note saved'); loadNotes(); }
  else { showToast('ti-alert-triangle', '#f87171', 'Could not save note'); }
});

// ── weather ──────────────────────────────────────────────────────
function renderWeather(data) {
  const empty = document.getElementById('weatherEmpty');
  const body = document.getElementById('weatherBody');
  if (!data || !data.ok) {
    empty.style.display = 'flex';
    empty.querySelector('p').textContent = (data && data.error) ? data.error : 'Weather unavailable right now.';
    body.style.display = 'none';
    return;
  }
  empty.style.display = 'none';
  body.style.display = 'block';
  document.getElementById('wTemp').textContent = data.temp + '°';
  document.getElementById('wCity').textContent = data.city || '—';
  document.getElementById('wRange').textContent = `H ${data.temp_max}° · L ${data.temp_min}°`;
  document.getElementById('wCond').textContent = (data.description || data.condition || '—') + (data.aqi_label ? ' · AQI ' + data.aqi_label : '');
}
async function loadWeather() {
  const res = await callApi('get_weather');
  if (res && res.data) renderWeather(res.data);
}

// ── system stats ─────────────────────────────────────────────────
async function pollStats() {
  const s = await callApi('get_system_stats');
  if (!s) return;
  document.getElementById('statCpu').textContent = Math.round(s.cpu) + '%';
  document.getElementById('statCpuBar').style.width = Math.round(s.cpu) + '%';
  document.getElementById('statRam').textContent = Math.round(s.ram) + '%';
  document.getElementById('statRamBar').style.width = Math.round(s.ram) + '%';
  document.getElementById('statDisk').textContent = Math.round(s.disk) + '%';
  document.getElementById('statDiskBar').style.width = Math.round(s.disk) + '%';
  document.getElementById('statDiskSub').textContent = `${s.disk_used_gb} GB / ${s.disk_total_gb} GB`;
  document.getElementById('statNetDown').textContent = `↓ ${s.net_down_mbps} Mbps`;
  document.getElementById('statNetUp').textContent = `↑ ${s.net_up_mbps} Mbps`;
}

// ── memory page ──────────────────────────────────────────────────
async function loadMemoryStats() {
  const s = await callApi('get_memory_stats');
  if (!s || !s.ok) { document.getElementById('memStatus').textContent = 'Unavailable'; return; }
  document.getElementById('memRingLabel').textContent = s.pct + '%';
  const circumference = 314;
  document.getElementById('memRingFill').setAttribute('stroke-dashoffset', circumference - (circumference * s.pct / 100));
  document.getElementById('memExchanges').textContent = `${s.exchange_count} / ${s.max_exchanges}`;
  document.getElementById('memSize').textContent = `${s.approx_mb} MB`;
  document.getElementById('memStatus').textContent = s.pct >= 90 ? 'Nearly full' : 'Healthy';
}
document.getElementById('exportBtn').addEventListener('click', async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true; btn.textContent = 'Exporting…';
  await callApi('export_memory');
  setTimeout(() => { btn.disabled = false; btn.textContent = 'Export Memory'; }, 1500);
});

// ── Sara Moments (shareable card) ────────────────────────────────────
async function openMomentsCard() {
  const data = await callApi('get_share_card_data');
  const overlay = document.getElementById('momentsOverlay');
  if (!data || !data.ok) {
    document.getElementById('momentsStreak').textContent = '0';
    document.getElementById('momentsMessages').textContent = '0';
    document.getElementById('momentsDays').textContent = '0';
    document.getElementById('momentsNudges').textContent = '0';
  } else {
    document.getElementById('momentsStreak').textContent = data.streak || 0;
    document.getElementById('momentsMessages').textContent = data.total_messages || 0;
    document.getElementById('momentsDays').textContent = data.days_used || 0;
    document.getElementById('momentsNudges').textContent = data.proactive_nudges || 0;
  }
  overlay.classList.remove('hidden');
}

function closeMomentsCard() {
  document.getElementById('momentsOverlay').classList.add('hidden');
}

async function saveMomentsAsImage() {
  const btn = document.getElementById('momentsSave');
  if (typeof html2canvas === 'undefined') {
    btn.textContent = 'Image export unavailable offline';
    setTimeout(() => { btn.textContent = '📸 Save as Image'; }, 2500);
    return;
  }
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Rendering…';
  try {
    const canvas = await html2canvas(document.getElementById('momentsCard'), {
      backgroundColor: null, scale: 2, useCORS: true
    });
    const link = document.createElement('a');
    link.download = 'sara-moments.png';
    link.href = canvas.toDataURL('image/png');
    link.click();
  } catch (e) {
    console.error('[Sara Moments] export failed', e);
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

document.getElementById('shareMomentsBtn').addEventListener('click', openMomentsCard);
document.getElementById('momentsClose').addEventListener('click', closeMomentsCard);
document.getElementById('momentsBackdrop').addEventListener('click', closeMomentsCard);
document.getElementById('momentsSave').addEventListener('click', saveMomentsAsImage);

// ── Setup Wizard (first-run onboarding) ──────────────────────────────
const SETUP_CHECKS = [
  {
    key: 'ollama_running',
    label: s => (s.llm_backend === 'gemini' ? 'Gemini API key' : 'Ollama running'),
    detail: s => (s.llm_backend === 'gemini'
      ? (s.gemini_key_set ? 'Configured' : 'Set GEMINI_API_KEY in your .env')
      : (s.ollama_running ? 'Connected' : 'Not detected on this machine')),
    ok: s => (s.llm_backend === 'gemini' ? s.gemini_key_set : s.ollama_running),
    fixAction: s => (s.llm_backend === 'gemini' ? null : 'open_ollama_download'),
    fixLabel: 'Get Ollama'
  },
  {
    key: 'llm_model_pulled',
    label: s => `Chat model (${s.llm_model_name || 'model'})`,
    detail: s => (s.llm_model_pulled ? 'Ready' : 'Needs to be downloaded once'),
    ok: s => s.llm_model_pulled,
    show: s => s.llm_backend !== 'gemini',
    fixAction: () => 'pull_llm_model',
    fixLabel: 'Download'
  },
  {
    key: 'embedding_model_pulled',
    label: s => `Notes search model (${s.embedding_model_name || 'model'})`,
    detail: s => (s.embedding_model_pulled ? 'Ready' : 'Needed for "what do my notes say" questions'),
    ok: s => s.embedding_model_pulled,
    show: s => s.rag_enabled,
    fixAction: () => 'pull_embedding_model',
    fixLabel: 'Download'
  },
  {
    key: 'kokoro_voice',
    label: () => 'Voice files',
    detail: s => (s.kokoro_model_present && s.kokoro_voices_present ? 'Ready' : 'See BUILD.md to add the voice model files'),
    ok: s => s.kokoro_model_present && s.kokoro_voices_present,
    fixAction: () => null,
    fixLabel: null
  }
];

let _lastSetupStatus = null;

function _setupIconFor(state) {
  if (state === 'ready') return '✓';
  if (state === 'error') return '!';
  if (state === 'checking') return '◌';
  return '·';
}

function renderSetupChecklist(status) {
  _lastSetupStatus = status;
  const list = document.getElementById('setupWizardChecklist');
  const allGoodBanner = status.all_ready
    ? `<div class="setup-check-allgood">✅ Everything looks good!</div>`
    : '';
  list.innerHTML = allGoodBanner + SETUP_CHECKS
    .filter(item => !item.show || item.show(status))
    .map(item => {
      const ok = item.ok(status);
      const stateClass = ok ? 'ready' : 'error';
      const fixAction = item.fixAction ? item.fixAction(status) : null;
      const fixBtn = (!ok && fixAction)
        ? `<button class="setup-check-fix" data-fix-action="${fixAction}">${item.fixLabel}</button>`
        : '';
      return `<div class="setup-check-item ${stateClass}" data-key="${item.key}">
        <div class="setup-check-icon">${_setupIconFor(ok ? 'ready' : 'error')}</div>
        <div class="setup-check-text"><b>${item.label(status)}</b><span>${item.detail(status)}</span></div>
        ${fixBtn}
      </div>`;
    }).join('');

  list.querySelectorAll('[data-fix-action]').forEach(btn => {
    btn.addEventListener('click', () => runSetupFix(btn.dataset.fixAction, btn));
  });

  const continueBtn = document.getElementById('setupWizardContinue');
  continueBtn.disabled = !status.all_ready;
  continueBtn.textContent = status.all_ready ? 'Get Started →' : 'Fix the items above to continue';
}

async function refreshSetupStatus() {
  const status = await callApi('check_setup_status');
  if (status) renderSetupChecklist(status);
  return status;
}

async function runSetupFix(action, btnEl) {
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = 'Working…'; }
  const logEl = document.getElementById('setupWizardLog');
  logEl.classList.add('show');
  logEl.textContent = '';
  await callApi('run_setup_fix', action);
}

function handleSetupProgress(action, state, message) {
  const logEl = document.getElementById('setupWizardLog');
  if (logEl) {
    logEl.classList.add('show');
    logEl.textContent = message;
    logEl.scrollTop = logEl.scrollHeight;
  }
  if (state === 'done' || state === 'error') {
    refreshSetupStatus();
  }
}

function showSetupWizard() {
  document.getElementById('setupWizardLog').classList.remove('show');
  document.getElementById('setupWizardOverlay').classList.remove('hidden');
  refreshSetupStatus();
}

async function initSetupWizard() {
  // Listeners hamesha attach hote hain (seen-check se pehle) — taaki
  // "Re-run Setup Check" se dobara khula wizard bhi fully functional rahe.
  const overlay = document.getElementById('setupWizardOverlay');
  document.getElementById('setupWizardRecheck').addEventListener('click', refreshSetupStatus);
  document.getElementById('setupWizardSkip').addEventListener('click', async () => {
    await callApi('mark_setup_wizard_seen');
    overlay.classList.add('hidden');
  });
  document.getElementById('setupWizardContinue').addEventListener('click', async () => {
    if (document.getElementById('setupWizardContinue').disabled) return;
    await callApi('mark_setup_wizard_seen');
    overlay.classList.add('hidden');
  });

  const seen = await callApi('get_setup_wizard_seen');
  if (seen && seen.seen) return;
  overlay.classList.remove('hidden');
  await refreshSetupStatus();
}
document.getElementById('rerunSetupBtn').addEventListener('click', showSetupWizard);

document.getElementById('rerunSetupBtn').addEventListener('click', showSetupWizard);

// ── proactive insights (Settings page) ──────────────────────────────
function _relTime(iso) {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  if (isNaN(then)) return '';
  const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}
const _PROACTIVE_TRIGGER_LABELS = {
  battery: '🔋 Battery',
  reminder: '⏰ Reminder',
  idle_break: '☕ Break suggestion',
  streak: '🔥 Streak milestone'
};
async function loadProactiveStats() {
  const s = await callApi('get_proactive_stats');
  if (!s || !s.ok) return;
  document.getElementById('proactiveTotal').textContent = s.total || 0;
  const byTrigger = s.by_trigger || {};
  document.getElementById('proactiveBattery').textContent = byTrigger.battery || 0;
  document.getElementById('proactiveReminder').textContent = byTrigger.reminder || 0;
  document.getElementById('proactiveIdleBreak').textContent = byTrigger.idle_break || 0;
  document.getElementById('proactiveStreak').textContent = byTrigger.streak || 0;

  const listEl = document.getElementById('proactiveRecentList');
  const recent = (s.recent || []).slice().reverse(); // newest first for display
  if (!recent.length) { listEl.textContent = 'No proactive activity yet.'; return; }
  listEl.innerHTML = recent.map(ev => {
    const label = _PROACTIVE_TRIGGER_LABELS[ev.trigger] || ev.trigger;
    const msg = (ev.message || '').replace(/</g, '&lt;');
    return `<div style="margin-bottom:6px;"><b>${label}</b> · ${_relTime(ev.timestamp)}<br>${msg}</div>`;
  }).join('');
}

// ── notes index status (Settings page) ────────────────────────────
// Small "X notes indexed | last synced: ..." status line, backed by
// sara/skills/notes_qa.py's get_notes_index_status() via the
// get_notes_status() Api method. Reuses _relTime() above rather than
// duplicating relative-time logic. Never assumes success — a disabled/
// unavailable notes feature just renders a plain, non-alarming message
// instead of leaving the "Checking…" placeholder stuck or throwing.
async function loadNotesStatus() {
  const el = document.getElementById('notesIndexStatus');
  if (!el) return;
  const s = await callApi('get_notes_status');
  if (!s || !s.ok || !s.enabled) {
    el.textContent = "Notes search isn't enabled right now.";
    return;
  }
  if (!s.count) {
    el.textContent = 'No notes indexed yet.';
    return;
  }
  const synced = s.last_synced ? _relTime(s.last_synced) : 'never';
  el.textContent = `${s.count} note${s.count === 1 ? '' : 's'} indexed · last synced: ${synced}`;
}

// ── skills manager (Settings page) ──────────────────────────────
// Renders sara.skills._LOADED_SKILLS (via get_skills_list()) as a list
// of settings-row toggles. Renders dynamically since the skill count
// isn't fixed — dropping a new .py file into sara/skills/ grows this
// list on its own. A broken/corrupt skill module still shows up (with
// an "Error" pill and its message) instead of taking the whole list
// down, matching the try/except-per-skill discovery in __init__.py.
async function loadSkills() {
  const wrap = document.getElementById('skillsList');
  if (!wrap) return;
  const res = await callApi('get_skills_list');
  const skills = (res && res.data) || [];
  if (!skills.length) {
    wrap.innerHTML = `<p style="font-size:12px; color:var(--text-lo);">No skills found in sara/skills/.</p>`;
    return;
  }
  wrap.innerHTML = skills.map(s => {
    let pill = '';
    if (s.status === 'error') pill = `<span class="skill-status-pill error">Error</span>`;
    else if (s.status === 'disabled') pill = `<span class="skill-status-pill disabled">Off</span>`;
    const desc = s.description || s.intent || s.name;
    const errDetail = (s.status === 'error' && s.error) ? ` — ${escapeHtml(s.error)}` : '';
    return `
      <div class="settings-row">
        <div>
          <b>${escapeHtml(s.name)}${pill}</b>
          <span>${escapeHtml(desc)}${errDetail}</span>
        </div>
        <div class="toggle ${s.enabled ? 'on' : ''}" data-skill-name="${escapeHtml(s.name)}"></div>
      </div>`;
  }).join('');

  wrap.querySelectorAll('[data-skill-name]').forEach(el => {
    el.addEventListener('click', async () => {
      const on = !el.classList.contains('on');
      el.classList.toggle('on', on);
      toggleSound(on);
      await callApi('set_skill_enabled', el.dataset.skillName, on);
      const note = document.getElementById('skillRestartNote');
      if (note) note.classList.add('show');
    });
  });
}

/* ══════════════════════════════════════════════════════════════════
   Premium music player
   ══════════════════════════════════════════════════════════════════ */
function fmtTime(sec) {
  sec = Math.max(0, Math.round(sec || 0));
  const m = Math.floor(sec / 60), s = sec % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}
let mediaPlaying = false;
let seekDragging = false;
let sleepTimerInterval = null;
let sleepTimerEndsAt = null;

async function pollMedia() {
  const s = await callApi('get_media_status');
  const titleWrap = document.getElementById('ppTitleWrap');
  const ppArt = document.getElementById('ppArt');
  if (!s || !s.ok || !s.active) {
    document.getElementById('ppTitle').textContent = 'Nothing playing';
    document.getElementById('ppArtist').textContent = 'Play something to control it here';
    document.getElementById('ppApp').textContent = '';
    ppArt.classList.remove('spinning', 'has-art');
    ppArt.style.backgroundImage = '';
    document.getElementById('ppPlayIcon').innerHTML = '<path d="M8 5v14l11-7z"/>';
    if (!seekDragging) { document.getElementById('ppSeek').value = 0; document.getElementById('ppCurTime').textContent = '0:00'; document.getElementById('ppDurTime').textContent = '0:00'; }
    mediaPlaying = false;
    return;
  }
  document.getElementById('ppTitle').textContent = s.title || 'Unknown track';
  document.getElementById('ppArtist').textContent = s.artist + (s.album ? ` — ${s.album}` : '') || '';
  document.getElementById('ppApp').textContent = s.app || '';
  titleWrap.classList.toggle('scroll', (s.title || '').length > 26);
  mediaPlaying = s.status === 'playing';
  if (s.art) {
    ppArt.style.backgroundImage = `url("${s.art}")`;
    ppArt.classList.add('has-art');
  } else {
    ppArt.style.backgroundImage = '';
    ppArt.classList.remove('has-art');
  }
  ppArt.classList.toggle('spinning', mediaPlaying);
  document.getElementById('ppPlayIcon').innerHTML = mediaPlaying ? '<rect x="6" y="5" width="4" height="14"/><rect x="14" y="5" width="4" height="14"/>' : '<path d="M8 5v14l11-7z"/>';
  const ppShuffle = document.getElementById('ppShuffle');
  const ppRepeat = document.getElementById('ppRepeat');
  const caps = s.caps || {};
  ppShuffle.classList.toggle('active', !!s.shuffle);
  ppShuffle.disabled = s.shuffle_supported === false && !caps.can_shuffle;
  ppRepeat.classList.toggle('active', s.repeat && s.repeat !== 'none');
  ppRepeat.classList.toggle('repeat-track', s.repeat === 'track');
  if (!seekDragging) {
    const dur = s.duration_sec || 0, pos = s.position_sec || 0;
    document.getElementById('ppSeek').max = Math.max(dur, 1);
    document.getElementById('ppSeek').value = pos;
    document.getElementById('ppCurTime').textContent = fmtTime(pos);
    document.getElementById('ppDurTime').textContent = fmtTime(dur);
  }
}
document.getElementById('ppPlayPause').addEventListener('click', () => {
  mediaPlaying = !mediaPlaying;
  callApi('toggle_music_playback', mediaPlaying);
  document.getElementById('ppArt').classList.toggle('spinning', mediaPlaying);
  document.getElementById('ppPlayIcon').innerHTML = mediaPlaying ? '<rect x="6" y="5" width="4" height="14"/><rect x="14" y="5" width="4" height="14"/>' : '<path d="M8 5v14l11-7z"/>';
});
document.getElementById('ppStop').addEventListener('click', () => {
  callApi('stop_music');
  mediaPlaying = false;
  document.getElementById('ppArt').classList.remove('spinning');
});
document.getElementById('ppNext').addEventListener('click', () => callApi('skip_next_track'));
document.getElementById('ppPrev').addEventListener('click', () => callApi('skip_previous_track'));
document.getElementById('ppShuffle').addEventListener('click', (e) => {
  const nowOn = !e.currentTarget.classList.contains('active');
  e.currentTarget.classList.toggle('active', nowOn);
  callApi('toggle_shuffle', nowOn);
});
document.getElementById('ppRepeat').addEventListener('click', async () => {
  await callApi('cycle_repeat_mode');
  pollMedia();
});

const ppSeek = document.getElementById('ppSeek');
ppSeek.addEventListener('input', () => { seekDragging = true; document.getElementById('ppCurTime').textContent = fmtTime(ppSeek.value); });
ppSeek.addEventListener('change', () => { callApi('seek_media', parseFloat(ppSeek.value)); setTimeout(() => seekDragging = false, 400); });

// ── sleep timer (fully client-side countdown, calls the real stop_music()) ──
const sleepBtn = document.getElementById('sleepTimerBtn');
const sleepMenu = document.getElementById('sleepMenu');
sleepBtn.addEventListener('click', (e) => { e.stopPropagation(); sleepMenu.classList.toggle('open'); });
document.addEventListener('click', () => sleepMenu.classList.remove('open'));
sleepMenu.querySelectorAll('button').forEach(btn => {
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    sleepMenu.querySelectorAll('button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const minutes = parseInt(btn.dataset.min);
    if (sleepTimerInterval) { clearInterval(sleepTimerInterval); sleepTimerInterval = null; }
    if (minutes === 0) {
      sleepBtn.classList.remove('active');
      document.getElementById('sleepLabel').textContent = 'Sleep Timer';
      sleepMenu.classList.remove('open');
      return;
    }
    sleepTimerEndsAt = Date.now() + minutes * 60000;
    sleepBtn.classList.add('active');
    updateSleepLabel();
    sleepTimerInterval = setInterval(updateSleepLabel, 1000);
    sleepMenu.classList.remove('open');
    showToast('ti-check', '#22d3ee', `Sleep timer set for ${minutes} minutes`);
  });
});
function updateSleepLabel() {
  const remaining = Math.max(0, Math.round((sleepTimerEndsAt - Date.now()) / 1000));
  if (remaining <= 0) {
    clearInterval(sleepTimerInterval); sleepTimerInterval = null;
    sleepBtn.classList.remove('active');
    document.getElementById('sleepLabel').textContent = 'Sleep Timer';
    sleepMenu.querySelectorAll('button').forEach(b => b.classList.remove('active'));
    callApi('stop_music');
    document.getElementById('ppArt').classList.remove('spinning');
    showToast('ti-check', '#8b5cf6', 'Sleep timer ended — playback stopped');
    return;
  }
  const m = Math.floor(remaining / 60), s = remaining % 60;
  document.getElementById('sleepLabel').textContent = `${m}:${String(s).padStart(2, '0')}`;
}

// ── AI Brain page ────────────────────────────────────────────────
document.querySelectorAll('.lang-opt').forEach(el => {
  el.addEventListener('click', () => {
    document.querySelectorAll('.lang-opt').forEach(o => o.classList.remove('active'));
    el.classList.add('active');
    callApi('set_language', el.dataset.lang);
  });
});
document.querySelector('.lang-opt[data-lang="auto"]').classList.add('active');

let micSensTimer, speedTimer;
document.getElementById('micSensSlider').addEventListener('input', (e) => {
  document.getElementById('micSensVal').textContent = e.target.value;
  clearTimeout(micSensTimer);
  micSensTimer = setTimeout(() => callApi('set_mic_sensitivity', parseInt(e.target.value)), 150);
});
document.getElementById('speechSpeedSlider').addEventListener('input', (e) => {
  document.getElementById('speechSpeedVal').textContent = e.target.value;
  clearTimeout(speedTimer);
  speedTimer = setTimeout(() => callApi('set_speech_speed', parseInt(e.target.value)), 150);
});

// ── settings page toggles ────────────────────────────────────────
// Generic — works for every element with data-setting, including the
// master "proactive_mode" toggle AND the 4 per-trigger sub-toggles
// (proactive_battery / proactive_reminders / proactive_idle /
// proactive_streak) added alongside it in index.html. No per-toggle
// wiring needed here since update_setting() on the backend is already
// generic too.
document.querySelectorAll('[data-setting]').forEach(el => {
  el.addEventListener('click', () => {
    const on = !el.classList.contains('on');
    el.classList.toggle('on', on);
    callApi('update_setting', el.dataset.setting, on);
    toggleSound(on);
  });
});

// ── restore UI state saved by a previous session ─────────────────
// The backend (Api.set_mute/set_focus_mode/update_setting/set_language/
// set_mic_sensitivity/set_speech_speed) persists every one of these via
// get_ui_settings(), but nothing ever read them back — every restart
// silently reset toggle switches, the language picker, and slider
// positions to their hardcoded HTML defaults even though the ACTUAL
// backend state (ears/tts/lang_state) was already correctly restored.
// This applies display-only — it never calls back into set_mute/
// set_language/etc, since the backend already holds these values;
// re-sending them would just be a redundant (harmless but wasteful)
// write of the same value back to itself.
function applyUISettings(data) {
  if (!data) return;

  if (data.muted === '1') {
    muted = true;
    document.getElementById('muteBtn').classList.add('active');
    document.getElementById('settingMute').classList.add('on');
  }
  if (data.focus_mode === '1') {
    focusOn = true;
    document.getElementById('focusBtn').classList.add('active');
    document.getElementById('settingFocus').classList.add('on');
    document.getElementById('qaFocus').classList.add('active');
  }
  if (data.language_mode === 'en' || data.language_mode === 'hi') {
    document.querySelectorAll('.lang-opt').forEach(o => o.classList.remove('active'));
    const opt = document.querySelector(`.lang-opt[data-lang="${data.language_mode}"]`);
    if (opt) opt.classList.add('active');
  }
  if (data.mic_sensitivity != null) {
    const v = parseInt(data.mic_sensitivity, 10);
    if (!isNaN(v)) {
      document.getElementById('micSensSlider').value = v;
      document.getElementById('micSensVal').textContent = v;
    }
  }
  if (data.speech_speed != null) {
    const v = parseInt(data.speech_speed, 10);
    if (!isNaN(v)) {
      document.getElementById('speechSpeedSlider').value = v;
      document.getElementById('speechSpeedVal').textContent = v;
    }
  }
  if (data['setting:sound_effects'] != null) {
    soundsOn = data['setting:sound_effects'] === '1';
    localStorage.setItem('sara_ui_sounds', soundsOn ? 'on' : 'off');
    document.getElementById('soundBtn').classList.toggle('active', soundsOn);
    document.getElementById('settingSounds').classList.toggle('on', soundsOn);
  }
  // NOTE (this revision): added 'setting:proactive_mode' plus the 4 new
  // per-trigger keys. The master key was already listed in app.js's
  // toggleMap before this change but get_ui_settings() never returned it
  // (see the BUGFIX comment in settings.py), so it silently never
  // restored — now that the backend returns it too, this map entry
  // finally does something. Missing/undefined for any of these five
  // still safely no-ops (the `if (data[key] != null)` guard below), which
  // is exactly the desired default-ON, backward-compatible behavior for
  // installations that predate these keys.
  const toggleMap = {
    'setting:startup_sound': 'settingStartupSound',
    'setting:show_notifications': 'settingNotifications',
    'setting:voice_replies': 'settingVoiceReplies',
    'setting:proactive_mode': 'settingProactiveMode',
    'setting:proactive_battery': 'settingProactiveBattery',
    'setting:proactive_reminders': 'settingProactiveReminders',
    'setting:proactive_idle': 'settingProactiveIdle',
    'setting:proactive_streak': 'settingProactiveStreak'
  };
  Object.entries(toggleMap).forEach(([key, id]) => {
    if (data[key] != null) {
      const el = document.getElementById(id);
      if (el) el.classList.toggle('on', data[key] === '1');
    }
  });
}
async function loadUISettings() {
  const res = await callApi('get_ui_settings');
  if (res && res.ok) applyUISettings(res.data);
}

// ── boot ─────────────────────────────────────────────────────────
function boot() {
  console.log('[boot] pywebview:', !!window.pywebview, 'api:', !!(window.pywebview && window.pywebview.api), 'methods:', window.pywebview && window.pywebview.api ? Object.keys(window.pywebview.api) : []);
  loadAssistantState();
  loadUISettings();
  loadReminders();
  loadNotes();
  loadWeather();
  loadMemoryStats();
  loadProactiveStats();
  loadNotesStatus();
  loadSkills();
  initSetupWizard();
  pollStats();
  pollMedia();
  refreshStatusBar();
  setInterval(refreshStatusBar, 2000);
  setInterval(pollStats, 3500);
  setInterval(pollMedia, 2000);
  setInterval(loadWeather, 15 * 60 * 1000);
  setInterval(loadProactiveStats, 60 * 1000);
  setInterval(loadNotesStatus, 60 * 1000);
}
// FIX (root cause of "preview mode, no backend connected"): pywebview
// injects window.pywebview ASYNCHRONOUSLY relative to this script running
// (documented pywebview race, see github.com/r0x0r/pywebview/issues/378).
// The old code only attached the 'pywebviewready' listener when
// window.pywebview already existed at parse time -- if it hadn't been
// injected yet (very common, happens within the first ~100-300ms), the
// `else` branch called boot() immediately with NO listener ever attached,
// so any callApi() made before injection finished landed on the mock
// fallback for good, even though the bridge became available moments
// later. pywebview's own official example always attaches this listener
// unconditionally -- do the same here, regardless of the current state.
window.addEventListener('pywebviewready', boot);
// Safety net for the (documented, sometimes flaky) case where
// 'pywebviewready' never fires at all -- boot() itself doesn't crash if
// window.pywebview.api isn't ready yet; callApi() just re-checks fresh
// on every call and falls back to mock until the real bridge shows up.
setTimeout(boot, 300);