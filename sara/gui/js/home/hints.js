/* ==========================================================================
   hints.js -- F3. While Sara is idle on the Home page, the small hint line under the orb rotates through things you can say
   ("Try: what's the weather"), every 8 s with a soft cross-fade. It never touches the line while Sara is awake / listening / speaking,
   while the window is hidden, on another page, or when something else (the backend footer) has written different text:
   in that case it takes that text as the new starting line. Feature key 'hints' (off in the Low tier, switchable in Fine-tune effects);
   no animation under reduced motion (the text just changes). Only commands that exist are listed.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA, doc = document;
  const hint = doc.getElementById('homeHint'); if (!SARA || !hint) return;
  const TIPS = [
    'Try: “what’s the weather”', 'Try: “set a timer for 5 minutes”', 'Try: “remind me to drink water at 5 pm”',
    'Try: “open Chrome”', 'Try: “volume up”', 'Try: “what time is it”', 'Try: “tell me the news”'
  ];
  const EVERY_MS = 8000, FADE_MS = 320;
  let base = hint.textContent, ours = hint.textContent, step = 0, busy = false;

  function idleHome() {
    const st = SARA.state && SARA.state.status;                       // 'sleeping' = waiting for the wake word (or paused); anything else = awake / busy
    return !doc.hidden && (SARA.current || 'home') === 'home' && (!st || st === 'sleeping');
  }
  function show(text) {
    ours = text;
    if (SARA.reduceMotion || !SARA.quality || !SARA.quality.allows('hints')) { hint.textContent = text; return; }
    busy = true; hint.classList.add('hint-swap');
    setTimeout(function () { hint.textContent = text; hint.classList.remove('hint-swap'); busy = false; }, FADE_MS);
  }
  function tick() {
    if (busy || !idleHome() || !SARA.quality || !SARA.quality.allows('hints')) return;
    if (hint.textContent !== ours) { base = hint.textContent; ours = base; step = 0; return; }     // someone else wrote the line: adopt it, restart the cycle
    if (!hint.textContent.trim() || hint.offsetParent === null) return;
    const i = step++ % (TIPS.length + 1);
    show(i === TIPS.length ? base : TIPS[i]);
  }
  setInterval(tick, EVERY_MS);
  SARA.hints = { tick: tick, tips: TIPS.slice() };
})();
