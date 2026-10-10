/* ==========================================================================
   flow.js -- tiny scroll/drag awareness. Sets <html class="is-scrolling"> while anything scrolls (and 160ms after) and
   <html class="is-dragging"> while a slider / seek bar / mini-player / sheet is being dragged. style/flow.css and js/premium.js
   use those flags to pause decorative work. Frontend only, ~1KB, no dependencies.
   ========================================================================== */
(function () {
  'use strict';
  const root = document.documentElement; let st = 0;
  document.addEventListener('scroll', function () {
    if (!root.classList.contains('is-scrolling')) root.classList.add('is-scrolling');
    clearTimeout(st); st = setTimeout(function () { root.classList.remove('is-scrolling'); }, 160);
  }, { capture: true, passive: true });
  const DRAG = 'input[type=range],.seek-wrap,.mini-player,.sheet,.set-rail';
  document.addEventListener('pointerdown', function (e) {
    if (e.button > 0 || !e.target || !e.target.closest) return;
    if (e.target.closest(DRAG)) root.classList.add('is-dragging');
  }, true);
  const clear = function () { root.classList.remove('is-dragging'); };
  window.addEventListener('pointerup', clear, true); window.addEventListener('pointercancel', clear, true); window.addEventListener('blur', clear);
})();
