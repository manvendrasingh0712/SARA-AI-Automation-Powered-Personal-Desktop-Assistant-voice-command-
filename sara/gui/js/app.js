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

// Live-caption streaming state (LLM chat replies only) — see
// window.saraEvent's 'transcript_chunk' / 'transcript' handling below.
let _streamingSaraBubble = null;
let _streamingSaraText = "";

// Voice input "preview" caption state — a dim/italic, live-updating
// caption of what the user is saying WHILE they're still speaking (see
// window.saraEvent's 'transcript_partial' handling below). Replaced by
// the real/accurate transcript the moment it arrives.
let _previewBubble = null;

// Appends one streamed sentence as a "live caption" — creates the
// bubble on the first chunk of a turn, appends text on every chunk
// after that. Mirrors appendChatMessage()'s DOM pattern (writes into
// #chatLog).
function appendSaraStreamChunk(role, sentence) {
  if (!sentence) return;
  if (!_streamingSaraBubble) {
    const log = document.getElementById('chatLog');
    const div = document.createElement('div');
    div.className = 'msg ' + (role === 'user' ? 'user' : 'sara');
    div.textContent = sentence;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;

    _streamingSaraBubble = { chat: div };
    _streamingSaraText = sentence;
  } else {
    _streamingSaraText += ' ' + sentence;
    _streamingSaraBubble.chat.textContent = _streamingSaraText;
    const log = document.getElementById('chatLog');
    log.scrollTop = log.scrollHeight;
  }
}

// ── mock backend (only used when window.pywebview is absent) ───────
const _mockState = {
  reminders: [
    { id: 1, date: '2026-07-14', time: '10:00', text: 'Maths Assignment', done: false },
    { id: 2, date: '2026-05-14', time: '16:00', text: 'Project Meeting', done: false },
    { id: 3, date: '2026-05-14', time: '20:00', text: 'Call with babe', done: false },
  ],
  notes: [],
  // mock backing store for the Routines UI, only used in preview mode
  // (no window.pywebview) -- mirrors what routines_api.py's
  // list_routines()/get_routine() return from the real PreferencesDB.
  routines: [],
  mediaActive: false, mediaPlaying: false, mediaPos: 0, mediaDur: 222,
  mediaShuffle: false, mediaRepeat: 'none',
  // NEW: mock backing store for the Mode Switcher UI, only used in
  // preview mode (no window.pywebview) -- mirrors what modes.py's
  // ApiModesMixin returns from the real PreferencesDB.
  activeMode: 'normal',
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
    // mock backend for the Routines UI (Automation page), matching
    // sara/gui/app/routines_api.py's real ApiRoutinesMixin shape so preview
    // mode (no window.pywebview) is still fully explorable.
    case 'list_routines':
      return { ok: true, data: _mockState.routines };
    case 'get_routine': {
      const found = _mockState.routines.find(r => r.name === args[0]);
      return found ? { ok: true, data: found } : { ok: false, data: null, error: 'Routine not found.' };
    }
    case 'save_routine': {
      const [rName, rLabel, rSteps, rTrigger] = args;
      if (!rName || !rSteps || !rSteps.length) return { ok: false, error: 'Routine needs a name and at least one step.' };
      const def = { name: rName, label: rLabel || rName, steps: rSteps, trigger_time: rTrigger || null };
      const idx = _mockState.routines.findIndex(r => r.name === rName);
      if (idx >= 0) _mockState.routines[idx] = def; else _mockState.routines.push(def);
      return { ok: true };
    }
    case 'delete_routine':
      _mockState.routines = _mockState.routines.filter(r => r.name !== args[0]);
      return { ok: true };
    case 'run_routine_now':
      setTimeout(() => window.saraEvent({ kind: 'transcript', args: ['sara', `(preview mode) Ran routine "${args[0]}" — no real backend connected.`] }), 500);
      return { ok: true };
    // NEW: mock backend for the Mode Switcher (Settings page), matching
    // sara/gui/app/modes.py's ApiModesMixin. Uses the same confirmation
    // text as _MODE_CONFIRMATIONS in sara/orchestrator/intent_handlers.py.
    case 'get_modes_status':
      return { ok: true, active_mode: _mockState.activeMode, modes: ['normal', 'study', 'work', 'gaming', 'home'] };
    case 'apply_mode': {
      const _MODE_CONFIRMATIONS_MOCK = {
        normal: 'Normal mode on — proactive nudges and mic sensitivity are back to default.',
        study: 'Study mode on — proactive nudges are off now.',
        work: 'Work mode on — proactive nudges are set to normal.',
        gaming: 'Gaming mode on — proactive nudges are off and mic sensitivity is lowered.',
        home: 'Home mode on — proactive nudges are fully on.',
      };
      const requested = String(args[0] || '').toLowerCase();
      const confirmation = _MODE_CONFIRMATIONS_MOCK[requested];
      if (!confirmation) return { ok: false, error: "I don't recognize that mode.", active_mode: null };
      _mockState.activeMode = requested;
      return { ok: true, active_mode: requested, message: confirmation };
    }
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
        position_sec: _mockState.mediaPos, duration_sec: _mockState.mediaDur,
        min_seek_sec: 0, max_seek_sec: _mockState.mediaDur,
        playback_rate: 1.0, timeline_updated_at: Date.now() / 1000,
        shuffle: _mockState.mediaShuffle, shuffle_supported: true,
        repeat: _mockState.mediaRepeat,
        caps: { can_next: true, can_prev: true, can_seek: true, can_shuffle: true, can_repeat: true },
        track_id: 'Midnight City (preview)|M83'
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
      _mockState.mediaPos = Math.max(0, Math.min(args[0], _mockState.mediaDur));
      return { ok: true };
    case 'toggle_shuffle':
      _mockState.mediaShuffle = !!args[0];
      return { ok: true, shuffle: _mockState.mediaShuffle, shuffle_supported: true };
    case 'cycle_repeat_mode': {
      const _order = ['none', 'track', 'list'];
      _mockState.mediaRepeat = _order[(_order.indexOf(_mockState.mediaRepeat) + 1) % _order.length];
      return { ok: true, mode: _mockState.mediaRepeat };
    }
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
    // record_command_usage() is a fire-and-forget counter bump (see
    // app.js callers); the mock just acks it.
    case 'record_command_usage':
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
    if (kind === 'transcript') {
      const role = args[0], text = args[1];
      if (role === 'user') {
        // Naya user turn shuru hua — pichli turn ka streaming state
        // carry na ho.
        _streamingSaraBubble = null;
        _streamingSaraText = "";
        // The final/accurate transcript has arrived — drop the dim
        // "preview" caption (if any) so it doesn't sit duplicated
        // alongside the real message.
        if (_previewBubble) {
          if (_previewBubble.chat && _previewBubble.chat.parentNode) _previewBubble.chat.remove();
          _previewBubble = null;
        }
        appendChatMessage(role, text);
      } else if (role === 'sara' && _streamingSaraBubble) {
        // Ye final/full text is turn ke liye already live-caption ke
        // through stream ho chuki thi — duplicate bubble create mat
        // karo, bas streaming state finalize/reset karo.
        _streamingSaraBubble = null;
        _streamingSaraText = "";
      } else {
        // Static/non-streamed reply (koi aur intent) — purana behavior.
        appendChatMessage(role, text);
      }
      playTone(role === 'user' ? 520 : 400, .05, 'sine', .03);
    }
    else if (kind === 'transcript_partial') {
      // Live caption preview while the user is still speaking — see
      // engine.py's _spawn_preview_transcribe()/_collect_speech() and
      // core_wiring.py's ears.listen(on_partial_transcript=...). Always
      // a full "so-far" transcript, not a chunk, so it REPLACES the
      // bubble's text rather than appending to it.
      const role = args[0], text = args[1];
      if (!text) {
        // nothing to show yet
      } else if (!_previewBubble) {
        const log = document.getElementById('chatLog');
        const div = document.createElement('div');
        div.className = 'msg ' + (role === 'user' ? 'user' : 'sara') + ' preview';
        div.style.opacity = '0.6';
        div.style.fontStyle = 'italic';
        div.textContent = text;
        log.appendChild(div);
        log.scrollTop = log.scrollHeight;

        _previewBubble = { chat: div };
      } else {
        _previewBubble.chat.textContent = text;
        const log = document.getElementById('chatLog');
        log.scrollTop = log.scrollHeight;
      }
    }
    else if (kind === 'transcript_chunk') { appendSaraStreamChunk(args[0], args[1]); }
    else if (kind === 'status') applySaraStatus(args[0]);
    else if (kind === 'footer') applyFooterText(args[0]);
    else if (kind === 'notification') { showToast(args[0], args[1], args[2]); maybeShowProactiveHint(args[0]); }
    else if (kind === 'proactive_notification') { showToast(args[0], args[1], args[2]); maybeShowProactiveHint(); }
        else if (kind === 'weather_update') renderWeather(args[0]);
    else if (kind === 'backend_ready') { refreshStatusBar(); }
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
  document.getElementById('settingSounds').setAttribute('aria-checked', soundsOn ? 'true' : 'false');
  callApi('update_setting', 'sound_effects', soundsOn);
  if (soundsOn) tapSound();
});
document.getElementById('soundBtn').classList.toggle('active', soundsOn);
document.getElementById('settingSounds').classList.toggle('on', soundsOn);
document.getElementById('settingSounds').setAttribute('aria-checked', soundsOn ? 'true' : 'false');

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
document.querySelectorAll('.btn, .qa-card, .qt-btn, .app-tile, .icon-btn, .lang-opt, .pp-controls button, .mic-btn, .send-btn, .mode-opt').forEach(attachRipple);

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
  var activePage = document.getElementById('page-' + page);
  if (activePage && window.saraApplyPageSlide) window.saraApplyPageSlide(activePage);
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
  // Persist to the real backend user-name system too (db.set_user_name +
  // brain.set_user_name), not just localStorage — see loadDisplayName()
  // below, which is what makes the DB authoritative on next load.
  if (v) callApi('set_display_name', v);
});
setGreeting();

// Reconciles the Home-page greeting/input with the real backend
// user-name (authoritative — can also be set via voice, "call me X").
// Runs after setGreeting()'s instant localStorage-based render above, so
// a name set via voice (or on another session) still wins once this
// resolves. Empty/missing DB name is left alone — localStorage/placeholder
// stays as-is.
async function loadDisplayName() {
  const res = await callApi('get_display_name');
  if (res && res.ok && res.name) {
    localStorage.setItem('sara_display_name', res.name);
    setGreeting();
  }
}

// ── Memory & Data card (Settings page) ──────────────────────────────
async function loadMemoryAndShareStats() {
  const mem = await callApi('get_memory_stats');
  if (mem && mem.ok) {
    document.getElementById('memoryPct').textContent = mem.pct + '%';
    document.getElementById('memoryExchanges').textContent = mem.exchange_count + ' / ' + mem.max_exchanges;
    document.getElementById('memorySizeMb').textContent = mem.approx_mb + ' MB';
  }
  const share = await callApi('get_share_card_data');
  if (share && share.ok) {
    document.getElementById('shareStreak').textContent = share.streak;
    document.getElementById('shareTotalMessages').textContent = share.total_messages;
    document.getElementById('shareDaysUsed').textContent = share.days_used != null ? share.days_used : '—';
  }
}
document.getElementById('exportMemoryBtn').addEventListener('click', async () => {
  const res = await callApi('export_memory');
  if (res && res.ok) showToast('ti-database', '#34d399', 'Exporting memory…');
  else showToast('ti-alert-triangle', '#f87171', 'Could not start memory export');
});

// ── Usage Analytics card (Settings page) ────────────────────────────
async function loadAnalyticsDashboard() {
  const res = await callApi('get_analytics_dashboard');
  if (!res || !res.ok) return;
  const d = res.data || {};
  document.getElementById('analyticsTotal').textContent = d.total_commands || 0;
  const top = d.top_commands || [];
  const listEl = document.getElementById('analyticsTopCommands');
  listEl.innerHTML = top.length
    ? top.map(c => {
        const name = String(c.name || '').replace(/</g, '&lt;');
        return `<div>${name} — ${c.count}×</div>`;
      }).join('')
    : 'No commands recorded yet.';
  const trend = d.daily_trend || [];
  let busiest = null;
  trend.forEach(t => { if (t.count > 0 && (!busiest || t.count > busiest.count)) busiest = t; });
  document.getElementById('analyticsBusiestDay').textContent = busiest ? `${busiest.date} (${busiest.count})` : '—';
}

// ── titlebar controls ────────────────────────────────────────────
document.getElementById('btnMin').addEventListener('click', () => callApi('minimize_window'));
document.getElementById('btnMax').addEventListener('click', () => callApi('toggle_maximize'));
document.getElementById('btnClose').addEventListener('click', () => callApi('close_window'));

let muted = false, focusOn = false;
function setMuted(v) {
  muted = v;
  document.getElementById('muteBtn').classList.toggle('active', muted);
  document.getElementById('settingMute').classList.toggle('on', muted);
  document.getElementById('settingMute').setAttribute('aria-checked', muted ? 'true' : 'false');
  callApi('set_mute', muted);
  toggleSound(muted);
}
function setFocus(v) {
  focusOn = v;
  document.getElementById('focusBtn').classList.toggle('active', focusOn);
  document.getElementById('settingFocus').classList.toggle('on', focusOn);
  document.getElementById('settingFocus').setAttribute('aria-checked', focusOn ? 'true' : 'false');
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
    // Random twinkle phase so all stars don't pulse in sync (see the
    // .orb-stars i keyframe animation added in index.html's polish layer).
    s.style.animationDelay = (Math.random() * 2.4).toFixed(2) + 's';
    container.appendChild(s);
  }
}
buildStarfield(document.getElementById('orbStars'), 55);

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
  const label = assistantActive
    ? (STATUS_LABELS[state] || STATUS_LABELS.sleeping)
    : 'Paused — Sara will not respond to the wake word';
  document.getElementById('orbStatus').textContent = label;
  if (window.SaraBackground) {
    if (state === 'listening' || state === 'waking') window.SaraBackground.setState('listening');
    else if (state === 'thinking') window.SaraBackground.setState('thinking');
    else if (state === 'speaking') window.SaraBackground.setState('speaking');
    else window.SaraBackground.clearState();
  }
}

// Real backend footer text ("Say 'sara' to wake me...", "Listening...",
// "Didn't catch that — still listening... (Xs to sleep)") drives the hint
// line under the orb, on both Home and Voice Command pages. Suppressed
// while explicitly paused so it can't stomp the clearer "Paused" hint set
// by renderAssistantState() below.
function applyFooterText(text) {
  if (!assistantActive) return;
  const hintHome = document.getElementById('orbHint');
  if (hintHome) hintHome.textContent = text;
}

function doWake() {
  wakeChime();
  applySaraStatus('waking'); // optimistic instant feedback; real push confirms/continues
  callApi('wake_now');
}
document.getElementById('chatMicBtn').addEventListener('click', doWake);
document.getElementById('orbBtn').addEventListener('click', doWake);
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
  document.getElementById('orbHint').textContent = assistantActive
    ? 'Say "Sara", tap the orb, or press Wake below.'
    : 'Press Resume when you want Sara listening again.';
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
  el.addEventListener('click', async () => {
    if (el.dataset.action === 'play_music') {
      // Play Music must be the real SMTC resume path (run_action already
      // routes this to toggle_music_playback(True) on the Python side) --
      // here we just need to wait for the real result instead of assuming
      // success, and tell the user plainly when there's nothing to play.
      el.disabled = true;
      const res = await callApi('run_action', 'play_music');
      el.disabled = false;
      if (res && res.ok) {
        mediaPlaying = true;
        const ppArt = document.getElementById('ppArt');
        if (ppArt) ppArt.classList.add('spinning');
        const ppPlayIcon = document.getElementById('ppPlayIcon');
        if (ppPlayIcon) ppPlayIcon.innerHTML = '<rect x="6" y="5" width="4" height="14"/><rect x="14" y="5" width="4" height="14"/>';
        pollMedia();
      } else {
        showToast('ti-alert-triangle', '#f87171', 'Nothing to play. Start a media app or load a track first.');
      }
      return;
    }
    callApi('run_action', el.dataset.action);
  });
});
document.querySelectorAll('[data-cmd]').forEach(el => {
  el.addEventListener('click', () => {
    gotoPage('chat');
    callApi('send_text_command', el.dataset.cmd);
    // Usage Analytics: mirror every dispatched command into the counter
    // owned by ApiAnalyticsMixin. Fire-and-forget — never blocks the
    // actual command.
    callApi('record_command_usage', el.dataset.cmd);
  });
});

// ── Quick Input modal (Weather / YouTube / Quick Note) ────────────
// Shared, reusable modal for [data-cmd-template] tiles that need a
// user-typed value before dispatching — same 3-call dispatch pattern
// as the [data-cmd] handler above (gotoPage + send_text_command +
// record_command_usage), just with {value} substituted in first.
let _quickInputTemplate = null;
document.querySelectorAll('[data-cmd-template]').forEach(el => {
  el.addEventListener('click', () => {
    _quickInputTemplate = el.dataset.cmdTemplate;
    document.getElementById('quickInputTitle').textContent = el.dataset.promptTitle || 'Quick Action';
    document.getElementById('quickInputLabel').textContent = el.dataset.promptLabel || 'Enter value';
    const field = document.getElementById('quickInputField');
    field.placeholder = el.dataset.promptPlaceholder || '';
    field.value = '';
    document.getElementById('quickInputModal').classList.add('open');
    setTimeout(() => field.focus(), 50);
  });
});
document.getElementById('quickInputCancel').addEventListener('click', () => {
  document.getElementById('quickInputModal').classList.remove('open');
});
function submitQuickInput() {
  const field = document.getElementById('quickInputField');
  const value = field.value.trim();
  if (!value || !_quickInputTemplate) return;
  const finalCommand = _quickInputTemplate.replace('{value}', value);
  document.getElementById('quickInputModal').classList.remove('open');
  gotoPage('chat');
  callApi('send_text_command', finalCommand);
  callApi('record_command_usage', finalCommand);
}
document.getElementById('quickInputSubmit').addEventListener('click', submitQuickInput);
document.getElementById('quickInputField').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); submitQuickInput(); }
});

async function doWifiToggle() {
  const res = await callApi('toggle_wifi');
  showToast(res && res.ok ? 'ti-check' : 'ti-alert-triangle', res && res.ok ? '#34d399' : '#f87171', (res && res.message) || 'Wi-Fi toggle attempted');
}
document.getElementById('qaWifi').addEventListener('click', doWifiToggle);
document.getElementById('qtWifiBtn').addEventListener('click', doWifiToggle);

// ── chat ─────────────────────────────────────────────────────────
// Shared HH:MM label for the new .msg-time element (see index.html's
// polish-layer CSS). Avatars need no JS — they're pure CSS via
// .msg::before, driven off the 'user'/'sara' class already set below.
function _msgTimeLabel() {
  return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}
function appendChatMessage(role, text) {
  const log = document.getElementById('chatLog');
  const div = document.createElement('div');
  div.className = 'msg ' + (role === 'user' ? 'user' : 'sara');
  const textSpan = document.createElement('span');
  textSpan.className = 'msg-text';
  textSpan.textContent = text;
  const timeSpan = document.createElement('span');
  timeSpan.className = 'msg-time';
  timeSpan.textContent = _msgTimeLabel();
  div.appendChild(textSpan);
  div.appendChild(timeSpan);
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
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
  // Usage Analytics: record every typed command too.
  callApi('record_command_usage', text);
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
  const fullCommand = 'search the web for ' + q;
  callApi('send_text_command', fullCommand);
  // Usage Analytics: record search commands too.
  callApi('record_command_usage', fullCommand);
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

// ══════════════════════════════════════════════════════════════════
// Routines (Automation page) — NEW
// Talks to sara/gui/app/routines_api.py's ApiRoutinesMixin, already
// wired into Api in engine.py. Steps are either:
//   { type: 'simple_action', key: <SIMPLE_ACTIONS key> }   -- picked from
//     a dropdown built from dispatch.py's SIMPLE_ACTIONS keys (below).
//   { type: 'intent'|'skill', name: <name>, args: <obj|null> } -- typed
//     manually under "Advanced…", since the full list of registered
//     intents/skills (and their argument shapes) isn't visible from the
//     files this UI was built against. The backend's own
//     _validate_routine_steps() is still the source of truth and will
//     reject an unknown/mistyped name with a clear error at Save time.
// ══════════════════════════════════════════════════════════════════

// Mirrors dispatch.py's SIMPLE_ACTIONS keys exactly -- this grouping/
// labelling is UI-only sugar. If dispatch.py's table ever changes, update
// this list to match; a stale/extra entry here just fails backend
// validation with a clear error instead of silently doing nothing.
const SIMPLE_ACTION_GROUPS = [
  { label: 'Power & Session', keys: ['lock_pc', 'sleep_system', 'hibernate_system', 'log_off', 'shutdown_system', 'restart_system', 'cancel_shutdown'] },
  { label: 'Volume', keys: ['max_volume', 'min_volume'] },
  { label: 'Brightness', keys: ['increase_brightness', 'decrease_brightness', 'max_brightness', 'min_brightness', 'get_brightness_status'] },
  { label: 'Window Management', keys: ['show_desktop', 'minimize_all_windows', 'restore_windows', 'maximize_active_window', 'minimize_active_window', 'close_active_window', 'snap_window_left', 'snap_window_right', 'switch_window', 'toggle_fullscreen'] },
  { label: 'Media', keys: ['play_pause_media', 'next_track', 'previous_track', 'stop_media'] },
  { label: 'Keyboard', keys: ['copy_selection', 'paste_clipboard', 'select_all', 'undo', 'redo'] },
  { label: 'Browser Tabs', keys: ['new_tab', 'close_tab', 'next_tab', 'prev_tab', 'reload_page'] },
  { label: 'Zoom & Scroll', keys: ['zoom_in', 'zoom_out', 'zoom_reset', 'scroll_up', 'scroll_down', 'scroll_top', 'scroll_bottom'] },
  { label: 'Network', keys: ['wifi_on', 'wifi_off', 'bluetooth_on', 'bluetooth_off'] },
  { label: 'Display', keys: ['dark_mode', 'light_mode'] },
  { label: 'Files & Notes', keys: ['empty_recycle_bin', 'read_notes', 'clear_notes'] },
  { label: 'Folders', keys: ['open_downloads', 'open_documents', 'open_desktop_folder', 'open_pictures', 'open_music', 'open_videos', 'open_this_pc', 'open_recycle_bin', 'open_file_explorer', 'open_control_panel', 'open_task_manager'] },
  { label: 'Windows Settings', keys: ['open_display_settings', 'open_sound_settings', 'open_bluetooth_settings', 'open_network_settings', 'open_update_settings', 'open_apps_settings', 'open_personalization_settings', 'open_privacy_settings', 'open_storage_settings', 'open_power_settings', 'open_about_settings', 'open_nightlight_settings', 'open_airplane_mode_settings'] },
  { label: 'System Info', keys: ['disk_usage', 'uptime', 'local_ip', 'gpu_status', 'temperature_status', 'process_list', 'list_services'] },
  { label: 'Timer', keys: ['cancel_timer'] },
];
const SIMPLE_ACTION_LABELS = {
  lock_pc: 'Lock PC', sleep_system: 'Sleep', hibernate_system: 'Hibernate', log_off: 'Log Off',
  shutdown_system: 'Shutdown', restart_system: 'Restart', cancel_shutdown: 'Cancel Shutdown',
  max_volume: 'Max Volume', min_volume: 'Mute Volume',
  increase_brightness: 'Increase Brightness', decrease_brightness: 'Decrease Brightness',
  max_brightness: 'Max Brightness', min_brightness: 'Min Brightness', get_brightness_status: 'Brightness Status',
  show_desktop: 'Show Desktop', minimize_all_windows: 'Minimize All Windows', restore_windows: 'Restore Windows',
  maximize_active_window: 'Maximize Active Window', minimize_active_window: 'Minimize Active Window',
  close_active_window: 'Close Active Window', snap_window_left: 'Snap Window Left', snap_window_right: 'Snap Window Right',
  switch_window: 'Switch Window', toggle_fullscreen: 'Toggle Fullscreen',
  play_pause_media: 'Play/Pause Media', next_track: 'Next Track', previous_track: 'Previous Track', stop_media: 'Stop Media',
  copy_selection: 'Copy', paste_clipboard: 'Paste', select_all: 'Select All', undo: 'Undo', redo: 'Redo',
  new_tab: 'New Tab', close_tab: 'Close Tab', next_tab: 'Next Tab', prev_tab: 'Previous Tab', reload_page: 'Reload Page',
  zoom_in: 'Zoom In', zoom_out: 'Zoom Out', zoom_reset: 'Reset Zoom', scroll_up: 'Scroll Up', scroll_down: 'Scroll Down',
  scroll_top: 'Scroll to Top', scroll_bottom: 'Scroll to Bottom',
  wifi_on: 'Wi-Fi On', wifi_off: 'Wi-Fi Off', bluetooth_on: 'Bluetooth On', bluetooth_off: 'Bluetooth Off',
  dark_mode: 'Dark Mode', light_mode: 'Light Mode',
  empty_recycle_bin: 'Empty Recycle Bin', read_notes: 'Read Notes', clear_notes: 'Clear Notes',
  open_downloads: 'Open Downloads', open_documents: 'Open Documents', open_desktop_folder: 'Open Desktop',
  open_pictures: 'Open Pictures', open_music: 'Open Music', open_videos: 'Open Videos', open_this_pc: 'Open This PC',
  open_recycle_bin: 'Open Recycle Bin', open_file_explorer: 'Open File Explorer', open_control_panel: 'Open Control Panel',
  open_task_manager: 'Open Task Manager',
  open_display_settings: 'Open Display Settings', open_sound_settings: 'Open Sound Settings',
  open_bluetooth_settings: 'Open Bluetooth Settings', open_network_settings: 'Open Network Settings',
  open_update_settings: 'Open Update Settings', open_apps_settings: 'Open Apps Settings',
  open_personalization_settings: 'Open Personalization Settings', open_privacy_settings: 'Open Privacy Settings',
  open_storage_settings: 'Open Storage Settings', open_power_settings: 'Open Power Settings',
  open_about_settings: 'Open About Settings', open_nightlight_settings: 'Open Night Light Settings',
  open_airplane_mode_settings: 'Open Airplane Mode Settings',
  disk_usage: 'Disk Usage', uptime: 'System Uptime', local_ip: 'Local IP Address', gpu_status: 'GPU Status',
  temperature_status: 'Temperature Status', process_list: 'Process List', list_services: 'Running Services',
  cancel_timer: 'Cancel Timer',
};

let _rtEditingName = null; // null = creating a new routine; a string = editing that routine (name locked)
let _rtSteps = [];         // in-memory step list while the modal is open

function rtStepSummary(step) {
  if (!step || typeof step !== 'object') return 'Unknown step';
  if (step.type === 'simple_action') return SIMPLE_ACTION_LABELS[step.key] || step.key;
  if (step.type === 'intent' || step.type === 'skill') return `${step.type}: ${step.name}`;
  return 'Unknown step';
}

function rtStepRowHtml(index, step) {
  const isAdvanced = step.type === 'intent' || step.type === 'skill';
  const optgroups = SIMPLE_ACTION_GROUPS.map(g => `<optgroup label="${g.label}">${
    g.keys.map(k => `<option value="${k}" ${!isAdvanced && step.key === k ? 'selected' : ''}>${SIMPLE_ACTION_LABELS[k] || k}</option>`).join('')
  }</optgroup>`).join('');
  return `
    <div class="rt-step-row" data-idx="${index}" style="background:rgba(255,255,255,0.03); border:1px solid var(--border-soft); border-radius:var(--r-sm); padding:10px; margin-bottom:8px;">
      <div style="display:flex; gap:8px; align-items:center;">
        <select class="rt-step-mode" style="flex:0 0 108px; padding:8px; border-radius:var(--r-sm); background:rgba(255,255,255,0.04); border:1px solid var(--border); font-size:12px;">
          <option value="simple_action" ${!isAdvanced ? 'selected' : ''}>Action</option>
          <option value="advanced" ${isAdvanced ? 'selected' : ''}>Advanced…</option>
        </select>
        <select class="rt-step-action" style="flex:1; padding:8px; border-radius:var(--r-sm); background:rgba(255,255,255,0.04); border:1px solid var(--border); font-size:12px; ${isAdvanced ? 'display:none;' : ''}">
          ${optgroups}
        </select>
        <div class="rt-step-advanced" style="flex:1; gap:8px; ${isAdvanced ? 'display:flex;' : 'display:none;'}">
          <select class="rt-step-adv-type" style="flex:0 0 70px; padding:8px; border-radius:var(--r-sm); background:rgba(255,255,255,0.04); border:1px solid var(--border); font-size:12px;">
            <option value="intent" ${step.type === 'intent' ? 'selected' : ''}>intent</option>
            <option value="skill" ${step.type === 'skill' ? 'selected' : ''}>skill</option>
          </select>
          <input type="text" class="rt-step-adv-name" placeholder="e.g. weather" value="${escapeHtml(step.name || '')}" style="flex:1; padding:8px; border-radius:var(--r-sm); background:rgba(255,255,255,0.04); border:1px solid var(--border); font-size:12px;">
        </div>
        <button class="rt-step-remove icon-btn" title="Remove step" style="flex-shrink:0; width:30px; height:30px;">✕</button>
      </div>
      <div style="${isAdvanced ? '' : 'display:none;'} margin-top:8px;" class="rt-step-adv-args-wrap">
        <input type="text" class="rt-step-adv-args" placeholder='Optional args as JSON, e.g. {"location":"Ajmer,IN"}' value='${escapeHtml(step._argsRaw != null ? step._argsRaw : (step.args ? JSON.stringify(step.args) : ''))}' style="width:100%; padding:8px; border-radius:var(--r-sm); background:rgba(255,255,255,0.04); border:1px solid var(--border); font-size:12px;">
      </div>
    </div>`;
}

function renderRtSteps() {
  const wrap = document.getElementById('rtStepsList');
  if (!_rtSteps.length) {
    wrap.innerHTML = `<p style="font-size:11.5px; color:var(--text-lo);">No steps yet — add at least one below.</p>`;
  } else {
    wrap.innerHTML = _rtSteps.map((s, i) => rtStepRowHtml(i, s)).join('');
  }
  wrap.querySelectorAll('.rt-step-row').forEach(row => {
    const idx = parseInt(row.dataset.idx, 10);
    row.querySelector('.rt-step-mode').addEventListener('change', (e) => {
      _rtSteps[idx] = e.target.value === 'advanced'
        ? { type: 'intent', name: '' }
        : { type: 'simple_action', key: SIMPLE_ACTION_GROUPS[0].keys[0] };
      renderRtSteps();
    });
    const actionSel = row.querySelector('.rt-step-action');
    if (actionSel) actionSel.addEventListener('change', (e) => { _rtSteps[idx] = { type: 'simple_action', key: e.target.value }; });
    const advType = row.querySelector('.rt-step-adv-type');
    const advName = row.querySelector('.rt-step-adv-name');
    const advArgs = row.querySelector('.rt-step-adv-args');
    if (advType) advType.addEventListener('change', (e) => { _rtSteps[idx].type = e.target.value; });
    if (advName) advName.addEventListener('input', (e) => { _rtSteps[idx].name = e.target.value; });
    if (advArgs) advArgs.addEventListener('input', (e) => { _rtSteps[idx]._argsRaw = e.target.value; });
    row.querySelector('.rt-step-remove').addEventListener('click', () => { _rtSteps.splice(idx, 1); renderRtSteps(); });
  });
}

function openRoutineModal(existing) {
  const errEl = document.getElementById('rtError');
  errEl.style.display = 'none';
  const nameInput = document.getElementById('rtName');
  if (existing) {
    _rtEditingName = existing.name;
    document.getElementById('routineModalTitle').textContent = 'Edit Routine';
    nameInput.value = existing.name;
    nameInput.disabled = true;
    document.getElementById('rtLabel').value = existing.label || '';
    document.getElementById('rtTriggerTime').value = existing.trigger_time || '';
    _rtSteps = (existing.steps || []).map(s => ({ ...s }));
  } else {
    _rtEditingName = null;
    document.getElementById('routineModalTitle').textContent = 'New Routine';
    nameInput.value = '';
    nameInput.disabled = false;
    document.getElementById('rtLabel').value = '';
    document.getElementById('rtTriggerTime').value = '';
    _rtSteps = [];
  }
  renderRtSteps();
  document.getElementById('routineModal').classList.add('open');
}

document.getElementById('addRoutineBtn').addEventListener('click', () => openRoutineModal(null));
document.getElementById('rtAddStepBtn').addEventListener('click', () => {
  _rtSteps.push({ type: 'simple_action', key: SIMPLE_ACTION_GROUPS[0].keys[0] });
  renderRtSteps();
});
document.getElementById('rtCancel').addEventListener('click', () => document.getElementById('routineModal').classList.remove('open'));

document.getElementById('rtSave').addEventListener('click', async () => {
  const errEl = document.getElementById('rtError');
  errEl.style.display = 'none';
  const name = document.getElementById('rtName').value.trim();
  const label = document.getElementById('rtLabel').value.trim();
  const triggerTime = document.getElementById('rtTriggerTime').value || null;

  if (!name) { errEl.textContent = 'Routine name is required.'; errEl.style.display = 'block'; return; }
  if (!_rtSteps.length) { errEl.textContent = 'Add at least one step.'; errEl.style.display = 'block'; return; }

  const steps = [];
  for (let i = 0; i < _rtSteps.length; i++) {
    const s = _rtSteps[i];
    if (s.type === 'simple_action') {
      steps.push({ type: 'simple_action', key: s.key });
    } else {
      if (!s.name || !s.name.trim()) { errEl.textContent = `Step ${i + 1}: name is required.`; errEl.style.display = 'block'; return; }
      let args = null;
      if (s._argsRaw && s._argsRaw.trim()) {
        try { args = JSON.parse(s._argsRaw); }
        catch (e) { errEl.textContent = `Step ${i + 1}: args must be valid JSON.`; errEl.style.display = 'block'; return; }
      }
      steps.push({ type: s.type, name: s.name.trim(), args });
    }
  }

  const res = await callApi('save_routine', name, label, steps, triggerTime);
  if (res && res.ok) {
    document.getElementById('routineModal').classList.remove('open');
    showToast('ti-check', '#34d399', 'Routine saved');
    loadRoutines();
  } else {
    errEl.textContent = (res && res.error) || 'Could not save routine.';
    errEl.style.display = 'block';
  }
});

async function loadRoutines() {
  const res = await callApi('list_routines');
  renderRoutinesList((res && res.data) || []);
}

function renderRoutinesList(routines) {
  const wrap = document.getElementById('routinesList');
  if (!routines.length) {
    wrap.innerHTML = `<div class="empty" style="padding:26px 6px;">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="m13 2-9 12h6l-1 8 9-12h-6l1-8Z"/></svg>
      <h4>No routines yet</h4>
      <p>Create one to chain several actions together, then run them all at once.</p>
    </div>`;
    return;
  }
  wrap.innerHTML = routines.map(r => `
    <div class="rem-item" data-routine-name="${escapeHtml(r.name)}">
      <div class="qa-icon" style="width:30px;height:30px;"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px;"><path d="m13 2-9 12h6l-1 8 9-12h-6l1-8Z"/></svg></div>
      <div class="rem-body">
        <b>${escapeHtml(r.label || r.name)}</b>
        <span>${(r.steps || []).map(rtStepSummary).map(escapeHtml).join(' → ')}${r.trigger_time ? ' · Triggers ' + to12h(r.trigger_time) : ''}</span>
      </div>
      <div style="display:flex; gap:6px; flex-shrink:0;">
        <button class="btn btn-ghost rt-test-btn" style="padding:7px 10px; font-size:11.5px;">Test</button>
        <button class="btn btn-ghost rt-edit-btn" style="padding:7px 10px; font-size:11.5px;">Edit</button>
        <button class="btn btn-ghost rt-del-btn" style="padding:7px 10px; font-size:11.5px; color:var(--red);">Delete</button>
      </div>
    </div>`).join('');

  wrap.querySelectorAll('.rt-test-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const name = btn.closest('[data-routine-name]').dataset.routineName;
      btn.disabled = true; btn.textContent = 'Running…';
      const res = await callApi('run_routine_now', name);
      showToast(res && res.ok ? 'ti-check' : 'ti-alert-triangle',
        res && res.ok ? '#34d399' : '#f87171',
        res && res.ok ? 'Routine started — check the transcript' : ((res && res.error) || 'Could not run routine'));
      setTimeout(() => { btn.disabled = false; btn.textContent = 'Test'; }, 1200);
    });
  });
  wrap.querySelectorAll('.rt-edit-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const name = btn.closest('[data-routine-name]').dataset.routineName;
      const res = await callApi('get_routine', name);
      if (res && res.ok && res.data) openRoutineModal(res.data);
      else showToast('ti-alert-triangle', '#f87171', (res && res.error) || 'Could not load routine');
    });
  });
  wrap.querySelectorAll('.rt-del-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const name = btn.closest('[data-routine-name]').dataset.routineName;
      if (!confirm(`Delete routine "${name}"?`)) return;
      const res = await callApi('delete_routine', name);
      if (res && res.ok) { showToast('ti-check', '#34d399', 'Routine deleted'); loadRoutines(); }
      else showToast('ti-alert-triangle', '#f87171', (res && res.error) || 'Could not delete routine');
    });
  });
}

// ══════════════════════════════════════════════════════════════════
// Mode Switcher (Settings page) — NEW
// Talks to sara/gui/app/modes.py's ApiModesMixin (get_modes_status /
// apply_mode), which reuses the exact same _MODE_BUNDLES/_MODE_ALIASES/
// _MODE_CONFIRMATIONS tables as the voice mode-switcher in
// sara/orchestrator/intent_handlers.py, so voice and GUI always apply
// the identical bundle and say the identical thing for the identical
// mode.
// ══════════════════════════════════════════════════════════════════
function renderActiveMode(activeMode) {
  document.querySelectorAll('.mode-opt').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.mode === activeMode);
  });
}
async function loadModesStatus() {
  const res = await callApi('get_modes_status');
  if (res && res.ok) renderActiveMode(res.active_mode);
}
document.querySelectorAll('.mode-opt').forEach(btn => {
  btn.addEventListener('click', async () => {
    const mode = btn.dataset.mode;
    btn.disabled = true;
    const res = await callApi('apply_mode', mode);
    btn.disabled = false;
    if (res && res.ok) {
      renderActiveMode(res.active_mode);
      showToast('ti-check', '#34d399', res.message || 'Mode applied');
      if (window.SaraBackground) window.SaraBackground.setPerfMode(res.active_mode === 'gaming');
    } else {
      showToast('ti-alert-triangle', '#f87171', (res && res.error) || 'Could not switch mode');
    }
  });
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
  if (window.SaraBackground) {
    const hour = new Date().getHours();
    window.SaraBackground.setTimeOfDay(hour >= 6 && hour < 18 ? 'day' : 'night');
  }
}

// ── system stats ─────────────────────────────────────────────────
async function pollStats() {
  const s = await callApi('get_system_stats');
  if (!s) return;
  document.getElementById('statNetDown').textContent = `↓ ${s.net_down_mbps} Mbps`;
  document.getElementById('statNetUp').textContent = `↑ ${s.net_up_mbps} Mbps`;
}

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
  streak: '🔥 Streak milestone',
  meeting: '📅 Meeting',
  routine: '⚙️ Routine'
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
  document.getElementById('proactiveMeeting').textContent = byTrigger.meeting || 0;
  document.getElementById('proactiveRoutine').textContent = byTrigger.routine || 0;

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

// ── calendar status (Settings page) ──────────────────────────────
// Backed by sara/tools/calendar.py's get_calendar_status() and
// get_today_events(), exposed via calendar_api.py's ApiCalendarMixin
// (already wired into Api in engine.py). Read-only display, no
// connect/OAuth UI here — just surfaces what the backend already knows.
function _fmtCalEventTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return ''; // all-day events use a date-only string; skip a time label for those
  return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}
async function loadCalendarStatus() {
  const statusEl = document.getElementById('calendarStatus');
  const listEl = document.getElementById('calendarEventsList');
  if (!statusEl || !listEl) return;

  const res = await callApi('get_calendar_status');
  const connected = !!(res && res.ok && res.data && res.data.connected);

  if (!connected) {
    statusEl.textContent = 'Not connected — add credentials.json to the project root, then ask Sara about your calendar to trigger one-time Google sign-in.';
    listEl.style.display = 'none';
    listEl.innerHTML = '';
    return;
  }

  const email = (res.data && res.data.email) || 'your Google account';
  statusEl.textContent = `Connected as ${email}`;

  const evRes = await callApi('get_today_calendar_events');
  const events = (evRes && evRes.ok && Array.isArray(evRes.data)) ? evRes.data : [];
  listEl.style.display = 'block';
  if (!events.length) {
    listEl.textContent = 'No events today.';
    return;
  }
  listEl.innerHTML = events.map(ev => {
    const time = _fmtCalEventTime(ev.start);
    const title = escapeHtml(ev.summary || '(No title)');
    return `<div style="margin-bottom:4px;">${time ? '<b>' + time + '</b> · ' : ''}${title}</div>`;
  }).join('');
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
        <div class="toggle ${s.enabled ? 'on' : ''}" data-skill-name="${escapeHtml(s.name)}" role="switch" tabindex="0" aria-checked="${s.enabled ? 'true' : 'false'}"></div>
      </div>`;
  }).join('');

  wrap.querySelectorAll('[data-skill-name]').forEach(el => {
    el.addEventListener('click', async () => {
      const on = !el.classList.contains('on');
      el.classList.toggle('on', on);
      el.setAttribute('aria-checked', on ? 'true' : 'false');
      toggleSound(on);
      await callApi('set_skill_enabled', el.dataset.skillName, on);
      const note = document.getElementById('skillRestartNote');
      if (note) note.classList.add('show');
    });
    addToggleKeyboardSupport(el);
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
let _pollMediaInFlight = false;
let _ppPendingCmd = false;

// ── unified player state (single source of truth for interpolation) ──
const playerState = {
  active: false, trackId: null, duration: 0,
  basePos: 0, baseTs: 0, playbackRate: 1.0, playing: false,
};
let _ppRafId = null;

function _ppClampPos(pos, dur) {
  if (!isFinite(pos) || pos !== pos) pos = 0; // NaN guard
  pos = Math.max(0, pos);
  if (dur > 0 && pos > dur) pos = dur;
  return pos;
}

function _ppLivePosition() {
  if (!playerState.active) return 0;
  if (!playerState.playing) return playerState.basePos;
  const elapsed = (Date.now() / 1000) - playerState.baseTs;
  return _ppClampPos(playerState.basePos + elapsed * (playerState.playbackRate || 1.0), playerState.duration);
}

function _ppSetFillVar(seekEl, value) {
  const max = parseFloat(seekEl.max) || 1;
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  seekEl.style.setProperty('--pp-fill', pct + '%');
}

function _ppRenderTick() {
  if (!playerState.active || seekDragging) { _ppRafId = null; return; }
  _ppRafId = requestAnimationFrame(_ppRenderTick);
  const pos = _ppLivePosition();
  const seekEl = document.getElementById('ppSeek');
  seekEl.value = pos;
  _ppSetFillVar(seekEl, pos);
  document.getElementById('ppCurTime').textContent = fmtTime(pos);
}
function _ppEnsureRenderLoop() {
  if (_ppRafId === null) _ppRafId = requestAnimationFrame(_ppRenderTick);
}
_ppRafId = requestAnimationFrame(_ppRenderTick);

// Applies a state class to the player card without flicker -- only
// touches the DOM when the state actually changed.
let _ppCardState = null;
function _ppSetCardState(state) {
  if (state === _ppCardState) return;
  const card = document.getElementById('playerCard');
  if (_ppCardState) card.classList.remove('pp-state-' + _ppCardState);
  card.classList.add('pp-state-' + state);
  _ppCardState = state;
}

const _PP_REPEAT_TITLE = { none: 'Repeat off', track: 'Repeat one', list: 'Repeat all' };

async function pollMedia() {
  // Guard against overlapping calls: get_media_status() runs a blocking
  // asyncio.run() with WinRT/artwork work on the Python side, which can
  // take longer than the 2s poll interval. Skip this tick if the
  // previous one hasn't returned yet; the next interval tick retries.
  if (_pollMediaInFlight) return;
  _pollMediaInFlight = true;
  try {
  const s = await callApi('get_media_status');
  const titleWrap = document.getElementById('ppTitleWrap');
  const ppArt = document.getElementById('ppArt');
  const ppSeekEl = document.getElementById('ppSeek');

  if (!s || !s.ok) {
    // Backend unavailable (winsdk missing, unexpected error) is a
    // distinct state from "no media session found" -- don't call it
    // "Nothing playing", which reads like the user just isn't playing
    // anything.
    _ppSetCardState('unavailable');
    document.getElementById('ppTitle').textContent = 'Media controls unavailable';
    document.getElementById('ppArtist').textContent = (s && s.error) || 'Could not reach the media backend.';
    document.getElementById('ppApp').textContent = '';
    ppArt.classList.remove('spinning', 'has-art');
    ppArt.style.backgroundImage = '';
    document.getElementById('ppPlayIcon').innerHTML = '<path d="M8 5v14l11-7z"/>';
    if (!seekDragging) { ppSeekEl.value = 0; document.getElementById('ppCurTime').textContent = '0:00'; document.getElementById('ppDurTime').textContent = '0:00'; }
    ppSeekEl.disabled = true;
    mediaPlaying = false;
    playerState.active = false; playerState.trackId = null; playerState.playing = false;
    playerState.basePos = 0; playerState.duration = 0;
    return;
  }
  if (!s.active) {
    _ppSetCardState('nomedia');
    document.getElementById('ppTitle').textContent = 'No media is currently playing';
    document.getElementById('ppArtist').textContent = 'Play something to control it here';
    document.getElementById('ppApp').textContent = '';
    ppArt.classList.remove('spinning', 'has-art');
    ppArt.style.backgroundImage = '';
    document.getElementById('ppPlayIcon').innerHTML = '<path d="M8 5v14l11-7z"/>';
    if (!seekDragging) { ppSeekEl.value = 0; document.getElementById('ppCurTime').textContent = '0:00'; document.getElementById('ppDurTime').textContent = '0:00'; }
    ppSeekEl.disabled = true;
    mediaPlaying = false;
    playerState.active = false; playerState.trackId = null; playerState.playing = false;
    playerState.basePos = 0; playerState.duration = 0;
    return;
  }

  const newTrackId = s.track_id || `${s.title || ''}|${s.artist || ''}`;
  const trackChanged = newTrackId !== playerState.trackId;
  const isChangingStatus = s.status === 'changing' || s.status === 'opened';

  if (isChangingStatus) {
    _ppSetCardState('changing');
  } else {
    _ppSetCardState(s.status === 'playing' ? 'playing' : 'paused');
  }

  // Only rewrite the title/artist/album/app text when the track identity
  // actually changed, or on first activation -- avoids re-triggering the
  // marquee/opacity transition on every ~poll tick for an unchanged track.
  if (trackChanged || !playerState.active) {
    document.getElementById('ppTitle').textContent = s.title || 'Unknown track';
    document.getElementById('ppArtist').textContent = s.artist + (s.album ? ` — ${s.album}` : '') || '';
    document.getElementById('ppApp').textContent = s.app || '';
    titleWrap.classList.toggle('scroll', (s.title || '').length > 26);
    if (s.art) {
      ppArt.style.backgroundImage = `url("${s.art}")`;
      ppArt.classList.add('has-art');
    } else {
      ppArt.style.backgroundImage = '';
      ppArt.classList.remove('has-art');
    }
  }

  mediaPlaying = s.status === 'playing';
  ppArt.classList.toggle('spinning', mediaPlaying);
  document.getElementById('ppPlayIcon').innerHTML = mediaPlaying ? '<rect x="6" y="5" width="4" height="14"/><rect x="14" y="5" width="4" height="14"/>' : '<path d="M8 5v14l11-7z"/>';

  const ppShuffle = document.getElementById('ppShuffle');
  const ppRepeat = document.getElementById('ppRepeat');
  const caps = s.caps || {};
  const shuffleOn = !!s.shuffle;
  ppShuffle.classList.toggle('active', shuffleOn);
  ppShuffle.setAttribute('aria-pressed', shuffleOn ? 'true' : 'false');
  ppShuffle.disabled = s.shuffle_supported === false || caps.can_shuffle === false;
  ppShuffle.setAttribute('aria-disabled', ppShuffle.disabled ? 'true' : 'false');

  const repeatOn = !!(s.repeat && s.repeat !== 'none');
  ppRepeat.classList.toggle('active', repeatOn);
  ppRepeat.classList.toggle('repeat-track', s.repeat === 'track');
  ppRepeat.setAttribute('aria-pressed', repeatOn ? 'true' : 'false');
  ppRepeat.title = _PP_REPEAT_TITLE[s.repeat] || 'Repeat off';
  ppRepeat.disabled = caps.can_repeat === false;
  ppRepeat.setAttribute('aria-disabled', ppRepeat.disabled ? 'true' : 'false');

  document.getElementById('ppNext').disabled = caps.can_next === false;
  document.getElementById('ppPrev').disabled = caps.can_prev === false;
  ppSeekEl.disabled = caps.can_seek === false;

  const dur = _ppClampPos(s.duration_sec || 0, Infinity);
  const pos = _ppClampPos(s.position_sec || 0, dur);
  const rate = (typeof s.playback_rate === 'number' && isFinite(s.playback_rate) && s.playback_rate > 0) ? s.playback_rate : 1.0;

  playerState.trackId = newTrackId;
  playerState.active = true;
  _ppEnsureRenderLoop();
  playerState.duration = dur;
  playerState.basePos = pos;
  playerState.baseTs = (typeof s.timeline_updated_at === 'number') ? s.timeline_updated_at : (Date.now() / 1000);
  playerState.playbackRate = rate;
  playerState.playing = mediaPlaying;

  if (!seekDragging) {
    ppSeekEl.max = Math.max(dur, 1);
    ppSeekEl.value = pos;
    _ppSetFillVar(ppSeekEl, pos);
    document.getElementById('ppCurTime').textContent = fmtTime(pos);
    document.getElementById('ppDurTime').textContent = fmtTime(dur);
  }
  } finally {
    _pollMediaInFlight = false;
  }
}
document.getElementById('ppPlayPause').addEventListener('click', async (e) => {
  const btn = e.currentTarget;
  if (_ppPendingCmd) return; // prevent command spam while a toggle is in flight
  _ppPendingCmd = true;
  btn.classList.add('pp-cmd-pending');
  const desired = !mediaPlaying;
  btn.disabled = true;
  const res = await callApi('toggle_music_playback', desired);
  btn.disabled = false;
  btn.classList.remove('pp-cmd-pending');
  _ppPendingCmd = false;
  if (res && res.ok) {
    mediaPlaying = desired;
    playerState.playing = desired;
    playerState.basePos = _ppLivePosition();
    playerState.baseTs = Date.now() / 1000;
    document.getElementById('ppArt').classList.toggle('spinning', mediaPlaying);
    document.getElementById('ppPlayIcon').innerHTML = mediaPlaying ? '<rect x="6" y="5" width="4" height="14"/><rect x="14" y="5" width="4" height="14"/>' : '<path d="M8 5v14l11-7z"/>';
  } else {
    showToast('ti-alert-triangle', '#f87171', 'Nothing to play. Start a media app or load a track first.');
    pollMedia();
  }
});
document.getElementById('ppStop').addEventListener('click', async () => {
  await callApi('stop_music');
  mediaPlaying = false;
  playerState.playing = false;
  document.getElementById('ppArt').classList.remove('spinning');
  pollMedia();
});
document.getElementById('ppNext').addEventListener('click', async (e) => {
  const btn = e.currentTarget;
  if (btn.disabled || _ppPendingCmd) return;
  _ppPendingCmd = true;
  btn.disabled = true;
  await callApi('skip_next_track');
  btn.disabled = false;
  _ppPendingCmd = false;
  pollMedia();
});
document.getElementById('ppPrev').addEventListener('click', async (e) => {
  const btn = e.currentTarget;
  if (btn.disabled || _ppPendingCmd) return;
  _ppPendingCmd = true;
  btn.disabled = true;
  await callApi('skip_previous_track');
  btn.disabled = false;
  _ppPendingCmd = false;
  pollMedia();
});
document.getElementById('ppShuffle').addEventListener('click', async (e) => {
  const btn = e.currentTarget;
  if (btn.disabled || _ppPendingCmd) return;
  const prevOn = btn.classList.contains('active');
  const requested = !prevOn;
  _ppPendingCmd = true;
  btn.disabled = true;
  btn.classList.add('pp-cmd-pending');
  const res = await callApi('toggle_shuffle', requested);
  btn.disabled = false;
  btn.classList.remove('pp-cmd-pending');
  _ppPendingCmd = false;
  // UI only ever reflects CONFIRMED backend state, never the optimistic
  // click -- on failure the previous (actual) state is preserved.
  let confirmed = prevOn;
  if (res && res.ok && res.shuffle !== null && res.shuffle !== undefined) {
    confirmed = !!res.shuffle;
  } else {
    showToast('ti-alert-triangle', '#f87171', 'Shuffle could not be changed.');
  }
  btn.classList.toggle('active', confirmed);
  btn.setAttribute('aria-pressed', confirmed ? 'true' : 'false');
});
document.getElementById('ppRepeat').addEventListener('click', async (e) => {
  const btn = e.currentTarget;
  if (btn.disabled || _ppPendingCmd) return;
  _ppPendingCmd = true;
  btn.disabled = true;
  btn.classList.add('pp-cmd-pending');
  const res = await callApi('cycle_repeat_mode');
  btn.disabled = false;
  btn.classList.remove('pp-cmd-pending');
  _ppPendingCmd = false;
  if (!res || !res.ok) {
    showToast('ti-alert-triangle', '#f87171', 'Repeat could not be changed.');
  }
  pollMedia();
});

const ppSeek = document.getElementById('ppSeek');
let _ppSeekPreview = 0;
ppSeek.addEventListener('input', () => {
  seekDragging = true;
  _ppSeekPreview = parseFloat(ppSeek.value) || 0;
  _ppSetFillVar(ppSeek, _ppSeekPreview);
  document.getElementById('ppCurTime').textContent = fmtTime(_ppSeekPreview);
});
ppSeek.addEventListener('change', async () => {
  const target = Math.max(0, Math.min(_ppSeekPreview, playerState.duration || parseFloat(ppSeek.max) || 0));
  const res = await callApi('seek_media', target);
  if (res && res.ok) {
    playerState.basePos = target;
    playerState.baseTs = Date.now() / 1000;
  }
  seekDragging = false;
  pollMedia(); // resynchronize against real backend state after the seek
});

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
async function updateSleepLabel() {
  const remaining = Math.max(0, Math.round((sleepTimerEndsAt - Date.now()) / 1000));
  if (remaining <= 0) {
    clearInterval(sleepTimerInterval); sleepTimerInterval = null;
    sleepBtn.classList.remove('active');
    document.getElementById('sleepLabel').textContent = 'Sleep Timer';
    sleepMenu.querySelectorAll('button').forEach(b => b.classList.remove('active'));
    await callApi('stop_music');
    // Resync from the confirmed backend state rather than assuming stop
    // succeeded -- the card should never claim playback stopped ahead of
    // the actual result.
    mediaPlaying = false;
    playerState.playing = false;
    document.getElementById('ppArt').classList.remove('spinning');
    await pollMedia();
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
    el.setAttribute('aria-checked', on ? 'true' : 'false');
    callApi('update_setting', el.dataset.setting, on);
    toggleSound(on);
  });
});

// -- keyboard accessibility for every settings toggle (role=switch) --
// Reuses the existing click handlers via el.click() instead of duplicating
// the toggle logic, so there is exactly one source of truth for what a
// toggle click/activation actually does. Safe to call on the static
// toggles once at startup, and again on any dynamically re-rendered
// (freshly created) toggle elements -- it never runs twice on the same
// DOM node.
function addToggleKeyboardSupport(el) {
  el.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar') {
      e.preventDefault();
      el.click();
    }
  });
}
document.querySelectorAll('.toggle').forEach(addToggleKeyboardSupport);

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
    document.getElementById('settingMute').setAttribute('aria-checked', 'true');
  }
  if (data.focus_mode === '1') {
    focusOn = true;
    document.getElementById('focusBtn').classList.add('active');
    document.getElementById('settingFocus').classList.add('on');
    document.getElementById('settingFocus').setAttribute('aria-checked', 'true');
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
    document.getElementById('settingSounds').setAttribute('aria-checked', soundsOn ? 'true' : 'false');
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
    'setting:proactive_streak': 'settingProactiveStreak',
    'setting:proactive_meetings': 'settingProactiveMeetings',
    'setting:proactive_routines': 'settingProactiveRoutines'
  };
  Object.entries(toggleMap).forEach(([key, id]) => {
    if (data[key] != null) {
      const el = document.getElementById(id);
      if (el) {
        const on = data[key] === '1';
        el.classList.toggle('on', on);
        el.setAttribute('aria-checked', on ? 'true' : 'false');
      }
    }
  });
}
async function loadUISettings() {
  const res = await callApi('get_ui_settings');
  if (res && res.ok) applyUISettings(res.data);
}

// ── boot ─────────────────────────────────────────────────────────
// FIX (duplicate-boot bug): boot() was being called twice on startup --
// once by the 'pywebviewready' listener below, and unconditionally
// again by the setTimeout(boot, 300) safety net a few lines down, even
// when 'pywebviewready' had already fired and boot() had already run.
// This doubled every startup API call AND, worse, registered every
// setInterval() (status bar, stats, media polling, weather refresh,
// etc.) twice -- so the app silently polled the backend at 2x the
// intended rate for its entire runtime, getting worse if boot() were
// ever triggered a third time by some other path. _booted guards
// against running the body more than once no matter how many times or
// from how many places boot() itself gets called.
let _booted = false;
let _bootedWithRealBridge = false;
function boot() {
  const bridgeReady = !!(window.pywebview && window.pywebview.api);
  // Once we've booted with the real bridge, nothing more to do.
  if (_bootedWithRealBridge) return;
  // Already booted via the mock/fallback path and the real bridge still
  // isn't here -- nothing changed, don't re-run yet.
  if (_booted && !bridgeReady) return;
  const firstBoot = !_booted;
  _booted = true;
  if (bridgeReady) _bootedWithRealBridge = true;
  console.log('[boot] pywebview:', !!window.pywebview, 'api:', !!(window.pywebview && window.pywebview.api), 'methods:', window.pywebview && window.pywebview.api ? Object.keys(window.pywebview.api) : []);
  loadAssistantState();
  loadUISettings();
  loadDisplayName();
  loadReminders();
  loadNotes();
  loadRoutines();
  loadModesStatus();
  loadWeather();
  loadProactiveStats();
  loadNotesStatus();
  loadCalendarStatus();
  loadSkills();
  loadMemoryAndShareStats();
  loadAnalyticsDashboard();
  pollStats();
  pollMedia();
  refreshStatusBar();
  if (firstBoot) {
    initSetupWizard();
    // H4 fix: these intervals used to do their full work even while the
    // window was minimized/hidden, wasting WinRT/psutil/network calls for
    // no visible benefit. Each callback is now gated on document.hidden
    // instead -- same 8 intervals, same frequencies while visible, no new
    // timers, and pollMedia's existing _pollMediaInFlight overlap guard is
    // untouched (it still runs inside pollMedia() itself).
    setInterval(() => { if (!document.hidden) refreshStatusBar(); }, 2000);
    setInterval(() => { if (!document.hidden) pollStats(); }, 3500);
    setInterval(() => { if (!document.hidden) pollMedia(); }, 3000);
    setInterval(() => { if (!document.hidden) loadWeather(); }, 15 * 60 * 1000);
    setInterval(() => { if (!document.hidden) loadProactiveStats(); }, 60 * 1000);
    setInterval(() => { if (!document.hidden) loadNotesStatus(); }, 60 * 1000);
    setInterval(() => { if (!document.hidden) loadCalendarStatus(); }, 5 * 60 * 1000);
    setInterval(() => { if (!document.hidden) loadMemoryAndShareStats(); }, 5 * 60 * 1000);
    setInterval(() => { if (!document.hidden) loadAnalyticsDashboard(); }, 5 * 60 * 1000);
    setInterval(() => { if (!document.hidden) loadReminders(); }, 60 * 1000);
    setInterval(() => { if (!document.hidden) loadNotes(); }, 60 * 1000);
    // Refresh once, immediately, when the window/tab becomes visible again
    // instead of waiting for the next long interval to roll around --
    // registered once here (firstBoot-guarded, same as the intervals above)
    // so this never double-attaches.
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) return;
      refreshStatusBar();
      pollStats();
      pollMedia();
      loadProactiveStats();
      loadNotesStatus();
      loadCalendarStatus();
      loadReminders();
      loadNotes();
    });
  }
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
// Safe to call unconditionally even if 'pywebviewready' already fired
// and ran boot(): the _booted guard inside boot() makes this a no-op
// in that case, instead of the double-init this used to cause.
setTimeout(boot, 300);

/* ══════════════════════════════════════════════════════════════════
   SARA CARD — swipe-to-delete, long-press context menu, drag-to-
   reorder, success-tick, empty-state illustrations. Applies to rows
   rendered into #reminderList, #notesList, #routinesList,
   #automationList, #sideReminders. Purely additive: attaches to any
   newly-added direct child via MutationObserver, alongside (not
   instead of) the existing "sara-list-stagger-item" observer.
   ══════════════════════════════════════════════════════════════════ */
(function () {

  /* ---- swipe-to-delete ---- */
  var saraCardSwipeState = null;
  var SARA_CARD_SWIPE_THRESHOLD = 90;

  function saraCardDissolveRow(row, cb) {
    row.style.transition = 'opacity .25s ease, max-height .25s ease, margin .25s ease, padding .25s ease';
    row.style.maxHeight = row.scrollHeight + 'px';
    row.style.overflow = 'hidden';
    requestAnimationFrame(function () {
      row.style.opacity = '0';
      row.style.maxHeight = '0px';
      row.style.marginTop = '0px';
      row.style.marginBottom = '0px';
      row.style.paddingTop = '0px';
      row.style.paddingBottom = '0px';
    });
    setTimeout(function () { if (cb) cb(); }, 260);
  }

  function saraCardInitSwipe(row) {
    if (row.dataset.saraSwipeInit) return;
    row.dataset.saraSwipeInit = '1';
    if (getComputedStyle(row).position === 'static') row.style.position = 'relative';
    if (!row.style.overflow) row.style.overflow = 'hidden';

    var bg = document.createElement('div');
    bg.className = 'sara-card-swipe-bg';
    bg.innerHTML = '<span>Delete</span>';
    row.insertBefore(bg, row.firstChild);

    var wrap = document.createElement('div');
    wrap.className = 'sara-card-swipe-content';
    var existing = Array.prototype.slice.call(row.children).filter(function (c) { return c !== bg; });
    existing.forEach(function (c) { wrap.appendChild(c); });
    row.appendChild(wrap);

    function begin(clientX) {
      saraCardSwipeState = { row: row, wrap: wrap, bg: bg, startX: clientX, curX: 0 };
      wrap.style.transition = 'none';
    }
    row.addEventListener('mousedown', function (e) { begin(e.clientX); });
    row.addEventListener('touchstart', function (e) { if (e.touches[0]) begin(e.touches[0].clientX); }, { passive: true });
  }

  function saraCardSwipeMove(clientX) {
    var s = saraCardSwipeState;
    if (!s) return;
    var dx = clientX - s.startX;
    if (dx > 0) dx = 0;
    s.curX = dx;
    s.wrap.style.transform = 'translateX(' + dx + 'px)';
    s.bg.style.opacity = String(Math.min(1, Math.abs(dx) / SARA_CARD_SWIPE_THRESHOLD));
  }

  function saraCardSwipeEnd() {
    var s = saraCardSwipeState;
    if (!s) return;
    saraCardSwipeState = null;
    s.wrap.style.transition = '';
    if (Math.abs(s.curX) > SARA_CARD_SWIPE_THRESHOLD) {
      saraCardDissolveRow(s.row, function () {
        var delBtn = s.row.querySelector('[data-del], .rem-del, .rt-del-btn');
        if (delBtn) {
          delBtn.click();
        } else {
          s.row.dispatchEvent(new CustomEvent('sara:card-delete', { detail: { element: s.row }, bubbles: true }));
        }
      });
    } else {
      s.wrap.style.transform = '';
      s.bg.style.opacity = '0';
    }
  }

  document.addEventListener('mousemove', function (e) { saraCardSwipeMove(e.clientX); });
  document.addEventListener('touchmove', function (e) {
    if (saraCardSwipeState && e.touches && e.touches[0]) saraCardSwipeMove(e.touches[0].clientX);
  }, { passive: true });
  document.addEventListener('mouseup', saraCardSwipeEnd);
  document.addEventListener('touchend', saraCardSwipeEnd);

  /* ---- long-press / right-click context menu ---- */
  function saraCardInitContextMenu(row) {
    if (row.dataset.saraCtxInit) return;
    row.dataset.saraCtxInit = '1';
    var timer = null;

    row.addEventListener('touchstart', function (e) {
      var t = e.touches[0];
      if (!t) return;
      var x = t.clientX, y = t.clientY;
      timer = setTimeout(function () { saraCardShowContextMenu(row, x, y); }, 450);
    }, { passive: true });
    row.addEventListener('touchmove', function () { clearTimeout(timer); });
    row.addEventListener('touchend', function () { clearTimeout(timer); });
    row.addEventListener('contextmenu', function (e) {
      e.preventDefault();
      saraCardShowContextMenu(row, e.clientX, e.clientY);
    });
  }

  function saraCardShowContextMenu(row, x, y) {
    var old = document.querySelector('.sara-card-ctx-menu');
    if (old) old.remove();

    var menu = document.createElement('div');
    menu.className = 'sara-card-ctx-menu';
    menu.innerHTML =
      '<button type="button" data-act="edit"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg><span>Edit</span></button>' +
      '<button type="button" data-act="delete"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/></svg><span>Delete</span></button>' +
      '<button type="button" data-act="pin"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 2l2 7h7l-5.5 4.5L17 21l-5-4-5 4 1.5-7.5L3 9h7Z"/></svg><span>Pin</span></button>';
    document.body.appendChild(menu);

    var mw = menu.offsetWidth, mh = menu.offsetHeight;
    var px = Math.min(x, window.innerWidth - mw - 8);
    var py = Math.min(y, window.innerHeight - mh - 8);
    menu.style.left = Math.max(4, px) + 'px';
    menu.style.top = Math.max(4, py) + 'px';
    requestAnimationFrame(function () { menu.classList.add('sara-card-ctx-open'); });

    function closeMenu() {
      menu.classList.remove('sara-card-ctx-open');
      setTimeout(function () { if (menu.parentNode) menu.remove(); }, 150);
      document.removeEventListener('mousedown', outsideClick, true);
    }
    function outsideClick(e) { if (!menu.contains(e.target)) closeMenu(); }
    setTimeout(function () { document.addEventListener('mousedown', outsideClick, true); }, 0);

    menu.querySelectorAll('button').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var act = btn.dataset.act;
        var evName = act === 'edit' ? 'sara:card-edit' : act === 'delete' ? 'sara:card-delete' : 'sara:card-pin';
        row.dispatchEvent(new CustomEvent(evName, { detail: { element: row }, bubbles: true }));
        closeMenu();
      });
    });
  }

  /* ---- drag-to-reorder (reminderList / routinesList only) ---- */
  function saraCardEmitReorder(container) {
    var rows = Array.prototype.slice.call(container.children).filter(function (r) { return !r.classList.contains('empty'); });
    var order = rows.map(function (r, i) {
      return r.getAttribute('data-id') || r.getAttribute('data-routine-name') || String(i);
    });
    container.dispatchEvent(new CustomEvent('sara:list-reordered', { detail: { containerId: container.id, newOrder: order }, bubbles: true }));
  }

  function saraCardInitDragHandle(row, container) {
    if (row.dataset.saraDragInit) return;
    row.dataset.saraDragInit = '1';

    var handle = document.createElement('div');
    handle.className = 'sara-card-drag-handle';
    handle.setAttribute('aria-label', 'Drag to reorder');
    handle.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="9" cy="6" r="1.4"/><circle cx="15" cy="6" r="1.4"/><circle cx="9" cy="12" r="1.4"/><circle cx="15" cy="12" r="1.4"/><circle cx="9" cy="18" r="1.4"/><circle cx="15" cy="18" r="1.4"/></svg>';
    row.insertBefore(handle, row.firstChild);

    handle.addEventListener('mousedown', function () { row.setAttribute('draggable', 'true'); });
    handle.addEventListener('touchstart', function () { row.setAttribute('draggable', 'true'); }, { passive: true });
    document.addEventListener('mouseup', function () {
      if (!row.classList.contains('sara-card-dragging')) row.removeAttribute('draggable');
    });

    row.addEventListener('dragstart', function (e) {
      row.classList.add('sara-card-dragging');
      try { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', ''); } catch (err) { }
    });
    row.addEventListener('dragend', function () {
      row.classList.remove('sara-card-dragging');
      row.removeAttribute('draggable');
      saraCardEmitReorder(container);
    });
    row.addEventListener('dragover', function (e) {
      e.preventDefault();
      var dragging = container.querySelector('.sara-card-dragging');
      if (!dragging || dragging === row) return;
      var rect = row.getBoundingClientRect();
      var after = (e.clientY - rect.top) > rect.height / 2;
      container.insertBefore(dragging, after ? row.nextSibling : row);
    });
  }

  /* ---- success checkmark morph (extends the existing pattern) ---- */
  window.saraCardSuccessTick = function (rowElement) {
    if (!rowElement) return;
    if (getComputedStyle(rowElement).position === 'static') rowElement.style.position = 'relative';
    var tick = document.createElement('div');
    tick.className = 'sara-card-success-tick';
    tick.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" aria-hidden="true"><path d="M4 12l5 5L20 6"/></svg>';
    rowElement.appendChild(tick);
    requestAnimationFrame(function () { tick.classList.add('sara-card-success-tick-in'); });
    setTimeout(function () { tick.classList.add('sara-card-success-tick-out'); }, 700);
    setTimeout(function () { if (tick.parentNode) tick.remove(); }, 1000);
  };

  /* ---- empty-state illustrations ---- */
  function saraCardEnhanceEmptyStates(container) {
    if (!container) return;
    container.querySelectorAll('.empty').forEach(function (el) {
      if (el.classList.contains('sara-card-empty-enhanced')) return;
      el.classList.add('sara-card-empty-enhanced');
      var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      svg.setAttribute('class', 'sara-card-empty-svg');
      svg.setAttribute('viewBox', '0 0 120 90');
      svg.setAttribute('aria-hidden', 'true');
      svg.innerHTML =
        '<rect x="8" y="8" width="104" height="74" rx="14" fill="none" stroke="currentColor" stroke-width="2" stroke-dasharray="6 6" opacity="0.35"/>' +
        '<circle cx="60" cy="45" r="16" fill="url(#saraCardEmptyGrad)"><animate attributeName="r" values="16;18;16" dur="2.6s" repeatCount="indefinite"/></circle>' +
        '<defs><radialGradient id="saraCardEmptyGrad" cx="35%" cy="30%" r="70%">' +
        '<stop offset="0%" stop-color="#ec4899"/><stop offset="55%" stop-color="#8b5cf6"/><stop offset="100%" stop-color="#3b82f6"/></radialGradient></defs>';
      el.insertBefore(svg, el.firstChild);
    });
  }

  /* ---- master wiring: observe the 5 containers ---- */
  var SARA_CARD_CONTAINER_IDS = ['reminderList', 'notesList', 'routinesList', 'automationList', 'sideReminders'];
  var SARA_CARD_DRAG_CONTAINER_IDS = ['reminderList', 'routinesList'];

  function saraCardProcessContainer(container) {
    if (!container) return;
    saraCardEnhanceEmptyStates(container);
    Array.prototype.forEach.call(container.children, function (row) {
      if (!(row instanceof HTMLElement) || row.classList.contains('empty')) return;
      saraCardInitSwipe(row);
      saraCardInitContextMenu(row);
      if (SARA_CARD_DRAG_CONTAINER_IDS.indexOf(container.id) !== -1) {
        saraCardInitDragHandle(row, container);
      }
    });
  }

  SARA_CARD_CONTAINER_IDS.forEach(function (id) {
    var container = document.getElementById(id);
    if (!container) return;
    saraCardProcessContainer(container);
    var obs = new MutationObserver(function () { saraCardProcessContainer(container); });
    obs.observe(container, { childList: true });
  });
})();

/* ══════════════════════════════════════════════════════════════════
   SARA MODAL — toast stacking, hold-to-confirm, inline error banner,
   modal "genie" close, achievement micro-toast. Applies to #toastStack
   and .modal-backdrop elements.
   ══════════════════════════════════════════════════════════════════ */

/* ---- toast stacking / collapse (3+ toasts) ---- */
(function () {
  var stack = document.getElementById('toastStack');
  if (!stack) return;
  var expanded = false;

  function evaluate() {
    var toasts = Array.prototype.slice.call(stack.children).filter(function (t) {
      return t.classList.contains('toast') && !t.classList.contains('sara-modal-toast-chip');
    });
    var chip = stack.querySelector('.sara-modal-toast-chip');

    if (toasts.length >= 3 && !expanded) {
      stack.classList.add('sara-modal-toast-stacked');
      toasts.forEach(function (t, i) {
        var fromEnd = toasts.length - 1 - i;
        t.style.setProperty('--sara-stack-i', fromEnd);
        t.classList.toggle('sara-modal-toast-collapsed', fromEnd >= 2);
      });
      if (!chip) {
        chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'sara-modal-toast-chip toast';
        stack.insertBefore(chip, stack.firstChild);
        chip.addEventListener('click', function () {
          expanded = true;
          stack.classList.remove('sara-modal-toast-stacked');
          Array.prototype.slice.call(stack.children).forEach(function (t) { t.classList.remove('sara-modal-toast-collapsed'); });
          chip.remove();
          setTimeout(function () { expanded = false; evaluate(); }, 4000);
        });
      }
      chip.textContent = '+' + (toasts.length - 2) + ' more';
    } else if (toasts.length < 3) {
      stack.classList.remove('sara-modal-toast-stacked');
      toasts.forEach(function (t) { t.classList.remove('sara-modal-toast-collapsed'); });
      if (chip) chip.remove();
    }
  }

  var obs = new MutationObserver(evaluate);
  obs.observe(stack, { childList: true });
})();

/* ---- hold-to-confirm destructive button (class: sara-modal-hold-confirm) ---- */
(function () {
  var HOLD_MS = 900;
  var CIRCUMFERENCE = 106.8;
  var activeState = null;

  function ensureRing(btn) {
    var ring = btn.querySelector('.sara-modal-hold-ring');
    if (ring) return ring;
    if (getComputedStyle(btn).position === 'static') btn.style.position = 'relative';
    ring = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    ring.setAttribute('class', 'sara-modal-hold-ring');
    ring.setAttribute('viewBox', '0 0 40 40');
    ring.innerHTML = '<circle cx="20" cy="20" r="17" fill="none" stroke="rgba(255,255,255,0.9)" stroke-width="3" stroke-linecap="round" stroke-dasharray="' + CIRCUMFERENCE + '" stroke-dashoffset="' + CIRCUMFERENCE + '" transform="rotate(-90 20 20)"/>';
    btn.appendChild(ring);
    return ring;
  }

  function startHold(btn) {
    if (activeState && activeState.btn === btn) return;
    var ring = ensureRing(btn);
    var circle = ring.querySelector('circle');
    circle.style.transition = 'none';
    circle.style.strokeDashoffset = String(CIRCUMFERENCE);
    void circle.getBoundingClientRect();
    circle.style.transition = 'stroke-dashoffset ' + HOLD_MS + 'ms linear';
    circle.style.strokeDashoffset = '0';
    ring.classList.add('sara-modal-hold-active');
    var timer = setTimeout(function () { finishHold(btn, true); }, HOLD_MS);
    activeState = { btn: btn, timer: timer, circle: circle, ring: ring };
  }

  function finishHold(btn, success) {
    if (!activeState || activeState.btn !== btn) return;
    clearTimeout(activeState.timer);
    var circle = activeState.circle;
    var ring = activeState.ring;
    if (success) {
      ring.classList.add('sara-modal-hold-complete');
      setTimeout(function () { ring.classList.remove('sara-modal-hold-active', 'sara-modal-hold-complete'); }, 300);
      activeState = null;
      btn.dataset.saraHoldProgrammatic = '1';
      btn.click();
      delete btn.dataset.saraHoldProgrammatic;
    } else {
      var cs = getComputedStyle(circle).strokeDashoffset;
      circle.style.transition = 'none';
      circle.style.strokeDashoffset = cs;
      void circle.getBoundingClientRect();
      circle.style.transition = 'stroke-dashoffset .25s ease';
      circle.style.strokeDashoffset = String(CIRCUMFERENCE);
      ring.classList.remove('sara-modal-hold-active');
      activeState = null;
    }
  }

  document.addEventListener('mousedown', function (e) {
    var btn = e.target.closest && e.target.closest('.sara-modal-hold-confirm');
    if (!btn) return;
    e.preventDefault();
    startHold(btn);
  });
  document.addEventListener('touchstart', function (e) {
    var btn = e.target.closest && e.target.closest('.sara-modal-hold-confirm');
    if (!btn) return;
    startHold(btn);
  }, { passive: true });
  ['mouseup', 'mouseleave'].forEach(function (evt) {
    document.addEventListener(evt, function (e) {
      var btn = e.target.closest && e.target.closest('.sara-modal-hold-confirm');
      if (btn && activeState && activeState.btn === btn) finishHold(btn, false);
    }, true);
  });
  document.addEventListener('touchend', function (e) {
    var btn = e.target.closest && e.target.closest('.sara-modal-hold-confirm');
    if (btn && activeState && activeState.btn === btn) finishHold(btn, false);
  });
  /* Block the immediate click on a hold-confirm button unless it was
     dispatched programmatically by finishHold() above — the existing
     onclick/addEventListener on the button itself is never touched,
     only re-fired via btn.click() on successful hold. */
  document.addEventListener('click', function (e) {
    var btn = e.target.closest && e.target.closest('.sara-modal-hold-confirm');
    if (!btn) return;
    if (!btn.dataset.saraHoldProgrammatic) {
      e.stopImmediatePropagation();
      e.preventDefault();
    }
  }, true);
})();

/* ---- inline dismissible error banner ---- */
window.saraShowErrorBanner = function (message, containerElement) {
  if (!containerElement) return;
  var banner = document.createElement('div');
  banner.className = 'sara-modal-error-banner';
  banner.innerHTML =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/></svg>' +
    '<span class="sara-modal-error-banner-text"></span>' +
    '<button type="button" class="sara-modal-error-banner-close" aria-label="Dismiss">×</button>';
  banner.querySelector('.sara-modal-error-banner-text').textContent = message || '';
  containerElement.insertBefore(banner, containerElement.firstChild);
  requestAnimationFrame(function () { banner.classList.add('sara-modal-error-banner-in'); });

  var dismissed = false;
  function dismiss() {
    if (dismissed) return;
    dismissed = true;
    banner.classList.remove('sara-modal-error-banner-in');
    banner.classList.add('sara-modal-error-banner-out');
    setTimeout(function () { if (banner.parentNode) banner.remove(); }, 300);
  }
  banner.querySelector('.sara-modal-error-banner-close').addEventListener('click', dismiss);
  setTimeout(dismiss, 6000);
};

/* ---- modal "genie" close (layered onto the existing open/close transition) ---- */
(function () {
  var lastPoint = { x: window.innerWidth / 2, y: window.innerHeight / 2 };
  document.addEventListener('click', function (e) { lastPoint = { x: e.clientX, y: e.clientY }; }, true);

  var openPoints = new WeakMap();
  document.querySelectorAll('.modal-backdrop').forEach(function (backdrop) {
    var modal = backdrop.querySelector('.modal');
    if (!modal) return;
    var obs = new MutationObserver(function () {
      if (backdrop.classList.contains('open')) {
        openPoints.set(backdrop, { x: lastPoint.x, y: lastPoint.y });
        return;
      }
      if (backdrop.classList.contains('sara-modal-closing')) return;
      var pt = openPoints.get(backdrop);
      var rect = modal.getBoundingClientRect();
      var targetX = 0, targetY = 0;
      if (pt && rect.width) {
        targetX = pt.x - (rect.left + rect.width / 2);
        targetY = pt.y - (rect.top + rect.height / 2);
      }
      modal.style.setProperty('--sara-modal-genie-x', targetX + 'px');
      modal.style.setProperty('--sara-modal-genie-y', targetY + 'px');
      backdrop.classList.add('sara-modal-closing');
      modal.classList.add('sara-modal-genie-out');
      setTimeout(function () {
        backdrop.classList.remove('sara-modal-closing');
        modal.classList.remove('sara-modal-genie-out');
      }, 300);
    });
    obs.observe(backdrop, { attributes: true, attributeFilter: ['class'] });
  });
})();

/* ---- achievement micro-toast ---- */
window.saraShowAchievementToast = function (text, emoji) {
  var stack = document.getElementById('toastStack');
  if (!stack) return;
  var t = document.createElement('div');
  t.className = 'toast sara-modal-achievement';
  t.innerHTML = '<div class="sara-modal-achievement-emoji" aria-hidden="true"></div><p></p>';
  t.querySelector('.sara-modal-achievement-emoji').textContent = emoji || '🎉';
  t.querySelector('p').textContent = text || 'Achievement unlocked!';
  stack.appendChild(t);
  if (window.saraCelebrate) window.saraCelebrate(18);
  setTimeout(function () {
    t.style.opacity = '0';
    t.style.transition = 'opacity .3s';
    setTimeout(function () { if (t.parentNode) t.remove(); }, 300);
  }, 4000);
};

// ══════════════════════════════════════════════════════════════════
// Merged from app-extra.js (UI polish layer)
// ══════════════════════════════════════════════════════════════════
var saraPrefersReducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
var saraIsFinePointer = window.matchMedia && window.matchMedia('(pointer: fine)').matches;


      (function () {
         var log = document.getElementById('chatLog');
         var typingEl = null;

         window.saraShowTyping = function () {
            if (!log || typingEl) return;
            typingEl = document.createElement('div');
            typingEl.className = 'msg sara typing';
            typingEl.innerHTML = '<span class="msg-text"><span class="dot"></span><span class="dot"></span><span class="dot"></span></span>';
            log.appendChild(typingEl);
            log.scrollTop = log.scrollHeight;
         };

         window.saraHideTyping = function () {
            if (typingEl && typingEl.parentNode) {
               typingEl.parentNode.removeChild(typingEl);
            }
            typingEl = null;
         };
      })();
   

      (function () {
         if (saraPrefersReducedMotion) return;
         var COLORS = ['#8b5cf6', '#ec4899', '#3b82f6'];

         function burst(x, y) {
            var count = 10;
            for (var i = 0; i < count; i++) {
               var p = document.createElement('span');
               p.className = 'sara-burst-particle';
               var angle = (Math.PI * 2 * i) / count + Math.random() * 0.3;
               var dist = 30 + Math.random() * 30;
               p.style.setProperty('--bx', Math.cos(angle) * dist + 'px');
               p.style.setProperty('--by', Math.sin(angle) * dist + 'px');
               p.style.left = x + 'px';
               p.style.top = y + 'px';
               p.style.background = COLORS[i % COLORS.length];
               document.body.appendChild(p);
               (function (el) {
                  setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 650);
               })(p);
            }
         }

         document.addEventListener('click', function (e) {
            var el = e.target.closest && e.target.closest('.btn-primary, #orbBtn');
            if (!el) return;
            burst(e.clientX, e.clientY);
         });
      })();
   

      (function () {
         if (saraPrefersReducedMotion) return;
         var ids = [
            'statNetDown', 'statNetUp',
            'wTemp', 'proactiveTotal', 'proactiveBattery', 'proactiveReminder',
            'proactiveIdleBreak', 'proactiveStreak'
         ];
         ids.forEach(function (id) {
            var el = document.getElementById(id);
            if (!el) return;
            var lastText = el.textContent;
            var obs = new MutationObserver(function () {
               if (el.textContent === lastText) return;
               lastText = el.textContent;
               el.classList.remove('sara-value-tick', 'sara-value-roll');
               void el.offsetWidth;
               el.classList.add('sara-value-tick', 'sara-value-roll');
            });
            obs.observe(el, { childList: true, characterData: true, subtree: true });
         });
      })();
   

      (function () {
         var navList = document.getElementById('navList');
         if (navList && !saraPrefersReducedMotion) {
            var navObs = new MutationObserver(function (mutations) {
               mutations.forEach(function (m) {
                  if (m.type !== 'attributes' || m.attributeName !== 'class') return;
                  var li = m.target;
                  if (li.classList.contains('active')) {
                     var svg = li.querySelector('svg');
                     if (!svg) return;
                     svg.classList.remove('sara-pop');
                     requestAnimationFrame(function () { svg.classList.add('sara-pop'); });
                  }
               });
            });
            navList.querySelectorAll('li').forEach(function (li) {
               navObs.observe(li, { attributes: true });
            });
         }

         if (!saraPrefersReducedMotion) {
            document.querySelectorAll('.empty').forEach(function (el) {
               el.classList.add('sara-skel');
            });
         }
      })();
   

      // Inject animated bars into the titlebar waveform element.
      (function () {
         var wf = document.getElementById('waveform');
         if (!wf || wf.querySelector('.sara-wave-bar')) return;
         for (var i = 0; i < 5; i++) {
            var bar = document.createElement('span');
            bar.className = 'sara-wave-bar';
            wf.appendChild(bar);
         }
      })();

      // 3) Send button: brief "paper plane fly" class toggle on click.
      (function () {
         var sendBtn = document.getElementById('chatSendBtn');
         if (!sendBtn) return;
         sendBtn.addEventListener('click', function () {
            sendBtn.classList.remove('sara-sending');
            void sendBtn.offsetWidth;
            sendBtn.classList.add('sara-sending');
            setTimeout(function () { sendBtn.classList.remove('sara-sending'); }, 420);
         });
      })();

      // 4) Typewriter caret on the hero subtitle line under the greeting.
      (function () {
         if (saraPrefersReducedMotion) return;
         var hero = document.querySelector('.hero p');
         if (hero) hero.classList.add('sara-type-caret');
      })();
   

      // 1) Liquid sliding pill behind the active sidebar item
      (function () {
         var navList = document.getElementById('navList');
         if (!navList) return;
         var pill = document.createElement('div');
         pill.className = 'sara-nav-pill';
         navList.insertBefore(pill, navList.firstChild);
         function movePill() {
            var activeLi = navList.querySelector('li.active');
            if (!activeLi) { pill.style.height = '0'; return; }
            var targetHeight = activeLi.offsetHeight;
            pill.style.transform = 'translateY(' + activeLi.offsetTop + 'px)';
            if (saraPrefersReducedMotion) { pill.style.height = targetHeight + 'px'; return; }
            pill.classList.add('sara-jelly');
            pill.style.height = (targetHeight * 1.22) + 'px';
            clearTimeout(pill._jellyTimer);
            pill._jellyTimer = setTimeout(function () {
               pill.style.height = targetHeight + 'px';
            }, 140);
         }
         movePill();
         window.addEventListener('resize', movePill);
         var navObs = new MutationObserver(function (mutations) {
            var changed = false;
            mutations.forEach(function (m) { if (m.type === 'attributes' && m.attributeName === 'class') changed = true; });
            if (changed) movePill();
         });
         navList.querySelectorAll('li').forEach(function (li) { navObs.observe(li, { attributes: true }); });
      })();

      // 1b) Time-based greeting word + emoji
      (function () {
         var hour = new Date().getHours();
         var word = 'Good evening', emoji = '🌙';
         if (hour < 12) { word = 'Good morning'; emoji = '☀️'; }
         else if (hour < 17) { word = 'Good afternoon'; emoji = '☕'; }
         var wordEl = document.getElementById('greetingWord');
         var emojiEl = document.getElementById('greetingEmoji');
         if (wordEl) wordEl.textContent = word;
         if (emojiEl) emojiEl.textContent = emoji;
      })();

      // 2) Text-scramble reveal for the greeting name on load
      (function () {
         var nameEl = document.getElementById('greetingName');
         if (!nameEl || saraPrefersReducedMotion) return;
         var finalText = nameEl.textContent;
         var chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz';
         var frame = 0, totalFrames = 14;
         nameEl.classList.add('sara-scrambling');
         var timer = setInterval(function () {
            frame++;
            var revealCount = Math.floor((frame / totalFrames) * finalText.length);
            var out = '';
            for (var i = 0; i < finalText.length; i++) {
               if (finalText[i] === ' ') { out += ' '; continue; }
               out += i < revealCount ? finalText[i] : chars[(Math.random() * chars.length) | 0];
            }
            nameEl.textContent = out;
            if (frame >= totalFrames) { clearInterval(timer); nameEl.textContent = finalText; nameEl.classList.remove('sara-scrambling'); }
         }, 45);
      })();

      // 4) Voice orb: inject an extra audio-reactive ring, active only while listening
      (function () {
         if (saraPrefersReducedMotion) return;
         ['orbWrap'].forEach(function (id) {
            var wrap = document.getElementById(id);
            if (!wrap || wrap.querySelector('.sara-audio-ring')) return;
            var ring = document.createElement('div');
            ring.className = 'sara-audio-ring';
            ring.style.animationDelay = '0.4s';
            wrap.appendChild(ring);
         });
      })();

      // 5) Modal: elastic wobble right after each open
      (function () {
         if (saraPrefersReducedMotion) return;
         document.querySelectorAll('.modal-backdrop').forEach(function (backdrop) {
            var modal = backdrop.querySelector('.modal');
            if (!modal) return;
            var obs = new MutationObserver(function () {
               if (backdrop.classList.contains('open')) {
                  modal.classList.remove('sara-wobble');
                  void modal.offsetWidth;
                  modal.classList.add('sara-wobble');
               }
            });
            obs.observe(backdrop, { attributes: true, attributeFilter: ['class'] });
         });
      })();
   

      // 2) List stagger cascade — dynamically-rendered list rows fade/rise in one after
      //    another instead of popping in as a block. Watches the containers app.js rewrites
      //    wholesale (innerHTML = ...) and re-applies a staggered delay to each direct child
      //    every time the content changes.
      (function () {
         if (saraPrefersReducedMotion) return;
         var containers = [
            'reminderList', 'notesList', 'routinesList', 'automationList',
            'sideReminders', 'anTopCommands', 'anTrendChart'
         ];
         var SAVE_BTN = { reminderList: 'remSave', notesList: 'saveNoteBtn' };
         containers.forEach(function (id) {
            var el = document.getElementById(id);
            if (!el) return;
            var prevCount = el.children.length;
            function applyStagger() {
               var children = el.children;
               for (var i = 0; i < children.length; i++) {
                  var child = children[i];
                  child.classList.remove('sara-list-stagger-item');
                  void child.offsetWidth;
                  child.style.animationDelay = (i * 0.05) + 's';
                  child.classList.add('sara-list-stagger-item');
               }
               var btnId = SAVE_BTN[id];
               if (btnId && children.length > prevCount) {
                  var btn = document.getElementById(btnId);
                  if (btn) {
                     btn.classList.remove('sara-btn-processing', 'sara-check-pop', 'sara-checkmark-morph');
                     void btn.offsetWidth;
                     btn.classList.add('sara-check-pop', 'sara-checkmark-morph');
                     setTimeout(function () { btn.classList.remove('sara-check-pop'); }, 450);
                     setTimeout(function () { btn.classList.remove('sara-checkmark-morph'); }, 1300);
                  }
               }
               prevCount = children.length;
            }
            applyStagger();
            var obs = new MutationObserver(applyStagger);
            obs.observe(el, { childList: true });
         });
         ['remSave', 'saveNoteBtn'].forEach(function (id) {
            var btn = document.getElementById(id);
            if (!btn) return;
            btn.addEventListener('click', function () {
               btn.classList.add('sara-btn-processing');
               setTimeout(function () { btn.classList.remove('sara-btn-processing'); }, 4000);
            });
         });
      })();
   

      // 1) Any .toggle switch gets a quick elastic bounce on click.
      (function () {
         if (saraPrefersReducedMotion) return;
         document.addEventListener('click', function (e) {
            var el = e.target.closest && e.target.closest('.toggle');
            if (!el) return;
            el.classList.remove('sara-bounce');
            void el.offsetWidth;
            el.classList.add('sara-bounce');
         });
      })();
   

      // Note: window.saraSetOrbState / window.saraSetAudioLevel are defined once, in the
      // "SARA ALIVE SYSTEM" script block further down (structural orb states, active-state
      // coordinator, etc.) — kept in a single place instead of being defined twice.
      (function () {
         if (saraPrefersReducedMotion) return;
         ['chatLog', 'searchLog'].forEach(function (id) {
            var log = document.getElementById(id);
            if (!log) return;
            var pending = null;
            var obs = new MutationObserver(function () {
               var last = log.lastElementChild;
               if (!last) return;
               clearTimeout(pending);
               pending = setTimeout(function () {
                  last.classList.remove('sara-stream-tick');
                  void last.offsetWidth;
                  last.classList.add('sara-stream-tick');
               }, 30);
            });
            obs.observe(log, { childList: true, characterData: true, subtree: true });
         });
      })();
   

      (function () {
         var backdrops = document.querySelectorAll('#reminderModal, #quickInputModal');
         if (!backdrops.length) return;
         function sync() {
            var anyOpen = false;
            backdrops.forEach(function (b) { if (b.classList.contains('open')) anyOpen = true; });
            document.body.classList.toggle('sara-modal-open', anyOpen);
         }
         var obs = new MutationObserver(sync);
         backdrops.forEach(function (b) { obs.observe(b, { attributes: true, attributeFilter: ['class'] }); });
         sync();
      })();
   

      (function () {
         if (saraPrefersReducedMotion) return;
         var CONFIGS = [
            { el: document.getElementById('chatInput'), phrases: ['Ask me to play music…', 'Type a message or give a command…', 'Set a reminder…', 'Ask me anything…'] },
            { el: document.getElementById('searchInput'), phrases: ['Search for weather…', 'Search anything on the web…', 'Look up latest news…', 'Find something online…'] }
         ];
         CONFIGS.forEach(function (cfg) {
            if (!cfg.el) return;
            var idx = 0, charIdx = 0, deleting = false, timer = null;
            function tick() {
               var word = cfg.phrases[idx];
               if (document.activeElement === cfg.el || cfg.el.value) {
                  timer = setTimeout(tick, 700);
                  return;
               }
               if (!deleting) {
                  charIdx++;
                  cfg.el.setAttribute('placeholder', word.slice(0, charIdx));
                  if (charIdx >= word.length) { deleting = true; timer = setTimeout(tick, 2000); return; }
                  timer = setTimeout(tick, 45);
               } else {
                  charIdx--;
                  cfg.el.setAttribute('placeholder', word.slice(0, charIdx));
                  if (charIdx <= 0) { deleting = false; idx = (idx + 1) % cfg.phrases.length; timer = setTimeout(tick, 300); return; }
                  timer = setTimeout(tick, 25);
               }
            }
            timer = setTimeout(tick, 3000);
         });
      })();
   

      (function () {
         if (saraPrefersReducedMotion) return;
         ['chatLog', 'searchLog'].forEach(function (id) {
            var log = document.getElementById(id);
            if (!log) return;
            var obs = new MutationObserver(function (mutations) {
               mutations.forEach(function (m) {
                  m.addedNodes.forEach(function (node) {
                     if (node.nodeType === 1 && node.classList && node.classList.contains('msg')) {
                        node.classList.add('sara-msg-in');
                     }
                  });
               });
            });
            obs.observe(log, { childList: true });
         });
      })();
   

      (function () {

         /* ---- 1. Inject the structural orb layers (orbiters / converge rings / waves) ---- */
         function buildOrbLayers(wrap) {
            if (!wrap || wrap.querySelector('.orb-orbiters')) return;

            var orbiters = document.createElement('div');
            orbiters.className = 'orb-orbiters';
            var radii = [42, 34, 50];
            var speeds = [2.2, 1.6, 2.9];
            for (var i = 0; i < 3; i++) {
               var rotor = document.createElement('div');
               rotor.className = 'orb-orbiter';
               rotor.style.animationDuration = speeds[i] + 's';
               rotor.style.animationDelay = (i * -0.6) + 's';
               var dot = document.createElement('i');
               dot.style.transform = 'translateY(' + (-radii[i]) + 'px)';
               rotor.appendChild(dot);
               orbiters.appendChild(rotor);
            }
            wrap.appendChild(orbiters);

            var c1 = document.createElement('div'); c1.className = 'orb-converge';
            var c2 = document.createElement('div'); c2.className = 'orb-converge c2';
            wrap.appendChild(c1); wrap.appendChild(c2);

            ['w1', 'w2', 'w3'].forEach(function (cls) {
               var w = document.createElement('div');
               w.className = 'orb-wave ' + cls;
               wrap.appendChild(w);
            });
         }
         ['orbWrap'].forEach(function (id) {
            buildOrbLayers(document.getElementById(id));
         });

         /* ---- 2. Full orb state machine (supersedes the earlier simple version) ---- */
         var STATES = ['idle', 'listening', 'thinking', 'speaking', 'success', 'error'];
         var STATUS_TEXT = {
            idle: 'Listening for the wake word', listening: 'Listening…',
            thinking: 'Thinking…', speaking: 'Speaking…',
            success: 'Done', error: 'Something went wrong'
         };
         var ONLINE_TEXT = {
            idle: 'SARA is online', listening: 'SARA is listening',
            thinking: 'SARA is thinking', speaking: 'SARA is speaking',
            success: 'SARA is online', error: 'SARA hit a snag'
         };
         var pendingIdleTimer = null;

         function orbCenter(wrap) {
            var r = wrap.getBoundingClientRect();
            return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
         }

         function orbSuccessBurst(color) {
            if (saraPrefersReducedMotion) return;
            var wrap = document.getElementById('orbWrap');
            if (!wrap) return;
            var c = orbCenter(wrap);
            var count = 12;
            for (var i = 0; i < count; i++) {
               var p = document.createElement('span');
               p.className = 'sara-burst-particle';
               var angle = (Math.PI * 2 * i) / count;
               var dist = 34 + Math.random() * 26;
               p.style.setProperty('--bx', Math.cos(angle) * dist + 'px');
               p.style.setProperty('--by', Math.sin(angle) * dist + 'px');
               p.style.left = c.x + 'px';
               p.style.top = c.y + 'px';
               p.style.background = color;
               document.body.appendChild(p);
               (function (el) { setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 650); })(p);
            }
         }

         window.saraSetOrbState = function (state) {
            if (STATES.indexOf(state) === -1) return;
            clearTimeout(pendingIdleTimer);
            document.querySelectorAll('.orb-universe').forEach(function (o) {
               STATES.forEach(function (s) { o.classList.remove(s); });
               if (state !== 'idle') o.classList.add(state);
            });
            var wf = document.getElementById('waveform');
            if (wf) wf.classList.toggle('active', state === 'listening' || state === 'speaking');
            ['orbStatus'].forEach(function (id) {
               var el = document.getElementById(id);
               if (el) el.textContent = STATUS_TEXT[state];
            });
            var onlineEl = document.getElementById('onlineStatus');
            var onlineText = document.getElementById('onlineStatusText');
            if (onlineEl) onlineEl.setAttribute('data-orb-state', state);
            if (onlineText) onlineText.textContent = ONLINE_TEXT[state];

            if (state === 'success') orbSuccessBurst('#34d399');
            if (state === 'error') orbSuccessBurst('#ef4444');
            if (state === 'success' || state === 'error') {
               pendingIdleTimer = setTimeout(function () { window.saraSetOrbState('idle'); }, 900);
            }
         };
         window.saraSetAudioLevel = function (level) {
            document.documentElement.style.setProperty('--sara-audio', Math.max(0, Math.min(1, level || 0)));
         };

         /* ---- 3. "Sara is active" global coordinator ---- */
         window.saraTaskStart = function () {
            document.body.classList.add('sara-active');
            var activePage = document.querySelector('.page.active');
            if (activePage) activePage.classList.add('sara-page-glow');
            window.saraSetOrbState('thinking');
         };
         window.saraTaskEnd = function (outcome) {
            window.saraSetOrbState(outcome === 'error' ? 'error' : 'success');
            setTimeout(function () {
               document.body.classList.remove('sara-active');
               document.querySelectorAll('.page.sara-page-glow').forEach(function (p) {
                  p.classList.remove('sara-page-glow');
               });
            }, 950);
         };

         /* ---- 4. Chat "AI thinking" sequence — richer typing bubble with a mini orb ---- */
         var chatLog = document.getElementById('chatLog');
         var thinkingEl = null;

         function showThinkingBubble() {
            if (!chatLog || thinkingEl) return;
            thinkingEl = document.createElement('div');
            thinkingEl.className = 'msg sara thinking-bubble';
            thinkingEl.innerHTML =
               '<span class="sara-mini-orb" aria-hidden="true"></span>' +
               '<span class="msg-text"><span class="dot"></span><span class="dot"></span><span class="dot"></span></span>';
            chatLog.appendChild(thinkingEl);
            chatLog.scrollTop = chatLog.scrollHeight;
            requestAnimationFrame(function () {
               if (thinkingEl) thinkingEl.classList.add('sara-bubble-expand');
            });
            window.saraSetOrbState('thinking');
         }
         function hideThinkingBubble() {
            if (thinkingEl && thinkingEl.parentNode) thinkingEl.parentNode.removeChild(thinkingEl);
            thinkingEl = null;
         }

         // Preserve both naming conventions so either wiring style works.
         window.saraShowTyping = showThinkingBubble;
         window.saraHideTyping = hideThinkingBubble;
         window.saraChatThinkingStart = showThinkingBubble;
         window.saraChatThinkingEnd = hideThinkingBubble;
         window.saraChatResponseStart = function () { window.saraSetOrbState('speaking'); };
         window.saraChatResponseEnd = function (outcome) { window.saraTaskEnd(outcome || 'success'); };

         /* Send button → brief spinner morph while Sara is "picking up" the message.
            Auto-clears once the thinking bubble appears, or after a safety timeout. */
         ['chatSendBtn', 'searchBtn'].forEach(function (id) {
            var btn = document.getElementById(id);
            if (!btn) return;
            if (!btn.querySelector('.sara-send-spin')) {
               var spin = document.createElement('span');
               spin.className = 'sara-send-spin';
               spin.innerHTML = '<i></i>';
               btn.appendChild(spin);
            }
            btn.addEventListener('click', function (e) {
               btn.classList.add('sara-morphing');
               setTimeout(function () { btn.classList.remove('sara-morphing'); }, 900);
               // Particle command trail: message travels from the button toward the orb.
               var r = btn.getBoundingClientRect();
               window.saraCommandTrail(r.left + r.width / 2, r.top + r.height / 2);
            });
         });

         /* ---- 5. Particle command trail ---- */
         window.saraCommandTrail = function (x, y) {
            if (saraPrefersReducedMotion) return;
            var wrap = document.getElementById('orbWrap');
            if (!wrap || typeof x !== 'number') return;
            var target = orbCenter(wrap);
            var count = 6;
            for (var i = 0; i < count; i++) {
               (function (i) {
                  setTimeout(function () {
                     var p = document.createElement('span');
                     p.className = 'sara-trail-particle';
                     p.style.left = x + 'px';
                     p.style.top = y + 'px';
                     p.style.setProperty('--tx', (target.x - x) + 'px');
                     p.style.setProperty('--ty', (target.y - y) + 'px');
                     document.body.appendChild(p);
                     setTimeout(function () { if (p.parentNode) p.parentNode.removeChild(p); }, 600);
                  }, i * 40);
               })(i);
            }
         };

         /* ---- 6. Wake-word wave sweep ---- */
         window.saraWakeWordDetected = function () {
            if (!saraPrefersReducedMotion) {
               var wave = document.createElement('div');
               wave.className = 'sara-wake-wave';
               document.body.appendChild(wave);
               setTimeout(function () { if (wave.parentNode) wave.parentNode.removeChild(wave); }, 750);
            }
            window.saraSetOrbState('listening');
         };
         ['orbBtn'].forEach(function (id) {
            var btn = document.getElementById(id);
            if (btn) btn.addEventListener('click', function () { window.saraWakeWordDetected(); });
         });

         /* ---- 7. Premium music player hooks ---- */
         var art = document.getElementById('ppArt');
         var artGlow = document.getElementById('ppArtGlow');

         window.saraMusicBassHit = function () {
            if (saraPrefersReducedMotion || !art) return;
            art.classList.remove('bass-hit');
            void art.offsetWidth;
            art.classList.add('bass-hit');
            setTimeout(function () { art.classList.remove('bass-hit'); }, 250);
         };
         window.saraMusicBeat = function () {
            if (saraPrefersReducedMotion || !artGlow) return;
            artGlow.classList.remove('pulse');
            void artGlow.offsetWidth;
            artGlow.classList.add('pulse');
         };

         window.saraSetMusicPlayState = function (state) {
            if (!art) return;
            if (state === 'playing') {
               art.classList.remove('settled');
               if (!art.classList.contains('spinning')) art.classList.add('spinning');
               art.style.animation = '';
               art.style.transform = '';
               art.style.animationPlayState = 'running';
            } else if (state === 'paused') {
               // Freeze mid-rotation (CSS keeps the current frame) and settle the scale down.
               art.style.animationPlayState = 'paused';
               art.classList.add('settled');
            } else if (state === 'stopped') {
               if (!art.classList.contains('spinning')) return;
               var cs = window.getComputedStyle(art);
               var tf = cs.transform;
               var angle = 0;
               if (tf && tf !== 'none') {
                  var m = tf.match(/^matrix\(([^)]+)\)$/);
                  if (m) {
                     var v = m[1].split(',').map(parseFloat);
                     angle = Math.atan2(v[1], v[0]) * (180 / Math.PI);
                  }
               }
               art.classList.remove('spinning');
               art.classList.add('settled');
               if (saraPrefersReducedMotion) { art.style.transform = ''; return; }
               art.style.transition = 'none';
               art.style.transform = 'rotate(' + angle + 'deg) scale(0.96)';
               void art.offsetWidth;
               art.style.transition = 'transform 1.4s cubic-bezier(.15,.9,.35,1)';
               art.style.transform = 'rotate(' + (angle + 30) + 'deg) scale(0.96)';
               setTimeout(function () {
                  art.style.transition = '';
                  art.style.transform = '';
               }, 1450);
            }
         };

         /* ---- Seek bar: energy-trail fill + hover time preview (polling keeps this in
            sync even if app.js sets `.value` directly without dispatching input events) ---- */
         var seek = document.getElementById('ppSeek');
         var seekFill = document.getElementById('ppSeekFill');
         var seekWrap = document.getElementById('ppSeekWrap');
         var seekTip = document.getElementById('ppSeekTooltip');
         var durEl = document.getElementById('ppDurTime');

         function parseTimeToSeconds(str) {
            if (!str) return 0;
            var parts = str.split(':').map(Number);
            if (parts.length === 2) return parts[0] * 60 + parts[1];
            if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
            return 0;
         }
         function formatSeconds(s) {
            s = Math.max(0, Math.round(s));
            var m = Math.floor(s / 60), r = s % 60;
            return m + ':' + (r < 10 ? '0' : '') + r;
         }

         if (seek && seekFill) {
            var lastVal = null;
            setInterval(function () {
               var v = seek.value;
               if (v === lastVal) return;
               lastVal = v;
               seekFill.style.width = v + '%';
            }, 250);
         }
         if (seek && seekWrap && seekTip && durEl) {
            seekWrap.addEventListener('mousemove', function (e) {
               var rect = seekWrap.getBoundingClientRect();
               var pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
               var totalSec = parseTimeToSeconds(durEl.textContent);
               seekTip.textContent = formatSeconds(pct * totalSec);
               seekTip.style.left = (pct * 100) + '%';
               seekTip.classList.add('show');
            });
            seekWrap.addEventListener('mouseleave', function () { seekTip.classList.remove('show'); });
         }

         /* ---- Play/pause morph (self-managed visual toggle; call saraSyncPlayButton()
            from app.js once real playback state is known, to keep it perfectly in sync) ---- */
         var PLAY_D = 'M8 5v14l11-7z';
         var PAUSE_D = 'M7 5h4v14H7zM13 5h4v14h-4z';
         var playBtn = document.getElementById('ppPlayPause');
         var playPath = document.getElementById('ppPlayPath');
         var isPlayingLocal = false;

         function applyPlayVisual(isPlaying) {
            if (!playPath || !playBtn) return;
            playPath.setAttribute('d', isPlaying ? PAUSE_D : PLAY_D);
            playBtn.classList.remove('sara-morph');
            void playBtn.offsetWidth;
            playBtn.classList.add('sara-morph');
            window.saraSetMusicPlayState(isPlaying ? 'playing' : 'paused');
         }
         window.saraSyncPlayButton = function (isPlaying) {
            isPlayingLocal = !!isPlaying;
            applyPlayVisual(isPlayingLocal);
         };
         if (playBtn) {
            playBtn.addEventListener('click', function () {
               isPlayingLocal = !isPlayingLocal;
               applyPlayVisual(isPlayingLocal);
            });
         }
         var stopBtn = document.getElementById('ppStop');
         if (stopBtn) {
            stopBtn.addEventListener('click', function () {
               isPlayingLocal = false;
               if (playPath) playPath.setAttribute('d', PLAY_D);
               window.saraSetMusicPlayState('stopped');
            });
         }

         /* ---- 8. First-load cinematic boot (once per browser session only) ---- */
         (function boot() {
            var already = false;
            try { already = sessionStorage.getItem('sara_booted') === '1'; } catch (e) { already = false; }
            if (already || saraPrefersReducedMotion) return;
            try { sessionStorage.setItem('sara_booted', '1'); } catch (e) {}

            var seq = [
               document.querySelector('.titlebar .status'),
               document.querySelector('.sidebar .brand'),
               document.getElementById('navList'),
               document.getElementById('orbWrap'),
               document.querySelector('.home-main .quick-grid'),
               document.querySelector('.chat-side')
            ];
            seq.forEach(function (el, i) {
               if (!el) return;
               el.classList.add('sara-boot-el');
               el.style.animationDelay = (i * 0.12) + 's';
            });
         })();
      })();
   

      (function () {

         /* ---- 1. Liquid morph on real state changes (guards repeat-same-state calls) ---- */
         (function () {
            var lastState = null;
            var baseSetOrbState = window.saraSetOrbState;
            if (typeof baseSetOrbState !== 'function') return;
            window.saraSetOrbState = function (state) {
               var changed = state !== lastState;
               baseSetOrbState(state);
               if (changed) {
                  lastState = state;
                  if (!saraPrefersReducedMotion) {
                     document.querySelectorAll('.orb-core').forEach(function (core) {
                        core.classList.remove('sara-orb-liquid-morph');
                        void core.offsetWidth;
                        core.classList.add('sara-orb-liquid-morph');
                        // The morph is a single 0.4s pass — the class MUST come back off once
                        // it finishes, otherwise its higher-specificity `animation` declaration
                        // permanently overrides .orb-core's continuous breathing animation.
                        clearTimeout(core._saraMorphTimer);
                        core._saraMorphTimer = setTimeout(function () {
                           core.classList.remove('sara-orb-liquid-morph');
                        }, 420);
                     });
                  }
                  // ---- 4. Idle dimming: reset instantly on any state change ----
                  document.querySelectorAll('.orb-universe').forEach(function (o) {
                     o.classList.remove('sara-orb-resting');
                  });
                  idleSince = state === 'idle' ? Date.now() : null;
               }
            };
         })();

         /* ---- 2. Real amplitude-reactive waveform bars, driven by saraSetAudioLevel ---- */
         (function () {
            var wf = document.getElementById('waveform');
            if (!wf) return;
            var bars = null;
            var barMultipliers = [];
            var lastLevel = -1;
            var rafPending = false;
            var baseSetAudioLevel = window.saraSetAudioLevel;
            if (typeof baseSetAudioLevel !== 'function') return;

            function ensureBars() {
               if (bars) return bars;
               bars = wf.querySelectorAll('.sara-wave-bar');
               barMultipliers = [];
               bars.forEach(function () { barMultipliers.push(0.65 + Math.random() * 0.7); });
               return bars;
            }

            function paint() {
               rafPending = false;
               if (saraPrefersReducedMotion) return;
               ensureBars();
               var level = lastLevel;
               var isLive = level > 0.01;
               wf.classList.toggle('sara-orb-audio-live', isLive);
               if (!isLive) return;
               for (var i = 0; i < bars.length; i++) {
                  var h = 6 + level * barMultipliers[i] * 30;
                  bars[i].style.height = h.toFixed(1) + 'px';
                  bars[i].style.opacity = (0.55 + level * 0.45).toFixed(2);
               }
            }

            window.saraSetAudioLevel = function (level) {
               baseSetAudioLevel(level);
               var clamped = Math.max(0, Math.min(1, level || 0));
               if (clamped === lastLevel) return; // nothing changed since last frame
               lastLevel = clamped;
               if (!rafPending) {
                  rafPending = true;
                  requestAnimationFrame(paint);
               }
            };
         })();

         /* ---- 3. Interrupted feedback — instant freeze-flash, then restore prior state ---- */
         (function () {
            document.querySelectorAll('.orb-universe').forEach(function (wrap) {
               if (wrap.querySelector('.sara-orb-interrupt-ring')) return;
               var ring = document.createElement('div');
               ring.className = 'sara-orb-interrupt-ring';
               wrap.appendChild(ring);
            });
            window.saraOrbInterrupt = function () {
               var onlineEl = document.getElementById('onlineStatus');
               var priorState = (onlineEl && onlineEl.getAttribute('data-orb-state')) || 'idle';
               document.querySelectorAll('.orb-universe').forEach(function (wrap) {
                  wrap.classList.remove('sara-orb-interrupt');
                  void wrap.offsetWidth;
                  wrap.classList.add('sara-orb-interrupt');
               });
               setTimeout(function () {
                  document.querySelectorAll('.orb-universe').forEach(function (wrap) {
                     wrap.classList.remove('sara-orb-interrupt');
                  });
                  if (window.saraSetOrbState) window.saraSetOrbState(priorState);
               }, 150);
            };
         })();

         /* ---- 4. Idle dimming over time (loop below; reset lives in the state-change hook above) ---- */
         var idleSince = Date.now();
         setInterval(function () {
            if (saraPrefersReducedMotion || !idleSince) return;
            if (Date.now() - idleSince > 20000) {
               document.querySelectorAll('.orb-universe').forEach(function (o) {
                  o.classList.add('sara-orb-resting');
               });
            }
         }, 1000);

         /* ---- 6. Track-change crossfade on the player art ---- */
         (function () {
            var artWrap = document.querySelector('.pp-art-wrap');
            var titleEl = document.getElementById('ppTitle');
            if (!artWrap || !titleEl) return;
            var lastTitleText = titleEl.textContent;

            window.saraPlayerTrackChanged = function () {
               if (saraPrefersReducedMotion) return;
               artWrap.classList.add('sara-player-track-out');
               setTimeout(function () {
                  artWrap.classList.remove('sara-player-track-out');
                  artWrap.classList.remove('sara-player-track-in');
                  void artWrap.offsetWidth;
                  artWrap.classList.add('sara-player-track-in');
               }, 180);
            };

            var titleObs = new MutationObserver(function () {
               if (titleEl.textContent === lastTitleText) return;
               lastTitleText = titleEl.textContent;
               window.saraPlayerTrackChanged();
            });
            titleObs.observe(titleEl, { childList: true, characterData: true, subtree: true });

            var artistEl = document.getElementById('ppArtist');
            if (artistEl) {
               var lastArtistText = artistEl.textContent;
               var artistObs = new MutationObserver(function () {
                  if (artistEl.textContent === lastArtistText) return;
                  lastArtistText = artistEl.textContent;
                  window.saraPlayerTrackChanged();
               });
               artistObs.observe(artistEl, { childList: true, characterData: true, subtree: true });
            }
         })();

         /* ---- 7. Sticky mini-player — mirrors the real card, appears once it scrolls out ---- */
         (function () {
            var playerCard = document.getElementById('playerCard');
            var sidebar = document.querySelector('.chat-side');
            if (!playerCard || !sidebar || !('IntersectionObserver' in window)) return;

            var mini = document.createElement('div');
            mini.className = 'sara-player-mini';
            mini.id = 'saraPlayerMini';
            mini.innerHTML =
               '<div class="sara-player-mini-art" id="saraPlayerMiniArt" aria-hidden="true"></div>' +
               '<div class="sara-player-mini-info">' +
               '<span class="sara-player-mini-title" id="saraPlayerMiniTitle">No media is currently playing</span>' +
               '<span class="sara-player-mini-artist" id="saraPlayerMiniArtist">Play something to control it here</span>' +
               '</div>' +
               '<button class="sara-player-mini-play" id="saraPlayerMiniPlay" title="Play/Pause" aria-label="Play or pause">' +
               '<svg viewBox="0 0 24 24" fill="currentColor" id="saraPlayerMiniPlayIcon" aria-hidden="true"><path d="M8 5v14l11-7z" /></svg>' +
               '</button>';
            sidebar.insertBefore(mini, sidebar.firstChild);

            var miniTitle = document.getElementById('saraPlayerMiniTitle');
            var miniArtist = document.getElementById('saraPlayerMiniArtist');
            var miniArt = document.getElementById('saraPlayerMiniArt');
            var miniPlayBtn = document.getElementById('saraPlayerMiniPlay');
            var realPlayBtn = document.getElementById('ppPlayPause');

            function syncMini() {
               var titleEl = document.getElementById('ppTitle');
               var artistEl = document.getElementById('ppArtist');
               if (titleEl) miniTitle.textContent = titleEl.textContent;
               if (artistEl) miniArtist.textContent = artistEl.textContent;
               var realArt = document.getElementById('ppArt');
               var isSpinning = !!(realArt && realArt.classList.contains('spinning'));
               miniArt.classList.toggle('sara-player-mini-spin', isSpinning);
            }
            syncMini();

            var mirrorObs = new MutationObserver(syncMini);
            ['ppTitle', 'ppArtist', 'ppArt'].forEach(function (id) {
               var el = document.getElementById(id);
               if (el) mirrorObs.observe(el, { attributes: true, childList: true, characterData: true, subtree: true });
            });

            if (miniPlayBtn && realPlayBtn) {
               miniPlayBtn.addEventListener('click', function () { realPlayBtn.click(); });
            }

            var io = new IntersectionObserver(function (entries) {
               entries.forEach(function (entry) {
                  mini.classList.toggle('sara-player-mini-show', !entry.isIntersecting);
               });
            }, { root: null, threshold: 0 });
            io.observe(playerCard);
         })();

         /* ---- 8. Volume slider — energy-trail fill + live percentage bubble while dragging ---- */
         (function () {
            var vol = document.getElementById('ppVolume');
            var fill = document.getElementById('saraPlayerVolumeFill');
            var bubble = document.getElementById('saraPlayerVolumeBubble');
            if (!vol || !fill || !bubble) return;

            function paint() {
               var v = vol.value;
               fill.style.width = v + '%';
               bubble.style.left = v + '%';
               bubble.textContent = v + '%';
            }
            paint();
            vol.addEventListener('input', function () {
               paint();
               bubble.classList.add('show');
            });
            ['change', 'blur'].forEach(function (evt) {
               vol.addEventListener(evt, function () { bubble.classList.remove('show'); });
            });
            vol.addEventListener('mousedown', function () { bubble.classList.add('show'); });
            document.addEventListener('mouseup', function () {
               if (document.activeElement !== vol) bubble.classList.remove('show');
            });
         })();

         /* ---- 9. Real audio-frequency visualizer — idle/invisible until fed real data ---- */
         (function () {
            var canvas = document.getElementById('ppSpectrum');
            if (!canvas || !canvas.getContext) return;
            var ctx = canvas.getContext('2d');
            var dpr = Math.min(window.devicePixelRatio || 1, 2);
            var raf = null;
            var latestData = null;

            function resize() {
               var rect = canvas.parentElement.getBoundingClientRect();
               var size = Math.max(rect.width, rect.height) || 96;
               canvas.width = size * dpr;
               canvas.height = size * dpr;
               canvas.style.width = size + 'px';
               canvas.style.height = size + 'px';
               ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
            }

            function draw() {
               raf = null;
               if (!latestData || !latestData.length) return;
               var w = canvas.clientWidth, h = canvas.clientHeight;
               if (!w || !h) return;
               ctx.clearRect(0, 0, w, h);
               var cx = w / 2, cy = h / 2;
               var innerR = Math.min(w, h) * 0.32;
               var maxLen = Math.min(w, h) * 0.16;
               var count = latestData.length;
               for (var i = 0; i < count; i++) {
                  var v = Math.max(0, Math.min(1, latestData[i] || 0));
                  var angle = (Math.PI * 2 * i) / count - Math.PI / 2;
                  var len = 3 + v * maxLen;
                  var x1 = cx + Math.cos(angle) * innerR;
                  var y1 = cy + Math.sin(angle) * innerR;
                  var x2 = cx + Math.cos(angle) * (innerR + len);
                  var y2 = cy + Math.sin(angle) * (innerR + len);
                  ctx.strokeStyle = 'rgba(139,92,246,' + (0.35 + v * 0.55).toFixed(2) + ')';
                  ctx.lineWidth = Math.max(1.5, (Math.PI * 2 * innerR / count) * 0.5);
                  ctx.lineCap = 'round';
                  ctx.beginPath();
                  ctx.moveTo(x1, y1);
                  ctx.lineTo(x2, y2);
                  ctx.stroke();
               }
            }

            window.saraSetMusicSpectrum = function (dataArray) {
               if (!dataArray || !dataArray.length) {
                  latestData = null;
                  canvas.classList.remove('sara-player-spectrum-active');
                  return;
               }
               latestData = dataArray;
               canvas.classList.add('sara-player-spectrum-active');
               if (!canvas.width) resize();
               if (!raf) raf = requestAnimationFrame(draw);
            };
            window.addEventListener('resize', function () { if (canvas.width) resize(); });
         })();

         /* ---- 10. "Up Next" queue peek ---- */
         (function () {
            var handle = document.getElementById('saraPlayerQueueHandle');
            var panel = document.getElementById('ppQueueList');
            if (!handle || !panel) return;

            handle.addEventListener('click', function () {
               var open = panel.classList.toggle('sara-player-queue-open');
               handle.setAttribute('aria-expanded', open ? 'true' : 'false');
            });

            window.saraSetMusicQueue = function (items) {
               panel.innerHTML = '';
               if (!items || !items.length) {
                  var empty = document.createElement('div');
                  empty.className = 'empty sara-player-queue-empty';
                  empty.innerHTML = '<p>Nothing queued yet.</p>';
                  panel.appendChild(empty);
                  return;
               }
               items.forEach(function (item, i) {
                  var row = document.createElement('div');
                  row.className = 'sara-player-queue-item';
                  row.style.animationDelay = (i * 0.05) + 's';
                  var title = document.createElement('b');
                  title.textContent = (item && item.title) || 'Untitled';
                  var artist = document.createElement('span');
                  artist.textContent = (item && item.artist) || '';
                  row.appendChild(title);
                  row.appendChild(artist);
                  panel.appendChild(row);
               });
            };
         })();
      })();
   

   // ══════════════════════════════════════════════════════════════
   // SARA FEATURE PASS — CHAT
   // Extends existing hooks (saraShowTyping, sara-stream-tick,
   // .sara-type-caret) — none redefined or removed.
   // ══════════════════════════════════════════════════════════════
   (function () {
      var chatLog = document.getElementById('chatLog');

      /* ---- 1. Word-by-word typewriter reveal + blinking caret while streaming ---- */
      if (chatLog) {
         var streamTimers = new WeakMap();
         var obs1 = new MutationObserver(function (mutations) {
            mutations.forEach(function (m) {
               var target = m.target.nodeType === 3 ? m.target.parentElement : m.target;
               var bubble = target && target.closest ? target.closest('.msg.sara') : null;
               if (!bubble || bubble.classList.contains('thinking-bubble')) return;
               bubble.classList.add('sara-type-caret');
               clearTimeout(streamTimers.get(bubble));
               streamTimers.set(bubble, setTimeout(function () {
                  bubble.classList.remove('sara-type-caret');
                  streamTimers.delete(bubble);
                  finalizeChatMessage(bubble);
               }, 500));
               if (!saraPrefersReducedMotion) {
                  var textEl = bubble.querySelector('.msg-text') || bubble;
                  textEl.classList.remove('sara-chat-streaming-char');
                  void textEl.offsetWidth;
                  textEl.classList.add('sara-chat-streaming-char');
               }
            });
         });
         obs1.observe(chatLog, { characterData: true, subtree: true, childList: true });
      }

      /* ---- 2 & 3 helpers: collapsible responses + terminal code card ---- */
      function wrapCodeContent(bubble) {
         if (bubble.dataset.saraChatCodeDone) return;
         var textEl = bubble.querySelector('.msg-text');
         if (!textEl) return;
         var raw = textEl.textContent || '';
         var hasFence = raw.indexOf('```') !== -1;
         var looksLikeShell = /^\s*[$#>]\s|^(npm|pip|git|cd|ls|dir|python|node|curl|sudo)\s/im.test(raw);
         if (!hasFence && !looksLikeShell) return;
         bubble.dataset.saraChatCodeDone = '1';
         var codeText = raw;
         if (hasFence) {
            var fenceMatch = raw.match(/```[a-zA-Z0-9]*\n?([\s\S]*?)```/);
            if (fenceMatch) codeText = fenceMatch[1];
         }
         var card = document.createElement('div');
         card.className = 'sara-chat-codecard';
         card.textContent = codeText.trim();
         var copyBtn = document.createElement('button');
         copyBtn.className = 'sara-chat-codecard-copy';
         copyBtn.type = 'button';
         copyBtn.setAttribute('aria-label', 'Copy code');
         copyBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>';
         copyBtn.addEventListener('click', function () {
            var text = codeText.trim();
            var doneFn = function () {
               copyBtn.classList.add('sara-chat-copied');
               copyBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M4 12l5 5L20 6"/></svg>';
               setTimeout(function () {
                  copyBtn.classList.remove('sara-chat-copied');
                  copyBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>';
               }, 1400);
            };
            if (navigator.clipboard && navigator.clipboard.writeText) {
               navigator.clipboard.writeText(text).then(doneFn).catch(doneFn);
            } else {
               doneFn();
            }
         });
         card.appendChild(copyBtn);
         if (hasFence) {
            var withoutFence = raw.replace(/```[a-zA-Z0-9]*\n?[\s\S]*?```/, '').trim();
            textEl.textContent = withoutFence;
         }
         textEl.parentNode.insertBefore(card, textEl.nextSibling);
      }

      function finalizeChatMessage(bubble) {
         if (bubble.dataset.saraChatFinalized) return;
         bubble.dataset.saraChatFinalized = '1';
         wrapCodeContent(bubble);
         requestAnimationFrame(function () {
            var h = bubble.scrollHeight;
            if (h > 220 && !bubble.classList.contains('sara-chat-collapsible')) {
               bubble.classList.add('sara-chat-collapsible');
               var pill = document.createElement('button');
               pill.className = 'sara-chat-readmore';
               pill.type = 'button';
               pill.textContent = 'Read more';
               pill.addEventListener('click', function () {
                  var expanded = bubble.classList.toggle('sara-chat-expanded');
                  pill.textContent = expanded ? 'Show less' : 'Read more';
               });
               bubble.appendChild(pill);
            }
         });
      }

      /* Catch sara bubbles appended without a character-data mutation
         (e.g. innerHTML set once, not streamed) */
      if (chatLog) {
         var obs2 = new MutationObserver(function (mutations) {
            mutations.forEach(function (m) {
               m.addedNodes.forEach(function (node) {
                  if (node.nodeType === 1 && node.classList && node.classList.contains('msg') &&
                     node.classList.contains('sara') && !node.classList.contains('thinking-bubble')) {
                     setTimeout(function () { finalizeChatMessage(node); }, 550);
                  }
               });
            });
         });
         obs2.observe(chatLog, { childList: true });
      }

      /* ---- 4. "Delivered" tick on user messages ---- */
      if (chatLog) {
         var obs3 = new MutationObserver(function (mutations) {
            mutations.forEach(function (m) {
               m.addedNodes.forEach(function (node) {
                  if (node.nodeType === 1 && node.classList && node.classList.contains('msg') &&
                     node.classList.contains('user') && !node.classList.contains('sara-chat-partial')) {
                     setTimeout(function () {
                        if (!node.parentNode) return;
                        var tick = document.createElement('span');
                        tick.className = 'sara-chat-delivered';
                        tick.setAttribute('aria-hidden', 'true');
                        tick.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M4 12l5 5L20 6"/></svg>';
                        node.appendChild(tick);
                        requestAnimationFrame(function () {
                           tick.classList.add('sara-chat-delivered-in');
                        });
                     }, 400);
                  }
               });
            });
         });
         obs3.observe(chatLog, { childList: true });
      }

      /* ---- 5. Live partial voice transcript ghost bubble ---- */
      var partialBubble = null;
      window.saraChatPartialTranscript = function (text) {
         if (!chatLog) return;
         if (text === null || text === undefined || text === '') {
            if (partialBubble && partialBubble.parentNode) partialBubble.parentNode.removeChild(partialBubble);
            partialBubble = null;
            return;
         }
         if (!partialBubble) {
            partialBubble = document.createElement('div');
            partialBubble.className = 'msg user sara-chat-partial';
            chatLog.appendChild(partialBubble);
         }
         partialBubble.textContent = text;
         chatLog.scrollTop = chatLog.scrollHeight;
      };
      if (chatLog) {
         var obs4 = new MutationObserver(function (mutations) {
            mutations.forEach(function (m) {
               m.addedNodes.forEach(function (node) {
                  if (node.nodeType === 1 && node.classList && node.classList.contains('msg') &&
                     node.classList.contains('user') && !node.classList.contains('sara-chat-partial')) {
                     if (partialBubble && partialBubble.parentNode) partialBubble.parentNode.removeChild(partialBubble);
                     partialBubble = null;
                  }
               });
            });
         });
         obs4.observe(chatLog, { childList: true });
      }
   })();


   // ══════════════════════════════════════════════════════════════
   // SARA FEATURE PASS — NAVIGATION & SIDEBAR
   // Does not touch orb, music player, cards, analytics, modals,
   // or settings.
   // ══════════════════════════════════════════════════════════════
   (function () {
      var navList = document.getElementById('navList');
      var quickActions = document.getElementById('quickActions');
      var sidebar = document.querySelector('.sidebar');

      /* ---- 6. Command palette overlay (Ctrl+K / Cmd+K) ---- */
      var palette = document.createElement('div');
      palette.id = 'saraCommandPalette';
      palette.innerHTML =
         '<div class="sara-nav-palette-box" role="dialog" aria-modal="true" aria-label="Command palette">' +
         '<input type="text" class="sara-nav-palette-input" id="saraCommandPaletteInput" placeholder="Type a command or page name…" autocomplete="off">' +
         '<div class="sara-nav-palette-list" id="saraCommandPaletteList"></div>' +
         '</div>';
      document.body.appendChild(palette);
      var paletteInput = document.getElementById('saraCommandPaletteInput');
      var paletteList = document.getElementById('saraCommandPaletteList');
      var paletteItems = [];
      var paletteActiveIdx = -1;
      var lastFocused = null;

      function buildPaletteItems() {
         var items = [];
         if (navList) {
            navList.querySelectorAll('li[data-page] button').forEach(function (btn) {
               var span = btn.querySelector('span');
               if (!span) return;
               items.push({ label: span.textContent.trim(), action: function () { btn.click(); } });
            });
         }
         if (quickActions) {
            quickActions.querySelectorAll('button b').forEach(function (b) {
               var btn = b.closest('button');
               if (!btn) return;
               items.push({ label: b.textContent.trim(), action: function () { btn.click(); } });
            });
         }
         return items;
      }

      function renderPaletteList(filter) {
         paletteList.innerHTML = '';
         var all = buildPaletteItems();
         var f = (filter || '').toLowerCase();
         paletteItems = all.filter(function (it) { return it.label.toLowerCase().indexOf(f) !== -1; });
         if (!paletteItems.length) {
            var empty = document.createElement('div');
            empty.className = 'sara-nav-palette-empty';
            empty.textContent = 'No matches';
            paletteList.appendChild(empty);
            paletteActiveIdx = -1;
            return;
         }
         paletteItems.forEach(function (it, i) {
            var row = document.createElement('div');
            row.className = 'sara-nav-palette-item';
            row.textContent = it.label;
            row.addEventListener('click', function () { runPaletteItem(i); });
            paletteList.appendChild(row);
         });
         paletteActiveIdx = 0;
         highlightPaletteActive();
      }

      function highlightPaletteActive() {
         var rows = paletteList.querySelectorAll('.sara-nav-palette-item');
         rows.forEach(function (r, i) {
            r.classList.toggle('sara-nav-palette-active', i === paletteActiveIdx);
         });
         var activeRow = rows[paletteActiveIdx];
         if (activeRow) activeRow.scrollIntoView({ block: 'nearest' });
      }

      function runPaletteItem(i) {
         var it = paletteItems[i];
         closePalette();
         if (it && it.action) it.action();
      }

      function openPalette() {
         lastFocused = document.activeElement;
         palette.classList.add('sara-nav-palette-open');
         paletteInput.value = '';
         renderPaletteList('');
         setTimeout(function () { paletteInput.focus(); }, 10);
      }
      function closePalette() {
         palette.classList.remove('sara-nav-palette-open');
         if (lastFocused && lastFocused.focus) lastFocused.focus();
      }

      document.addEventListener('keydown', function (e) {
         var isMac = navigator.platform.toUpperCase().indexOf('MAC') !== -1;
         var mod = isMac ? e.metaKey : e.ctrlKey;
         if (mod && (e.key === 'k' || e.key === 'K')) {
            e.preventDefault();
            if (palette.classList.contains('sara-nav-palette-open')) closePalette();
            else openPalette();
            return;
         }
         if (!palette.classList.contains('sara-nav-palette-open')) return;
         if (e.key === 'Escape') { e.preventDefault(); closePalette(); }
         else if (e.key === 'ArrowDown') { e.preventDefault(); paletteActiveIdx = Math.min(paletteActiveIdx + 1, paletteItems.length - 1); highlightPaletteActive(); }
         else if (e.key === 'ArrowUp') { e.preventDefault(); paletteActiveIdx = Math.max(paletteActiveIdx - 1, 0); highlightPaletteActive(); }
         else if (e.key === 'Enter') { e.preventDefault(); if (paletteActiveIdx > -1) runPaletteItem(paletteActiveIdx); }
         else if (e.key === 'Tab') {
            var focusables = palette.querySelectorAll('input, .sara-nav-palette-item');
            if (!focusables.length) return;
            var first = focusables[0], last = focusables[focusables.length - 1];
            if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
            else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
         }
      });
      paletteInput.addEventListener('input', function () { renderPaletteList(paletteInput.value); });
      palette.addEventListener('click', function (e) { if (e.target === palette) closePalette(); });

      /* ---- 7. Collapsible sidebar ---- */
      if (sidebar) {
         var brandEl = sidebar.querySelector('.brand');
         var collapseBtn = document.createElement('button');
         collapseBtn.id = 'sidebarCollapseBtn';
         collapseBtn.type = 'button';
         collapseBtn.setAttribute('aria-label', 'Collapse sidebar');
         collapseBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg>';
         if (brandEl) brandEl.appendChild(collapseBtn);

         function storageGet(key) {
            try {
               if (window.storage && window.storage.get) {
                  return window.storage.get(key).then(function (r) { return r ? r.value : null; }).catch(function () { return Promise.resolve(localFallbackGet(key)); });
               }
            } catch (e) {}
            return Promise.resolve(localFallbackGet(key));
         }
         function localFallbackGet(key) {
            try { return localStorage.getItem(key); } catch (e) { return null; }
         }
         function storageSet(key, value) {
            try {
               if (window.storage && window.storage.set) {
                  window.storage.set(key, value).catch(function () { localFallbackSet(key, value); });
                  return;
               }
            } catch (e) {}
            localFallbackSet(key, value);
         }
         function localFallbackSet(key, value) {
            try { localStorage.setItem(key, value); } catch (e) {}
         }

         function applyCollapsed(collapsed) {
            sidebar.classList.toggle('sara-nav-collapsed', collapsed);
            collapseBtn.setAttribute('aria-label', collapsed ? 'Expand sidebar' : 'Collapse sidebar');
         }

         collapseBtn.addEventListener('click', function () {
            var collapsed = !sidebar.classList.contains('sara-nav-collapsed');
            applyCollapsed(collapsed);
            storageSet('sara_sidebar_collapsed', collapsed ? '1' : '0');
         });

         storageGet('sara_sidebar_collapsed').then(function (v) {
            applyCollapsed(v === '1');
         });
      }

      /* ---- 8. Directional page-slide ---- */
      (function () {
         var navLis = navList ? Array.prototype.slice.call(navList.querySelectorAll('li[data-page]')) : [];
         function navIndexForPageId(pageId) {
            var pageName = pageId.replace('page-', '');
            for (var i = 0; i < navLis.length; i++) {
               if (navLis[i].getAttribute('data-page') === pageName) return i;
            }
            return -1;
         }
         var lastActiveIdx = -1;
         var initialActive = document.querySelector('.page.active');
         if (initialActive) lastActiveIdx = navIndexForPageId(initialActive.id);

         // Called directly from gotoPage() -- NOT via MutationObserver, so it
         // can never re-trigger itself off its own class change (that was the
         // freeze bug: an observer that watched the exact class it mutated,
         // causing an infinite add/remove loop that compounded on every nav).
         window.saraApplyPageSlide = function (page) {
            var idx = navIndexForPageId(page.id);
            page.classList.remove('sara-nav-slide-right', 'sara-nav-slide-left');
            if (lastActiveIdx !== -1 && idx !== -1 && idx !== lastActiveIdx) {
               var dir = idx > lastActiveIdx ? 'sara-nav-slide-right' : 'sara-nav-slide-left';
               requestAnimationFrame(function () { page.classList.add(dir); });
            }
            if (idx !== -1) lastActiveIdx = idx;
         };
      })();

      /* ---- 9. Sidebar badge pop-in ---- */
      window.saraSetNavBadge = function (pageName, count) {
         if (!navList) return;
         var li = navList.querySelector('li[data-page="' + pageName + '"]');
         if (!li) return;
         var btn = li.querySelector('button');
         if (!btn) return;
         var badge = btn.querySelector('.nav-badge');
         var had = badge && badge.textContent.trim() !== '' && !isNaN(parseInt(badge.textContent, 10)) && parseInt(badge.textContent, 10) > 0;
         if (!count || count <= 0) {
            if (badge && badge.dataset.saraNavNumeric) badge.parentNode.removeChild(badge);
            return;
         }
         if (!badge) {
            badge = document.createElement('span');
            badge.className = 'nav-badge';
            badge.dataset.saraNavNumeric = '1';
            btn.appendChild(badge);
         }
         badge.textContent = String(count);
         if (!had) {
            badge.classList.remove('sara-nav-badge-pop');
            void badge.offsetWidth;
            badge.classList.add('sara-nav-badge-pop');
         } else {
            badge.classList.remove('sara-value-tick');
            void badge.offsetWidth;
            badge.classList.add('sara-value-tick');
         }
      };

      /* ---- 10. Multi-step progress trail (reusable component, not auto-wired anywhere) ---- */
      window.saraShowStepProgress = function (container, currentStep, totalSteps) {
         if (!container || !totalSteps) return;
         container.innerHTML = '';
         var wrap = document.createElement('div');
         wrap.className = 'sara-nav-progress';
         for (var i = 1; i <= totalSteps; i++) {
            var dot = document.createElement('div');
            dot.className = 'sara-nav-progress-step';
            if (i < currentStep) dot.classList.add('sara-nav-progress-done');
            if (i === currentStep) dot.classList.add('sara-nav-progress-current');
            wrap.appendChild(dot);
            if (i < totalSteps) {
               var line = document.createElement('div');
               line.className = 'sara-nav-progress-line';
               var fill = document.createElement('div');
               fill.className = 'sara-nav-progress-line-fill';
               var pct = i < currentStep ? 100 : 0;
               fill.style.width = pct + '%';
               line.appendChild(fill);
               wrap.appendChild(line);
            }
         }
         var label = document.createElement('span');
         label.className = 'sara-nav-progress-label';
         label.textContent = 'Step ' + currentStep + ' of ' + totalSteps;
               wrap.appendChild(label);
      container.appendChild(wrap);
      };
   })();