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
      $('memPct').textContent = mem.pct + '%'; $('memExchanges').textContent = mem.exchange_count + ' / ' + mem.max_exchanges; $('memSize').textContent = mem.approx_mb + ' MB';
    }
    const sh = await SARA.callApi('get_share_card_data');
    if (sh && sh.ok) {
      $('shStreak').textContent = sh.streak + (sh.streak === 1 ? ' day' : ' days');
      $('shMessages').textContent = sh.total_messages; $('shDays').textContent = sh.days_used != null ? sh.days_used : '—';
      $('shNudges').textContent = sh.proactive_nudges != null ? sh.proactive_nudges : '—';
    }
  }
  $('exportMemoryBtn').addEventListener('click', async function () {
    const res = await SARA.callApi('export_memory');
    if (res && res.ok) SARA.ok('Exporting memory…'); else SARA.fail('Could not start memory export');
  });

  /* ---- analytics ---- */
  function shortDate(s) { const d = new Date(s + 'T00:00:00'); return isNaN(d.getTime()) ? s : d.toLocaleDateString([], { month: 'short', day: 'numeric' }); }
  async function loadAnalytics() {
    const res = await SARA.callApi('get_analytics_dashboard');
    if (!res || !res.ok) return;
    const d = res.data || {};
    $('anTotal').textContent = d.total_commands || 0;
    const top = (d.top_commands || []).slice(0, 5), max = top.length ? Math.max.apply(null, top.map((c) => c.count)) : 1;
    $('anTop').innerHTML = top.length ? top.map((c) =>
      '<div class="bar-row"><span class="b-name" title="' + esc(c.name) + '">' + esc(c.name) + '</span><span class="b-track"><span class="b-fill" style="display:block;width:' +
      Math.max(6, Math.round((c.count / max) * 100)) + '%"></span></span><span class="b-n">' + c.count + '×</span></div>').join('') : 'No commands recorded yet.';
    const trend = d.daily_trend || [];
    let busiest = null; trend.forEach((t) => { if (t.count > 0 && (!busiest || t.count > busiest.count)) busiest = t; });
    $('anBusiest').textContent = busiest ? shortDate(busiest.date) + ' (' + busiest.count + ')' : '—';
    const tmax = Math.max.apply(null, trend.map((t) => t.count).concat([1]));
    $('anTrend').innerHTML = trend.map((t) => '<span title="' + esc(t.date + ': ' + t.count) + '" style="height:' + Math.max(4, Math.round((t.count / tmax) * 100)) + '%"></span>').join('');
    $('anTrendFrom').textContent = trend.length ? shortDate(trend[0].date) : ''; $('anTrendTo').textContent = trend.length ? shortDate(trend[trend.length - 1].date) : '';
  }

  SARA.onBoot(function () { loadMemory(); loadAnalytics(); });
  SARA.on('page', function (p) { if (p === 'settings') { loadMemory(); loadAnalytics(); } });
  SARA.every(5 * 60 * 1000, function () { loadMemory(); loadAnalytics(); });
})();
