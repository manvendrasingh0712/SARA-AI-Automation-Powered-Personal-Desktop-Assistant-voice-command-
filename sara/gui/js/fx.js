/* ==========================================================================
   fx.js -- small cross-page motion helpers. NO backend calls.
   1) Sliding active-page underline in the bottom nav.  2) Direction-aware page slide (left/right by nav order).
   3) Safety net for skeleton placeholders: if data never arrives, swap the shimmer for a quiet "—"/message.
   4) Cursor parallax: nudges the ambient nebula (style/ambient.css) a few px toward the pointer, rAF-throttled.
   5) Ripple: delegated pointerdown -> small expanding-circle press effect on pill/chip/dock/quick-action buttons.
   Listens to the 'page' event from js/core.js.  Styles: style/layout.css (.nav-ind, .page.slide-*), components.css (.skel, .ripple).
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

  /* ---- 4) cursor parallax (skipped entirely under reduced-motion) ---- */
  if (!SARA.reduceMotion) {
    const root = document.documentElement;
    let mx = 0, my = 0, tx = 0, ty = 0, raf = 0;
    function applyParallax() {
      raf = 0;
      tx += (mx - tx) * 0.08; ty += (my - ty) * 0.08;
      root.style.setProperty('--mx', tx.toFixed(1) + 'px');
      root.style.setProperty('--my', ty.toFixed(1) + 'px');
      if (Math.abs(mx - tx) > 0.05 || Math.abs(my - ty) > 0.05) raf = requestAnimationFrame(applyParallax);
    }
    window.addEventListener('pointermove', function (e) {
      mx = (e.clientX / window.innerWidth - 0.5) * 8;    // -4px..4px -- deliberately tiny, this is ambience not a gimmick
      my = (e.clientY / window.innerHeight - 0.5) * 8;
      if (!raf) raf = requestAnimationFrame(applyParallax);
    }, { passive: true });
  }

  /* ---- 5) ripple: one delegated listener covers buttons added later by any page's own JS too ---- */
  const RIPPLE_SEL = '.pill-btn,.chip,.dock-btn,.qa-btn,.mc-play,.app-tile';
  document.addEventListener('pointerdown', function (e) {
    if (SARA.reduceMotion) return;
    const el = e.target.closest(RIPPLE_SEL); if (!el || el.disabled) return;
    const rect = el.getBoundingClientRect();
    const size = Math.max(rect.width, rect.height) * 1.6;
    const span = document.createElement('span');
    span.className = 'ripple';
    span.style.width = span.style.height = size + 'px';
    span.style.left = (e.clientX - rect.left - size / 2) + 'px';
    span.style.top = (e.clientY - rect.top - size / 2) + 'px';
    el.appendChild(span);
    span.addEventListener('animationend', () => span.remove());
  }, { passive: true });
})();
