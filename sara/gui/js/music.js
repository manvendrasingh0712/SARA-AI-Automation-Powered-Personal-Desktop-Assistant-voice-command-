/* ==========================================================================
   music.js -- Music card (on the Apps page; click the head to expand into the full player)
   + floating mini-player above the nav + VU-style spectrum.
   API calls: get_media_status (polled every 2s), toggle_music_playback, run_action('play_music'),
   stop_music, skip_next_track, skip_previous_track, seek_media, toggle_shuffle, cycle_repeat_mode,
   get_media_volume, set_media_volume, list_media_sessions, select_media_session, toggle_session_mute,
   get_master_volume, set_master_volume, toggle_master_mute.
   The spectrum is a simulated visual flourish (smoothed layered waves while playing, gentle decay when paused);
   the backend has no audio-level data, and the UI never presents it as real audio.
   Styles: style/music.css.  Markup: index.html #musicCard, #miniPlayer, plus the optional
   per-feature elements documented above each block below (#mcVolume, #mcSessions, #mcSysVolume,
   #sleepBtn, ...) -- every one of them quietly no-ops if its markup isn't present yet.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA, $ = SARA.$;
  const PLAY = '<path d="M8 5v14l11-7z"/>', PAUSE = '<path d="M7 5h3v14H7zM14 5h3v14h-3z"/>';
  const NOTHING = 'Nothing to play. Start a media app or load a track first.';
  const card = $('musicCard'), mini = $('miniPlayer'), seek = $('seek');
  const st = { active: false, playing: false, trackId: null, basePos: 0, baseTs: 0, rate: 1, duration: 0, shuffle: false };
  let dragging = false, pending = false, inflight = false;

  /* ---- spectrum bars ---- */
  const bars = [];
  for (let i = 0; i < 40; i++) { const s = document.createElement('span'); $('spectrum').appendChild(s); bars.push(s); }
  const levels = bars.map(() => 3);
  const REST_LEVEL = 3;
  let spectrumLive = false;
  function spectrumTick(now) {
    if (SARA.current !== 'apps' || !card.classList.contains('expanded')) { spectrumLive = false; return; }
    const t = (now || 0) / 1000;
    let settled = true;
    bars.forEach(function (bar, i) {
      let target = REST_LEVEL;
      if (st.playing) {
        settled = false;
        if (SARA.reduceMotion) target = 6 + Math.abs(Math.sin(i * 0.5)) * 10;          // calm static pattern
        else {
          const beat = 0.55 + 0.45 * Math.abs(Math.sin(t * 2.1));                      // shared slow pulse
          const a = Math.sin(t * (2.6 + (i % 5) * 0.35) + i * 0.7), b = Math.sin(t * (1.3 + (i % 3) * 0.4) + i * 1.9);
          target = 4 + (0.5 + 0.5 * (a * 0.6 + b * 0.4)) * 24 * beat * (0.65 + 0.35 * Math.sin((i / (bars.length - 1)) * Math.PI));
        }
      }
      levels[i] += (target - levels[i]) * (target > levels[i] ? 0.35 : 0.14);          // quick attack, slow decay
      if (Math.abs(levels[i] - REST_LEVEL) > 0.05) settled = false;                    // still easing back to rest -> keep animating
      bar.style.height = levels[i].toFixed(1) + 'px'; bar.style.opacity = (0.3 + (levels[i] / 34) * 0.6).toFixed(2);
    });
    if (st.playing || !settled) requestAnimationFrame(spectrumTick);
    else spectrumLive = false;   // fully at rest: stop redrawing a flat spectrum every frame
  }
  function kickSpectrum() {
    if (spectrumLive || SARA.current !== 'apps' || !card.classList.contains('expanded')) return;
    spectrumLive = true; requestAnimationFrame(spectrumTick);
  }
  SARA.on('page', function (p) { if (p === 'apps') kickSpectrum(); });

  /* ---- album-art accent color extraction (feature: dynamic theming) ----
     Runs only when setArt() is called with a genuinely new track (setArt
     is itself only invoked from the trackId-changed branch in render(),
     plus renderIdle() clearing it) -- never on every 2s poll.

     COLOR BOOST (requested: card looked "dull" against the app's dark
     theme): a plain pixel-average of a photo very often lands in a
     desaturated, mid-grey-ish zone -- fine on a white background, but it
     reads as flat/muddy next to a dark UI where saturated, brighter
     accents are what actually pop. So the raw averaged RGB is converted
     to HSL and pushed toward a minimum saturation/lightness band before
     being used, rather than used as-is. c2 (the gradient's second stop)
     used to just be c1 scaled to 55% brightness in RGB space, which also
     desaturates as a side effect (scaling toward black in RGB muddies
     the hue); it's now derived in HSL instead, keeping the same hue and
     saturation and only dropping lightness, which reads as a real
     gradient instead of "same color, dirtier". */
  function rgbToHsl(r, g, b) {
    r /= 255; g /= 255; b /= 255;
    const max = Math.max(r, g, b), min = Math.min(r, g, b);
    let h = 0, s = 0; const l = (max + min) / 2;
    const d = max - min;
    if (d !== 0) {
      s = d / (1 - Math.abs(2 * l - 1));
      switch (max) {
        case r: h = ((g - b) / d) % 6; break;
        case g: h = (b - r) / d + 2; break;
        default: h = (r - g) / d + 4;
      }
      h *= 60; if (h < 0) h += 360;
    }
    return [h, s, l];
  }
  function hslToRgb(h, s, l) {
    const c = (1 - Math.abs(2 * l - 1)) * s, x = c * (1 - Math.abs((h / 60) % 2 - 1)), m = l - c / 2;
    let r1 = 0, g1 = 0, b1 = 0;
    if (h < 60) { r1 = c; g1 = x; } else if (h < 120) { r1 = x; g1 = c; } else if (h < 180) { g1 = c; b1 = x; }
    else if (h < 240) { g1 = x; b1 = c; } else if (h < 300) { r1 = x; b1 = c; } else { r1 = c; b1 = x; }
    return [Math.round((r1 + m) * 255), Math.round((g1 + m) * 255), Math.round((b1 + m) * 255)];
  }
  function boostAccentPair(r, g, b) {
    let [h, s, l] = rgbToHsl(r, g, b);
    s = Math.min(1, Math.max(s, 0.55));            // never let it go washed-out/grey
    const l1 = Math.min(0.72, Math.max(l, 0.5));    // brighter primary stop -- visible on a dark panel
    const l2 = Math.max(0.28, l1 - 0.22);           // same hue/saturation, just darker -- a real gradient, not mud
    const [r1, g1, b1] = hslToRgb(h, s, l1);
    const [r2, g2, b2] = hslToRgb(h, s, l2);
    return ['rgb(' + r1 + ',' + g1 + ',' + b1 + ')', 'rgb(' + r2 + ',' + g2 + ',' + b2 + ')'];
  }
  let artColorToken = 0;
  function applyAccentColors(c1, c2) {
    // DEBUG (Task 1, item 2c): confirms whether real colors or a (null,
    // null) reset actually reached here, and prints exactly which DOM
    // elements got the CSS custom properties set on them.
    console.log('[accent] applyAccentColors', c1, c2, 'targets:', [card, mini].map((el) => el && el.id));
    [card, mini].forEach(function (el) {
      if (!el) return;
      if (c1 && c2) { el.style.setProperty('--music-accent-1', c1); el.style.setProperty('--music-accent-2', c2); }
      else { el.style.removeProperty('--music-accent-1'); el.style.removeProperty('--music-accent-2'); }  // fall back to CSS defaults (--core/--think)
    });
  }
  function extractAccentColors(url) {
    const token = ++artColorToken;
    if (!url) { applyAccentColors(null, null); return; }
    try {
      const img = new Image();
      img.onload = function () {
        // DEBUG (Task 1, item 2a): confirms the image itself decoded.
        console.log('[accent] img.onload fired, naturalSize=', img.naturalWidth, 'x', img.naturalHeight);
        if (token !== artColorToken) { console.log('[accent] stale token, discarding'); return; }  // a newer track already arrived; discard this stale result
        try {
          const SIZE = 48;
          const cv = document.createElement('canvas'); cv.width = SIZE; cv.height = SIZE;
          const ctx = cv.getContext('2d');
          ctx.drawImage(img, 0, 0, SIZE, SIZE);
          const data = ctx.getImageData(0, 0, SIZE, SIZE).data;  // throws on a canvas-taint (cross-origin) image
          // DEBUG (Task 1, item 2b): confirms getImageData did NOT throw.
          console.log('[accent] getImageData ok, bytes=', data.length);
          const buckets = new Map(); const STEP = 24;
          for (let i = 0; i < data.length; i += 4) {
            if (data[i + 3] < 128) continue;                      // skip transparent pixels
            const r = data[i], g = data[i + 1], b = data[i + 2];
            const max = Math.max(r, g, b), min = Math.min(r, g, b), lightness = (max + min) / 2;
            if (max - min < 12 && (lightness < 22 || lightness > 235)) continue;  // skip near-black/near-white/grey (usually padding, not the art)
            const key = (r / STEP | 0) + ',' + (g / STEP | 0) + ',' + (b / STEP | 0);
            const cur = buckets.get(key) || { r: 0, g: 0, b: 0, n: 0 };
            cur.r += r; cur.g += g; cur.b += b; cur.n++; buckets.set(key, cur);
          }
          let best = null;
          buckets.forEach(function (v) { if (!best || v.n > best.n) best = v; });
          if (!best) { console.log('[accent] no eligible bucket (art may be all near-black/white/grey)'); applyAccentColors(null, null); return; }
          const r1 = Math.round(best.r / best.n), g1 = Math.round(best.g / best.n), b1 = Math.round(best.b / best.n);
          const [c1, c2] = boostAccentPair(r1, g1, b1);
          applyAccentColors(c1, c2);
        } catch (e) { console.error('[accent] extraction threw:', e); applyAccentColors(null, null); }  // canvas-taint or any extraction error -> default colors, never break the card
      };
      img.onerror = function (e) { console.error('[accent] img.onerror fired (image failed to decode)', e); if (token === artColorToken) applyAccentColors(null, null); };
      img.src = url;
    } catch (e) { console.error('[accent] extractAccentColors threw synchronously:', e); applyAccentColors(null, null); }
  }

  /* ---- rendering ---- */
  function setIcons(playing) { ['mcPlayIcon', 'ppPlayIcon', 'mpPlayIcon'].forEach((id) => { $(id).innerHTML = playing ? PAUSE : PLAY; }); }
  function setArt(url) {
    const bg = url ? 'url("' + String(url).replace(/"/g, '%22') + '")' : '';
    $('artBlock').style.backgroundImage = bg; $('artBlock').classList.toggle('has-art', !!url); $('mpArt').style.backgroundImage = bg;
    extractAccentColors(url);
  }
  function setFill(pos) { seek.style.setProperty('--fill', (seek.max > 0 ? Math.min(100, (pos / seek.max) * 100) : 0) + '%'); }
  function setBtns(caps, s) {
    caps = caps || {};
    // Next/Prev/Shuffle/Repeat: NOT gated on caps.can_* anymore -- those
    // flags come from WinRT's is_next_enabled/is_previous_enabled/
    // is_shuffle_enabled/is_repeat_enabled, which plenty of apps (browser-tab
    // media especially) never populate even when the action genuinely works.
    // Trusting them for disabling meant "sometimes clickable, sometimes not"
    // for the exact same app. Buttons stay enabled whenever a track is
    // active; the real API call's {ok:false} (see the click handlers below)
    // is what surfaces a failure now. `shuffle_supported` is a different,
    // more reliable signal (the app never reports a shuffle-state boolean at
    // all) so that one is still respected.
    $('ppShuffle').disabled = !s || s.shuffle_supported === false;
    $('ppRepeat').disabled = !s;
    $('ppNext').disabled = !s;
    $('ppPrev').disabled = !s;
    $('ppStop').disabled = !s;
    seek.disabled = !s || caps.can_seek === false;
  }
  function renderIdle(title, sub) {
    st.active = false; st.playing = false; st.trackId = null; st.duration = 0; st.basePos = 0;
    $('trackTitle').textContent = title; $('trackArtist').textContent = sub;
    setArt(''); setIcons(false); setBtns(null, null);
    seek.max = 1; seek.value = 0; setFill(0); $('curTime').textContent = '0:00'; $('durTime').textContent = '0:00';
    card.classList.remove('playing'); mini.classList.remove('show', 'paused');
    $('ppShuffle').classList.remove('active'); $('ppRepeat').classList.remove('active', 'repeat-track');
    if ($('mcVolume')) $('mcVolume').disabled = true;
    kickSpectrum();
  }
  function render(s) {
    if (!s || !s.ok) { renderIdle('Media controls unavailable', (s && s.error) || 'Could not reach the media backend.'); return; }
    if (!s.active) { renderIdle('Nothing playing', 'Play something in Spotify or Chrome'); return; }
    const id = s.track_id || (s.title || '') + '|' + (s.artist || '');
    if (id !== st.trackId || !st.active) {
      st.trackId = id;
      const title = s.title || 'Unknown track', artist = (s.artist || '') + (s.album ? ' — ' + s.album : '');
      $('trackTitle').textContent = title; $('trackArtist').textContent = artist + (s.app ? (artist ? ' · ' : '') + s.app : '');
      $('mpTitle').textContent = title; $('mpArtist').textContent = s.artist || ''; setArt(s.art || '');
    }
    st.active = true; st.playing = s.status === 'playing'; st.shuffle = !!s.shuffle;
    kickSpectrum();
    setIcons(st.playing); card.classList.toggle('playing', st.playing);
    mini.classList.add('show'); mini.classList.toggle('paused', !st.playing);
    setBtns(s.caps, s);
    $('ppShuffle').classList.toggle('active', st.shuffle);
    $('ppRepeat').classList.toggle('active', !!(s.repeat && s.repeat !== 'none'));
    $('ppRepeat').classList.toggle('repeat-track', s.repeat === 'track');
    const dur = Math.max(0, s.duration_sec || 0), pos = Math.max(0, Math.min(s.position_sec || 0, dur || Infinity));
    st.duration = dur; st.basePos = pos; st.rate = (typeof s.playback_rate === 'number' && s.playback_rate > 0) ? s.playback_rate : 1;
    st.baseTs = typeof s.timeline_updated_at === 'number' ? s.timeline_updated_at : Date.now() / 1000;
    if (!dragging) {
      seek.max = Math.max(dur, 1); seek.value = pos; setFill(pos);
      $('curTime').textContent = SARA.fmtDuration(pos); $('durTime').textContent = SARA.fmtDuration(dur);
    }
  }
  /* ---- session switcher (Feature: pick WHICH app's session to control,
     for when more than one is playing at once -- e.g. Spotify desktop AND
     a YouTube tab both audible. Without this, the widget could only ever
     show/control whichever one _pick_active_session's "prefer anything
     Playing" heuristic happened to land on, with no way to switch.
     Backend: list_media_sessions() / select_media_session(app_id).
     Expects markup: <div id="mcSessions" class="session-switcher"></div>
     inside the expanded card body. Optional -- if absent, this whole
     block quietly no-ops.

     NEW FEATURE: per-session MUTE, directly from a chip -- lets the user
     silence one specific app (e.g. a noisy background YouTube tab) while
     leaving whatever they're actually switched to untouched, without
     first switching to it. Backend: toggle_session_mute(app_id), which
     reuses the same tiered Core Audio matching as the main volume slider
     but applied to an arbitrary app_id, independent of which session is
     "current". mutedIds is a small client-side set purely for the icon
     state -- list_media_sessions() doesn't report per-session mute (that
     would mean running the tiered pycaw match for every live session on
     every 2s poll, which is needlessly expensive for something only the
     mute button itself needs); the icon is instead updated directly from
     each toggle call's own confirmed response, the same "trust the
     confirmed value, not the request" pattern used everywhere else in
     this file (see setVolUI, toggle_shuffle, set_media_volume...). */
  const sessionsBox = $('mcSessions');
  let sessionsKey = '';  // cheap fingerprint so re-render only happens on real change (avoids flicker)
  const mutedIds = new Set();
  const MUTE_ICON = '<svg viewBox="0 0 24 24"><path d="M4 9v6h4l5 5V4L8 9H4z"/></svg>';
  const MUTE_ICON_OFF = '<svg viewBox="0 0 24 24"><path d="M4 9v6h4l5 5V4L8 9H4z"/><path class="mute-x" d="M15.2 9.2l4.6 4.6M19.8 9.2l-4.6 4.6"/></svg>';
  function renderSessions(list) {
    if (!sessionsBox) return;
    if (!list || list.length < 2) { sessionsBox.innerHTML = ''; sessionsBox.classList.remove('show'); sessionsKey = ''; return; }
    const key = list.map((s) => s.app_id + ':' + s.current + ':' + s.playing).join('|');
    if (key === sessionsKey) return;
    sessionsKey = key;
    sessionsBox.classList.add('show');
    sessionsBox.innerHTML = list.map(function (s) {
      const name = s.app_name || 'App';
      const label = name + (s.title ? ' · ' + s.title : '');
      const id = String(s.app_id).replace(/"/g, '&quot;');
      const muted = mutedIds.has(s.app_id);
      return '<div class="session-chip' + (s.current ? ' active' : '') + (s.playing ? ' playing' : '') +
        '" data-app-id="' + id + '" title="' + label.replace(/"/g, '&quot;') + '">' +
        '<span class="chip-name">' + name + '</span>' +
        '<button class="chip-mute' + (muted ? ' muted' : '') + '" data-app-id="' + id +
        '" aria-label="Mute this app" title="' + (muted ? 'Unmute' : 'Mute') + ' ' + name + '">' +
        (muted ? MUTE_ICON_OFF : MUTE_ICON) + '</button></div>';
    }).join('');
  }
  if (sessionsBox) {
    sessionsBox.addEventListener('click', async function (e) {
      const muteBtn = e.target.closest('.chip-mute');
      if (muteBtn) {
        e.stopPropagation();
        if (pending) return;
        pending = true;
        const appId = muteBtn.dataset.appId;
        const res = await SARA.callApi('toggle_session_mute', appId);
        pending = false;
        if (res && res.ok) {
          if (res.muted) mutedIds.add(appId); else mutedIds.delete(appId);
          muteBtn.classList.toggle('muted', !!res.muted);
          muteBtn.innerHTML = res.muted ? MUTE_ICON_OFF : MUTE_ICON;
          muteBtn.title = (res.muted ? 'Unmute' : 'Mute') + ' this app';
        } else SARA.fail('Could not mute that app.');
        return;
      }
      const btn = e.target.closest('.session-chip');
      if (!btn || btn.classList.contains('active') || pending) return;
      pending = true;
      const res = await SARA.callApi('select_media_session', btn.dataset.appId);
      pending = false;
      if (res && res.ok) { sessionsKey = ''; poll(); }  // force a fresh render + immediate status refresh
      else SARA.fail('Could not switch to that app.');
    });
  }
  async function pollSessions() {
    if (!sessionsBox || !card.classList.contains('expanded')) return;  // only worth the extra call while visible
    const res = await SARA.callApi('list_media_sessions');
    if (res && res.ok) renderSessions(res.sessions);
  }

  /* ---- volume (Task 2: Windows per-app volume mixer via pycaw, backend
     matches the active SMTC session's app to its Core Audio session by
     process name -- see media.py get_media_volume/set_media_volume for
     why this can't be done through SMTC itself).
     Expects markup in index.html: a <input type="range" id="mcVolume">
     (0-100) and, optionally, an icon element id="mcVolumeIcon" that gets
     a "muted" class toggled on it. Both are optional -- if absent, this
     whole block quietly no-ops. */
  const volSlider = $('mcVolume'), volIcon = $('mcVolumeIcon');
  let volDragging = false;
  function setVolFill(pct) { volSlider.style.setProperty('--vol-fill', pct + '%'); }
  function setVolUI(vol, muted) {
    if (!volSlider) return;
    volSlider.disabled = false;
    volSlider.value = Math.round((vol || 0) * 100);
    setVolFill(volSlider.value);
    if (volIcon) volIcon.classList.toggle('muted', !!muted);
  }
  async function pollVolume() {
    if (!volSlider || !st.active || volDragging) return;
    const res = await SARA.callApi('get_media_volume');
    if (res && res.ok) setVolUI(res.volume, res.muted);
    else volSlider.disabled = true;  // e.g. UWP app with no matching Core Audio session -- see media.py comment
  }
  if (volSlider) {
    volSlider.addEventListener('input', function () {
      volDragging = true; setVolFill(volSlider.value);
      // DEBUG (Task 1): confirms the slider's own 'input' handler fires
      // on drag. Remove once Problem 1 is confirmed fixed.
      console.log('[volume] input event, value=', volSlider.value);
    });
    volSlider.addEventListener('change', async function () {
      const level = Math.max(0, Math.min(100, parseFloat(volSlider.value) || 0)) / 100;
      // DEBUG (Task 1): confirms 'change' fires and set_media_volume is
      // genuinely called with the expected level. Remove once Problem 1
      // is confirmed fixed.
      console.log('[volume] change event, calling set_media_volume with level=', level);
      const res = await SARA.callApi('set_media_volume', level);
      console.log('[volume] set_media_volume result:', res);
      if (!res || !res.ok) console.warn('[volume] set_media_volume failed:', res && res.error);
      volDragging = false;
    });
  }

  /* ---- system (master) volume (Feature: the previous slider only ever
     controlled ONE app's Core Audio session -- this controls the actual
     Windows output device level, via media.py get_master_volume /
     set_master_volume / toggle_master_mute. Independent of st.active,
     since master volume exists even with nothing playing.
     Expects markup: <input type="range" id="mcSysVolume"> and, optionally,
     id="mcSysVolumeIcon" (gets a "muted" class + acts as a mute button).
     Both optional -- quietly no-ops if absent. */
  const sysVolSlider = $('mcSysVolume'), sysVolIcon = $('mcSysVolumeIcon');
  let sysVolDragging = false;
  function setSysVolFill(pct) { sysVolSlider.style.setProperty('--vol-fill', pct + '%'); }
  function setSysVolUI(vol, muted) {
    if (!sysVolSlider) return;
    sysVolSlider.value = Math.round((vol || 0) * 100);
    setSysVolFill(sysVolSlider.value);
    if (sysVolIcon) sysVolIcon.classList.toggle('muted', !!muted);
  }
  async function pollSystemVolume() {
    if (!sysVolSlider || sysVolDragging || !card.classList.contains('expanded')) return;  // only worth polling while visible
    const res = await SARA.callApi('get_master_volume');
    if (res && res.ok) setSysVolUI(res.volume, res.muted);
  }
  if (sysVolSlider) {
    sysVolSlider.addEventListener('input', function () { sysVolDragging = true; setSysVolFill(sysVolSlider.value); });
    sysVolSlider.addEventListener('change', async function () {
      const level = Math.max(0, Math.min(100, parseFloat(sysVolSlider.value) || 0)) / 100;
      const res = await SARA.callApi('set_master_volume', level);
      if (!res || !res.ok) console.warn('[sysvolume] set_master_volume failed:', res && res.error);
      sysVolDragging = false;
    });
  }
  if (sysVolIcon) {
    sysVolIcon.addEventListener('click', async function () {
      const res = await SARA.callApi('toggle_master_mute');
      if (res && res.ok) sysVolIcon.classList.toggle('muted', !!res.muted);
    });
  }

  /* ---- sleep timer (Feature: auto-pause after N minutes, with a gentle
     ~12s volume fade-out first so playback doesn't just cut off mid-word.
     Expects markup: <button id="sleepBtn">, <div id="sleepMenu"> holding
     buttons with data-min="15|30|45|60" (clicking sleepBtn again while
     armed cancels it), and optionally <span id="sleepBadge"> for the live
     countdown. All optional -- quietly no-ops if the button is absent. */
  const sleepBtn = $('sleepBtn'), sleepMenu = $('sleepMenu'), sleepBadge = $('sleepBadge');
  const sleepState = { endsAt: null, tickId: null, fading: false };
  function sleepCancel() {
    sleepState.endsAt = null; sleepState.fading = false;
    if (sleepState.tickId) { clearInterval(sleepState.tickId); sleepState.tickId = null; }
    if (sleepBtn) sleepBtn.classList.remove('active');
    if (sleepBadge) sleepBadge.textContent = '';
  }
  async function sleepFadeAndPause() {
    if (sleepState.fading) return;
    sleepState.fading = true;
    if (sleepBadge) sleepBadge.textContent = 'Fading…';
    // Best-effort fade: only works for apps get_media_volume/set_media_volume
    // can resolve (see media.py's comment on _pycaw_session_for's known
    // UWP-app limitation). If it can't, we just pause on schedule -- the
    // timer's core promise (music stops) still holds either way.
    const before = await SARA.callApi('get_media_volume');
    const startVol = (before && before.ok) ? before.volume : null;
    if (startVol !== null) {
      const STEPS = 10, STEP_MS = 1200;  // ~12s fade
      for (let i = 1; i <= STEPS; i++) {
        await new Promise((r) => setTimeout(r, STEP_MS));
        await SARA.callApi('set_media_volume', Math.max(0, startVol * (1 - i / STEPS)));
      }
    }
    await SARA.callApi('toggle_music_playback', false);
    if (startVol !== null) await SARA.callApi('set_media_volume', startVol);  // restore -- next play shouldn't start silent
    sleepCancel();
    poll();
  }
  function sleepArm(minutes) {
    sleepCancel();
    if (!minutes) return;
    sleepState.endsAt = Date.now() + minutes * 60000;
    if (sleepBtn) sleepBtn.classList.add('active');
    sleepState.tickId = setInterval(function () {
      const remain = sleepState.endsAt - Date.now();
      if (remain <= 15000 && !sleepState.fading) { sleepFadeAndPause(); return; }
      if (sleepBadge) {
        const m = Math.max(0, Math.floor(remain / 60000)), s = Math.max(0, Math.floor((remain % 60000) / 1000));
        sleepBadge.textContent = m + ':' + String(s).padStart(2, '0');
      }
    }, 1000);
  }
  if (sleepBtn && sleepMenu) {
    sleepBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      if (sleepState.endsAt) { sleepCancel(); return; }  // click while armed = cancel
      sleepMenu.classList.toggle('show');
    });
    sleepMenu.addEventListener('click', function (e) {
      const btn = e.target.closest('button[data-min]');
      if (!btn) return;
      sleepMenu.classList.remove('show');
      sleepArm(parseInt(btn.dataset.min, 10) || 0);
    });
    document.addEventListener('click', function (e) {
      if (!sleepMenu.contains(e.target) && e.target !== sleepBtn) sleepMenu.classList.remove('show');
    });
  }

  async function poll() {
    if (inflight) return; inflight = true;
    try {
      render(await SARA.callApi('get_media_status'));
      await pollVolume(); await pollSessions(); await pollSystemVolume();
    } finally { inflight = false; }
  }
  setInterval(function () {                    // smooth progress between polls
    if (!st.active || dragging || document.hidden) return;
    let pos = st.basePos + (st.playing ? (Date.now() / 1000 - st.baseTs) * st.rate : 0);
    pos = Math.max(0, Math.min(pos, st.duration || pos));
    seek.value = pos; setFill(pos); $('curTime').textContent = SARA.fmtDuration(pos);
  }, 500);

  /* ---- commands (one at a time; UI only trusts the polled backend state) ---- */
  async function run(btn, fn) {
    if (pending) return; pending = true; if (btn) btn.disabled = true;
    try { await fn(); } finally { pending = false; if (btn) btn.disabled = false; await poll(); }
  }
  function togglePlay(btn) {
    run(btn, async function () {
      const res = st.active ? await SARA.callApi('toggle_music_playback', !st.playing) : await SARA.callApi('run_action', 'play_music');
      if (!res || !res.ok) SARA.fail(NOTHING);
    });
  }
  $('mcPlay').addEventListener('click', (e) => { e.stopPropagation(); togglePlay(e.currentTarget); });
  $('ppPlay').addEventListener('click', (e) => togglePlay(e.currentTarget));
  $('mpPlay').addEventListener('click', (e) => { e.stopPropagation(); togglePlay(e.currentTarget); });
  $('ppStop').addEventListener('click', (e) => run(e.currentTarget, () => SARA.callApi('stop_music')));
  $('ppNext').addEventListener('click', (e) => run(e.currentTarget, async function () {
    const res = await SARA.callApi('skip_next_track');
    if (!res || !res.ok) SARA.fail('This app does not support skipping forward.');
  }));
  $('ppPrev').addEventListener('click', (e) => run(e.currentTarget, async function () {
    const res = await SARA.callApi('skip_previous_track');
    if (!res || !res.ok) SARA.fail('This app does not support skipping back.');
  }));
  $('ppShuffle').addEventListener('click', (e) => run(e.currentTarget, async function () {
    const res = await SARA.callApi('toggle_shuffle', !st.shuffle);
    if (!res || !res.ok) SARA.fail('Shuffle could not be changed.');
  }));
  $('ppRepeat').addEventListener('click', (e) => run(e.currentTarget, async function () {
    const res = await SARA.callApi('cycle_repeat_mode');
    if (!res || !res.ok) SARA.fail('Repeat could not be changed.');
  }));
  seek.addEventListener('input', function () {
    dragging = true; const v = parseFloat(seek.value) || 0; setFill(v); $('curTime').textContent = SARA.fmtDuration(v);
  });
  seek.addEventListener('change', async function () {
    const target = Math.max(0, Math.min(parseFloat(seek.value) || 0, st.duration || parseFloat(seek.max) || 0));
    const res = await SARA.callApi('seek_media', target);
    if (res && res.ok) { st.basePos = target; st.baseTs = Date.now() / 1000; }
    dragging = false; poll();
  });

  /* ---- expand / mini-player ---- */
  $('mcHead').addEventListener('click', function (e) { if (!e.target.closest('#mcPlay')) { card.classList.toggle('expanded'); kickSpectrum(); } });
  mini.addEventListener('click', function () { SARA.gotoPage('apps'); card.classList.add('expanded'); });

  SARA.onBoot(poll);
  SARA.on('visible', poll);
  SARA.every(2000, poll);
})();