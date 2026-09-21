/* ==========================================================================
   fx.js -- small cross-page motion helpers. NO backend calls.
   1) Sliding active-page underline in the bottom nav.  2) Direction-aware page slide (left/right by nav order).
   3) Safety net for skeleton placeholders: if data never arrives, swap the shimmer for a quiet "—"/message.
   Listens to the 'page' event from js/core.js.  Styles: style/layout.css (.nav-ind, .page.slide-*), components.css (.skel).
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA, $ = SARA.$;
  const nav = $('navBar'), ind = $('navInd');

  /* ---- 1) nav indicator ---- */
  function moveInd() {
    const b = nav.querySelector('button.active'); if (!b || !ind) return;
    ind.style.width = b.offsetWidth + 'px';
    ind.style.transform = 'translateX(' + b.offsetLeft + 'px)';
  }
  if (ind) {
    ind.classList.add('no-anim');                       // first placement (and font-load re-measure) should not animate
    setTimeout(() => ind.classList.remove('no-anim'), 600);
    SARA.on('page', moveInd);
    window.addEventListener('resize', moveInd);
    moveInd(); requestAnimationFrame(moveInd); setTimeout(moveInd, 200);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(moveInd);
  }

  /* ---- 2) page slide direction ---- */
  const order = Array.prototype.map.call(nav.querySelectorAll('button[data-page]'), (b) => b.dataset.page);
  let prev = 'home';
  SARA.on('page', function (name) {
    const page = $('page-' + name); if (!page) return;
    const dir = order.indexOf(name) >= order.indexOf(prev) ? 'slide-r' : 'slide-l';
    page.classList.remove('slide-r', 'slide-l'); page.classList.add(dir);
    prev = name;
  });

  /* ---- 3) skeleton safety net ---- */
  setTimeout(function () {
    document.querySelectorAll('[data-skel]').forEach(function (el) { if (el.querySelector('.skel')) el.textContent = 'Unavailable right now.'; });
    document.querySelectorAll('.skel-inline').forEach((el) => el.replaceWith(document.createTextNode('—')));
  }, 25000);
})();
