/* ==========================================================================
   radial.js -- F6. Press and hold the orb (about half a second) and six quick actions fan out around it. Tap one to run it; Esc, a tap
   outside, changing page or hiding the window closes the menu. A normal tap on the orb still wakes Sara exactly as before.
   The actions are the same tiles as on the Apps page (SARA.apps from js/apps/apps.js: Notepad, Calculator, Chrome, Screenshot, Spotify,
   Task Manager), run through the same function, so nothing new is sent to the backend. Keyboard: the menu buttons are normal buttons
   (Tab / arrows / Enter), and the command palette has a "Quick actions" entry that opens the menu (SARA.radial.open()).
   Feature key 'radial' (Fine-tune effects; turning it off disables the long-press, the palette entry still works).
   Animation = transform / opacity only, none under reduced motion. Styles: style/shared/guide.css. Load AFTER js/apps/apps.js and js/home/home.js.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA, doc = document; if (!SARA) return;
  const stage = doc.getElementById('stage'); if (!stage) return;
  const WANT = ['Notepad', 'Calculator', 'Chrome', 'Screenshot', 'Spotify', 'Task Manager'];
  const HOLD_MS = 520, MOVE_PX = 9;
  const NS = 'http://www.w3.org/2000/svg';
  let menu = null, holdTimer = 0, startX = 0, startY = 0, swallowClick = false, prevFocus = null;

  const allowed = function () { return !SARA.quality || SARA.quality.allows('radial'); };

  function items() {
    const a = SARA.apps; if (!a || !a.open) return [];
    return WANT.map(function (l) { return a.open.filter(function (i) { return i.label === l; })[0]; }).filter(Boolean);
  }
  function icon(name) {
    const s = doc.createElementNS(NS, 'svg'); s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('aria-hidden', 'true');
    const g = doc.createElementNS(NS, 'g'); g.innerHTML = (SARA.apps && SARA.apps.icon && SARA.apps.icon[name]) || '';       // static icon markup from apps.js
    while (g.firstChild) s.appendChild(g.firstChild); return s;
  }

  function close(restoreFocus) {
    if (!menu) return;
    const m = menu; menu = null; doc.removeEventListener('keydown', onKey, true);
    m.classList.remove('open'); m.classList.add('closing');
    setTimeout(function () { m.remove(); }, SARA.reduceMotion ? 0 : 180);
    if (restoreFocus && prevFocus && prevFocus.focus) prevFocus.focus({ preventScroll: true });
    prevFocus = null;
  }
  function onKey(e) {
    if (!menu) return;
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(true); return; }
    if (e.key.indexOf('Arrow') === 0) {                                  // arrows walk around the ring
      const b = Array.prototype.slice.call(menu.querySelectorAll('.rm-item')), i = b.indexOf(doc.activeElement);
      e.preventDefault(); const step = (e.key === 'ArrowRight' || e.key === 'ArrowDown') ? 1 : -1;
      b[(i + step + b.length) % b.length].focus();
    }
  }

  function open() {
    if (menu) return;
    const list = items(); if (!list.length) return;
    if ((SARA.current || 'home') !== 'home') return;
    prevFocus = doc.activeElement;
    const r = stage.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    const radius = Math.max(84, Math.min(118, Math.min(r.width, r.height) * 0.46));
    menu = doc.createElement('div'); menu.className = 'rm'; menu.setAttribute('role', 'menu'); menu.setAttribute('aria-label', 'Quick actions');
    menu.style.setProperty('--cx', cx + 'px'); menu.style.setProperty('--cy', cy + 'px');
    list.forEach(function (it, i) {
      const ang = (-90 + i * (360 / list.length)) * Math.PI / 180;
      const b = doc.createElement('button'); b.type = 'button'; b.className = 'rm-item'; b.setAttribute('role', 'menuitem'); b.setAttribute('aria-label', it.label);
      b.style.setProperty('--x', (Math.cos(ang) * radius).toFixed(1) + 'px'); b.style.setProperty('--y', (Math.sin(ang) * radius).toFixed(1) + 'px');
      b.style.setProperty('--d', (i * 35) + 'ms');
      b.appendChild(icon(it.icon)); const lab = doc.createElement('span'); lab.className = 'rm-label'; lab.textContent = it.label; b.appendChild(lab);
      b.addEventListener('click', function (e) { e.stopPropagation(); close(false); try { SARA.apps.run(it); } catch (err) { console.error('[radial]', err); } });
      menu.appendChild(b);
    });
    menu.addEventListener('pointerdown', function (e) { if (e.target === menu) close(true); });          // a tap outside the ring closes it
    doc.body.appendChild(menu);
    doc.addEventListener('keydown', onKey, true);
    SARA.sound && SARA.sound.tap();
    requestAnimationFrame(function () { menu && menu.classList.add('open'); const f = menu && menu.querySelector('.rm-item'); if (f && !SARA.reduceMotion) f.focus({ preventScroll: true }); });
  }

  /* ---- long-press detection on the orb ---- */
  function cancelHold() { clearTimeout(holdTimer); holdTimer = 0; }
  stage.addEventListener('pointerdown', function (e) {
    if (e.button !== 0 || !allowed() || menu) return;
    startX = e.clientX; startY = e.clientY; cancelHold();
    holdTimer = setTimeout(function () { holdTimer = 0; swallowClick = true; open(); }, HOLD_MS);
  });
  stage.addEventListener('pointermove', function (e) { if (holdTimer && Math.hypot(e.clientX - startX, e.clientY - startY) > MOVE_PX) cancelHold(); });
  ['pointerup', 'pointercancel', 'pointerleave'].forEach(function (ev) { stage.addEventListener(ev, cancelHold); });
  stage.addEventListener('contextmenu', function (e) { if (swallowClick || menu) e.preventDefault(); });
  // the click that ends a long press must not also wake Sara: swallow it in the capture phase, before home.js sees it
  stage.addEventListener('click', function (e) { if (swallowClick) { swallowClick = false; e.stopImmediatePropagation(); e.preventDefault(); } }, true);
  doc.addEventListener('pointerup', function () { setTimeout(function () { swallowClick = false; }, 0); }, true);

  SARA.on('page', function () { close(false); });
  doc.addEventListener('visibilitychange', function () { if (doc.hidden) close(false); });
  SARA.radial = { open: open, close: function () { close(true); }, isOpen: function () { return !!menu; } };
})();
