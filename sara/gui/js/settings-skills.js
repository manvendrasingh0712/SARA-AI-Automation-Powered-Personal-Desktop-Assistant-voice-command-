/* ==========================================================================
   settings-skills.js -- Settings accordions for Skills (list + on/off) and Calendar (connection status).
   API calls: get_skills_list, set_skill_enabled.  Calendar data arrives via SARA 'calendar'
   (published by js/today.js, which owns get_calendar_status / get_today_calendar_events).
   A broken skill still shows (with its error) instead of hiding the list.
   Styles: style/settings.css.  Markup: index.html #page-settings.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA, $ = SARA.$, esc = SARA.escapeHtml;

  async function loadSkills() {
    const wrap = $('skillsList');
    const res = await SARA.callApi('get_skills_list');
    const skills = (res && res.data) || [];
    if (!skills.length) { wrap.textContent = 'No skills found in sara/skills/.'; return; }
    wrap.className = ''; wrap.style.cssText = 'display:flex;flex-direction:column;gap:12px;';
    wrap.innerHTML = skills.map(function (s) {
      const cls = s.status === 'error' ? ' err' : (!s.enabled || s.status === 'disabled' ? ' off' : '');
      const desc = (s.description || s.intent || s.name) + (s.status === 'error' && s.error ? ' — ' + s.error : '');
      return '<div class="skill-row' + cls + '"><span class="s-main"><span class="s-dot"></span>' + esc(s.name) +
        (s.status === 'error' ? ' <small style="color:var(--danger)">error</small>' : '') + '<span class="s-desc">' + esc(desc) + '</span></span>' +
        '<div class="toggle' + (s.enabled ? ' on' : '') + '" data-skill="' + esc(s.name) + '" role="switch" tabindex="0" aria-checked="' + (s.enabled ? 'true' : 'false') + '"></div></div>';
    }).join('');
    wrap.querySelectorAll('[data-skill]').forEach(function (el) {
      SARA.bindToggle(el, async function (on) {
        el.closest('.skill-row').classList.toggle('off', !on);
        await SARA.callApi('set_skill_enabled', el.dataset.skill, on);
        $('skillRestartNote').hidden = false;
      });
    });
  }

  SARA.on('calendar', function (cal) {
    const st = $('calStatus');
    st.textContent = cal.connected ? 'Connected' + (cal.email ? ' as ' + cal.email : '') : 'Not connected'; st.className = cal.connected ? 'ok' : '';
    $('calEvents').innerHTML = !cal.connected
      ? 'Add credentials.json to the project root, then ask Sara about your calendar to trigger the one-time Google sign-in.'
      : (cal.events.length ? cal.events.map(function (ev) {
          const d = new Date(ev.start), t = (!ev.start || /^\d{4}-\d{2}-\d{2}$/.test(String(ev.start)) || isNaN(d.getTime())) ? '' : '<b>' + esc(SARA.fmtClockTime(d)) + '</b> · ';
          return '<div>' + t + esc(ev.summary || '(No title)') + '</div>';
        }).join('') : 'No events today.');
  });

  SARA.onBoot(loadSkills);
  SARA.on('page', function (p) { if (p === 'settings') loadSkills(); });
})();
