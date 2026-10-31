/* ==========================================================================
   settings-data.js -- Settings accordions that show numbers: Proactive suggestions (stats), Memory & usage,
   Analytics.  API calls: get_memory_stats, get_share_card_data, export_memory, get_analytics_dashboard.
   Listens to SARA 'proactive' (published by js/today.js, which owns the get_proactive_stats call).
   (The proactive toggles themselves are wired generically in js/settings.js via data-setting.)
   Styles: style/settings.css.  Markup: index.html #page-settings.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA, $ = SARA.$, esc = SARA.escapeHtml;

  /* ---- first-view count-up: runs once per number, the first time Settings is on screen; later refreshes only swap the text ---- */
  const COUNT_IDS = ['memPct', 'memExchanges', 'memSize', 'shStreak', 'shMessages', 'shDays', 'shNudges'];
  function reveal(el) {
    if (el._cu === 'done') return; el._cu = 'done';
    const m = /^(-?\d+(?:\.(\d+))?)(.*)$/.exec(el.textContent);
    if (!m || SARA.reduceMotion) return;
    const end = parseFloat(m[1]), dec = m[2] ? m[2].length : 0, tail = m[3], tok = el._tok = (el._tok || 0) + 1;
    if (!(end > 0)) return;
    const t0 = performance.now();
    (function step(now) {
      if (el._tok !== tok) return;
      const k = Math.min(1, (now - t0) / 700);
      el.textContent = (end * (1 - Math.pow(1 - k, 3))).toFixed(dec) + tail;
      if (k < 1) requestAnimationFrame(step);
    })(t0);
  }
  function put(el, text) {
    if (!el) return;
    el._tok = (el._tok || 0) + 1; el.textContent = String(text);
    if (el._cu === 'done') return;
    el._cu = 'wait';
    if (SARA.current === 'settings') reveal(el);
  }

  /* ---- proactive stats ---- */
  const TRIGGERS = { battery: 'Battery', reminder: 'Reminders', idle_break: 'Breaks', streak: 'Streaks', meeting: 'Meetings', routine: 'Routines' };
  SARA.on('proactive', function (s) {
    $('pvTotal').textContent = s.total || 0;
    const by = s.by_trigger || {};
    $('pvBreakdown').textContent = Object.keys(TRIGGERS).map((k) => TRIGGERS[k] + ' ' + (by[k] || 0)).join(' · ');
    const recent = (s.recent || []).slice().reverse();
    $('pvRecent').innerHTML = recent.length ? recent.map((ev) =>
      '<div class="recent-line"><b>' + esc(TRIGGERS[ev.trigger] || ev.trigger) + '</b> · ' + esc(SARA.relTime(ev.timestamp)) + '<small>' + esc(ev.message || '') + '</small></div>').join('')
      : 'No proactive activity yet.';
  });

  /* ---- memory + share card ---- */
  async function loadMemory() {
    const mem = await SARA.callApi('get_memory_stats');
    if (mem && mem.ok) {
      put($('memPct'), mem.pct + '%'); put($('memExchanges'), mem.exchange_count + ' / ' + mem.max_exchanges); put($('memSize'), mem.approx_mb + ' MB');
      const ring = $('memRing'), pv = Math.max(0, Math.min(100, parseFloat(mem.pct) || 0));
      if (ring) { ring.style.setProperty('--p', pv); $('memRingTxt').textContent = Math.round(pv) + '%'; }
    }
    const sh = await SARA.callApi('get_share_card_data');
    if (sh && sh.ok) {
      put($('shStreak'), sh.streak + (sh.streak === 1 ? ' day' : ' days'));
      put($('shMessages'), sh.total_messages); put($('shDays'), sh.days_used != null ? sh.days_used : '—');
      put($('shNudges'), sh.proactive_nudges != null ? sh.proactive_nudges : '—');
    }
  }
  $('exportMemoryBtn').addEventListener('click', async function () {
    const res = await SARA.callApi('export_memory');
    if (res && res.ok) SARA.ok('Exporting memory…'); else SARA.fail('Could not start memory export');
  });

  /* ---- 14-day trend draw-in: the bars are revealed left -> right ONCE, the first time the chart is really on screen
     (Settings open + data rendered). Never on refresh or on later visits. The trend is DOM bars (not SVG/canvas), so
     the reveal is a clip-path sweep (style/settings.css .trend.draw-in). The class is removed when the sweep ends:
     leaving/entering the page (display:none) would otherwise restart a CSS animation that is still attached. ---- */
  let trendDrawn = '', trendPending = '';
  function playTrendDraw() {
    const el = $('anTrend');
    if (!el || !trendPending || trendPending === trendDrawn || !el.children.length || SARA.current !== 'settings') return;
    trendDrawn = trendPending;
    if (el.classList.contains('draw-in')) return;
    if (SARA.reduceMotion) return;
    let timer = 0;
    const end = function () {
      clearTimeout(timer); el.classList.remove('draw-in');
      el.removeEventListener('animationend', end); el.removeEventListener('animationcancel', end);
    };
    el.addEventListener('animationend', end); el.addEventListener('animationcancel', end);
    timer = setTimeout(end, 1400);                               // safety net if no animation event ever arrives
    el.classList.add('draw-in');
  }

  /* ---- analytics ---- */
  function shortDate(s) { const d = new Date(s + 'T00:00:00'); return isNaN(d.getTime()) ? s : d.toLocaleDateString([], { month: 'short', day: 'numeric' }); }
  function longDate(s) { const d = new Date(s + 'T00:00:00'); return isNaN(d.getTime()) ? s : d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' }); }
  const fmtInt = (v) => Math.round(v).toLocaleString();
  const fmtPct = (v) => Math.round(v) + '%';
  const cmdWord = (n) => n + (n === 1 ? ' command' : ' commands');
  const KPI_VALUE_IDS = ['anMessages', 'anTotal', 'anNudges', 'anSuccess'];
  let anTok = 0, anTopKey = null, anTopPending = false, anTrendData = [];

  function countTo(el, to, fmt) {
    const tok = el._ktok = (el._ktok || 0) + 1;
    const from = typeof el._kcur === 'number' ? el._kcur : 0;
    if (SARA.reduceMotion || from === to || SARA.current !== 'settings' || document.hidden) { el._kcur = to; el.textContent = fmt(to); return; }
    const t0 = performance.now(), dur = 650;
    (function step(now) {
      if (el._ktok !== tok) return;
      if (SARA.current !== 'settings' || document.hidden) { el._kcur = to; el.textContent = fmt(to); return; }
      const k = Math.min(1, (now - t0) / dur), cur = from + (to - from) * (1 - Math.pow(1 - k, 3));
      el._kcur = cur; el.textContent = fmt(cur);
      if (k < 1) requestAnimationFrame(step); else { el._kcur = to; el.textContent = fmt(to); }
    })(t0);
  }
  function kpiSet(valId, wrapId, value, fmt) {
    const el = $(valId), wrap = $(wrapId); if (!el || !wrap) return;
    if (typeof value !== 'number' || !isFinite(value)) { wrap.hidden = true; return; }
    wrap.hidden = false; el._kfmt = fmt;
    if (el._kv === value) return;
    el._kv = value;
    if (SARA.current !== 'settings') {
      el._ktok = (el._ktok || 0) + 1; el._kcur = value; el.textContent = fmt(value);
      if (!el._kdone) el._kpend = true;
      return;
    }
    el._kdone = true; el._kpend = false;
    countTo(el, value, fmt);
  }
  function anReveal() {
    if (SARA.current !== 'settings') return;
    playTrendDraw();
    KPI_VALUE_IDS.forEach(function (id) {
      const el = $(id);
      if (el && el._kpend && typeof el._kv === 'number') { el._kpend = false; el._kdone = true; el._kcur = 0; countTo(el, el._kv, el._kfmt || fmtInt); }
    });
    if (anTopPending) {
      anTopPending = false;
      if (!SARA.reduceMotion) {
        $('anTop').querySelectorAll('.b-fill').forEach(function (f, i) {
          if (typeof f.animate === 'function') f.animate([{ width: '0%' }, { width: f.style.width }], { duration: 600, delay: i * 60, easing: 'cubic-bezier(.22,1,.36,1)', fill: 'backwards' });
        });
      }
    }
  }
  (function initTrendTip() {
    const tr = $('anTrend'), tip = $('anTip'), chart = $('anChart');
    if (!tr || !tip || !chart) return;
    function show(sp) {
      const t = anTrendData[+sp.dataset.i]; if (!t) return;
      tip.textContent = longDate(t.date) + ' · ' + cmdWord(t.count);
      const half = tip.offsetWidth / 2, x = sp.offsetLeft + sp.offsetWidth / 2;
      tip.style.left = Math.max(half, Math.min(chart.clientWidth - half, x)) + 'px';
      tip.classList.add('show');
    }
    function hide() { tip.classList.remove('show'); }
    tr.addEventListener('pointerover', function (e) { const sp = e.target.closest('span[data-i]'); if (sp) show(sp); });
    tr.addEventListener('pointerleave', hide);
    tr.addEventListener('focusin', function (e) { const sp = e.target.closest('span[data-i]'); if (sp) show(sp); });
    tr.addEventListener('focusout', hide);
  })();
  async function loadAnalytics() {
    const tok = ++anTok;
    const got = await Promise.all([
      SARA.callApi('get_analytics_dashboard'),
      SARA.callApi('get_share_card_data'),
      SARA.callApi('get_action_timeline', 200, null)
    ]);
    if (tok !== anTok) return;
    const res = got[0], sh = got[1], tl = got[2];
    if (!res || !res.ok) return;
    const d = res.data || {};
    const loading = $('anLoading'); if (loading) loading.remove();
    const commands = Number(d.total_commands) || 0;
    const messages = sh && sh.ok && typeof sh.total_messages === 'number' ? sh.total_messages : null;
    const ps = d.proactive_stats || {};
    const nudges = typeof ps.total === 'number' ? ps.total : null;
    let okN = 0, failN = 0;
    if (tl && tl.ok && Array.isArray(tl.data)) tl.data.forEach(function (r) { if (r.outcome === 'success') okN++; else if (r.outcome === 'fail') failN++; });
    const rated = okN + failN, success = rated > 0 ? (okN / rated) * 100 : null;
    const trend = d.daily_trend || [], top = (d.top_commands || []).slice(0, 5);
    let busiest = null; trend.forEach((t) => { if (t.count > 0 && (!busiest || t.count > busiest.count)) busiest = t; });
    const active = commands > 0 || !!busiest || top.length > 0 || messages > 0 || nudges > 0;
    $('anEmpty').hidden = active; $('anBody').hidden = !active;
    if (!active) { trendPending = ''; return; }
    kpiSet('anMessages', 'anKpiMessages', messages, fmtInt);
    kpiSet('anTotal', 'anKpiCommands', commands, fmtInt);
    kpiSet('anNudges', 'anKpiNudges', nudges, fmtInt);
    kpiSet('anSuccess', 'anKpiSuccess', success, fmtPct);
    const lbl = $('anSuccessLbl'); if (lbl) lbl.textContent = rated ? 'Action success · last ' + rated : 'Action success';
    const topKey = top.map((c) => c.name + ':' + c.count).join('|');
    if (topKey !== anTopKey) {
      anTopKey = topKey; anTopPending = true;
      const max = top.length ? Math.max.apply(null, top.map((c) => Number(c.count) || 0).concat([1])) : 1;
      $('anTop').innerHTML = top.length ? top.map(function (c, i) {
        const n = Number(c.count) || 0;
        return '<div class="bar-row"><span class="b-rank">' + (i + 1) + '</span><span class="b-name" title="' + esc(c.name) + '">' + esc(c.name) + '</span><span class="b-track"><span class="b-fill" style="display:block;width:' +
          Math.max(6, Math.round((n / max) * 100)) + '%"></span></span><span class="b-n">' + n + '×</span></div>';
      }).join('') : '<div class="acc-note">No commands recorded yet.</div>';
    }
    anTrendData = trend;
    const tmax = Math.max.apply(null, trend.map((t) => t.count).concat([1]));
    const peakIdx = busiest ? trend.indexOf(busiest) : -1;
    const tr = $('anTrend');
    if (tr.children.length !== trend.length) {
      tr.innerHTML = trend.map(function (t, i) { return '<span tabindex="0" data-i="' + i + '"></span>'; }).join('');
    }
    Array.prototype.forEach.call(tr.children, function (sp, i) {
      const t = trend[i];
      sp.style.height = Math.max(4, Math.round((t.count / tmax) * 100)) + '%';
      sp.setAttribute('aria-label', longDate(t.date) + ': ' + cmdWord(t.count));
      sp.classList.toggle('peak', i === peakIdx); sp.classList.toggle('zero', !t.count);
    });
    $('anBusiest').textContent = busiest ? longDate(busiest.date) + ' · ' + cmdWord(busiest.count) : 'No activity in this period';
    trendPending = busiest ? trend.map((t) => t.date + ':' + t.count).join('|') : '';
    $('anTrendFrom').textContent = trend.length ? shortDate(trend[0].date) : ''; $('anTrendTo').textContent = trend.length ? shortDate(trend[trend.length - 1].date) : '';
    anReveal();
    playTrendDraw();                                       // no-op unless this is the first time the chart is visible
  }

  /* ---- frequently missed commands ---- */
  async function loadFrequentMisses() {
    const el = $('missedCommands');
    if (!el) return;
    try {
      const res = await SARA.callApi('get_frequent_misses', 15, 2);
      const data = (res && res.ok && res.data) || [];
      if (!data.length) {
        el.innerHTML = '<div class="recent-line">Nothing repeated yet.</div>';
        return;
      }
      el.innerHTML = data.map(function (item) {
        return '<div class="recent-line">' + esc(item.text) + ' <small>(' + item.count + '&times;)</small></div>';
      }).join('');
    } catch (e) {
      el.innerHTML = '<div class="recent-line">Couldn\'t load this right now.</div>';
    }
  }

  /* ---- action timeline ---- */
  const ACTION_ICON = { success: '✓', fail: '✗', skipped: '○' };
  function actionLabel(name) {
    return (name || '').split('_').map((w) => w ? w[0].toUpperCase() + w.slice(1) : w).join(' ');
  }
  async function loadActionTimeline(filter) {
    filter = filter || 'all';
    const res = await SARA.callApi('get_action_timeline', 30, filter === 'all' ? null : filter);
    const el = $('actionTimeline');
    if (!el) return;
    if (!res || !res.ok || !res.data || !res.data.length) {
      el.innerHTML = 'No activity yet.';
      return;
    }
    el.innerHTML = res.data.map((entry) => {
      const icon = ACTION_ICON[entry.outcome] || '•';
      const reasonHtml = entry.reason ? '<small>' + esc(entry.reason) + '</small>' : '';
      return '<div class="recent-line"><b>' + esc(icon) + ' ' + esc(actionLabel(entry.action_name)) + '</b> · ' +
        esc(SARA.relTime(entry.timestamp)) + reasonHtml + '</div>';
    }).join('');
  }
  document.querySelectorAll('#page-settings [data-filter]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      document.querySelectorAll('#page-settings [data-filter]').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      loadActionTimeline(btn.getAttribute('data-filter'));
    });
  });

  SARA.onBoot(function () { loadMemory(); loadAnalytics(); loadActionTimeline('all'); loadFrequentMisses(); });
  SARA.on('page', function (p) {
    if (p === 'settings') {
      COUNT_IDS.forEach(function (id) { const el = $(id); if (el && el._cu === 'wait') reveal(el); });
      anReveal(); loadMemory(); loadAnalytics(); loadActionTimeline('all'); loadFrequentMisses();
    }
  });
  SARA.every(5 * 60 * 1000, function () { loadMemory(); loadAnalytics(); });
})();
