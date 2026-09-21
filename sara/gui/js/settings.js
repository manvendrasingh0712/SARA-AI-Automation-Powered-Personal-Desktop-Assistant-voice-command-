/* ==========================================================================
   settings.js -- the top block of the Settings page: display name, listening, mute, focus, voice replies,
   notifications, sound effects, startup sound, language, mic sensitivity, speech speed, and the
   "restore saved settings on launch" step.
   API calls: set_display_name, set_assistant_active (via SARA.setAssistantActive in home.js), set_mute,
   set_focus_mode, update_setting, set_language, set_mic_sensitivity, set_speech_speed, get_ui_settings.
   Exposes SARA.setFocus / SARA.setMute (also used by the Today sheet chip in js/today.js).
   The accordions further down the page live in settings-panels.js / settings-data.js / settings-skills.js.
   Styles: style/settings.css.  Markup: index.html #page-settings.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA, $ = SARA.$;

  /* ---- display name ---- */
  try { const n = localStorage.getItem('sara_display_name'); if (n) $('setName').value = n; } catch (e) {}
  SARA.on('name', function (n) { if (document.activeElement !== $('setName')) $('setName').value = n; });
  $('setName').addEventListener('change', async function () {
    const v = $('setName').value.trim(); if (!v) return;
    const res = await SARA.callApi('set_display_name', v);
    if (res && res.ok) { SARA.setDisplayName(res.name || v); SARA.ok('Name saved'); } else SARA.fail((res && res.error) || 'Could not save name');
  });

  /* ---- mute / focus / listening ---- */
  SARA.setMute = async function (on, persist) {
    SARA.state.muted = !!on; SARA.setToggle($('setMute'), on); SARA.emit('mute', !!on);
    if (persist) await SARA.callApi('set_mute', !!on);
  };
  SARA.setFocus = async function (on, persist) {
    SARA.state.focus = !!on; SARA.setToggle($('setFocus'), on); SARA.emit('focus', !!on);
    if (persist) await SARA.callApi('set_focus_mode', !!on);
  };
  SARA.bindToggle($('setMute'), (on) => SARA.setMute(on, true));
  SARA.bindToggle($('setFocus'), (on) => SARA.setFocus(on, true));
  SARA.bindToggle($('setActive'), (on) => SARA.setAssistantActive(on, true));

  /* ---- generic on/off settings (update_setting) ---- */
  document.querySelectorAll('[data-setting]').forEach(function (el) {
    SARA.bindToggle(el, (on) => SARA.callApi('update_setting', el.dataset.setting, on));
  });
  SARA.bindToggle($('setSounds'), function (on) { SARA.sound.set(on); SARA.callApi('update_setting', 'sound_effects', on); });

  /* ---- language ---- */
  function showLang(lang) {
    document.querySelectorAll('#langChips .chip').forEach((c) => c.classList.toggle('on', c.dataset.lang === lang));
  }
  $('langChips').addEventListener('click', function (e) {
    const chip = e.target.closest('.chip'); if (!chip) return;
    SARA.sound.tap(); showLang(chip.dataset.lang); SARA.callApi('set_language', chip.dataset.lang);
  });

  /* ---- sliders (debounced) ---- */
  function slider(id, valId, apiName) {
    const s = $(id), v = $(valId); let timer;
    s.addEventListener('input', function () {
      v.textContent = s.value; clearTimeout(timer);
      timer = setTimeout(() => SARA.callApi(apiName, parseInt(s.value, 10)), 150);
    });
  }
  slider('micSlider', 'micSliderVal', 'set_mic_sensitivity');
  slider('speedSlider', 'speedSliderVal', 'set_speech_speed');
  function setSlider(id, valId, v) {
    const n = parseInt(v, 10); if (isNaN(n)) return; $(id).value = n; $(valId).textContent = n;
  }

  /* ---- restore what the backend saved last session (display only -- never re-sends) ---- */
  async function restore() {
    const res = await SARA.callApi('get_ui_settings');
    if (!res || !res.ok || !res.data) return;
    const d = res.data;
    SARA.setMute(d.muted === '1', false);
    SARA.setFocus(d.focus_mode === '1', false);
    if (d.language_mode === 'en' || d.language_mode === 'hi') showLang(d.language_mode);
    if (d.mic_sensitivity != null) setSlider('micSlider', 'micSliderVal', d.mic_sensitivity);
    if (d.speech_speed != null) setSlider('speedSlider', 'speedSliderVal', d.speech_speed);
    if (d['setting:sound_effects'] != null) { const on = d['setting:sound_effects'] === '1'; SARA.sound.set(on); SARA.setToggle($('setSounds'), on); }
    document.querySelectorAll('[data-setting]').forEach(function (el) {
      const v = d['setting:' + el.dataset.setting]; if (v != null) SARA.setToggle(el, v === '1');
    });
  }
  SARA.onBoot(restore);
  SARA.setToggle($('setSounds'), SARA.sound.on);
})();
