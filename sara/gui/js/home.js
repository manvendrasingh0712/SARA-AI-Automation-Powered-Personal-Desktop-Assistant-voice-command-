/* ==========================================================================
   home.js -- Home page: clock, greeting, the canvas orb, and the ONE place Sara's live status is shown.
   Backend link: push event 'status' (sleeping|waking|listening|thinking|speaking) drives the orb colour,
   caption, and every page's mini-orb + status text.  'footer' text -> the small hint line.
   API calls: wake_now, stop_sara, get_assistant_active, set_assistant_active, get_display_name.
   Orb polish: colour/energy/wobble ease between states, particle density + short trails follow energy,
   and a slow centre drift so even idle is never static (drift/trails/density-change are off under reduced-motion).
   Exposes: SARA.setStatus, SARA.wake, SARA.setAssistantActive, SARA.setDisplayName, SARA.orb {t, energy}.
   Styles: style/home.css.  Markup: index.html #page-home.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA, $ = SARA.$;

  const THINK_RGB = [139, 111, 216], SPEAK_RGB = [232, 192, 143];      // --think / --speak
  const mixRGB = (a, b, k) => a.map((v, i) => Math.round(v + (b[i] - v) * k));
  const MODES = {
    idle:      { rgb: [63, 216, 196],  energy: 0.30, cls: 'core-idle',      caption: 'idle',       status: 'listening quietly' },
    listening: { rgb: [63, 216, 196],  energy: 1.00, cls: 'core-listening', caption: 'listening…', status: 'listening' },
    thinking:  { rgb: THINK_RGB,       energy: 0.72, cls: 'core-thinking',  caption: 'thinking…',  status: 'thinking' },
    speaking:  { rgb: SPEAK_RGB,       energy: 0.88, cls: 'core-speaking',  caption: 'speaking',   status: 'speaking' },
    // working = Sara is DOING something (opening an app, running a task). Colour sits exactly halfway between
    // thinking (purple) and speaking (amber); on top of that the orb gets a rotating arc + counter-wobble (see draw()).
    working:   { rgb: mixRGB(THINK_RGB, SPEAK_RGB, 0.5), energy: 0.92, cls: 'core-working', caption: 'working…', status: 'working' }
  };
  const BACKEND_TO_MODE = { sleeping: 'idle', waking: 'listening', listening: 'listening', thinking: 'thinking', speaking: 'speaking', working: 'working' };
  const warnedStatus = {};   // an unknown backend status silently falls back to idle -- warn once so it can't hide again
  const MUTED_RGB = [85, 98, 96];
  let modeName = 'idle', idleSince = Date.now();
  const rgbStr = (rgb, a) => 'rgba(' + rgb[0] + ',' + rgb[1] + ',' + rgb[2] + ',' + a + ')';
  const isPausedIdle = () => !SARA.state.assistantActive && modeName === 'idle';

  /* ---- ambient mode: two-tier "Sara has been sitting idle" experience, driven purely by real
     user input (mouse/keyboard/click), independent of the assistant's own status. Only engages
     while the assistant itself is idle (BACKEND_TO_MODE 'idle') and the home page is on screen --
     if Sara starts listening/thinking/speaking/working, or the user navigates away, it snaps back
     to normal immediately. No new backend calls: Tier 2's "next up" line reuses the exact item the
     Today card (js/today.js) already fetched and rendered, via the 'nextItem' event. ---- */
  const AMBIENT_T1_MS = 75000;    // ~75s: "Tier 1 -- standby" (sensible point in the 60-90s the spec asked for)
  const AMBIENT_T2_MS = 270000;   // ~4.5min: "Tier 2 -- ambient" (sensible point in the 4-5min the spec asked for)
  let ambientTier = 0, lastActivity = Date.now(), nextItem = null;
  const pageHome = $('page-home');
  SARA.on('nextItem', function (item) { nextItem = item; if (ambientTier === 2) renderAmbientNext(); });

  const ambientPanel = document.createElement('div'); ambientPanel.className = 'ambient-panel';
  const ambientClock = document.createElement('div'); ambientClock.className = 'ambient-clock';
  const ambientNext = document.createElement('div'); ambientNext.className = 'ambient-next';
  ambientPanel.appendChild(ambientClock); ambientPanel.appendChild(ambientNext);
  if (pageHome) pageHome.appendChild(ambientPanel);
  function renderAmbientNext() {
    ambientNext.textContent = nextItem ? 'Next: ' + nextItem.text + ', ' + nextItem.time : '';
  }
  function tickAmbientClock() { ambientClock.textContent = SARA.fmtClockTime(new Date()); }
  let ambientClockTimer = 0;

  function setAmbientTier(tier) {
    if (tier === ambientTier) return;
    ambientTier = tier;
    if (pageHome) { pageHome.classList.toggle('ambient-t1', tier === 1); pageHome.classList.toggle('ambient-t2', tier === 2); }
    if (tier === 2) {
      if (typeof SARA.getNextItem === 'function') nextItem = SARA.getNextItem();
      renderAmbientNext(); tickAmbientClock();
      requestAnimationFrame(() => ambientPanel.classList.add('show'));
      clearInterval(ambientClockTimer); ambientClockTimer = setInterval(tickAmbientClock, 1000);
    } else {
      ambientPanel.classList.remove('show');
      clearInterval(ambientClockTimer); ambientClockTimer = 0;
    }
    refreshCaption();
  }
  function markActivity() {
    lastActivity = Date.now();
    if (ambientTier !== 0) setAmbientTier(0);   // Tier 2 -> normal directly, never through Tier 1
  }
  ['mousemove', 'keydown', 'mousedown', 'touchstart', 'wheel'].forEach(function (ev) {
    document.addEventListener(ev, markActivity, { passive: true });
  });
  function checkAmbientIdle() {
    if ((SARA.current || 'home') !== 'home' || modeName !== 'idle') { if (ambientTier !== 0) setAmbientTier(0); lastActivity = Date.now(); return; }
    const elapsed = Date.now() - lastActivity;
    if (elapsed >= AMBIENT_T2_MS) setAmbientTier(2);
    else if (elapsed >= AMBIENT_T1_MS) setAmbientTier(1);
  }
  SARA.every(1000, checkAmbientIdle);   // SARA.every (core.js) already skips ticks while the tab is hidden
  SARA.on('page', function (p) { if (p !== 'home') setAmbientTier(0); else lastActivity = Date.now(); });

  /* ---- clock + greeting ---- */
  function clock() { $('timeVal').textContent = SARA.fmtClockTime(new Date()); }
  clock(); setInterval(clock, 15000);
  SARA.setDisplayName = function (name) {
    name = (name || '').trim(); if (!name) return;
    try { localStorage.setItem('sara_display_name', name); } catch (e) {}
    $('greetName').textContent = name; SARA.emit('name', name);
  };
  try { const n = localStorage.getItem('sara_display_name'); if (n) $('greetName').textContent = n; } catch (e) {}
  SARA.onBoot(async function () {
    const res = await SARA.callApi('get_display_name');
    if (res && res.ok && res.name) SARA.setDisplayName(res.name);
  });

  /* ---- status chrome: caption, mini-orbs, page status text ---- */
  const caption = $('homeCaption');
  let captionKey = '';
  function idleCaption() {
    if (!SARA.state.assistantActive) return 'paused';
    if (ambientTier === 1) return 'Ready when you are.';
    const m = Math.floor((Date.now() - idleSince) / 60000);
    if (m < 1) return 'idle';
    return m < 60 ? 'idle since ' + m + 'm' : 'idle since ' + Math.floor(m / 60) + 'h';
  }
  function refreshCaption() {
    const m = MODES[modeName];
    const text = modeName === 'idle' ? idleCaption() : m.caption;
    const cls = isPausedIdle() ? 'core-idle' : m.cls;
    const key = text + '|' + cls; if (key === captionKey) return; captionKey = key;
    caption.classList.add('swap');
    setTimeout(function () {
      caption.textContent = text; caption.className = 'home-caption swap ' + cls;
      requestAnimationFrame(() => caption.classList.remove('swap'));
    }, 140);
  }
  function refreshChrome() {
    const m = MODES[modeName], paused = isPausedIdle();
    const rgb = paused ? MUTED_RGB : m.rgb;
    document.querySelectorAll('[data-mini]').forEach(function (el) {
      el.style.background = rgbStr(rgb, 1); el.style.boxShadow = '0 0 8px ' + rgbStr(rgb, 0.9);
    });
    document.querySelectorAll('[data-status-text]').forEach(function (el) {
      el.textContent = paused ? 'paused' : m.status;
      el.style.color = modeName === 'idle' ? 'var(--muted)' : rgbStr(m.rgb, 1);
    });
    refreshCaption();
  }
  SARA.setStatus = function (state) {
    SARA.state.status = state;
    const next = BACKEND_TO_MODE[state] || 'idle';
    if (!BACKEND_TO_MODE[state] && !warnedStatus[state]) { warnedStatus[state] = 1; console.warn('[status] unknown backend status "' + state + '" -> showing idle'); }
    if (next === 'idle' && modeName !== 'idle') idleSince = Date.now();
    modeName = next;
    if (next !== 'idle') { setAmbientTier(0); lastActivity = Date.now(); }
    refreshChrome();
    $('stopBtn').hidden = (next === 'idle');
    SARA.emit('status', state);
  };
  SARA.on('ev:status', (state) => SARA.setStatus(state));
  // source: 'mic' while listening, 'tts' while speaking. Ignored otherwise (e.g. a late
  // 'tts' level arriving just after we've moved on to 'thinking').
  SARA.on('ev:audio_level', function (source, level) {
    const relevant = (source === 'mic' && modeName === 'listening') || (source === 'tts' && modeName === 'speaking');
    audioLevelTarget = relevant ? Math.max(0, Math.min(1, Number(level) || 0)) : 0;
  });
  setInterval(function () { if (modeName === 'idle') refreshCaption(); }, 30000);

  const hint = $('homeHint');
  SARA.on('ev:footer', function (text) { if (SARA.state.assistantActive && text) hint.textContent = text; });

  /* ---- Live caption: what the user is saying RIGHT NOW (backend push 'transcript_partial' role,text) ----
     One short line just under the orb caption. Real-time data, so NO typewriter: each partial replaces the
     previous one instantly; only the show/hide is a soft fade. Long phrases keep the newest words visible
     (older words fade out on the left). Empty text, the final 'transcript', or leaving the listening state
     hides it; an 8s safety timer hides it if the "clear" event ever gets lost.
     The element is created here (no index.html change): a zero-height anchor after #homeCaption, so it
     never moves the orb. While it is visible it takes the spot of the hint line (hint is hidden meanwhile). */
  const LIVE_STALE_MS = 8000;
  let liveTimer = 0;
  const liveAnchor = document.createElement('div'); liveAnchor.className = 'home-live-anchor';
  const live = document.createElement('div'); live.className = 'home-live'; live.id = 'homeLive';
  const liveText = document.createElement('span'); liveText.className = 'home-live-text';
  live.appendChild(liveText); liveAnchor.appendChild(live);
  if (caption && caption.parentNode) caption.parentNode.insertBefore(liveAnchor, caption.nextSibling);

  function hideLive() {
    clearTimeout(liveTimer); liveTimer = 0;
    live.classList.remove('show'); hint.classList.remove('muted');
  }
  function showLive(text) {
    liveText.textContent = text;
    live.classList.toggle('still', !!SARA.reduceMotion);                    // in-app reduced-motion flag (CSS media query covers the OS one)
    live.classList.toggle('long', liveText.offsetWidth > live.clientWidth); // too wide -> right-align so the newest words stay visible
    live.classList.add('show'); hint.classList.add('muted');
    clearTimeout(liveTimer); liveTimer = setTimeout(hideLive, LIVE_STALE_MS);
  }
  SARA.on('ev:transcript_partial', function (role, text) {
    if (role && role !== 'user') return;
    text = String(text == null ? '' : text).trim();
    if (!text) { hideLive(); return; }
    showLive(text);
  });
  SARA.on('ev:transcript', function (role) { if (role === 'user') hideLive(); });          // final text arrived -> partial is done
  SARA.on('status', function (s) { if (s !== 'listening' && s !== 'waking') hideLive(); }); // Sara moved on (thinking/working/speaking/sleeping)

  /* ---- Live Activity pill: what Sara is doing right now ----
     Backend push 'activity' (state: start|done|error, icon, text). A new event REPLACES the current
     one instantly (no queue). done/error linger ~1.8s then fade; a 'start' that never gets a
     done/error (lost event) is hidden after 60s so the pill can never get stuck. */
  const act = $('homeActivity'), actGlyph = $('actGlyph'), actText = $('actText');
  const ACT_GLYPH = { start: '◉', done: '✓', error: '⚠︎' };
  const ACT_LINGER_MS = 1800, ACT_STUCK_MS = 60000, ACT_FADE_MS = 260;
  let actTimer = 0;
  function hideActivity() {
    clearTimeout(actTimer);
    act.classList.remove('show');
    actTimer = setTimeout(function () { act.hidden = true; }, ACT_FADE_MS);
  }
  SARA.on('ev:activity', function (state, icon, text) {
    if (!act || !ACT_GLYPH[state]) return;
    clearTimeout(actTimer);
    act.hidden = false;
    act.dataset.state = state; act.dataset.icon = icon || '';
    actGlyph.textContent = ACT_GLYPH[state]; actText.textContent = text || '';
    void act.offsetWidth;                       // restart the fade-in even if a fade-out was in progress
    act.classList.add('show');
    actTimer = setTimeout(hideActivity, state === 'start' ? ACT_STUCK_MS : ACT_LINGER_MS);
  });

  /* ---- wake + pause ---- */
  SARA.wake = function () {
    SARA.sound.wake();
    SARA.setStatus('waking');            // optimistic; the real 'status' pushes take over
    SARA.callApi('wake_now');
  };
  $('stage').addEventListener('click', SARA.wake);

  function renderPause() {
    const on = SARA.state.assistantActive;
    $('pauseBtn').textContent = on ? 'pause listening' : 'resume listening';
    hint.textContent = on ? 'Say “Sara”, or tap the orb.' : 'Resume listening when you want Sara back.';
    SARA.setToggle($('setActive'), on);
  }
  SARA.setAssistantActive = async function (active, persist) {
    SARA.state.assistantActive = !!active;
    renderPause(); refreshChrome();
    if (persist) await SARA.callApi('set_assistant_active', !!active);
  };
  $('stopBtn').addEventListener('click', async function () {   // interrupt speech / cancel the current turn
    await SARA.callApi('stop_sara'); SARA.setStatus('sleeping');
  });
  $('pauseBtn').addEventListener('click', function () {
    const next = !SARA.state.assistantActive;
    SARA.sound.toggle(next); SARA.setAssistantActive(next, true);
  });
  SARA.onBoot(async function () {
    const res = await SARA.callApi('get_assistant_active');
    if (res && typeof res.active === 'boolean') SARA.setAssistantActive(res.active, false);
  });

  /* ---- the orb (canvas; drawing code carried over from the mockup) ---- */
  const canvas = $('org'), ctx = canvas.getContext ? canvas.getContext('2d') : null, stage = $('stage');
  const orb = SARA.orb = { t: 0, energy: MODES.idle.energy };
  let curRGB = MODES.idle.rgb.slice(), lisW = 0, spkW = 0, wrkW = 0;   // lisW/spkW/wrkW = eased 0..1 weights of the listening/speaking/working effects
  // Real mic/TTS amplitude (0..1), pushed by the backend as 'audio_level' (source, level).
  // audioLevelTarget decays on its own each frame so a stalled/missing event stream fades
  // back to the plain simulated motion instead of freezing the orb at a stale level.
  let audioLevel = 0, audioLevelTarget = 0;  function fit() {
    if (!ctx) return;
    const rect = stage.getBoundingClientRect(); if (!rect.width) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = rect.width * dpr; canvas.height = rect.height * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  window.addEventListener('resize', fit); fit();
  SARA.on('page', (p) => { if (p === 'home') requestAnimationFrame(fit); });

  const particles = Array.from({ length: 32 }, () => ({
    a: Math.random() * Math.PI * 2, r: 0.32 + Math.random() * 0.42, speed: (Math.random() - 0.5) * 0.0016,
    size: 0.6 + Math.random() * 1.3, phase: Math.random() * Math.PI * 2, twinkle: 0.4 + Math.random() * 0.6
  }));

  /* ---- perf: pause the rAF loop entirely when nobody can see it (tab hidden or a different page),
     and adaptively trim particle/ring density on slow machines instead of just getting janky. ---- */
  let orbRunning = false, lastFrameTs = 0, avgFrameMs = 16.7, quality = 1;
  const shouldRunOrb = () => !document.hidden && (SARA.current || 'home') === 'home';
  function scheduleOrb() { if (!orbRunning) { orbRunning = true; lastFrameTs = 0; requestAnimationFrame(draw); } }
  document.addEventListener('visibilitychange', function () { if (!document.hidden) scheduleOrb(); });
  SARA.on('page', function (p) { if (p === 'home') scheduleOrb(); });

  function draw(now) {
    if (!shouldRunOrb()) { orbRunning = false; return; }   // resumed by scheduleOrb() once visible/on-page again
    now = now || performance.now();
    const dt = lastFrameTs ? (now - lastFrameTs) : 16.7; lastFrameTs = now;
    avgFrameMs += (Math.min(dt, 200) - avgFrameMs) * 0.05;                 // smoothed frame time (clamp huge tab-switch gaps)
    // Below ~50fps -> step quality down a notch; once frame time is healthy again, ease back up
    // gradually (never snap) so the change itself isn't a visible jump.
    const targetQuality = avgFrameMs > 20 ? Math.max(0.55, quality - 0.12) : 1;
    quality += (targetQuality - quality) * 0.04;
    orb.t += 1; const t = orb.t;
    const paused = isPausedIdle();
    const target = MODES[modeName], tRGB = paused ? MUTED_RGB : target.rgb, tEnergy = paused ? 0.12 : target.energy;
    for (let i = 0; i < 3; i++) curRGB[i] += (tRGB[i] - curRGB[i]) * 0.045;
    orb.energy += (tEnergy - orb.energy) * 0.05;
    lisW += ((modeName === 'listening' ? 1 : 0) - lisW) * 0.06;
    spkW += ((modeName === 'speaking' ? 1 : 0) - spkW) * 0.06;
    wrkW += ((modeName === 'working' ? 1 : 0) - wrkW) * 0.06;
    audioLevelTarget *= 0.96;                          // fades to 0 if events stop arriving
    audioLevel += (audioLevelTarget - audioLevel) * 0.25;
    // 0.65 floor keeps the existing simulated motion as the base (never fully flat);
    // up to +70% on top of that when the real level is loud.
    const ambientMul = ambientTier === 2 ? 0.45 : (ambientTier === 1 ? 0.75 : 1);
    const curEnergy = orb.energy * (0.65 + 0.7 * audioLevel) * ambientMul;
    const w = stage.clientWidth, h = stage.clientHeight;
    if (ctx && w > 0) {
      ctx.clearRect(0, 0, w, h);
      const still = SARA.reduceMotion;
      const cx = w / 2 + (still ? 0 : Math.sin(t * 0.0031) * 3 + Math.sin(t * 0.0017 + 1.3) * 2);
      const cy = h / 2 + (still ? 0 : Math.cos(t * 0.0027) * 3 + Math.sin(t * 0.0013 + 0.6) * 2);
      const rgbv = curRGB.map((v) => Math.round(v)), r = rgbv[0], g = rgbv[1], b = rgbv[2];

      const dens = (still ? 0.6 : 0.45 + curEnergy * 0.55) * quality;   // more particles fade in as Sara gets more active; trimmed by adaptive quality
      for (let pi = 0; pi < particles.length; pi++) {
        const p = particles[pi];
        p.a += p.speed * (1 + curEnergy * 0.6);
        const vis = Math.max(0, Math.min(1, (dens * 1.15 - pi / particles.length) * 5));
        if (vis <= 0.01) continue;
        const wobble = Math.sin(t * 0.01 + p.phase) * 6;
        const rad = p.r * Math.min(w, h) * 0.5 + wobble;
        const x = cx + Math.cos(p.a) * rad, y = cy + Math.sin(p.a) * rad * 0.86;
        const flick = 0.25 + Math.abs(Math.sin(t * 0.02 * p.twinkle + p.phase)) * 0.45;
        const alpha = flick * 0.55 * (0.4 + curEnergy * 0.6) * vis;
        if (!still && vis > 0.05) {                          // short fading tail behind the particle
          const tr = (0.05 + curEnergy * 0.09) * (p.speed < 0 ? -1 : 1);
          ctx.beginPath(); ctx.moveTo(x, y);
          ctx.lineTo(cx + Math.cos(p.a - tr) * rad, cy + Math.sin(p.a - tr) * rad * 0.86);
          ctx.strokeStyle = rgbStr(rgbv, (alpha * 0.45).toFixed(3)); ctx.lineWidth = p.size * 0.8; ctx.lineCap = 'round'; ctx.stroke();
        }
        ctx.beginPath(); ctx.arc(x, y, p.size + curEnergy * 0.6, 0, Math.PI * 2);
        ctx.fillStyle = rgbStr(rgbv, alpha.toFixed(3)); ctx.fill();
      }
      const breathe = 1 + Math.sin(t * 0.018) * (0.045 + curEnergy * 0.05) + Math.sin(t * 0.007 + 1.4) * (0.02 + curEnergy * 0.02);
      const baseR = Math.min(w, h) * 0.155 * breathe;
      const ringCount = Math.max(2, Math.round(4 * quality));
      for (let ring = ringCount; ring >= 1; ring--) {
        const lag = ring * 0.9;
        const rr = baseR * (1 + ring * 0.5) + Math.sin(t * 0.014 - lag) * 5 * curEnergy + Math.sin(t * 0.006 + lag) * 3;
        const grad = ctx.createRadialGradient(cx, cy, rr * 0.35, cx, cy, rr);
        grad.addColorStop(0, rgbStr(rgbv, (0.10 / ring).toFixed(3))); grad.addColorStop(1, rgbStr(rgbv, 0));
        ctx.fillStyle = grad; ctx.beginPath(); ctx.arc(cx, cy, rr, 0, Math.PI * 2); ctx.fill();
      }
      if (wrkW > 0.01) {   // 'working': a comet-like arc circling the core ("something is being done"); reduced-motion -> static dashed ring
        const orbitR = baseR * 1.85;
        ctx.save(); ctx.lineWidth = 2.2; ctx.lineCap = 'butt';
        if (still) {
          ctx.setLineDash([2, 8]); ctx.strokeStyle = rgbStr(rgbv, (0.4 * wrkW).toFixed(3));
          ctx.beginPath(); ctx.arc(cx, cy, orbitR, 0, Math.PI * 2); ctx.stroke();
        } else {
          ctx.strokeStyle = rgbStr(rgbv, (0.10 * wrkW).toFixed(3));           // faint full track
          ctx.beginPath(); ctx.arc(cx, cy, orbitR, 0, Math.PI * 2); ctx.stroke();
          const head = t * 0.05, sweep = Math.PI * 1.15, segs = 24;          // ~1.9s per lap at 60fps
          for (let s = 0; s < segs; s++) {
            const fade = 1 - s / segs;
            ctx.strokeStyle = rgbStr(rgbv, (0.85 * fade * fade * wrkW).toFixed(3));
            ctx.beginPath(); ctx.arc(cx, cy, orbitR, head - sweep * (s + 1) / segs, head - sweep * s / segs); ctx.stroke();
          }
        }
        ctx.restore();
      }
      ctx.beginPath();
      const pts = 100;
      for (let i = 0; i <= pts; i++) {
        const a = (i / pts) * Math.PI * 2;
        const wob = Math.sin(a * 3 + t * 0.045) * 3.2 * curEnergy + Math.sin(a * 5 - t * 0.028) * 1.8 * curEnergy +
          Math.sin(a * 2 + t * 0.011) * 2.4 +
          lisW * Math.sin(t * 0.5 + a * 9) * 2.2 +
          spkW * Math.sin(t * 0.32 + a * 6) * 2.6 +
          (still ? 0 : wrkW * Math.sin(a * 4 - t * 0.09) * 2.4);   // working: wave travelling the opposite way to the arc
        const rr = baseR + wob, x = cx + Math.cos(a) * rr, y = cy + Math.sin(a) * rr;
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.closePath();
      const coreGrad = ctx.createRadialGradient(cx - baseR * 0.28, cy - baseR * 0.32, baseR * 0.05, cx, cy, baseR * 1.15);
      coreGrad.addColorStop(0, rgbStr([Math.min(255, r + 55), Math.min(255, g + 45), Math.min(255, b + 40)], 0.95));
      coreGrad.addColorStop(0.45, rgbStr(rgbv, 0.85)); coreGrad.addColorStop(1, rgbStr(rgbv, 0.08));
      ctx.fillStyle = coreGrad;
      ctx.shadowColor = rgbStr(rgbv, 0.55 + curEnergy * 0.3); ctx.shadowBlur = 26 * curEnergy + 10;
      ctx.fill(); ctx.shadowBlur = 0;
      const hlA = t * 0.006, hlx = cx + Math.cos(hlA) * baseR * 0.32, hly = cy + Math.sin(hlA) * baseR * 0.32 * 0.7 - baseR * 0.28;
      const hl = ctx.createRadialGradient(hlx, hly, 0, hlx, hly, baseR * 0.5);
      hl.addColorStop(0, 'rgba(255,255,255,0.28)'); hl.addColorStop(1, 'rgba(255,255,255,0)');
      ctx.fillStyle = hl; ctx.beginPath(); ctx.arc(hlx, hly, baseR * 0.5, 0, Math.PI * 2); ctx.fill();
    }
    requestAnimationFrame(draw);
  }
  scheduleOrb();
  refreshChrome(); renderPause();
})();