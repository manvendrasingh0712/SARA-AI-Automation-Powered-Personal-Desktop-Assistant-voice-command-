/* ==========================================================================
   clock-card.js -- Home Clock Card: live time + seconds + date + dynamic
   greeting, expanding (like the Today card) into a sheet with Clock / Timer /
   Stopwatch modes and 5 clock-only themes.
   Clock/timer/stopwatch math is all real-timestamp based (Date.now() diffs),
   never a naive per-second counter, so nothing drifts if a tab is backgrounded.
   Backend link: API calls start_timer / cancel_timer / start_stopwatch /
   pause_stopwatch / resume_stopwatch / reset_stopwatch (app/clock_api.py),
   which push 'ev:timer_state' / 'ev:stopwatch_state' -- the SAME events this
   file listens for below, so a future voice command that calls those same
   Api methods drives this card with no further frontend changes.
   Styles: style/clock-card.css.  Markup: index.html #clockCard, #clockSheet.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA, $ = SARA.$;
  const THEMES = ['sara', 'aurora', 'midnight', 'glass', 'pulse'];
  const RING_R = 96, RING_C = 2 * Math.PI * RING_R;

  const card = $('clockCard'), sheet = $('clockSheet'), sheetInner = $('clockSheetInner');
  if (!card || !sheet || !sheetInner) return;   // markup missing -- fail quietly, rest of Home still works

  const el = {
    ccGreet: $('ccGreet'), ccModeLabel: $('ccModeLabel'), ccTime: $('ccTime'), ccAmpm: $('ccAmpm'),
    ccSec: $('ccSec'), ccDate: $('ccDate'), ccStatus: $('ccStatus'), ccBarFill: $('ccBarFill'),
    csGreet: $('csGreet'), csModeLabel: $('csModeLabel'), csTime: $('csTime'), csSub: $('csSub'),
    csStatus: $('csStatus'), csRingFg: $('csRingFg'),
    timerPanel: $('csTimerPanel'), timerMins: $('csTimerMins'), timerStart: $('csTimerStart'),
    timerControls: $('csTimerControls'), timerPause: $('csTimerPause'), timerResume: $('csTimerResume'), timerCancel: $('csTimerCancel'),
    swPanel: $('csStopwatchPanel'), swStart: $('swStart'), swPause: $('swPause'), swResume: $('swResume'), swReset: $('swReset')
  };
  el.csRingFg.style.strokeDasharray = RING_C.toFixed(2);

  /* ---- theme (isolated to this card only -- see style/clock-card.css) ---- */
  function applyTheme(name) {
    if (THEMES.indexOf(name) < 0) name = 'sara';
    card.dataset.clockTheme = name; sheetInner.dataset.clockTheme = name;
    document.querySelectorAll('#csThemes .chip').forEach((c) => c.classList.toggle('on', c.dataset.theme === name));
    try { localStorage.setItem('sara_clock_theme', name); } catch (e) {}
  }
  let savedTheme = 'sara';
  try { savedTheme = localStorage.getItem('sara_clock_theme') || 'sara'; } catch (e) {}
  applyTheme(savedTheme);
  $('csThemes').addEventListener('click', function (e) {
    const b = e.target.closest('.chip[data-theme]'); if (!b) return;
    SARA.sound.tap(); applyTheme(b.dataset.theme);
    if (!SARA.reduceMotion) { b.classList.remove('just-picked'); void b.offsetWidth; b.classList.add('just-picked'); }
  });

  /* ---- hover spotlight (Home card only -- the expanded sheet doesn't need it): a soft radial
     highlight that tracks the pointer, same rAF-throttle idea as fx.js's ambient parallax.
     Fully skipped under reduced-motion (listener never attached). ---- */
  if (!SARA.reduceMotion) {
    let sx = 50, sy = 0, raf = 0;
    function applySpot() { raf = 0; card.style.setProperty('--spot-x', sx.toFixed(1) + '%'); card.style.setProperty('--spot-y', sy.toFixed(1) + '%'); }
    card.addEventListener('pointermove', function (e) {
      const r = card.getBoundingClientRect();
      sx = ((e.clientX - r.left) / r.width) * 100; sy = ((e.clientY - r.top) / r.height) * 100;
      if (!raf) raf = requestAnimationFrame(applySpot);
    }, { passive: true });
  }

  /* ---- date/status sub-line reveal: a brief fade whenever the visible text actually changes
     (mode switch, day rollover) -- not on every 1s tick. ---- */
  function revealIfChanged(el, text) {
    if (el.textContent === text) return;
    el.textContent = text;
    if (SARA.reduceMotion) return;
    el.classList.remove('reveal'); void el.offsetWidth; el.classList.add('reveal');
  }

  /* ---- state machine: clock | timer | stopwatch (only one is ever dominant) ---- */
  let mode = 'clock';
  let timer = null;       // {target, duration, paused, remainingAtPause, completed, timeoutBackToClock}
  let stopwatch = null;   // {startTs, elapsedBefore, running}

  function pad2(n) { return String(n).padStart(2, '0'); }
  function greetingText(d) {
    const h = d.getHours();
    if (h < 5) return 'Good Night';
    if (h < 12) return 'Good Morning';
    if (h < 17) return 'Good Afternoon';
    if (h < 21) return 'Good Evening';
    return 'Good Night';
  }

  function setMode(next) {
    mode = next;
    const isClock = mode === 'clock';
    el.ccModeLabel.hidden = isClock; el.csModeLabel.hidden = isClock;
    el.ccGreet.hidden = !isClock; el.csGreet.hidden = !isClock;
    if (!isClock) { el.ccModeLabel.textContent = el.csModeLabel.textContent = mode.toUpperCase(); }
    el.timerPanel.hidden = mode !== 'timer';
    el.swPanel.hidden = mode !== 'stopwatch';
    if (mode !== 'stopwatch') sheetInner.dataset.swRunning = '0';
    if (mode !== 'timer') sheetInner.dataset.urgent = '0';
    document.querySelectorAll('.cs-mode-btn').forEach((b) => b.classList.toggle('on', b.dataset.mode === mode));
    card.dataset.mode = mode;
    tick();
  }

  function renderClock(now) {
    let h = now.getHours(); const m = pad2(now.getMinutes()), s = now.getSeconds();
    const ap = h >= 12 ? 'PM' : 'AM'; h = h % 12 || 12;
    const t = h + ':' + m;
    el.ccTime.textContent = t; el.ccAmpm.textContent = ap; el.ccSec.textContent = pad2(s);
    el.csTime.textContent = t + ' ' + ap;
    const day = now.toLocaleDateString(undefined, { weekday: 'long' });
    const date = now.toLocaleDateString(undefined, { day: 'numeric', month: 'long' });
    revealIfChanged(el.ccDate, day + ', ' + date);
    el.csSub.textContent = pad2(s) + ' sec \u00b7 ' + day + ', ' + date;   // ticks every second on purpose -- no reveal here
    const greet = greetingText(now);
    if (el.ccGreet.textContent !== greet) { el.ccGreet.textContent = greet; el.csGreet.textContent = greet; }
    const frac = s / 60;
    el.ccBarFill.style.width = (frac * 100).toFixed(1) + '%';
    el.csRingFg.style.strokeDashoffset = (RING_C * (1 - frac)).toFixed(2);
    el.ccStatus.hidden = true; el.csStatus.hidden = true;
  }

  function renderTimer(now) {
    if (!timer) return;
    const remaining = timer.paused ? timer.remainingAtPause : Math.max(0, timer.target - now.getTime());
    const totalSec = Math.round(remaining / 1000);
    const text = pad2(Math.floor(totalSec / 60)) + ':' + pad2(totalSec % 60);
    el.ccTime.textContent = text; el.ccAmpm.textContent = ''; el.ccSec.textContent = '';
    el.csTime.textContent = text;
    const label = timer.completed ? 'Timer complete' : (timer.paused ? 'Timer paused' : 'Timer running');
    revealIfChanged(el.ccDate, label); el.csSub.textContent = label;
    const frac = timer.duration > 0 ? Math.max(0, Math.min(1, remaining / (timer.duration * 1000))) : 0;
    el.ccBarFill.style.width = (frac * 100).toFixed(1) + '%';
    el.csRingFg.style.strokeDashoffset = (RING_C * (1 - frac)).toFixed(2);
    // Urgency: last 15% of the countdown gets the danger-tinted ring (see style/clock-card.css).
    // Cleared as soon as it's no longer true -- never sticks around after a pause/resume/cancel.
    sheetInner.dataset.urgent = (!timer.completed && frac > 0 && frac <= 0.15) ? '1' : '0';
    if (!timer.paused && remaining <= 0 && !timer.completed) {
      timer.completed = true;
      el.ccStatus.hidden = false; el.csStatus.hidden = false;
      el.ccStatus.textContent = el.csStatus.textContent = 'Timer complete';
      sheetInner.classList.add('cs-pulse'); setTimeout(() => sheetInner.classList.remove('cs-pulse'), 900);
      SARA.toast('ti-alarm-clock', 'var(--core)', 'Timer complete', { tone: 'notify' });
      timer.timeoutBackToClock = setTimeout(function () { timer = null; setMode('clock'); }, 6000);
    }
  }

  function renderStopwatch(now) {
    if (!stopwatch) return;
    const elapsed = stopwatch.running ? (stopwatch.elapsedBefore + (now.getTime() - stopwatch.startTs)) : stopwatch.elapsedBefore;
    const totalSec = Math.floor(elapsed / 1000);
    const hh = Math.floor(totalSec / 3600), mm = pad2(Math.floor((totalSec % 3600) / 60)), ss = pad2(totalSec % 60);
    const text = (hh > 0 ? hh + ':' : '') + mm + ':' + ss;
    el.ccTime.textContent = text; el.ccAmpm.textContent = ''; el.ccSec.textContent = '';
    el.csTime.textContent = text;
    const label = stopwatch.running ? 'Stopwatch running' : 'Stopwatch paused';
    revealIfChanged(el.ccDate, label); el.csSub.textContent = label;
    sheetInner.dataset.swRunning = stopwatch.running ? '1' : '0';
  }

  function tick() {
    const now = new Date();
    if (mode === 'timer') renderTimer(now);
    else if (mode === 'stopwatch') renderStopwatch(now);
    else renderClock(now);
  }
  setInterval(tick, 1000); tick();

  /* ---- open / close (same overlay mechanism as the Today card, js/today.js) ---- */
  function openSheet() { SARA.openOverlay('clockSheet'); tick(); }
  card.addEventListener('click', openSheet);
  card.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openSheet(); } });
  $('clockSheetClose').addEventListener('click', () => SARA.closeOverlay('clockSheet'));
  document.querySelectorAll('.cs-mode-btn').forEach((b) => b.addEventListener('click', function () { setMode(b.dataset.mode); }));

  /* ---- timer controls ---- */
  function beginTimer(seconds) {
    seconds = Math.max(1, Math.round(seconds));
    timer = { target: Date.now() + seconds * 1000, duration: seconds, paused: false, remainingAtPause: 0, completed: false };
    el.timerControls.hidden = false; el.timerPause.hidden = false; el.timerResume.hidden = true;
    setMode('timer');
  }
  el.timerStart.addEventListener('click', async function () {
    const mins = Math.max(1, Math.min(720, parseInt(el.timerMins.value, 10) || 10));
    await SARA.callApi('start_timer', mins * 60);
    beginTimer(mins * 60);
  });
  el.timerPause.addEventListener('click', function () {
    if (!timer || timer.paused) return;
    timer.remainingAtPause = Math.max(0, timer.target - Date.now()); timer.paused = true;
    el.timerPause.hidden = true; el.timerResume.hidden = false; tick();
  });
  el.timerResume.addEventListener('click', function () {
    if (!timer || !timer.paused) return;
    timer.target = Date.now() + timer.remainingAtPause; timer.paused = false;
    el.timerPause.hidden = false; el.timerResume.hidden = true; tick();
  });
  el.timerCancel.addEventListener('click', async function () {
    await SARA.callApi('cancel_timer');
    if (timer && timer.timeoutBackToClock) clearTimeout(timer.timeoutBackToClock);
    timer = null; el.timerControls.hidden = true; sheetInner.dataset.urgent = '0'; setMode('clock');
  });

  /* ---- stopwatch controls ---- */
  el.swStart.addEventListener('click', async function () {
    await SARA.callApi('start_stopwatch');
    stopwatch = { startTs: Date.now(), elapsedBefore: 0, running: true };
    el.swStart.hidden = true; el.swPause.hidden = false; el.swResume.hidden = true; setMode('stopwatch');
  });
  el.swPause.addEventListener('click', async function () {
    if (!stopwatch || !stopwatch.running) return;
    await SARA.callApi('pause_stopwatch');
    stopwatch.elapsedBefore += Date.now() - stopwatch.startTs; stopwatch.running = false;
    el.swPause.hidden = true; el.swResume.hidden = false; tick();
  });
  el.swResume.addEventListener('click', async function () {
    if (!stopwatch || stopwatch.running) return;
    await SARA.callApi('resume_stopwatch');
    stopwatch.startTs = Date.now(); stopwatch.running = true;
    el.swResume.hidden = true; el.swPause.hidden = false; tick();
  });
  el.swReset.addEventListener('click', async function () {
    await SARA.callApi('reset_stopwatch');
    stopwatch = { startTs: Date.now(), elapsedBefore: 0, running: false };
    el.swStart.hidden = false; el.swPause.hidden = true; el.swResume.hidden = true; tick();
  });

  /* ---- SARA voice-command hooks: whenever the backend calls the same
     start_timer/start_stopwatch/etc. Api methods (app/clock_api.py), it pushes
     these events, so the card reacts even if a button was never clicked. ---- */
  SARA.on('ev:timer_state', function (state) {
    if (!state) return;
    if (state.status === 'started' && state.duration) beginTimer(state.duration);
    else if (state.status === 'cancelled') {
      if (timer && timer.timeoutBackToClock) clearTimeout(timer.timeoutBackToClock);
      timer = null; el.timerControls.hidden = true; sheetInner.dataset.urgent = '0'; setMode('clock');
    } else tick();
  });
  SARA.on('ev:stopwatch_state', function (state) {
    if (!state) return;
    if (state.status === 'started') {
      stopwatch = { startTs: Date.now(), elapsedBefore: 0, running: true };
      el.swStart.hidden = true; el.swPause.hidden = false; el.swResume.hidden = true; setMode('stopwatch');
    } else if (state.status === 'reset') {
      stopwatch = { startTs: Date.now(), elapsedBefore: 0, running: false };
      el.swStart.hidden = false; el.swPause.hidden = true; el.swResume.hidden = true; tick();
    } else tick();
  });

  setMode('clock');
})();