/* ==========================================================================
   router-view.js -- Settings > Smart routing card (mode, 7-day usage, learning reset).
   Backend: sara/gui/app/router_api.py (get_router_stats, set_router_mode, reset_router).
   Markup: index.html #grp-router.  Styles: style/router-view.css.  Dynamic text via textContent only.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA;
  SARA.routerView = SARA.routerView || {};
  const SHOW_AT = 50;
  let busy = false;

  const isNum = (v) => typeof v === 'number' && isFinite(v);
  function node(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function setText(id, text) {
    const el = SARA.$(id);
    if (el) el.textContent = text === undefined || text === null ? '' : String(text);
  }
  function setHidden(id, hide) {
    const el = SARA.$(id);
    if (el) el.hidden = !!hide;
  }

  function paintModes(mode, gateOk) {
    document.querySelectorAll('#grp-router .rt-mode').forEach(function (b) {
      const m = b.dataset.mode;
      b.setAttribute('aria-pressed', m === mode ? 'true' : 'false');
      b.disabled = m === 'on' && !gateOk;
    });
  }

  function renderGate(gate) {
    const el = SARA.$('rtGate');
    if (!el) return;
    el.textContent = '';
    if (!gate || !gate.message) { el.hidden = true; return; }
    el.textContent = String(gate.message);
    el.hidden = false;
  }

  function renderUsage(tiers, usage) {
    const box = SARA.$('rtUsage');
    if (!box) return;
    box.textContent = '';
    const counts = tiers.map(function (t) { return isNum(usage[t.name]) ? usage[t.name] : 0; });
    const max = Math.max(1, ...counts);
    tiers.forEach(function (t, i) {
      const row = node('div', 'rt-bar-row');
      row.appendChild(node('span', 'rt-bar-name', String(t.name)));
      const track = node('span', 'rt-track');
      const fill = node('span', 'rt-fill');
      fill.style.setProperty('--rt-w', Math.round((counts[i] / max) * 100) + '%');
      track.appendChild(fill);
      row.appendChild(track);
      row.appendChild(node('span', 'rt-bar-count', String(counts[i])));
      box.appendChild(row);
    });
  }

  function renderLegend(tiers) {
    const box = SARA.$('rtLegend');
    if (!box) return;
    box.textContent = '';
    tiers.forEach(function (t) {
      const cost = isNum(t.cost) ? String(t.cost) : '-';
      box.appendChild(node('span', 'rt-chip', String(t.name) + ' \u00b7 ' + String(t.backend) + ' \u00b7 ' + String(t.model) + ' \u00b7 cost ' + cost));
    });
  }

  function render(d) {
    const tiers = Array.isArray(d.tiers) ? d.tiers : [];
    const noop = tiers.length < 2;
    const usage = d.usage && typeof d.usage === 'object' ? d.usage : {};
    setHidden('rtEmpty', !noop);
    setHidden('rtUsage', noop);
    setHidden('rtLegend', noop);
    paintModes(d.mode, !!(d.gate && d.gate.ok));
    renderGate(d.gate);
    if (!noop) { renderUsage(tiers, usage); renderLegend(tiers); }

    const shown = !!d.shown && !noop;
    setHidden('rtNums', !shown);
    setHidden('rtCollecting', shown || noop);
    if (shown) {
      setText('rtLatency', isNum(d.avg_latency_ms) ? Math.round(d.avg_latency_ms) + ' ms' : '-');
      setText('rtSaved', isNum(d.calls_saved_vs_main) ? String(d.calls_saved_vs_main) : '-');
    } else {
      const n = isNum(d.samples) ? d.samples : 0;
      setText('rtCollecting', 'collecting data... ' + n + ' / ' + SHOW_AT);
    }
  }

  function renderEmpty() {
    ['rtUsage', 'rtLegend', 'rtNums', 'rtCollecting', 'rtGate'].forEach(function (id) { setHidden(id, true); });
    setHidden('rtEmpty', true);
    setText('rtNote', 'Smart routing data is not available.');
    setHidden('rtNote', false);
  }

  async function refresh() {
    if (busy || !SARA.$('grp-router')) return;
    busy = true;
    try {
      const r = await SARA.callApi('get_router_stats');
      if (!r || !r.ok || !r.data) { renderEmpty(); return; }
      setHidden('rtNote', true);
      render(r.data);
    } catch (e) {
      renderEmpty();
    } finally {
      busy = false;
    }
  }
  SARA.routerView.refresh = refresh;

  function setMode(next) {
    SARA.callApi('set_router_mode', next).then(function (r) {
      if (r && r.ok) {
        setHidden('rtNote', true);
      } else {
        setText('rtNote', (r && r.message) || 'Could not change the mode.');
        setHidden('rtNote', false);
      }
      refresh();
    }).catch(function () { refresh(); });
  }

  function showConfirm(on) {
    setHidden('rtConfirm', !on);
    setHidden('rtReset', on);
  }

  document.querySelectorAll('#grp-router .rt-mode').forEach(function (b) {
    b.addEventListener('click', function () {
      if (!b.disabled) setMode(b.dataset.mode);
    });
  });

  const resetBtn = SARA.$('rtReset');
  if (resetBtn) resetBtn.addEventListener('click', function () { showConfirm(true); });
  const noBtn = SARA.$('rtResetNo');
  if (noBtn) noBtn.addEventListener('click', function () { showConfirm(false); });
  const yesBtn = SARA.$('rtResetYes');
  if (yesBtn) {
    yesBtn.addEventListener('click', function () {
      SARA.callApi('reset_router').then(function () { showConfirm(false); refresh(); })
        .catch(function () { showConfirm(false); refresh(); });
    });
  }

  SARA.on('page', function (name) { if (name === 'settings') refresh(); else showConfirm(false); });
  SARA.every(10000, refresh, { pages: ['settings'] });
})();