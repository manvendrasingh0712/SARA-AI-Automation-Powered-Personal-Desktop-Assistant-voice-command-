/* ==========================================================================
   whats-new.js -- F12. A small "What's new" sheet, shown ONCE per release of the interface, a moment after start-up.
   Edit RELEASE below when something visible ships (change `id`, rewrite `items`). A brand-new install (no earlier SARA settings in this
   browser profile) just records the current id and shows nothing. Per device: localStorage 'sara_whats_new' (in-memory fallback).
   Reuses the .ax-* dialog look of js/shared/a11y.js (style/shared/a11y.css). SARA.whatsNew.open() shows it on demand.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA, doc = document; if (!SARA) return;
  const RELEASE = {
    id: 'gui-2026-11',
    title: 'What’s new',
    items: [
      ['Four new themes', 'Obsidian Gold, Deep Ocean, Sunset Glass and Forest Night. (Cyber Blue is now Deep Ocean.)'],
      ['Auto day / night', 'Settings › Appearance: Paper by day, your dark theme at night. Hover a theme card to preview it.'],
      ['Sound packs', 'Settings › Voice: pick Glass, Soft, Minimal or Sci-Fi and set the volume.'],
      ['Lighter and smoother', 'Less CPU and GPU while idle, no blur behind the dock and cards, calmer Balanced tier.'],
      ['Shortcuts', 'Press ? for the shortcut list and Ctrl+K for the command palette.']
    ]
  };
  const KEY = 'sara_whats_new', mem = {};
  const get = function (k) { try { const v = localStorage.getItem(k); if (v !== null) return v; } catch (e) { /* blocked */ } return (k in mem) ? mem[k] : null; };
  const set = function (k, v) { mem[k] = v; try { localStorage.setItem(k, v); } catch (e) { /* blocked */ } };
  const el = function (tag, cls, text) { const n = doc.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
  const usedBefore = function () {
    try { for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i) || ''; if (k.indexOf('sara_') === 0 && k !== KEY) return true; } } catch (e) { /* blocked */ }
    return false;
  };

  let dlg = null;
  function open() {
    if (dlg) return; const prev = doc.activeElement;
    const back = el('div', 'ax-back'), box = el('div', 'ax-dialog wn-dialog'); box.setAttribute('role', 'dialog'); box.setAttribute('aria-modal', 'true'); box.setAttribute('aria-label', RELEASE.title);
    const head = el('div', 'ax-head'); head.appendChild(el('div', 'ax-title', RELEASE.title));
    const x = el('button', 'ax-x', '×'); x.type = 'button'; x.setAttribute('aria-label', 'Close'); head.appendChild(x); box.appendChild(head);
    const body = el('div', 'ax-body wn-body'); const sec = el('section', 'ax-sec');
    RELEASE.items.forEach(function (it) { const row = el('div', 'ax-row wn-row'); row.appendChild(el('b', 'wn-t', it[0])); row.appendChild(el('span', 'wn-d', it[1])); sec.appendChild(row); });
    body.appendChild(sec); box.appendChild(body);
    const foot = el('div', 'wn-foot'); const ok = el('button', 'pill-btn primary', 'Got it'); ok.type = 'button'; foot.appendChild(ok); box.appendChild(foot);
    back.appendChild(box); doc.body.appendChild(back); dlg = back; requestAnimationFrame(function () { back.classList.add('open'); });
    const close = function () {
      if (!dlg) return; doc.removeEventListener('keydown', onKey, true); back.classList.remove('open'); dlg = null;
      setTimeout(function () { back.remove(); }, 240); if (prev && prev.focus) prev.focus({ preventScroll: true });
    };
    const onKey = function (e) { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); } };
    doc.addEventListener('keydown', onKey, true); x.addEventListener('click', close); ok.addEventListener('click', close);
    back.addEventListener('pointerdown', function (e) { if (e.target === back) close(); });
    ok.focus({ preventScroll: true });
  }

  function maybeShow() {
    if (get(KEY) === RELEASE.id) return;
    if (get(KEY) === null && !usedBefore()) { set(KEY, RELEASE.id); return; }           // fresh install: nothing is "new"
    if (doc.hidden || doc.querySelector('.overlay.open,.pal-overlay.open,.boot-overlay,.ax-back')) { setTimeout(maybeShow, 4000); return; }
    set(KEY, RELEASE.id); open();
  }
  SARA.whatsNew = { open: open, release: RELEASE };
  if (SARA.onBoot) SARA.onBoot(function () { setTimeout(maybeShow, 3000); });
})();
