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
    try { document.documentElement.lang = (lang === 'hi') ? 'hi' : 'en'; } catch (e) { /* ignore */ }   // screen readers / fonts pick the right voice
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
  // soft tick while dragging the voice sliders (throttled inside the sound engine)
  ['micSlider', 'speedSlider'].forEach(function (id) { $(id).addEventListener('input', function () { SARA.sound.sliderTick(); }); });

  /* ---- sound volume + packs (per device, localStorage only: SARA.sound owns persistence) ---- */
  (function () {
    const vol = $('soundVolume'), volVal = $('soundVolumeVal'), packs = $('soundPacks');
    if (!vol || !packs) return;
    vol.value = Math.round(SARA.sound.volume * 100); volVal.textContent = vol.value;
    let tick = 0;
    vol.addEventListener('input', function () {
      volVal.textContent = vol.value; SARA.sound.setVolume(vol.value / 100);
      clearTimeout(tick); tick = setTimeout(function () { SARA.sound.tap(); }, 120);     // let the user hear the new level
    });
    SARA.sound.packs.forEach(function (p) {
      const c = document.createElement('span'); c.className = 'chip' + (p.id === SARA.sound.pack ? ' on' : ''); c.dataset.pack = p.id; c.textContent = p.label;
      c.setAttribute('role', 'button'); c.tabIndex = 0; packs.appendChild(c);
    });
    function pick(c) {
      if (!c) return; SARA.sound.setPack(c.dataset.pack);
      packs.querySelectorAll('.chip').forEach(function (x) { x.classList.toggle('on', x === c); });
    }
    packs.addEventListener('click', function (e) { pick(e.target.closest('.chip')); });
    packs.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(e.target.closest('.chip')); } });
  })();
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

    /* ---- group list (redesign spec 9.1): exactly one .set-group is visible; the rail buttons choose it ----
       The old scroll-spy is gone: with hidden groups it could only mis-highlight. Cards keep their ids, so everything that finds a
       card or a control by id keeps working, whether or not its group is on screen (a hidden group is display:none, nothing is removed). */
    const groups = Array.prototype.slice.call(page.querySelectorAll('.set-group'));
    const links = rail ? Array.prototype.slice.call(rail.querySelectorAll('button[data-group]')) : [];
    const K_GROUP = 'sara.settings.group', body = page.querySelector('.page-body');
    groups.forEach(function (g) { Array.prototype.forEach.call(g.querySelectorAll('.set-card'), function (c, i) { c.style.setProperty('--i', i); }); });
    const keyOf = function (el) { const g = el && el.closest && el.closest('.set-group'); return g ? g.dataset.group : ''; };
    let current = '', searchPick = null, clearSearch = null;
    function showGroup(key) {
      if (!key || key === current || !groups.some(function (g) { return g.dataset.group === key; })) return false;
      current = key;
      groups.forEach(function (g) { g.classList.toggle('on', g.dataset.group === key); });
      links.forEach(function (l) {
        const on = l.dataset.group === key; l.classList.toggle('on', on);
        if (on) l.setAttribute('aria-current', 'true'); else l.removeAttribute('aria-current');
      });
      if (body) body.scrollTop = 0;
      try { localStorage.setItem(K_GROUP, key); } catch (e) {}
      return true;
    }
    function pick(key) { if (!(searchPick && searchPick(key))) showGroup(key); }     // while a search is on, the list moves through the results instead
    links.forEach(function (l, n) {
      l.addEventListener('click', function () { SARA.sound.tap(); pick(l.dataset.group); });
      l.addEventListener('keydown', function (e) {               // arrow keys, Home / End move through the list
        const k = e.key === 'ArrowDown' ? n + 1 : e.key === 'ArrowUp' ? n - 1 : e.key === 'Home' ? 0 : e.key === 'End' ? links.length - 1 : -1;
        if (k < 0) return; e.preventDefault();
        const t = links[(k + links.length) % links.length]; t.focus(); pick(t.dataset.group);
      });
    });
    /* public: SARA.settings.open('system', 'grp-analytics') -- palette, tour and the Home Usage card use it */
    SARA.settings = {
      open: function (key, cardId) {
        const card = cardId ? $(cardId) : null;
        if (clearSearch) clearSearch();
        showGroup(key || keyOf(card));
        if (!card || !body) return;
        let touched = false;
        const stop = function () { touched = true; };
        ['wheel', 'pointerdown', 'keydown'].forEach(function (n) { body.addEventListener(n, stop, { once: true, passive: true }); });
        requestAnimationFrame(function () { card.scrollIntoView({ block: 'start', behavior: reduce() ? 'auto' : 'smooth' }); once(card, 'set-flash', 1000); });
        // cards above it can shrink while the smooth scroll runs (their data arrives), which leaves the target off by a few px: align once more unless the user took over
        setTimeout(function () {
          ['wheel', 'pointerdown', 'keydown'].forEach(function (n) { body.removeEventListener(n, stop); });
          if (!touched && current === keyOf(card)) card.scrollIntoView({ block: 'start', behavior: 'auto' });
        }, 650);
      },
      showGroupOf: function (el) { return showGroup(keyOf(el)); },
      current: function () { return current; }
    };
    let saved = ''; try { saved = localStorage.getItem(K_GROUP) || ''; } catch (e) {}
    current = ''; showGroup(saved || 'general');
    if (!current) showGroup('general');

    /* ---- search across every group ( "/" to focus, Enter = jump to the first hit, Esc = clear ) ----
       While a query is on, the pane lists the matching cards of ALL groups, each card trimmed to its matching rows, and the
       group list shows how many hits each group has. Nothing is removed: hiding is a class, and clearing it restores the page. */
    if (rail) {
      const ROWS = '.set-row,.info-row,.skill-row,.stat,.theme-card,.pill-btn,.chip,.section-label,.acc-note';
      const wrap = document.createElement('div'); wrap.className = 'set-search-wrap';
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('class', 'ic sm'); svg.setAttribute('aria-hidden', 'true');
      const use = document.createElementNS('http://www.w3.org/2000/svg', 'use'); use.setAttribute('href', '#i-search'); svg.appendChild(use);
      const inp = document.createElement('input');
      inp.type = 'search'; inp.className = 'set-search'; inp.placeholder = 'Search settings\u2026'; inp.autocomplete = 'off'; inp.title = 'Press / to search';
      inp.setAttribute('aria-label', 'Search settings');
      const x = document.createElement('button'); x.type = 'button'; x.className = 'set-search-x'; x.textContent = '\u00d7'; x.hidden = true; x.setAttribute('aria-label', 'Clear search');
      const status = document.createElement('div'); status.className = 'set-search-status'; status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
      wrap.appendChild(svg); wrap.appendChild(inp); wrap.appendChild(x);
      rail.insertBefore(status, rail.firstChild); rail.insertBefore(wrap, status);
      const empty = document.createElement('div'); empty.className = 'set-search-empty'; empty.hidden = true; cards.appendChild(empty);
      const badges = {};
      links.forEach(function (l) { const b = document.createElement('span'); b.className = 'set-count'; b.hidden = true; l.appendChild(b); badges[l.dataset.group] = b; });

      /* hide what has no hit inside it; a hit keeps everything below it (a label keeps what follows it, see below) */
      function prune(box) {
        Array.prototype.forEach.call(box.children, function (el) {
          if (el.classList.contains('set-card-head') || el.classList.contains('set-glow') || el.classList.contains('hit') || el.classList.contains('s-keep')) return;
          if (el.querySelector('.hit,.s-keep')) prune(el); else el.classList.add('s-hide');
        });
      }
      function reset() {
        page.querySelectorAll('.hit').forEach(function (n) { n.classList.remove('hit'); });
        page.querySelectorAll('.s-hide').forEach(function (n) { n.classList.remove('s-hide'); });
        page.querySelectorAll('.s-keep').forEach(function (n) { n.classList.remove('s-keep'); });
        groups.forEach(function (g) { g.classList.remove('s-match'); });
        all.forEach(function (c) { c.classList.remove('dim'); });
      }
      function run() {
        const q = inp.value.trim().toLowerCase();
        reset();
        page.classList.toggle('set-searching', !!q);
        x.hidden = !inp.value;
        links.forEach(function (l) { badges[l.dataset.group].hidden = !q; l.classList.remove('none'); });
        empty.hidden = true;
        if (!q) { status.textContent = ''; return; }
        let total = 0, inGroups = 0;
        groups.forEach(function (g) {
          let n = 0;
          g.querySelectorAll('.set-card').forEach(function (c) {
            if (c.id === 'grp-quick') return;
            const hd = c.querySelector('.set-card-head'), headHit = !!hd && hd.textContent.toLowerCase().indexOf(q) >= 0;
            let hits = 0;
            c.querySelectorAll(ROWS).forEach(function (r) {
              if (r.closest('[hidden]') || r.parentElement.closest('.hit')) return;
              const meta = r.classList.contains('theme-card') ? r.querySelector('.tc-meta') : null;     // not the "SARA / ACTIVE" text of the mini preview
              if ((meta || r).textContent.toLowerCase().indexOf(q) < 0) return;
              r.classList.add('hit'); hits++;
              if (r.classList.contains('section-label')) { let k = r.nextElementSibling; while (k && !k.classList.contains('section-label')) { k.classList.add('s-keep'); k = k.nextElementSibling; } }
            });
            if (headHit && !hits) hits = 1;
            c.classList.toggle('dim', !hits);
            if (hits && !headHit) prune(c);
            n += hits;
          });
          g.classList.toggle('s-match', n > 0);
          const b = badges[g.dataset.group]; if (b) { b.textContent = String(n); const l = b.parentElement; l.classList.toggle('none', !n); }
          total += n; if (n) inGroups++;
        });
        status.textContent = total ? total + (total === 1 ? ' result' : ' results') + ' in ' + inGroups + (inGroups === 1 ? ' group' : ' groups') : 'No results';
        if (!total) { empty.textContent = 'No setting matches \u201c' + inp.value.trim() + '\u201d.'; empty.hidden = false; }
        if (body) body.scrollTop = 0;
      }
      clearSearch = function () { if (inp.value) { inp.value = ''; run(); } };
      searchPick = function (key) {
        if (!inp.value.trim()) return false;
        const g = groups.filter(function (n) { return n.dataset.group === key; })[0];
        if (g && g.classList.contains('s-match')) { g.scrollIntoView({ block: 'start', behavior: reduce() ? 'auto' : 'smooth' }); return true; }
        clearSearch(); return false;                                                        // a group without hits: leave the search and open it
      };
      function jump() {
        run();
        const q = inp.value.trim(); if (!q) return;
        const firstCard = all.filter(function (c) { return c.id !== 'grp-quick' && !c.classList.contains('dim'); })[0]; if (!firstCard) return;
        const hit = firstCard.querySelector('.hit');
        clearSearch(); showGroup(keyOf(firstCard));
        requestAnimationFrame(function () {
          (hit || firstCard).scrollIntoView({ block: hit ? 'center' : 'start', behavior: reduce() ? 'auto' : 'smooth' });
          if (hit) once(hit, 'row-flash', 1300); else once(firstCard, 'set-flash', 1000);
        });
      }
      let st = 0;
      inp.addEventListener('input', function () { clearTimeout(st); st = setTimeout(run, 90); });
      inp.addEventListener('keydown', function (e) {
        e.stopPropagation();
        if (e.key === 'Escape') { clearTimeout(st); clearSearch(); inp.blur(); }
        if (e.key === 'Enter') { clearTimeout(st); jump(); }
      });
      x.addEventListener('click', function () { clearTimeout(st); clearSearch(); inp.focus(); });
      document.addEventListener('keydown', function (e) {
        if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey || SARA.current !== 'settings') return;
        const tg = e.target; if (tg && (/^(input|textarea|select)$/i.test(tg.tagName) || tg.isContentEditable)) return;
        if (!inp.offsetParent) return;
        e.preventDefault(); inp.focus(); inp.select();
      });
    }

    /* ---- quick controls (mirror the real toggles) ---- */
    const proxies = Array.prototype.slice.call(document.querySelectorAll('[data-proxy]'));      // the five quick tiles now sit in the Home dock
    function realOf(p) {
      const k = p.dataset.proxy;
      return k.indexOf('setting:') === 0 ? page.querySelector('[data-setting="' + k.slice(8) + '"]') : $(k);   // the real toggle stays in Settings
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
    document.addEventListener('click', function (e) {
      const p = e.target.closest('[data-proxy]'); if (!p) return;
      const r = realOf(p); if (!r) return;
      once(p.querySelector('.qc-sw'), 'fx-halo', 600); r.click();
    });
    sync(); SARA.onBoot(function () { setTimeout(sync, 400); }); SARA.on('page', function (p) { if (p === 'home') sync(); });

    /* ---- one-shot feedback: toggle halo + card flash ---- */
    function toggleFx(t) { once(t, 'fx-halo', 600); once(t.closest('.set-card'), 'set-flash', 900); }
    cards.addEventListener('click', function (e) {
      const t = e.target.closest('.toggle'); if (t) toggleFx(t);
      const ch = e.target.closest('#langChips .chip'); if (ch) once(ch.closest('.set-card'), 'set-flash', 900);
    });
    cards.addEventListener('click', function (e) {
      const row = e.target.closest('.set-row,.info-row.tog,.skill-row'); if (!row) return;
      if (e.target.closest('.toggle,button,input,select,textarea,a,label,[role="slider"]')) return;
      const sw = row.querySelectorAll('.toggle'); if (sw.length === 1) sw[0].click();
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
    const sliders = [$('micSlider'), $('speedSlider'), $('soundVolume')].filter(Boolean);
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
    });
  })();
})();
