/* ==========================================================================
   tour.js -- F5. A short spotlight tour of the Home screen (5 steps), offered ONCE after the first-run setup wizard is closed
   (the wizard emits 'setup_done' with {first:true}). Skippable at any step (Skip, Esc, or a click on the dark area); "Back" / "Next" move
   along; it can be replayed any time from Settings (button "Replay tour") or the command palette. Per device: localStorage 'sara_tour_done'
   (in-memory fallback). Each step spotlights a real element that is visible right now; a step whose element is missing or hidden is skipped.
   Accessible: role=dialog, focus moves to the card and returns when it closes, Enter / Right = next, Left = back, Esc = skip, text is read from the DOM.
   Animation = opacity only (the spotlight and the card cross-fade between steps); none under reduced motion. Styles: style/shared/guide.css.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA, doc = document; if (!SARA) return;
  const KEY = 'sara_tour_done', mem = {};
  const get = function (k) { try { const v = localStorage.getItem(k); if (v !== null) return v; } catch (e) { /* blocked */ } return (k in mem) ? mem[k] : null; };
  const set = function (k, v) { mem[k] = v; try { localStorage.setItem(k, v); } catch (e) { /* blocked */ } };
  const el = function (tag, cls, text) { const n = doc.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };

  const STEPS = [
    { sel: '#stage', title: 'This is Sara', text: 'Say “Sara”, or tap the orb, and talk. Press and hold the orb for a ring of quick actions.', side: 'right' },
    { sel: '#navBar', title: 'Move around', text: 'Home, Chat, Notes, Apps, Automation and Settings. Alt + 1–6 jumps between them.', side: 'top' },
    { sel: '#todayCard', title: 'Today at a glance', text: 'Weather and what is next on your calendar and reminders. Tap it for the full day.', side: 'right' },
    { sel: '.nav button[data-page="settings"]', title: 'Make it yours', text: 'Themes (even auto day / night), sounds, voice, memory and privacy live in Settings.', side: 'top' },
    { sel: null, title: 'Two shortcuts', text: 'Ctrl + K opens the command palette; press ? any time to see every keyboard shortcut.' }
  ];

  let root = null, spot = null, card = null, idx = 0, steps = [], prevFocus = null, busy = false;

  function visible(n) { if (!n) return false; const r = n.getBoundingClientRect(); return r.width > 4 && r.height > 4 && n.offsetParent !== null || (n.id === 'navBar' && r.width > 4); }
  function resolve() {
    return STEPS.map(function (s) { return { s: s, node: s.sel ? doc.querySelector(s.sel) : null }; }).filter(function (x) { return !x.s.sel || visible(x.node); });
  }
  function place(step) {
    const node = step.node, pad = 10, vw = window.innerWidth, vh = window.innerHeight;
    let left, top;
    if (!node) {                                                    // no target: centred card, no spotlight
      spot.style.opacity = '0'; left = (vw - card.offsetWidth) / 2; top = (vh - card.offsetHeight) / 2;
    } else {
      const r = node.getBoundingClientRect();
      const x = Math.max(4, r.left - pad), y = Math.max(4, r.top - pad), w = Math.min(vw - 8, r.width + pad * 2), h = Math.min(vh - 8, r.height + pad * 2);
      spot.style.cssText = 'left:' + x + 'px;top:' + y + 'px;width:' + w + 'px;height:' + h + 'px;border-radius:' + (node.id === 'stage' ? '50%' : '18px') + ';opacity:1';
      const side = step.s.side;
      if (side === 'top') { left = x + w / 2 - card.offsetWidth / 2; top = y - card.offsetHeight - 14; }
      else if (side === 'right') { left = x + w + 16; top = y + h / 2 - card.offsetHeight / 2; }
      else { left = x + w / 2 - card.offsetWidth / 2; top = y + h + 14; }
      if (left + card.offsetWidth > vw - 12 && side === 'right') left = x - card.offsetWidth - 16;    // no room on the right: put it on the left
    }
    card.style.left = Math.max(12, Math.min(vw - card.offsetWidth - 12, left)) + 'px';
    card.style.top = Math.max(12, Math.min(vh - card.offsetHeight - 12, top)) + 'px';
  }
  function render() {
    const st = steps[idx]; busy = true;
    card.classList.remove('show');
    setTimeout(function () {
      if (!root) return;
      card.querySelector('.tr-title').textContent = st.s.title;
      card.querySelector('.tr-text').textContent = st.s.text;
      card.querySelector('.tr-count').textContent = (idx + 1) + ' / ' + steps.length;
      card.querySelector('.tr-back').hidden = idx === 0;
      card.querySelector('.tr-next').textContent = idx === steps.length - 1 ? 'Done' : 'Next';
      place(st); card.classList.add('show'); busy = false;
      card.querySelector('.tr-next').focus({ preventScroll: true });
    }, SARA.reduceMotion ? 0 : 140);
  }
  function end(finished) {
    if (!root) return;
    set(KEY, '1'); doc.removeEventListener('keydown', onKey, true); window.removeEventListener('resize', onResize);
    const r = root; root = null; r.classList.remove('open'); setTimeout(function () { r.remove(); }, SARA.reduceMotion ? 0 : 220);
    if (prevFocus && prevFocus.focus) prevFocus.focus({ preventScroll: true });
    if (finished && SARA.toast) SARA.toast('ti-check', 'var(--core)', 'You are set. Settings › Setup has "Replay tour".', { variant: 'info' });
  }
  function go(d) {
    if (busy && !SARA.reduceMotion) return;
    const n = idx + d; if (n < 0) return; if (n >= steps.length) { end(true); return; }
    idx = n; render();
  }
  function onKey(e) {
    if (!root) return;
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); end(false); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); go(1); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); go(-1); }
    else if (e.key === 'Tab') {                                       // keep focus inside the card
      const f = Array.prototype.slice.call(card.querySelectorAll('button')).filter(function (b) { return !b.hidden; });
      if (!f.length) return; const i = f.indexOf(doc.activeElement);
      if (e.shiftKey && i <= 0) { e.preventDefault(); f[f.length - 1].focus(); } else if (!e.shiftKey && i === f.length - 1) { e.preventDefault(); f[0].focus(); }
    }
  }
  function onResize() { if (root && steps[idx]) place(steps[idx]); }

  function start() {
    if (root) return;
    if ((SARA.current || 'home') !== 'home') SARA.gotoPage('home');
    setTimeout(function () {
      steps = resolve(); if (!steps.length) return;
      idx = 0; prevFocus = doc.activeElement;
      root = el('div', 'tr'); root.setAttribute('aria-hidden', 'false');
      spot = el('div', 'tr-spot'); card = el('div', 'tr-card'); card.setAttribute('role', 'dialog'); card.setAttribute('aria-modal', 'true'); card.setAttribute('aria-label', 'Quick tour');
      card.appendChild(el('div', 'tr-count')); card.appendChild(el('div', 'tr-title')); card.appendChild(el('div', 'tr-text'));
      const row = el('div', 'tr-row');
      const skip = el('button', 'link-btn tr-skip', 'Skip tour'); skip.type = 'button';
      const back = el('button', 'pill-btn tr-back', 'Back'); back.type = 'button';
      const next = el('button', 'pill-btn primary tr-next', 'Next'); next.type = 'button';
      row.appendChild(skip); row.appendChild(back); row.appendChild(next); card.appendChild(row);
      root.appendChild(spot); root.appendChild(card); doc.body.appendChild(root);
      skip.addEventListener('click', function () { end(false); }); back.addEventListener('click', function () { go(-1); }); next.addEventListener('click', function () { go(1); });
      root.addEventListener('pointerdown', function (e) { if (e.target === root) end(false); });        // a click on the dark area skips
      doc.addEventListener('keydown', onKey, true); window.addEventListener('resize', onResize);
      requestAnimationFrame(function () { root && root.classList.add('open'); render(); });
    }, 120);
  }

  SARA.tour = { start: start, end: function () { end(false); }, done: function () { return get(KEY) === '1'; }, reset: function () { set(KEY, '0'); } };
  // offered once, right after the first-run wizard closes (not for people who finished it before this tour existed)
  SARA.on('setup_done', function (info) {
    if (!info || !info.first || get(KEY) === '1') return;
    setTimeout(start, 700);
  });
})();
