/* ==========================================================================
   apps-order.js -- F8. Reorder the tiles on the Apps page ("Open" and "Quick tools") by dragging them, or with the keyboard:
   focus a tile and press Alt+Left / Alt+Right (Alt+Up / Alt+Down jump a row). Every change shows a toast with Undo; "Reset order" in the
   hint line restores the built-in order. The order is per device (localStorage 'sara_apps_order', in-memory fallback) and keyed by the
   tile label, so a tile that is added or removed in js/apps/apps.js never breaks a saved order (unknown labels go to the end).
   Frontend only: no backend call, no change to what a tile does. Load AFTER js/apps/apps.js.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA, doc = document; if (!SARA) return;
  const GRIDS = ['appsGrid', 'toolsGrid'], KEY = 'sara_apps_order', mem = {};
  const ls = {
    get: function (k) { try { const v = localStorage.getItem(k); if (v !== null) return v; } catch (e) { /* blocked */ } return (k in mem) ? mem[k] : null; },
    set: function (k, v) { mem[k] = v; try { localStorage.setItem(k, v); } catch (e) { /* blocked */ } },
    del: function (k) { delete mem[k]; try { localStorage.removeItem(k); } catch (e) { /* blocked */ } }
  };
  const label = function (t) { const s = t.querySelector('span'); return s ? s.textContent : ''; };
  const tiles = function (g) { return Array.prototype.slice.call(g.querySelectorAll('.app-tile')); };
  const gridEls = GRIDS.map(function (id) { return doc.getElementById(id); }).filter(Boolean);
  if (!gridEls.length) return;
  const defaults = {};                                            // built-in order, captured before any saved order is applied
  gridEls.forEach(function (g) { defaults[g.id] = tiles(g).map(label); });

  function read() { try { const o = JSON.parse(ls.get(KEY) || '{}'); return o && typeof o === 'object' ? o : {}; } catch (e) { return {}; } }
  function snapshot() { const o = {}; gridEls.forEach(function (g) { o[g.id] = tiles(g).map(label); }); return o; }
  function isDefault(o) { return gridEls.every(function (g) { return JSON.stringify((o || snapshot())[g.id]) === JSON.stringify(defaults[g.id]); }); }
  function arrange(order) {                                       // order: { gridId: [labels] }  (missing / unknown labels keep their place at the end)
    gridEls.forEach(function (g) {
      const want = (order && order[g.id]) || []; if (!Array.isArray(want)) return;
      const all = tiles(g), byLabel = {}; all.forEach(function (t) { byLabel[label(t)] = t; });
      const seen = {}, out = [];
      want.forEach(function (l) { if (byLabel[l] && !seen[l]) { seen[l] = 1; out.push(byLabel[l]); } });
      all.forEach(function (t) { if (!seen[label(t)]) out.push(t); });
      out.forEach(function (t) { g.appendChild(t); });
    });
  }
  function persist() { if (isDefault()) ls.del(KEY); else ls.set(KEY, JSON.stringify(snapshot())); paintHint(); }

  /* ---- hint line under the "Open" label ---- */
  const firstLabel = gridEls[0].previousElementSibling;
  const hint = doc.createElement('div'); hint.className = 'apps-hint';
  const hintText = doc.createElement('span'); hintText.textContent = 'Drag a tile to reorder it, or focus one and press Alt + arrow keys. ';
  const reset = doc.createElement('button'); reset.type = 'button'; reset.className = 'link-btn'; reset.textContent = 'Reset order'; reset.hidden = true;
  hint.appendChild(hintText); hint.appendChild(reset);
  if (firstLabel && firstLabel.parentNode) firstLabel.parentNode.insertBefore(hint, gridEls[0]);
  function paintHint() { reset.hidden = isDefault(); }

  /* ---- change + undo ---- */
  function changed(before) {
    persist();
    if (SARA.toast) SARA.toast('ti-check', 'var(--core)', 'Apps reordered', { variant: 'info', undo: function () { arrange(before); persist(); } });
  }
  reset.addEventListener('click', function () { const before = snapshot(); arrange(defaults); persist(); if (SARA.toast) SARA.toast('ti-check', 'var(--core)', 'Order reset', { variant: 'info', undo: function () { arrange(before); persist(); } }); });

  /* ---- make tiles draggable ---- */
  gridEls.forEach(function (g) {
    const mark = function () { tiles(g).forEach(function (t) { t.draggable = true; t.setAttribute('aria-roledescription', 'reorderable tile'); }); };
    mark();
    let drag = null, before = null;
    g.addEventListener('dragstart', function (e) {
      const t = e.target.closest && e.target.closest('.app-tile'); if (!t || !g.contains(t)) return;
      drag = t; before = snapshot(); t.classList.add('dragging');
      try { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', label(t)); } catch (err) { /* ignore */ }
    });
    g.addEventListener('dragover', function (e) {
      if (!drag) return; e.preventDefault(); try { e.dataTransfer.dropEffect = 'move'; } catch (err) { /* ignore */ }
      const over = e.target.closest && e.target.closest('.app-tile'); if (!over || over === drag || !g.contains(over)) return;
      const r = over.getBoundingClientRect(), after = (e.clientX - r.left) > r.width / 2;      // right half = place after, left half = before
      const ref = after ? over.nextElementSibling : over;
      if (ref !== drag && drag.nextElementSibling !== ref) g.insertBefore(drag, ref);
      else if (ref === null && g.lastElementChild !== drag) g.appendChild(drag);
    });
    g.addEventListener('drop', function (e) { if (drag) e.preventDefault(); });
    g.addEventListener('dragend', function () {
      if (!drag) return; drag.classList.remove('dragging'); drag = null;
      if (JSON.stringify(snapshot()) !== JSON.stringify(before)) changed(before);
      before = null;
    });
    /* keyboard: Alt + arrows move the focused tile (the tile's own Enter handler is untouched) */
    g.addEventListener('keydown', function (e) {
      if (!e.altKey || e.ctrlKey || e.metaKey || e.key.indexOf('Arrow') !== 0) return;
      const t = e.target.closest && e.target.closest('.app-tile'); if (!t || !g.contains(t)) return;
      const all = tiles(g), i = all.indexOf(t);
      const perRow = Math.max(1, Math.round(g.clientWidth / (t.offsetWidth + 10)));
      const to = e.key === 'ArrowLeft' ? i - 1 : e.key === 'ArrowRight' ? i + 1 : e.key === 'ArrowUp' ? i - perRow : i + perRow;
      if (to < 0 || to >= all.length || to === i) { e.preventDefault(); return; }
      e.preventDefault(); e.stopPropagation();
      if (!g._keyBefore) g._keyBefore = snapshot();                 // the order before the first key of this burst
      g.insertBefore(t, to > i ? all[to].nextElementSibling : all[to]); t.focus(); persist();
      clearTimeout(g._keyT);                                         // one toast (with Undo) per burst of key presses, not one per press
      g._keyT = setTimeout(function () { const b = g._keyBefore; g._keyBefore = null; if (b && JSON.stringify(snapshot()) !== JSON.stringify(b)) changed(b); }, 900);
    }, true);
  });

  arrange(read()); paintHint();
  SARA.appsOrder = { reset: function () { arrange(defaults); persist(); }, snapshot: snapshot, defaults: function () { return JSON.parse(JSON.stringify(defaults)); } };
})();
