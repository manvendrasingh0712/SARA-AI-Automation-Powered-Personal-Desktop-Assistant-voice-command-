/* settings-fx.js -- advanced effects layer for Settings (additive; Settings-only; load AFTER settings.js).
   One rAF-throttled pointer handler drives: edge light, magnetic controls, theme-card tilt. Everything else is one-shot. */
(function () {
  'use strict';
  const SARA = window.SARA, $ = SARA.$;
  const page = $('page-settings'), cards = page && page.querySelector('.set-cards'), rail = $('setRail');
  if (!page || !cards) return;
  const rm = function () { return !!SARA.reduceMotion; };
  const fine = !!(window.matchMedia && window.matchMedia('(hover:hover) and (pointer:fine)').matches);
  const all = Array.prototype.slice.call(cards.querySelectorAll('.set-card'));

  /* per-card layers: .set-edge (pointer-lit border), .set-fx (clipped layer for waves/particles) */
  all.forEach(function (c) {
    const e = document.createElement('span'), f = document.createElement('span');
    e.className = 'set-edge'; f.className = 'set-fx'; e.setAttribute('aria-hidden', 'true'); f.setAttribute('aria-hidden', 'true');
    c.insertBefore(f, c.firstChild); c.insertBefore(e, c.firstChild); c._e = e; c._f = f;
  });
  const btns = rail ? Array.prototype.slice.call(rail.querySelectorAll('button[data-target]')) : [];
  btns.forEach(function (b, i) { b.style.setProperty('--ri', i); });

  /* ---- pointer: edge light, magnetic controls, theme tilt ---- */
  if (fine && !rm()) {
    const MAG = '.qc-tile,.pill-btn,.set-rail button';
    let ev = null, raf = 0, mag = null, tilt = null;
    const drop = function (el) { el.style.removeProperty('--mx'); el.style.removeProperty('--my'); };
    const untilt = function (el) { el.classList.remove('tilt'); el.style.removeProperty('--rx'); el.style.removeProperty('--ry'); };
    page.addEventListener('pointermove', function (e) {
      if (e.pointerType !== 'mouse') return;
      ev = e;
      if (raf) return;
      raf = requestAnimationFrame(function () {
        raf = 0; const t = ev && ev.target; if (!t || !t.closest) return;
        const c = t.closest('.set-card');
        if (c && c._e) { const r = c.getBoundingClientRect(); c._e.style.setProperty('--ex', (ev.clientX - r.left) + 'px'); c._e.style.setProperty('--ey', (ev.clientY - r.top) + 'px'); }
        const m = t.closest(MAG);
        if (mag && mag !== m) drop(mag);
        if (m) {
          const r = m.getBoundingClientRect();
          m.style.setProperty('--mx', (((ev.clientX - (r.left + r.width / 2)) / r.width) * 8).toFixed(1) + 'px');
          m.style.setProperty('--my', (((ev.clientY - (r.top + r.height / 2)) / r.height) * 6).toFixed(1) + 'px');
        }
        mag = m;
        const th = t.closest('.theme-card');
        if (tilt && tilt !== th) untilt(tilt);
        if (th) {
          const r = th.getBoundingClientRect(), px = (ev.clientX - r.left) / r.width, py = (ev.clientY - r.top) / r.height;
          th.classList.add('tilt');
          th.style.setProperty('--rx', ((px - 0.5) * 10).toFixed(1) + 'deg'); th.style.setProperty('--ry', ((0.5 - py) * 10).toFixed(1) + 'deg');
          th.style.setProperty('--gx', (px * 100).toFixed(0) + '%'); th.style.setProperty('--gy', (py * 100).toFixed(0) + '%');
        }
        tilt = th;
      });
    }, { passive: true });
    page.addEventListener('pointerleave', function () { if (mag) { drop(mag); mag = null; } if (tilt) { untilt(tilt); tilt = null; } });
  }

  /* ---- toggle shockwave + particle burst (one-shot, removed when finished) ---- */
  function pulseAt(el) {
    if (rm() || !el) return;
    const c = el.closest('.set-card'); if (!c || !c._f) return;
    setTimeout(function () {
      const on = el.classList.contains('on') || (el.parentNode && el.parentNode.classList.contains('on')), cr = c.getBoundingClientRect(), r = el.getBoundingClientRect();
      if (!r.width) return;
      const x = r.left + r.width / 2 - cr.left, y = r.top + r.height / 2 - cr.top;
      const w = document.createElement('span'); w.className = 'fx-wave'; w.style.left = x + 'px'; w.style.top = y + 'px';
      c._f.appendChild(w); setTimeout(function () { w.remove(); }, 900);
      if (!on || !el.animate) return;
      for (let i = 0; i < 8; i++) {
        const p = document.createElement('i'); p.className = 'fx-p'; p.style.left = x + 'px'; p.style.top = y + 'px'; c._f.appendChild(p);
        const a = (i / 8) * Math.PI * 2 + Math.random() * 0.4, d = 22 + Math.random() * 16;
        const an = p.animate([
          { transform: 'translate(-50%,-50%) scale(1)', opacity: 1 },
          { transform: 'translate(calc(-50% + ' + (Math.cos(a) * d).toFixed(1) + 'px),calc(-50% + ' + (Math.sin(a) * d).toFixed(1) + 'px)) scale(0)', opacity: 0 }
        ], { duration: 520, easing: 'cubic-bezier(.22,1,.36,1)' });
        an.onfinish = function () { p.remove(); };
      }
    }, 60);
  }
  cards.addEventListener('click', function (e) {
    const t = e.target.closest('.toggle'); if (t) { pulseAt(t); return; }
    const q = e.target.closest('.qc-tile'); if (q) pulseAt(q.querySelector('.qc-sw'));
  });
  cards.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const t = e.target.closest && e.target.closest('.toggle'); if (t) pulseAt(t);
  });

  /* ---- title "decrypt" scramble (one-shot, bounded) ---- */
  const GL = '▮▯░▒01#%$&*+<>?/';
  function scramble(el) {
    if (rm() || !el || el._sc) return;
    const fin = el.textContent; if (!fin) return;
    el._sc = 1; el.style.minWidth = el.offsetWidth + 'px';
    const t0 = performance.now(); let last = 0;
    (function step(now) {
      if (now - last >= 40 || now - t0 >= 620) {
        last = now;
        const k = Math.min(1, (now - t0) / 620), keep = Math.floor(fin.length * k);
        let s = '';
        for (let i = 0; i < fin.length; i++) s += (i < keep || fin[i] === ' ') ? fin[i] : GL[Math.floor(Math.random() * GL.length)];
        el.textContent = k >= 1 ? fin : s;
        if (k >= 1) { el.style.minWidth = ''; el._sc = 0; return; }
      }
      requestAnimationFrame(step);
    })(t0);
  }
  if ('IntersectionObserver' in window) {
    const io = new IntersectionObserver(function (es) {
      es.forEach(function (en) {
        if (!en.isIntersecting) return;
        const c = en.target; io.unobserve(c);
        const d = 150 + (parseInt(c.style.getPropertyValue('--i'), 10) || 0) * 45;
        setTimeout(function () {
          scramble(c.querySelector('.set-card-title'));
          if (c.id === 'grp-memory') { const r = $('memRing'); if (r && !rm()) { r.classList.add('scan'); setTimeout(function () { r.classList.remove('scan'); }, 1400); } }
        }, d);
      });
    }, { threshold: 0.25 });
    all.forEach(function (c) { io.observe(c); });
  }
  if (rail) rail.addEventListener('click', function (e) {
    const b = e.target.closest('button[data-target]'); if (!b) return;
    const c = $(b.dataset.target); if (c) setTimeout(function () { scramble(c.querySelector('.set-card-title')); }, 280);
  });

  /* ---- quick-controls counter: "N of 5 on" with a rolling digit ---- */
  const real = [$('setActive'), $('setFocus'), $('setMute'), page.querySelector('[data-setting="show_notifications"]'), page.querySelector('[data-setting="voice_replies"]')].filter(Boolean);
  const sub = page.querySelector('#grp-quick .set-card-sub');
  let lastN = -1;
  function count() {
    if (!sub || !real.length) return;
    const n = real.filter(function (r) { return r.classList.contains('on'); }).length;
    if (n === lastN) return;
    const first = lastN < 0; lastN = n;
    sub.innerHTML = '<b class="odo">' + n + '</b> of ' + real.length + ' on';
    if (!first && !rm() && sub.firstChild.animate) sub.firstChild.animate([{ transform: 'translateY(70%)', opacity: 0 }, { transform: 'none', opacity: 1 }], { duration: 340, easing: 'cubic-bezier(.34,1.56,.64,1)' });
  }
  if (window.MutationObserver) { const mo = new MutationObserver(count); real.forEach(function (r) { mo.observe(r, { attributes: true, attributeFilter: ['class'] }); }); }
  count(); SARA.onBoot(function () { setTimeout(count, 500); setTimeout(count, 1500); });

  /* ---- sliders: glow while dragging ---- */
  const dragging = new Set();
  page.addEventListener('pointerdown', function (e) { const s = e.target.closest && e.target.closest('input[type=range]'); if (s) { s.classList.add('drag'); dragging.add(s); } });
  ['pointerup', 'pointercancel'].forEach(function (n) { window.addEventListener(n, function () { dragging.forEach(function (s) { s.classList.remove('drag'); }); dragging.clear(); }); });

  /* ---- rail scroll-progress line (only the scroller that contains the rail) ---- */
  let sp = 0;
  page.addEventListener('scroll', function (e) {
    const t = e.target; if (sp || !rail || !t || t.nodeType !== 1 || !t.contains(rail)) return;
    sp = requestAnimationFrame(function () { sp = 0; const m = t.scrollHeight - t.clientHeight; if (m > 0) rail.style.setProperty('--prog', (t.scrollTop / m).toFixed(3)); });
  }, { capture: true, passive: true });

  /* ---- settings search ( "/" to focus, Enter = jump, Esc = clear ) ---- */
  if (rail) {
    const inp = document.createElement('input');
    inp.type = 'search'; inp.className = 'set-search'; inp.placeholder = 'Search settings  /'; inp.autocomplete = 'off'; inp.setAttribute('aria-label', 'Search settings');
    rail.insertBefore(inp, rail.querySelector('button'));
    const ROWS = '.set-row,.info-row,.skill-row,.qc-tile,.stat';
    let st = 0;
    function run() {
      const q = inp.value.trim().toLowerCase();
      all.forEach(function (c) {
        let hits = 0;
        c.querySelectorAll(ROWS).forEach(function (r) { const h = !!q && r.textContent.toLowerCase().indexOf(q) >= 0; r.classList.toggle('hit', h); if (h) hits++; });
        const hd = c.querySelector('.set-card-head'), hm = !!q && !!hd && hd.textContent.toLowerCase().indexOf(q) >= 0;
        c.classList.toggle('dim', !!q && !hits && !hm);
      });
    }
    inp.addEventListener('input', function () { clearTimeout(st); st = setTimeout(run, 90); });
    inp.addEventListener('keydown', function (e) {
      e.stopPropagation();
      if (e.key === 'Escape') { inp.value = ''; run(); inp.blur(); }
      if (e.key === 'Enter') {
        let best = null, bt = 1e9;
        all.forEach(function (c) { if (c.classList.contains('dim') || !inp.value.trim()) return; const r = c.getBoundingClientRect(), k = r.top * 10 + r.left / 1000; if (k < bt) { bt = k; best = c; } });
        if (best) best.scrollIntoView({ behavior: rm() ? 'auto' : 'smooth', block: 'start' });
      }
    });
    document.addEventListener('keydown', function (e) {
      if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey || SARA.current !== 'settings') return;
      const tg = e.target; if (tg && (/^(input|textarea|select)$/i.test(tg.tagName) || tg.isContentEditable)) return;
      if (!inp.offsetParent) return;
      e.preventDefault(); inp.focus(); inp.select();
    });
  }
})();
