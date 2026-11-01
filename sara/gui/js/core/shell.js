/* ==========================================================================
   shell.js -- behaviour of the app shell (redesign phase 2): the left sidebar and the page header bar.
   Owns: sidebar expand on hover / keyboard focus (>= 1180 px) and the "keep open" pin, the logo (-> Home), the light/dark quick
   toggle, the header search button (opens the command palette), the hairline under the header once a page is scrolled,
   and the skip-to-content link.  The active-page pill itself is positioned by js/fx/fx.js, the page switching by js/core/core.js.
   Styles: style/core/shell.css.  Markup: index.html (#navBar, .page-header).  Nothing here calls the backend.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA, $ = SARA.$;
  const nav = $('navBar'); if (!nav) return;
  const mm = function (q) { return window.matchMedia ? window.matchMedia(q) : { matches: false, addEventListener: function () {} }; };
  const rail = mm('(min-width:1000px)'), wide = mm('(min-width:1180px)');
  const store = {
    get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  };

  /* ---- layout mode, for anything that wants to know ---- */
  function mode() { document.body.dataset.nav = rail.matches ? 'rail' : 'bar'; }
  mode(); rail.addEventListener && rail.addEventListener('change', mode);

  /* ---- expand on hover (after 250 ms) or keyboard focus; collapse shortly after the pointer leaves ---- */
  let openT = 0, closeT = 0;
  function setOpen(on) {
    nav.classList.toggle('open', !!on);
    if (!on) { const t = $('navTip'); if (t) t.classList.remove('show'); }
  }
  nav.addEventListener('pointerenter', function (e) {
    if (e.pointerType === 'touch' || !wide.matches) return;
    clearTimeout(closeT); openT = setTimeout(function () { setOpen(true); }, 250);
  });
  nav.addEventListener('pointerleave', function () { clearTimeout(openT); closeT = setTimeout(function () { setOpen(false); }, 140); });
  nav.addEventListener('focusin', function (e) {
    if (!wide.matches || !e.target.matches || !e.target.matches(':focus-visible')) return;
    clearTimeout(closeT); setOpen(true);
  });
  nav.addEventListener('focusout', function (e) { if (!nav.contains(e.relatedTarget)) closeT = setTimeout(function () { setOpen(false); }, 140); });
  wide.addEventListener && wide.addEventListener('change', function () { if (!wide.matches) setOpen(false); });

  /* ---- pin ---- */
  const pin = $('navPin'), K_PIN = 'sara.nav.pinned';
  function applyPin(on) {
    document.body.classList.toggle('nav-pinned', !!on);
    if (pin) {
      pin.setAttribute('aria-pressed', on ? 'true' : 'false');
      const t = on ? 'Let the sidebar collapse' : 'Keep the sidebar open';
      pin.title = t; pin.setAttribute('aria-label', t);
    }
    requestAnimationFrame(function () { window.dispatchEvent(new Event('resize')); });     // the indicator and open sheets re-measure
  }
  applyPin(store.get(K_PIN) === '1');
  if (pin) pin.addEventListener('click', function () {
    const on = !document.body.classList.contains('nav-pinned');
    applyPin(on); store.set(K_PIN, on ? '1' : '0'); SARA.sound.tap();
  });

  /* ---- logo -> Home ---- */
  const logo = $('navLogo');
  if (logo) logo.addEventListener('click', function () { SARA.sound.tone(380, 0.035, 'sine', 0.022); SARA.gotoPage(logo.dataset.goto || 'home'); });

  /* ---- light / dark quick toggle: Paper by day, the dark theme you last used otherwise (same memory key as Settings > Auto day / night) ---- */
  const themeBtn = $('navTheme'), K_DARK = 'sara_auto_dark';
  function isLight(id) {
    const t = (SARA.theme && SARA.theme.list || []).filter(function (x) { return x.id === id; })[0];
    return !!t && t.category === 'light';
  }
  if (themeBtn) themeBtn.addEventListener('click', function () {
    if (!SARA.theme) return;
    const cur = SARA.theme.current();
    if (isLight(cur)) SARA.theme.apply(store.get(K_DARK) || 'sara');
    else { if (cur !== 'custom') store.set(K_DARK, cur); SARA.theme.apply('paper'); }
    SARA.sound.tap();
  });

  /* ---- header: search button + hairline once the page body is scrolled ---- */
  document.addEventListener('click', function (e) {
    const b = e.target.closest && e.target.closest('[data-open-palette]');
    if (b && SARA.openPalette) { SARA.sound.tap(); SARA.openPalette(); }
  });
  document.querySelectorAll('.page').forEach(function (page) {
    const head = page.querySelector('.page-header'), body = page.querySelector('.page-body');
    if (!head || !body) return;
    body.addEventListener('scroll', function () { head.classList.toggle('scrolled', body.scrollTop > 8); }, { passive: true });
  });

  /* ---- skip link: first stop of the Tab order, lands on the visible page ---- */
  const skip = $('skipLink');
  if (skip) skip.addEventListener('click', function (e) {
    e.preventDefault();
    const page = document.querySelector('.page.active'); if (!page) return;
    const target = page.querySelector('.page-body') || page;
    if (!target.hasAttribute('tabindex')) target.setAttribute('tabindex', '-1');
    target.focus({ preventScroll: true });
  });
})();
