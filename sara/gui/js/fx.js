/* ==========================================================================
   fx.js -- small cross-page motion helpers. NO backend calls.
   1) Sliding active-page underline in the bottom nav.  2) Direction-aware page slide (left/right by nav order).
   3) Safety net for skeleton placeholders: if data never arrives, swap the shimmer for a quiet "—"/message.
   4) Cursor parallax: nudges the ambient nebula (style/ambient.css) a few px toward the pointer, rAF-throttled.
   5) Ripple: delegated pointerdown -> small expanding-circle press effect on pill/chip/dock/quick-action buttons.
   6) Command impact: on a successful 'activity' event, a soft energy wave travels from the orb to the relevant nav item.
   7) Glass sheen: cursor-following light, 1px sheen, border light and a tiny depth shift on cards, nav and quick actions.
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
    // staggered card/row entrance window (style/layout.css .stagger-in) -- only right after a page switch,
    // so periodic list refreshes never replay it. Timer is per-page and cleared on re-entry.
    if (!SARA.reduceMotion) {
      clearTimeout(page._stg); page.classList.add('stagger-in');
      page._stg = setTimeout(function () { page.classList.remove('stagger-in'); }, 450);
    }
    prev = name;
  });

  /* ---- 2b) shared-element page transition ---- */
  let ghost = null, ghostAnim = null, ghostFrom = null;
  function ghostClear() {
    if (ghostAnim) { try { ghostAnim.cancel(); } catch (e) {} ghostAnim = null; }
    if (ghost) { ghost.remove(); ghost = null; }
  }
  function presenceAnchor(name, settled) {
    let el = null;
    if (name === 'home') el = $('stage');
    else if (name === 'chat') el = $('chatMic');
    else { const pg = $('page-' + name); el = pg && pg.querySelector('.mini-orb'); }
    if (!el) return null;
    const r = settled ? SARA.restRect(el) : el.getBoundingClientRect();
    if (!r.width) return null;
    return { x: r.left + r.width / 2, y: r.top + r.height / 2, d: name === 'home' ? r.width * 0.31 : Math.max(r.width, r.height) };
  }
  SARA.on('pagebefore', function (name, from) {
    ghostFrom = null;
    if (SARA.skipSharedOnce || SARA.reduceMotion || document.hidden || name === from || typeof document.body.animate !== 'function') return;
    if (ghost) { const g = ghost.getBoundingClientRect(); ghostFrom = { x: g.left + g.width / 2, y: g.top + g.height / 2, d: g.width }; }
    else ghostFrom = presenceAnchor(from, false);
  });
  SARA.on('page', function (name) {
    const from = ghostFrom; ghostFrom = null;
    if (!from) return;
    ghostClear();
    const to = presenceAnchor(name, true);
    if (!to || !(from.d > 0)) return;
    const d0 = Math.max(12, from.d), sc = Math.max(0.15, to.d / d0);
    const T = function (x, y, s) { return 'translate(' + (x - d0 / 2).toFixed(1) + 'px,' + (y - d0 / 2).toFixed(1) + 'px) scale(' + s.toFixed(3) + ')'; };
    const g = document.createElement('div'); g.className = 'shared-ghost'; g.setAttribute('aria-hidden', 'true');
    g.style.width = g.style.height = d0 + 'px'; g.style.transform = T(from.x, from.y, 1);
    document.body.appendChild(g); ghost = g;
    const a = g.animate([
      { transform: T(from.x, from.y, 1), opacity: 0.85, easing: 'cubic-bezier(.22,1,.36,1)' },
      { transform: T(to.x, to.y, sc), opacity: 0.85, offset: 0.7, easing: 'linear' },
      { transform: T(to.x, to.y, sc), opacity: 0 }
    ], { duration: 520, easing: 'linear', fill: 'forwards' });
    ghostAnim = a;
    a.onfinish = a.oncancel = function () { if (ghostAnim === a) ghostClear(); };
  });

  /* ---- 3) skeleton safety net ---- */
  setTimeout(function () {
    document.querySelectorAll('[data-skel]').forEach(function (el) { if (el.querySelector('.skel')) el.textContent = 'Unavailable right now.'; });
    document.querySelectorAll('.skel-inline').forEach((el) => el.replaceWith(document.createTextNode('—')));
  }, 25000);

  /* ---- 3b) unified card spotlight (today/next cards; clock-card.js does its own) ---- */
  if (!SARA.reduceMotion) {
    ['.today-card', '.next-card'].forEach(function (sel) {
      const c = document.querySelector(sel); if (!c) return;
      let raf = 0, x = 50, y = 0;
      c.addEventListener('pointermove', function (e) {
        const r = c.getBoundingClientRect();
        x = (e.clientX - r.left) / r.width * 100; y = (e.clientY - r.top) / r.height * 100;
        if (!raf) raf = requestAnimationFrame(function () { raf = 0; c.style.setProperty('--spot-x', x.toFixed(1) + '%'); c.style.setProperty('--spot-y', y.toFixed(1) + '%'); });
      }, { passive: true });
    });
  }

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

  /* ---- 6) command impact: purely visual reaction to the existing 'activity' (state 'done') push ---- */
  const IMPACT_MAP = [
    [/music|song|track|playlist|spotify|volume|pause|resume|\bskip/i, 'apps'],
    [/remind|alarm|timer|\bnotes?\b|todo|calendar|schedule/i, 'notes'],
    [/routine|automation|macro|\bmode\b/i, 'automation'],
    [/setting|theme|dark mode|brightness|wi-?fi|bluetooth|\bmute|focus/i, 'settings'],
    [/\bopen|launch|chrome|browser|youtube|google|search|website|folder|\bfiles?\b|\bapps?\b/i, 'apps']
  ];
  const impact = { ring: null, bolt: null, anims: [], timer: 0, hitEl: null, hitTimer: 0 };
  function impactEls() {
    if (impact.ring) return;
    impact.ring = document.createElement('div'); impact.ring.className = 'impact-ring'; impact.ring.setAttribute('aria-hidden', 'true');
    impact.bolt = document.createElement('div'); impact.bolt.className = 'impact-bolt'; impact.bolt.setAttribute('aria-hidden', 'true');
    document.body.appendChild(impact.ring); document.body.appendChild(impact.bolt);
  }
  function impactClear() {
    impact.anims.forEach(function (a) { try { a.cancel(); } catch (e) { /* already finished */ } });
    impact.anims = [];
    clearTimeout(impact.timer); clearTimeout(impact.hitTimer);
    if (impact.hitEl) { impact.hitEl.classList.remove('impact-hit'); impact.hitEl = null; }
    if (impact.ring) { impact.ring.style.display = 'none'; impact.bolt.style.display = 'none'; }
  }
  function impactOrigin() {
    let el = (!SARA.current || SARA.current === 'home') ? $('stage') : null;
    if (!el) { const pg = $('page-' + SARA.current); el = pg && pg.querySelector('.mini-orb'); }
    if (el) { const r = el.getBoundingClientRect(); if (r.width) return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; }
    return { x: window.innerWidth / 2, y: Math.min(80, window.innerHeight * 0.15) };
  }
  function impactHit(el) {
    el.classList.remove('impact-hit'); void el.offsetWidth; el.classList.add('impact-hit'); impact.hitEl = el;
    impact.hitTimer = setTimeout(function () { el.classList.remove('impact-hit'); if (impact.hitEl === el) impact.hitEl = null; }, 1100);
  }
  SARA.on('ev:activity', function (state, icon, text) {
    if (state !== 'done' || document.hidden || !nav) return;
    impactClear();
    const hay = (icon || '') + ' ' + (text || '');
    let page = null;
    for (let i = 0; i < IMPACT_MAP.length; i++) { if (IMPACT_MAP[i][0].test(hay)) { page = IMPACT_MAP[i][1]; break; } }
    const btn = page ? nav.querySelector('button[data-page="' + page + '"]') : null, target = btn || nav;
    if (SARA.reduceMotion || typeof document.body.animate !== 'function') { impactHit(target); return; }
    impactEls();
    const o = impactOrigin(), tr = target.getBoundingClientRect(), tx = tr.left + tr.width / 2, ty = tr.top + tr.height / 2;
    const dx = tx - o.x, dy = ty - o.y, ang = Math.atan2(dy, dx) * 180 / Math.PI, travel = Math.max(380, Math.min(760, 140 + Math.hypot(dx, dy) * 0.5));
    const RS = 260, BL = 96, ring = impact.ring, bolt = impact.bolt;
    ring.style.display = 'block'; bolt.style.display = 'block';
    const r0 = 'translate(' + (o.x - RS / 2).toFixed(1) + 'px,' + (o.y - RS / 2).toFixed(1) + 'px) scale(';
    const aR = ring.animate([{ transform: r0 + '.12)', opacity: 0.75 }, { transform: r0 + '1)', opacity: 0 }], { duration: 640, easing: 'cubic-bezier(.16,.84,.3,1)', fill: 'forwards' });
    const b0 = 'translate(' + (o.x - BL).toFixed(1) + 'px,' + (o.y - 1).toFixed(1) + 'px) rotate(' + ang.toFixed(1) + 'deg)';
    const b1 = 'translate(' + (tx - BL).toFixed(1) + 'px,' + (ty - 1).toFixed(1) + 'px) rotate(' + ang.toFixed(1) + 'deg)';
    const aB = bolt.animate([{ transform: b0, opacity: 0 }, { transform: b0, opacity: 1, offset: 0.12 }, { transform: b1, opacity: 1, offset: 0.82 }, { transform: b1, opacity: 0 }], { duration: travel, easing: 'cubic-bezier(.4,0,.2,1)', fill: 'forwards' });
    aR.onfinish = function () { ring.style.display = 'none'; };
    aB.onfinish = function () { bolt.style.display = 'none'; };
    impact.anims = [aR, aB];
    impact.timer = setTimeout(function () { impactHit(target); }, Math.round(travel * 0.8));
  });

  /* ---- 7) glass sheen: one delegated pointermove, rAF-coalesced; only transform/opacity/background/border change ---- */
  const GLASS_SEL = '.set-card,.stat,.perf-num,.an-peak,.music-card,.nav,.qa-btn,.setup-check';
  const GL_TRANS = 'translate .4s var(--ease-premium), scale .4s var(--ease-premium), box-shadow .35s ease, border-color .3s ease';
  let glEl = null, glRaf = 0, glX = 0, glY = 0;
  function glEnsure(el) {
    if (el._gl && el._gl.parentNode === el) return el._gl;
    if (document.documentElement.classList.contains('theme-anim')) return null;
    if (!el._glInit) {
      el._glInit = true;
      const cs = window.getComputedStyle(el);
      if (cs.position === 'static') el.style.position = 'relative';
      const tr = cs.transition;
      el.style.transition = (tr && !/^all 0s/.test(tr) ? tr + ', ' : '') + GL_TRANS;
    }
    const s = document.createElement('span'); s.className = 'gl-sheen'; s.setAttribute('aria-hidden', 'true');
    el.appendChild(s); el._gl = s;
    return s;
  }
  function glLeave() {
    if (!glEl) return;
    glEl.classList.remove('gl-on'); glEl.style.removeProperty('--gdx'); glEl.style.removeProperty('--gdy'); glEl = null;
  }
  function glFrame() {
    glRaf = 0;
    const el = glEl; if (!el) return;
    const r = el.getBoundingClientRect(); if (!r.width || !r.height) return;
    const x = glX - r.left, y = glY - r.top;
    el.style.setProperty('--gx', x.toFixed(0) + 'px'); el.style.setProperty('--gy', y.toFixed(0) + 'px');
    el.style.setProperty('--gdx', ((x / r.width - 0.5) * 2).toFixed(2)); el.style.setProperty('--gdy', ((y / r.height - 0.5) * 2).toFixed(2));
  }
  document.addEventListener('pointermove', function (e) {
    if (SARA.reduceMotion || e.pointerType === 'touch') { glLeave(); return; }
    const t = e.target && e.target.closest ? e.target.closest(GLASS_SEL) : null;
    if (t !== glEl) {
      glLeave();
      if (t && glEnsure(t)) { glEl = t; t.classList.add('gl-on'); }
    }
    if (glEl) { glX = e.clientX; glY = e.clientY; if (!glRaf) glRaf = requestAnimationFrame(glFrame); }
  }, { passive: true });
  document.documentElement.addEventListener('pointerleave', glLeave);
  window.addEventListener('blur', glLeave);
  SARA.on('page', glLeave);
})();
