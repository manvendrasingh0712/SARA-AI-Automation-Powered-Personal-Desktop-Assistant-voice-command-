/* ==========================================================================
   aurum.js -- tiny helper for the "idle" effects (78-80) in style/aurum.css.
   After 25s without input the UI calms down (nav + header dim, dust brightens, orbit ring glows); any input restores it.
   Frontend only. Load right BEFORE js/main.js (after premium.js). Skipped under prefers-reduced-motion.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA;
  if (!SARA || SARA.reduceMotion || !document.body) return;
  const body = document.body, IDLE_MS = 25000;
  let timer = 0, idle = false;
  function wake() {
    if (idle) { idle = false; body.classList.remove('au-idle'); }
    clearTimeout(timer);
    timer = setTimeout(function () { if (!document.hidden) { idle = true; body.classList.add('au-idle'); } }, IDLE_MS);
  }
  ['pointermove', 'pointerdown', 'keydown', 'wheel', 'touchstart'].forEach(function (t) {
    window.addEventListener(t, wake, { passive: true, capture: true });
  });
  document.addEventListener('visibilitychange', function () { if (document.hidden) { clearTimeout(timer); if (idle) { idle = false; body.classList.remove('au-idle'); } } else wake(); });
  wake();
})();
