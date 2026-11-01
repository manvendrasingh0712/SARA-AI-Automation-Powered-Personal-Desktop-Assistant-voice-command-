/* ==========================================================================
   usage-card.js -- F7. A small "Last 7 days" card on Home (top right): how many commands you gave each day, as seven bars plus the total.
   Data: the existing get_analytics_dashboard call (daily_trend). Refreshed when the Home page opens, at most once a minute. The card stays
   hidden until there is something to show (no commands in 7 days = no empty chart), and it is hidden on small windows (style/home/home.css).
   Click or Enter opens Settings at the full trend chart. Bars animate with transform only, and not at all under reduced motion.
   Markup: index.html #usageCard.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA, doc = document, $ = SARA.$;
  const card = $('usageCard'), bars = $('ucBars'), days = $('ucDays'), totalEl = $('ucTotal');
  if (!card || !bars) return;
  const DAYS = 7, MIN_GAP_MS = 60000;
  let lastLoad = 0, busy = false, shown = false;

  function dayLetter(s) { const d = new Date(s + 'T00:00:00'); return isNaN(d.getTime()) ? '' : d.toLocaleDateString([], { weekday: 'narrow' }); }
  function dayLong(s) { const d = new Date(s + 'T00:00:00'); return isNaN(d.getTime()) ? s : d.toLocaleDateString([], { weekday: 'long' }); }

  function paint(trend) {
    const week = trend.slice(-DAYS).map(function (t) { return { date: String(t.date || ''), count: Math.max(0, Number(t.count) || 0) }; });
    const total = week.reduce(function (a, t) { return a + t.count; }, 0), max = Math.max.apply(null, week.map(function (t) { return t.count; }).concat([1]));
    if (!week.length || total === 0) { card.hidden = true; shown = false; return; }
    totalEl.textContent = total.toLocaleString();
    bars.textContent = ''; days.textContent = '';
    week.forEach(function (t, i) {
      const b = doc.createElement('span'); b.className = 'uc-bar' + (i === week.length - 1 ? ' today' : ''); b.style.setProperty('--v', (Math.max(0.06, t.count / max)).toFixed(3));
      b.title = dayLong(t.date) + ': ' + t.count + (t.count === 1 ? ' command' : ' commands'); bars.appendChild(b);
      const l = doc.createElement('span'); l.textContent = dayLetter(t.date); days.appendChild(l);
    });
    card.setAttribute('aria-label', 'Usage in the last 7 days: ' + total + (total === 1 ? ' command' : ' commands') + '. Open analytics.');
    const first = !shown; shown = true; card.hidden = false;
    card.classList.remove('ready');
    if (first && !SARA.reduceMotion) requestAnimationFrame(function () { requestAnimationFrame(function () { card.classList.add('ready'); }); });
    else card.classList.add('ready');
  }

  async function load(force) {
    if (busy || (!force && performance.now() - lastLoad < MIN_GAP_MS)) return;
    busy = true; lastLoad = performance.now();
    try {
      const res = await SARA.callApi('get_analytics_dashboard');
      const trend = res && res.ok && res.data && res.data.daily_trend;
      if (Array.isArray(trend)) paint(trend); else { card.hidden = true; shown = false; }
    } catch (e) { card.hidden = true; shown = false; }
    finally { busy = false; }
  }

  function open() {
    SARA.sound && SARA.sound.tap(); SARA.gotoPage('settings');
    setTimeout(function () { const t = $('anTrend') || $('anChart'); if (t && t.scrollIntoView) t.scrollIntoView({ block: 'center', behavior: SARA.reduceMotion ? 'auto' : 'smooth' }); }, 220);
  }
  card.addEventListener('click', open);
  card.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
  SARA.onBoot(function () { load(true); });
  SARA.on('page', function (p) { if (p === 'home') load(false); });
  SARA.usageCard = { refresh: function () { return load(true); } };
})();
