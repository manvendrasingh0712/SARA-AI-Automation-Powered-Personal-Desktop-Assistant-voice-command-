/* ==========================================================================
   perf.js -- Settings > System > "Response speed" block (TTFA p50/p95, stage split bar,
   last 10 turns, "Collect timing data" toggle).
   Backend: sara/gui/app/perf_api.py (get_perf_summary, get_recent_turns, set_telemetry_enabled).
   Markup: index.html #perfBlock.  Styles: style/perf.css.  Dynamic text via textContent only.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA;
  SARA.perf = SARA.perf || {};
  const MIN_SAMPLES = 5;
  const STAGES = [
    { key: 'stt', label: 'STT', cls: 'perf-stt' },
    { key: 'route', label: 'Route', cls: 'perf-route' },
    { key: 'llm_ttft', label: 'LLM', cls: 'perf-llm' },
    { key: 'tool', label: 'Tool', cls: 'perf-tool' },
    { key: 'tts_start', label: 'Speech start', cls: 'perf-tts' }
  ];
  let busy = false;

  const isNum = (v) => typeof v === 'number' && isFinite(v);
  const secs = (ms) => (isNum(ms) ? (ms / 1000).toFixed(2) : '-');
  const msText = (ms) => (isNum(ms) ? Math.round(ms) + ' ms' : '-');
  function node(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function setNote(text) {
    const note = SARA.$('perfNote');
    if (!note) return;
    note.hidden = !text;
    note.textContent = text || '';
  }

  function renderBar(stageAvg) {
    const bar = SARA.$('perfBar'), legend = SARA.$('perfLegend');
    bar.textContent = '';
    legend.textContent = '';
    const parts = [];
    STAGES.forEach(function (st) {
      const v = stageAvg ? stageAvg[st.key] : null;
      if (!isNum(v) || v <= 0) return;
      const label = st.label + ' ' + msText(v);
      const seg = node('div', 'perf-seg ' + st.cls);
      seg.style.flexGrow = String(v);
      seg.setAttribute('aria-label', label);
      seg.title = label;
      bar.appendChild(seg);
      const item = node('span', 'perf-leg');
      item.appendChild(node('i', 'perf-dot ' + st.cls));
      item.appendChild(document.createTextNode(label));
      legend.appendChild(item);
      parts.push(label);
    });
    bar.setAttribute('aria-label', parts.length ? 'Average stage split: ' + parts.join(', ') : 'Average stage split: no data');
  }

  function renderRecent(turns) {
    const box = SARA.$('perfRecent');
    box.textContent = '';
    if (!turns.length) { box.appendChild(node('div', 'recent-line', 'No turns recorded yet.')); return; }
    turns.slice(0, 10).forEach(function (t) {
      const line = node('div', 'recent-line perf-turn');
      line.appendChild(node('span', 'perf-route-name', String(t.route || '-')));
      line.appendChild(node('span', 'perf-ms', msText(t.ttfa_ms) + ' / ' + msText(t.total_ms)));
      const outcome = String(t.outcome || '-');
      line.appendChild(node('span', 'perf-out ' + (outcome === 'ok' ? 'perf-good' : (outcome === 'error' ? 'perf-bad' : '')), outcome));
      box.appendChild(line);
    });
  }

  function render(summary, turns) {
    const count = isNum(summary.count) ? summary.count : 0;
    SARA.$('perfP50').textContent = secs(summary.ttfa_p50);
    SARA.$('perfP95').textContent = secs(summary.ttfa_p95);
    if (!summary.enabled && count === 0) setNote('Timing collection is off.');
    else setNote(count < MIN_SAMPLES ? 'collecting...' : '');
    renderBar(summary.stage_avg);
    renderRecent(turns);
    SARA.setToggle(SARA.$('perfToggle'), !!summary.enabled);
  }

  function renderEmpty(message) {
    SARA.$('perfP50').textContent = '-';
    SARA.$('perfP95').textContent = '-';
    setNote(message);
    renderBar(null);
    SARA.$('perfRecent').textContent = '';
  }

  async function refresh() {
    if (busy || !SARA.$('perfBlock')) return;
    busy = true;
    try {
      const s = await SARA.callApi('get_perf_summary', 50);
      if (!s || !s.ok || !s.data) { renderEmpty('Timing data is not available.'); return; }
      const r = await SARA.callApi('get_recent_turns', 10);
      render(s.data, r && r.ok && Array.isArray(r.data) ? r.data : []);
    } catch (e) {
      renderEmpty('Timing data is not available.');
    } finally {
      busy = false;
    }
  }
  SARA.perf.refresh = refresh;

  SARA.bindToggle(SARA.$('perfToggle'), function (on, el) {
    SARA.callApi('set_telemetry_enabled', on).then(function (r) {
      if (r && r.ok) { SARA.setToggle(el, !!r.enabled); refresh(); }
      else SARA.setToggle(el, !on);
    });
  });
  SARA.on('page', function (name) { if (name === 'settings') refresh(); });
  SARA.every(10000, refresh, { pages: ['settings'] });
})();