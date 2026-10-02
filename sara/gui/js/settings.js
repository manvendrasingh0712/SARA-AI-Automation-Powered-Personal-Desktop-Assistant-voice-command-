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

  /* ---- Settings control-center layer: section rail, quick controls, spotlight, one-shot feedback ----
     Settings-only. Quick-control tiles never hold state of their own: a tile click clicks the real toggle, and a
     MutationObserver on those 5 real toggles mirrors their on/off back onto the tiles. */
  (function () {
    const page = $('page-settings'), cards = page && page.querySelector('.set-cards'), rail = $('setRail');
    if (!page || !cards) return;
    const reduce = function () { return !!SARA.reduceMotion; };
    function once(el, cls, ms) {
      if (!el || reduce()) return;
      el.classList.remove(cls); void el.offsetWidth; el.classList.add(cls);
      clearTimeout(el['_t' + cls]); el['_t' + cls] = setTimeout(function () { el.classList.remove(cls); }, ms);
    }
    const all = Array.prototype.slice.call(cards.querySelectorAll('.set-card'));
    all.forEach(function (c) {
      const g = document.createElement('span'); g.className = 'set-glow'; g.setAttribute('aria-hidden', 'true');
      c.insertBefore(g, c.firstChild); c._g = g;
    });

    /* ---- section rail ---- */
    const links = rail ? Array.prototype.slice.call(rail.querySelectorAll('[data-target]')) : [];
    const ind = rail && rail.querySelector('.set-rail-ind');
    links.forEach(function (l, i) { const c = $(l.dataset.target); if (c) c.style.setProperty('--i', i); });
    let active = links.length ? links[0].dataset.target : '', pinned = false;
    function place() {
      const a = rail && rail.querySelector('.on');
      if (!a || !ind || !a.offsetWidth) return;
      ind.style.setProperty('--x', a.offsetLeft + 'px'); ind.style.setProperty('--y', a.offsetTop + 'px');
      ind.style.setProperty('--w', a.offsetWidth + 'px'); ind.style.setProperty('--h', a.offsetHeight + 'px');
      if (rail.scrollWidth > rail.clientWidth + 2) rail.scrollTo({ left: a.offsetLeft - (rail.clientWidth - a.offsetWidth) / 2, behavior: reduce() ? 'auto' : 'smooth' });
    }
    function setActive(id) {
      if (id === active && rail.querySelector('.on')) return;
      active = id;
      links.forEach(function (l) {
        const on = l.dataset.target === id; l.classList.toggle('on', on);
        if (on) l.setAttribute('aria-current', 'true'); else l.removeAttribute('aria-current');
      });
      place();
    }
    links.forEach(function (l) {
      l.addEventListener('click', function () {
        const t = $(l.dataset.target); if (!t) return;
        SARA.sound.tap(); pinned = true; setActive(l.dataset.target);
        t.scrollIntoView({ behavior: reduce() ? 'auto' : 'smooth', block: 'start' });
        once(t, 'set-flash', 1000);
      });
    });
    ['wheel', 'touchstart', 'keydown'].forEach(function (n) { page.addEventListener(n, function () { pinned = false; }, { passive: true }); });
    if (links.length && 'IntersectionObserver' in window) {
      const seen = new Set();
      const io = new IntersectionObserver(function (es) {
        es.forEach(function (e) { if (e.isIntersecting) seen.add(e.target.id); else seen.delete(e.target.id); });
        if (pinned) return;
        for (let i = 0; i < links.length; i++) if (seen.has(links[i].dataset.target)) { setActive(links[i].dataset.target); break; }
      }, { rootMargin: '-12% 0px -62% 0px' });
      links.forEach(function (l) { const t = $(l.dataset.target); if (t) io.observe(t); });
    }
    let rz = 0;
    window.addEventListener('resize', function () { if (rz) return; rz = requestAnimationFrame(function () { rz = 0; place(); }); });

    /* ---- quick controls (mirror the real toggles) ---- */
    const proxies = Array.prototype.slice.call(page.querySelectorAll('[data-proxy]'));
    function realOf(p) {
      const k = p.dataset.proxy;
      return k.indexOf('setting:') === 0 ? page.querySelector('[data-setting="' + k.slice(8) + '"]') : $(k);
    }
    function sync() {
      proxies.forEach(function (p) {
        const r = realOf(p); if (!r) return;
        const on = r.classList.contains('on'); p.classList.toggle('on', on); p.setAttribute('aria-checked', on ? 'true' : 'false');
      });
    }
    if (proxies.length && window.MutationObserver) {
      const mo = new MutationObserver(sync);
      proxies.forEach(function (p) { const r = realOf(p); if (r) mo.observe(r, { attributes: true, attributeFilter: ['class', 'aria-checked'] }); });
    }
    page.addEventListener('click', function (e) {
      const p = e.target.closest('[data-proxy]'); if (!p) return;
      const r = realOf(p); if (!r) return;
      once(p.querySelector('.qc-sw'), 'fx-halo', 600); r.click();
    });
    sync(); SARA.onBoot(function () { setTimeout(sync, 400); });

    /* ---- one-shot feedback: toggle halo + card flash ---- */
    function toggleFx(t) { once(t, 'fx-halo', 600); once(t.closest('.set-card'), 'set-flash', 900); }
    cards.addEventListener('click', function (e) {
      const t = e.target.closest('.toggle'); if (t) toggleFx(t);
      const ch = e.target.closest('#langChips .chip'); if (ch) once(ch.closest('.set-card'), 'set-flash', 900);
    });
    cards.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      const t = e.target.closest && e.target.closest('.toggle'); if (t) toggleFx(t);
    });

    /* ---- pointer spotlight (mouse only, one rAF-throttled handler) ---- */
    if (!reduce() && window.matchMedia && window.matchMedia('(hover:hover)').matches) {
      let pend = null, raf = 0;
      cards.addEventListener('pointermove', function (e) {
        if (e.pointerType !== 'mouse') return;
        pend = e;
        if (raf) return;
        raf = requestAnimationFrame(function () {
          raf = 0;
          const c = pend && pend.target.closest ? pend.target.closest('.set-card') : null;
          if (!c || !c._g) return;
          const r = c.getBoundingClientRect();
          c._g.style.setProperty('--smx', (pend.clientX - r.left).toFixed(0) + 'px');
          c._g.style.setProperty('--smy', (pend.clientY - r.top).toFixed(0) + 'px');
        });
      }, { passive: true });
    }

    /* ---- sliders: track fill ---- */
    const sliders = [$('micSlider'), $('speedSlider')].filter(Boolean);
    function fill(s) { s.style.setProperty('--fill', (((s.value - s.min) / ((s.max - s.min) || 1)) * 100).toFixed(1)); }
    sliders.forEach(function (s) {
      fill(s); s.addEventListener('input', function () { fill(s); });
      s.addEventListener('change', function () { once(s.closest('.set-card'), 'set-flash', 900); });
    });
    const hs = $('hueSlider'); if (hs) hs.addEventListener('change', function () { once($('grp-appearance'), 'set-flash', 900); });

    /* ---- theme bloom ---- */
    const tg = $('themeGrid'), ap = $('grp-appearance');
    if (tg && ap) tg.addEventListener('click', function (e) {
      const b = e.target.closest('.theme-card'); if (!b || reduce()) return;
      const r = ap.getBoundingClientRect(), br = b.getBoundingClientRect();
      ap.style.setProperty('--bx', (br.left + br.width / 2 - r.left).toFixed(0) + 'px');
      ap.style.setProperty('--by', (br.top + br.height / 2 - r.top).toFixed(0) + 'px');
      requestAnimationFrame(function () { once(ap, 'set-bloom', 950); });
    });

    /* ---- page open: staggered entrance, re-sync ---- */
    SARA.on('page', function (p) {
      if (p !== 'settings') return;
      sliders.forEach(fill); sync(); once(page, 'set-enter', 1100);
      requestAnimationFrame(place);
    });
  })();
})();
