/* ==========================================================================
   today.js -- the "Today" card on Home (weather + next items) and its expanded sheet
   (weather details, Wi-Fi / Focus chips, recent notices, full schedule).
   API calls: get_weather, get_calendar_status, get_today_calendar_events, get_proactive_stats,
   toggle_wifi (+ set_focus_mode via SARA.setFocus in settings.js).  Push event: 'weather_update'.
   Publishes: SARA.emit('calendar', {connected,email,events}) and SARA.emit('proactive', stats)
   for js/settings-skills.js / js/settings-data.js.  Listens: 'reminders' (from notes-reminders.js).
   Styles: style/home.css.  Markup: index.html #todayCard, #todaySheet.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA, $ = SARA.$;
  let weather = null, cal = { connected: false, email: null, events: [] }, reminders = [], proactive = null;

  /* ---- weather ---- */
  function renderWeather() {
    const d = weather;
    if (!d || !d.ok) {
      const err = (d && d.error) ? String(d.error) : '';
      $('tcTemp').textContent = '--°'; $('tcCond').textContent = 'Weather unavailable';
      $('tcSub').textContent = err.length > 34 ? 'Check weather setup' : err;
      $('shTemp').textContent = '--°'; $('shCond').textContent = 'Weather unavailable'; $('shMeta').textContent = err;
      return;
    }
    const cond = d.description || d.condition || '—', aqi = d.aqi_label ? ' · AQI ' + d.aqi_label : '';
    $('tcTemp').textContent = d.temp + '°'; $('tcCond').textContent = cond; $('tcSub').textContent = (d.city || '') + aqi;
    $('shTemp').textContent = d.temp + '°'; $('shCond').textContent = cond + (d.city ? ' · ' + d.city : '');
    $('shMeta').textContent = 'H:' + d.temp_max + '° · L:' + d.temp_min + '°' + (d.humidity != null ? ' · Humidity ' + d.humidity + '%' : '') + aqi;
  }
  async function loadWeather() {
    const res = await SARA.callApi('get_weather');
    if (res && res.data) { weather = res.data; renderWeather(); }
  }
  SARA.on('ev:weather_update', function (data) { weather = data; renderWeather(); });

  /* ---- calendar ---- */
  async function loadCalendar() {
    const st = await SARA.callApi('get_calendar_status');
    const connected = !!(st && st.ok && st.data && st.data.connected);
    let events = [];
    if (connected) {
      const ev = await SARA.callApi('get_today_calendar_events');
      if (ev && ev.ok && Array.isArray(ev.data)) events = ev.data;
    }
    cal = { connected: connected, email: connected ? (st.data.email || null) : null, events: events };
    SARA.emit('calendar', cal); SARA.idle(renderSchedule);
  }

  /* ---- proactive notices ---- */
  async function loadProactive() {
    const s = await SARA.callApi('get_proactive_stats');
    if (s && s.ok) { proactive = s; SARA.emit('proactive', s); SARA.idle(renderNotices); }
  }
  const NOTICE_CLASS = { battery: 'warn', meeting: 'think', idle_break: 'think', reminder: 'info', streak: 'info', routine: 'info' };
  function renderNotices() {
    const wrap = $('noticeList');
    const recent = ((proactive && proactive.recent) || []).slice().reverse().slice(0, 4);
    wrap.innerHTML = recent.length ? recent.map(function (ev) {
      return '<div class="notice-row ' + (NOTICE_CLASS[ev.trigger] || 'info') + '"><span class="n-dot"></span><span class="n-text">' +
        SARA.escapeHtml(ev.message || '') + '</span><span class="n-when">' + SARA.escapeHtml(SARA.relTime(ev.timestamp)) + '</span></div>';
    }).join('') : '<div class="empty" style="padding:8px 4px;">Nothing to flag right now.</div>';
  }

  /* ---- schedule = today's calendar events + today's open reminders ---- */
  function scheduleItems() {
    const items = [], today = SARA.todayStr(0);
    cal.events.forEach(function (ev) {
      const allDay = !ev.start || /^\d{4}-\d{2}-\d{2}$/.test(String(ev.start)), d = new Date(ev.start);
      const ok = !allDay && !isNaN(d.getTime());
      items.push({ sort: ok ? d.getHours() * 60 + d.getMinutes() : -1, time: ok ? SARA.fmtClockTime(d) : 'All day', text: ev.summary || '(No title)' });
    });
    reminders.filter((r) => r.date === today && !r.done).forEach(function (r) {
      const p = String(r.time || '').split(':');
      items.push({ sort: (parseInt(p[0], 10) || 0) * 60 + (parseInt(p[1], 10) || 0), time: SARA.fmt12h(r.time), text: r.text });
    });
    return items.sort((a, b) => a.sort - b.sort);
  }
  function renderSchedule() {
    const items = scheduleItems(), now = new Date(), nowMin = now.getHours() * 60 + now.getMinutes();
    $('scheduleList').innerHTML = items.length ? items.map((i) =>
      '<div class="row"><span class="r-time">' + SARA.escapeHtml(i.time) + '</span><span class="r-text">' + SARA.escapeHtml(i.text) + '</span></div>').join('')
      : '<div class="empty" style="padding:8px 4px;">Nothing scheduled today.</div>';
    const upcoming = items.filter((i) => i.sort < 0 || i.sort >= nowMin).slice(0, 2);
    $('tcNext').innerHTML = upcoming.length ? upcoming.map((i) =>
      '<div class="tc-item"><span class="tc-t">' + SARA.escapeHtml(i.time) + '</span><span class="tc-x">' + SARA.escapeHtml(i.text) + '</span></div>').join('')
      : '<div class="tc-empty">' + (items.length ? 'Nothing else today' : 'Nothing scheduled today') + '</div>';
    // Same data the Today card just rendered, reused (no new backend call) by ambient mode (home.js).
    // Exposed both ways: SARA.getNextItem() for a pull on first entering ambient (works regardless of
    // script load order), and the 'nextItem' event for live updates while ambient is already showing.
    SARA.getNextItem = function () { return upcoming[0] || null; };
    SARA.emit('nextItem', upcoming[0] || null);
  }
  SARA.on('reminders', function (list) { reminders = list || []; renderSchedule(); });

  /* ---- sheet open/close + quick chips ---- */
  function openSheet() { renderWeather(); renderNotices(); renderSchedule(); SARA.openOverlay('todaySheet'); }
  $('todayCard').addEventListener('click', openSheet);
  $('todayCard').addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openSheet(); } });
  $('todayClose').addEventListener('click', () => SARA.closeOverlay('todaySheet'));

  $('qaWifi').addEventListener('click', async function () {
    const res = await SARA.callApi('toggle_wifi');
    const ok = !!(res && res.ok);
    SARA.toast(ok ? 'ti-check' : 'ti-alert-triangle', ok ? '#3FD8C4' : '#D97A6B', (res && res.message) || 'Wi-Fi toggle attempted');
  });
  $('qaFocus').addEventListener('click', () => SARA.setFocus(!SARA.state.focus, true));
  SARA.on('focus', (on) => $('qaFocus').classList.toggle('on', !!on));

  SARA.onBoot(function () { loadWeather(); loadCalendar(); loadProactive(); });
  SARA.on('visible', function () { loadCalendar(); loadProactive(); });
  SARA.every(15 * 60 * 1000, loadWeather);
  SARA.every(5 * 60 * 1000, loadCalendar);
  SARA.every(60 * 1000, loadProactive);

  /* ---- "Next" card: nearest upcoming timed item (calendar event or reminder).
     Reuses scheduleItems() above -> no new backend call. Hidden when nothing is upcoming. ---- */
  function relLabel(m) {
    if (m <= 0) return 'Starting now';
    if (m < 60) return 'Starts in ' + m + ' min';
    const h = Math.floor(m / 60), r = m % 60;
    return 'Starts in ' + h + ' hr' + (r ? ' ' + r + ' min' : '');
  }
  function renderNext() {
    const card = $('nextCard'); if (!card) return;
    const now = new Date(), nowMin = now.getHours() * 60 + now.getMinutes();
    const nxt = scheduleItems().filter((i) => i.time && i.time !== 'All day' && i.sort >= nowMin)[0];
    if (!nxt) { card.hidden = true; return; }
    $('nxTitle').textContent = nxt.text + ' \u2014 ' + nxt.time;
    $('nxRel').textContent = relLabel(nxt.sort - nowMin);
    card.hidden = false;
  }
  SARA.on('nextItem', renderNext);        // fires whenever calendar/reminders data is re-rendered
  SARA.on('visible', renderNext);
  SARA.every(30 * 1000, renderNext);      // live countdown
  if ($('nextCard')) {
    $('nextCard').addEventListener('click', openSheet);
    $('nextCard').addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openSheet(); } });
  }
  renderSchedule(); renderNotices();
})();
