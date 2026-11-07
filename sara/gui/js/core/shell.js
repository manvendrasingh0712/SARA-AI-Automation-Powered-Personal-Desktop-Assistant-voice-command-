/* ==========================================================================
   shell.js -- behaviour of the app shell and horizontal navigation.
   Owns: the responsive label pin, navbar quick controls, the logo (-> Home), the light/dark quick
   toggle, the header search button (opens the command palette), the hairline under the header once a page is scrolled,
   and the skip-to-content link.  The active-page pill itself is positioned by js/fx/fx.js, the page switching by js/core/core.js.
   Styles: style/core/shell.css.  Markup: index.html (#navBar, .page-header).  Nothing here calls the backend.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA, $ = SARA.$;
  const nav = $('navBar'); if (!nav) return;
  const store = {
    get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  };

  /* ---- preserve the label pin preference for compact window sizes ---- */
  const pin = $('navPin'), K_PIN = 'sara.nav.pinned';
  function applyPin(on) {
    document.body.classList.toggle('nav-pinned', !!on);
    if (pin) {
      pin.setAttribute('aria-pressed', on ? 'true' : 'false');
      const t = on ? 'Let navigation labels collapse' : 'Keep navigation labels expanded';
      pin.title = t; pin.setAttribute('aria-label', t);
    }
    requestAnimationFrame(function () { window.dispatchEvent(new Event('resize')); });     // the indicator and open sheets re-measure
  }
  applyPin(store.get(K_PIN) === '1');
  if (pin) pin.addEventListener('click', function () {
    const on = !document.body.classList.contains('nav-pinned');
    applyPin(on); store.set(K_PIN, on ? '1' : '0'); SARA.sound.tap();
  });

  /* ---- navbar quick controls ---- */
  const quick = $('navQuick'), quickMenu = $('navQuickMenu');
  function closeQuick() {
    if (!quick || !quickMenu) return;
    quickMenu.hidden = true; quick.setAttribute('aria-expanded', 'false');
  }
  if (quick && quickMenu) {
    quick.addEventListener('click', function () {
      const open = quickMenu.hidden;
      quickMenu.hidden = !open; quick.setAttribute('aria-expanded', open ? 'true' : 'false');
      SARA.sound.tap();
    });
    document.addEventListener('click', function (e) {
      if (!quickMenu.hidden && !quickMenu.contains(e.target) && !quick.contains(e.target)) closeQuick();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !quickMenu.hidden) { closeQuick(); quick.focus(); }
    });
    quickMenu.addEventListener('click', function (e) {
      if (e.target.closest('[data-proxy]')) closeQuick();
    });
  }

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
    if (isLight(cur)) {
      const dark = store.get(K_DARK);                         // Auto day / night can have remembered a light theme here: that would change nothing
      SARA.theme.apply(dark && !isLight(dark) ? dark : 'sara');
    } else { if (cur !== 'custom') store.set(K_DARK, cur); SARA.theme.apply('paper'); }
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

  /* ---- Home header: "Good evening, <name>" (the name is written into #greetName by js/home/home.js) ---- */
  const gw = $('homeGreetWord'), gc = $('homeGreetComma'), gn = $('greetName');
  function greeting() {
    const h = new Date().getHours();
    return h < 5 ? 'Good night' : h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : h < 21 ? 'Good evening' : 'Good night';
  }
  function paintGreeting() {
    if (gw) gw.textContent = greeting();
    if (gc && gn) gc.hidden = !gn.textContent.trim();
  }
  if (gw) {
    paintGreeting(); setInterval(function () { if (!document.hidden) paintGreeting(); }, 60000);
    if (gn && window.MutationObserver) new MutationObserver(paintGreeting).observe(gn, { childList: true, characterData: true, subtree: true });
  }

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
