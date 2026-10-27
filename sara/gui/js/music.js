/* ==========================================================================
   music.js -- Music card (on the Apps page; click the head to expand into the full player)
   + floating mini-player above the nav + VU-style spectrum.
   API calls: get_media_status (polled every 2s), toggle_music_playback, run_action('play_music'),
   stop_music, skip_next_track, skip_previous_track, seek_media, toggle_shuffle, cycle_repeat_mode,
   get_media_volume, set_media_volume, toggle_media_mute, list_media_sessions, select_media_session,
   toggle_session_mute, get_master_volume, set_master_volume, toggle_master_mute.
   The spectrum is a simulated visual flourish (smoothed layered waves while playing, gentle decay when paused);
   the backend has no audio-level data, and the UI never presents it as real audio.
   Styles: style/music.css.  Markup: index.html #musicCard, #miniPlayer, plus the optional
   per-feature elements documented above each block below (#trackSource, #seekWrap, #mcVolume, #mcSessions,
   #mcSysVolume, #sleepBtn, ...) -- every one of them quietly no-ops if its markup isn't present yet.
   Card state classes toggled here (consumed by music.css): has-track, playing, paused, reduce-motion.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA, $ = SARA.$;
  const PLAY = '<path d="M8 5v14l11-7z"/>', PAUSE = '<path d="M7 5h3v14H7zM14 5h3v14h-3z"/>';
  const NOTHING = 'Nothing to play. Start a media app or load a track first.';
  const SEEK_THUMB_PX = 14;   // keep in sync with --seek-thumb in music.css (used to place the hover/drag time tip)
  const card = $('musicCard'), mini = $('miniPlayer'), seek = $('seek'), audioModal = $('audioModal');
  const seekWrap = $('seekWrap') || seek.parentElement, seekTip = $('seekTip');
  const st = { active: false, playing: false, trackId: null, basePos: 0, baseTs: 0, rate: 1, duration: 0, shuffle: false, source: '' };
  let dragging = false, pending = false, inflight = false;

  /* ---- reduced motion: mirror SARA.reduceMotion onto the card/mini-player so music.css can switch animations off ---- */
  function syncMotion() {
    const r = !!SARA.reduceMotion;
    card.classList.toggle('reduce-motion', r);
    if (mini) mini.classList.toggle('reduce-motion', r);
    if (audioModal) audioModal.classList.toggle('reduce-motion', r);
  }

  /* ---- spectrum bars ---- */
  const bars = [];
  for (let i = 0; i < 40; i++) { const s = document.createElement('span'); $('spectrum').appendChild(s); bars.push(s); }
  const levels = bars.map(() => 3);
  const REST_LEVEL = 3;
  let spectrumLive = false;
  function spectrumTick(now) {
    if (document.hidden || SARA.current !== 'apps' || !card.classList.contains('expanded')) { spectrumLive = false; return; }
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
    if (spectrumLive || document.hidden || SARA.current !== 'apps' || !card.classList.contains('expanded')) return;
    spectrumLive = true; requestAnimationFrame(spectrumTick);
  }
  SARA.on('page', function (p) { if (p === 'apps') kickSpectrum(); });
  document.addEventListener('visibilitychange', kickSpectrum);   // the loop parks itself while the window is hidden; resume on return

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
    [card, mini, audioModal].forEach(function (el) {
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
        if (token !== artColorToken) { return; }  // a newer track already arrived; discard this stale result
        try {
          const SIZE = 48;
          const cv = document.createElement('canvas'); cv.width = SIZE; cv.height = SIZE;
          const ctx = cv.getContext('2d');
          ctx.drawImage(img, 0, 0, SIZE, SIZE);
          const data = ctx.getImageData(0, 0, SIZE, SIZE).data;  // throws on a canvas-taint (cross-origin) image
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
          if (!best) { applyAccentColors(null, null); return; }
          const r1 = Math.round(best.r / best.n), g1 = Math.round(best.g / best.n), b1 = Math.round(best.b / best.n);
          const [c1, c2] = boostAccentPair(r1, g1, b1);
          applyAccentColors(c1, c2);
        } catch (e) { console.error('[accent] extraction threw:', e); applyAccentColors(null, null); }  // canvas-taint or any extraction error -> default colors, never break the card
      };
      img.onerror = function (e) { console.error('[accent] img.onerror fired (image failed to decode)', e); if (token === artColorToken) applyAccentColors(null, null); };
      img.src = url;
    } catch (e) { console.error('[accent] extractAccentColors threw synchronously:', e); applyAccentColors(null, null); }
  }

  /* ---- source badge (the app/source the media session reports in get_media_status's `app` field --
     whatever the backend supplies, never hardcoded; hidden when there is no active track or no source) ---- */
  const srcBadge = $('trackSource'), srcName = $('trackSourceName');
  let volRow = null;   // assigned in the volume block below; setSource() relabels it with the active app
  function setSource(app) {
    const name = String(app || '').trim();
    if (name === st.source) return;
    st.source = name;
    if (srcBadge) {
      if (srcName) srcName.textContent = name;
      srcBadge.title = name ? 'Playing from ' + name : '';
      srcBadge.hidden = !name;
    }
    if (volRow) volRow.title = name ? name + ' volume' : 'Media volume';
  }

  /* ---- rendering ---- */
  function setIcons(playing) { ['mcPlayIcon', 'ppPlayIcon', 'mpPlayIcon'].forEach((id) => { $(id).innerHTML = playing ? PAUSE : PLAY; }); }
  function setArt(url) {
    const bg = url ? 'url("' + String(url).replace(/"/g, '%22') + '")' : '';
    $('artBlock').style.backgroundImage = bg; $('artBlock').classList.toggle('has-art', !!url); $('mpArt').style.backgroundImage = bg;
    extractAccentColors(url);
  }
  /* The rail/thumb are drawn from --seek-p (0..1) on #seekWrap using the exact (interpolated) position, so they glide
     between the 500ms ticks. The native input only needs --fill for backwards compatibility. */
  function setFill(pos) {
    const max = parseFloat(seek.max) || 0;
    const p = max > 0 ? Math.max(0, Math.min(1, pos / max)) : 0;
    seekWrap.style.setProperty('--seek-p', p.toFixed(4));
    mini.style.setProperty('--mp-p', p.toFixed(4));
    seek.style.setProperty('--fill', (p * 100).toFixed(2) + '%');
  }
  function setDragging(v) { dragging = v; seekWrap.classList.toggle('dragging', v); }
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
    setSource('');
    setArt(''); setIcons(false); setBtns(null, null);
    seek.max = 1; seek.value = 0; setFill(0); $('curTime').textContent = '0:00'; $('durTime').textContent = '0:00';
    card.classList.remove('playing', 'paused', 'has-track'); mini.classList.remove('show', 'paused');
    $('ppShuffle').classList.remove('active'); $('ppRepeat').classList.remove('active', 'repeat-track');
    $('ppShuffle').setAttribute('aria-pressed', 'false'); $('ppRepeat').setAttribute('aria-pressed', 'false');
    if ($('mcVolume')) { $('mcVolume').disabled = true; setVolUnavailable(); }
    kickSpectrum();
  }
  function render(s) {
    if (!s || !s.ok) { renderIdle('Media controls unavailable', (s && s.error) || 'Could not reach the media backend.'); return; }
    if (!s.active) { renderIdle('Nothing playing', 'Play something in Spotify or Chrome'); return; }
    const id = s.track_id || (s.title || '') + '|' + (s.artist || '');
    if (id !== st.trackId || !st.active) {
      st.trackId = id;
      const title = s.title || 'Unknown track', artist = (s.artist || '') + (s.album ? ' — ' + s.album : '');
      $('trackTitle').textContent = title; $('trackArtist').textContent = artist;
      $('mpTitle').textContent = title; $('mpArtist').textContent = s.artist || ''; setArt(s.art || '');
    }
    setSource(s.app);   // after the track block: a session switch can change the app without changing the track id
    st.active = true; st.playing = s.status === 'playing'; st.shuffle = !!s.shuffle;
    kickSpectrum();
    setIcons(st.playing); card.classList.add('has-track'); card.classList.toggle('playing', st.playing); card.classList.toggle('paused', !st.playing);
    mini.classList.add('show'); mini.classList.toggle('paused', !st.playing);
    setBtns(s.caps, s);
    $('ppShuffle').classList.toggle('active', st.shuffle);
    $('ppRepeat').classList.toggle('active', !!(s.repeat && s.repeat !== 'none'));
    $('ppRepeat').classList.toggle('repeat-track', s.repeat === 'track');
    $('ppShuffle').setAttribute('aria-pressed', st.shuffle ? 'true' : 'false');
    $('ppRepeat').setAttribute('aria-pressed', s.repeat && s.repeat !== 'none' ? 'true' : 'false');
    const dur = Math.max(0, s.duration_sec || 0), pos = Math.max(0, Math.min(s.position_sec || 0, dur || Infinity));
    st.duration = dur; st.basePos = pos; st.rate = (typeof s.playback_rate === 'number' && s.playback_rate > 0) ? s.playback_rate : 1;
    st.baseTs = typeof s.timeline_updated_at === 'number' ? s.timeline_updated_at : Date.now() / 1000;
    if (!dragging) {
      seek.max = Math.max(dur, 1); seek.value = pos; setFill(pos);
      $('curTime').textContent = SARA.fmtDuration(pos); $('durTime').textContent = SARA.fmtDuration(dur);
    }
  }

  /* ---- seek hover / drag / keyboard time preview (events only -- no timers, no extra polling) ---- */
  function seekFrac() { const max = parseFloat(seek.max) || 1; return Math.max(0, Math.min(1, (parseFloat(seek.value) || 0) / max)); }
  function showSeekTip(frac) {
    if (!seekTip) return;
    seekTip.textContent = SARA.fmtDuration(frac * (st.duration || parseFloat(seek.max) || 0));
    const w = seekWrap.clientWidth, half = seekTip.offsetWidth / 2;
    const x = SEEK_THUMB_PX / 2 + (w - SEEK_THUMB_PX) * frac;
    seekTip.style.left = Math.max(half, Math.min(w - half, x)) + 'px';
    seekTip.classList.add('show');
  }
  function hideSeekTip() { if (seekTip) seekTip.classList.remove('show'); }
  seek.addEventListener('pointermove', function (e) {
    if (seek.disabled || dragging) return;
    const r = seek.getBoundingClientRect(), usable = r.width - SEEK_THUMB_PX;
    if (usable <= 0) return;
    showSeekTip(Math.max(0, Math.min(1, (e.clientX - r.left - SEEK_THUMB_PX / 2) / usable)));
  });
  seek.addEventListener('pointerleave', function () { if (!dragging && !seek.matches(':focus-visible')) hideSeekTip(); });
  seek.addEventListener('blur', hideSeekTip);

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

  /* ---- combined audio panel: per-app volume + system volume (two independent sliders in one section) ---- */

  /* ---- per-app volume (Task 2: Windows per-app volume mixer via pycaw, backend
     matches the active SMTC session's app to its Core Audio session by
     process name -- see media.py get_media_volume/set_media_volume for
     why this can't be done through SMTC itself).
     Expects markup in index.html: a <input type="range" id="mcVolume">
     (0-100) and, optionally, an icon element id="mcVolumeIcon" that gets
     a "muted" class toggled on it, a mute button id="mcVolumeBtn" and a
     readout id="mcVolumeVal". All optional -- if absent, this whole
     block quietly no-ops. */
  const volSlider = $('mcVolume'), volIcon = $('mcVolumeIcon'), volBtn = $('mcVolumeBtn'), volVal = $('mcVolumeVal');
  volRow = volSlider ? volSlider.closest('.volume-row') : null;
  let volDragging = false;
  function setVolFill(pct) {
    volSlider.style.setProperty('--vol-fill', pct + '%');
    volSlider.style.setProperty('--vol-p', (pct / 100).toFixed(3));
    if (volVal) volVal.textContent = Math.round(pct) + '%';
  }
  function setVolMuted(muted) {
    [volIcon, volBtn, volRow].forEach(function (el) { if (el) el.classList.toggle('muted', !!muted); });
    if (volBtn) { volBtn.setAttribute('aria-pressed', muted ? 'true' : 'false'); volBtn.title = muted ? 'Unmute app audio' : 'Mute app audio'; }
  }
  function setVolUI(vol, muted) {
    if (!volSlider) return;
    volSlider.disabled = false;
    if (volBtn) volBtn.disabled = false;
    volSlider.value = Math.round((vol || 0) * 100);
    setVolFill(volSlider.value);
    setVolMuted(muted);
  }
  function setVolUnavailable() {   // no active track, or no matching Core Audio session (e.g. UWP app -- see media.py)
    if (!volSlider) return;
    volSlider.disabled = true;
    if (volBtn) volBtn.disabled = true;
    if (volVal) volVal.textContent = '—';
  }
  async function pollVolume() {
    if (!volSlider || !st.active || volDragging) return;
    const res = await SARA.callApi('get_media_volume');
    if (res && res.ok) setVolUI(res.volume, res.muted);
    else setVolUnavailable();
  }
  if (volSlider) {
    volSlider.addEventListener('input', function () { volDragging = true; setVolFill(volSlider.value); });
    volSlider.addEventListener('change', async function () {
      const level = Math.max(0, Math.min(100, parseFloat(volSlider.value) || 0)) / 100;
      const res = await SARA.callApi('set_media_volume', level);
      if (!res || !res.ok) console.warn('[volume] set_media_volume failed:', res && res.error);
      volDragging = false;
    });
  }
  if (volBtn) {
    volBtn.addEventListener('click', async function () {
      if (volSlider.disabled) return;
      try {
        const res = await SARA.callApi('toggle_media_mute');
        if (res && res.ok && typeof res.muted === 'boolean') setVolMuted(res.muted);   // trust the confirmed value
        else console.warn('[volume] toggle_media_mute failed:', res && res.error);
      } catch (err) { console.warn('[volume] toggle_media_mute threw:', err); }
      pollVolume();
    });
  }

  /* ---- system (master) volume (Feature: the previous slider only ever
     controlled ONE app's Core Audio session -- this controls the actual
     Windows output device level, via media.py get_master_volume /
     set_master_volume / toggle_master_mute. Independent of st.active,
     since master volume exists even with nothing playing.
     Expects markup: <input type="range" id="mcSysVolume"> and, optionally,
     id="mcSysVolumeIcon" (gets a "muted" class), id="mcSysVolumeBtn" (the
     mute button; the icon itself is used if there is no button) and a
     readout id="mcSysVolumeVal". All optional -- quietly no-ops if absent. */
  const sysVolSlider = $('mcSysVolume'), sysVolIcon = $('mcSysVolumeIcon'), sysVolBtn = $('mcSysVolumeBtn'), sysVolVal = $('mcSysVolumeVal');
  const sysVolRow = sysVolSlider ? sysVolSlider.closest('.volume-row') : null;
  let sysVolDragging = false;
  function setSysVolFill(pct) {
    sysVolSlider.style.setProperty('--vol-fill', pct + '%');
    sysVolSlider.style.setProperty('--vol-p', (pct / 100).toFixed(3));
    if (sysVolVal) sysVolVal.textContent = Math.round(pct) + '%';
  }
  function setSysVolMuted(muted) {
    [sysVolIcon, sysVolBtn, sysVolRow].forEach(function (el) { if (el) el.classList.toggle('muted', !!muted); });
    if (sysVolBtn) { sysVolBtn.setAttribute('aria-pressed', muted ? 'true' : 'false'); sysVolBtn.title = muted ? 'Unmute system audio' : 'Mute system audio'; }
  }
  function setSysVolUI(vol, muted) {
    if (!sysVolSlider) return;
    sysVolSlider.value = Math.round((vol || 0) * 100);
    setSysVolFill(sysVolSlider.value);
    setSysVolMuted(muted);
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
  const sysMuteTarget = sysVolBtn || sysVolIcon;
  if (sysMuteTarget) {
    sysMuteTarget.addEventListener('click', async function () {
      const res = await SARA.callApi('toggle_master_mute');
      if (res && res.ok) setSysVolMuted(res.muted);
    });
  }

  /* ---- sleep timer (Feature: auto-pause after N minutes, with a gentle
     ~12s volume fade-out first so playback doesn't just cut off mid-word.
     Expects markup: <button id="sleepBtn"> (with optional <span id="sleepState"> Off/On pill),
     <div id="sleepMenu"> popover holding buttons with data-min="15|30|45|60" (a button with
     data-min="0", e.g. #sleepOff, cancels), optional <span id="sleepBadge"> for the live countdown and
     optional <button id="sleepCancel"> quick-cancel. Clicking sleepBtn toggles the popover whether or not a
     timer is armed (picking a duration while armed resets it). All optional -- quietly no-ops if the
     button is absent. */
  const sleepBtn = $('sleepBtn'), sleepMenu = $('sleepMenu'), sleepBadge = $('sleepBadge');
  const sleepStateEl = $('sleepState'), sleepCancelBtn = $('sleepCancel'), sleepOff = $('sleepOff');
  const sleepRow = sleepBtn ? sleepBtn.closest('.sleep-row') : null;
  const SLEEP_FADE_LEAD_MS = 15000;   // start the fade this long before the deadline
  const sleepState = { endsAt: null, tickId: null, fading: false, total: 0, minutes: 0, run: 0 };
  function setSleepMenu(open) {
    if (!sleepMenu) return;
    sleepMenu.classList.toggle('show', open);
    if (sleepBtn) sleepBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
  }
  function sleepRender() {   // state-change UI only (the per-second countdown lives in sleepTick)
    const on = !!sleepState.endsAt;
    if (sleepBtn) {
      sleepBtn.classList.toggle('active', on);
      sleepBtn.setAttribute('aria-label', on ? 'Sleep timer on. Change or cancel' : 'Sleep timer off. Set a timer');
    }
    if (sleepRow) {
      sleepRow.classList.toggle('active', on); sleepRow.classList.toggle('fading', on && sleepState.fading);
      if (!on) sleepRow.style.removeProperty('--sleep-p');
    }
    if (sleepStateEl) sleepStateEl.textContent = on ? 'On' : 'Off';
    if (sleepCancelBtn) sleepCancelBtn.hidden = !on;
    if (sleepOff) sleepOff.hidden = !on;
    if (sleepMenu) sleepMenu.querySelectorAll('button[data-min]').forEach(function (b) {
      b.classList.toggle('selected', on && (parseInt(b.dataset.min, 10) || 0) === sleepState.minutes);
    });
    if (!on && sleepBadge) sleepBadge.textContent = '';
  }
  function sleepCancel() {
    sleepState.endsAt = null; sleepState.fading = false; sleepState.total = 0; sleepState.minutes = 0;
    sleepState.run++;   // lets an in-flight fade notice it was cancelled/replaced and stop
    if (sleepState.tickId) { clearInterval(sleepState.tickId); sleepState.tickId = null; }
    sleepRender();
  }
  async function sleepFadeAndPause() {
    if (sleepState.fading) return;
    sleepState.fading = true;
    const run = sleepState.run;
    sleepRender();
    if (sleepBadge) sleepBadge.textContent = 'Fading…';
    // Best-effort fade: only works for apps get_media_volume/set_media_volume
    // can resolve (see media.py's comment on _pycaw_session_for's known
    // UWP-app limitation). If it can't, we just pause on schedule -- the
    // timer's core promise (music stops) still holds either way.
    const before = await SARA.callApi('get_media_volume');
    const startVol = (before && before.ok) ? before.volume : null;
    const aborted = async function () {   // cancelled/replaced mid-fade: put the volume back and leave playback alone
      if (run === sleepState.run) return false;
      if (startVol !== null) await SARA.callApi('set_media_volume', startVol);
      return true;
    };
    if (startVol !== null) {
      const STEPS = 10, STEP_MS = 1200;  // ~12s fade
      for (let i = 1; i <= STEPS; i++) {
        await new Promise((r) => setTimeout(r, STEP_MS));
        if (await aborted()) return;
        await SARA.callApi('set_media_volume', Math.max(0, startVol * (1 - i / STEPS)));
      }
    }
    if (await aborted()) return;
    await SARA.callApi('toggle_music_playback', false);
    if (startVol !== null) await SARA.callApi('set_media_volume', startVol);  // restore -- next play shouldn't start silent
    sleepCancel();
    poll();
  }
  function sleepTick() {
    if (!sleepState.endsAt || sleepState.fading) return;
    const remain = sleepState.endsAt - Date.now();
    if (remain <= SLEEP_FADE_LEAD_MS) { sleepFadeAndPause(); return; }
    if (sleepBadge) {
      const m = Math.max(0, Math.floor(remain / 60000)), s = Math.max(0, Math.floor((remain % 60000) / 1000));
      sleepBadge.textContent = m + ':' + String(s).padStart(2, '0');
    }
    if (sleepRow && sleepState.total) sleepRow.style.setProperty('--sleep-p', Math.max(0, Math.min(1, remain / sleepState.total)).toFixed(4));
  }
  function sleepArm(minutes) {
    sleepCancel();
    if (!minutes) return;
    sleepState.total = minutes * 60000; sleepState.minutes = minutes;
    sleepState.endsAt = Date.now() + sleepState.total;
    sleepRender(); sleepTick();
    sleepState.tickId = setInterval(sleepTick, 1000);
  }
  if (sleepBtn && sleepMenu) {
    sleepBtn.setAttribute('aria-haspopup', 'true'); sleepBtn.setAttribute('aria-expanded', 'false');
    sleepBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      setSleepMenu(!sleepMenu.classList.contains('show'));
    });
    sleepMenu.addEventListener('click', function (e) {
      const btn = e.target.closest('button[data-min]');
      if (!btn) return;
      setSleepMenu(false);
      sleepArm(parseInt(btn.dataset.min, 10) || 0);
    });
    if (sleepCancelBtn) sleepCancelBtn.addEventListener('click', function (e) { e.stopPropagation(); setSleepMenu(false); sleepArm(0); });
    document.addEventListener('click', function (e) {
      if (!sleepMenu.contains(e.target) && !sleepBtn.contains(e.target)) setSleepMenu(false);
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && sleepMenu.classList.contains('show')) { setSleepMenu(false); sleepBtn.focus(); }
    });
    sleepRender();
  }

  /* ---- audio modal (Feature: the media + system volume sliders live in a small modal card opened from #audioBtn,
     so the music card itself stays minimal. The sliders and mute buttons inside keep their original ids, so the
     volume blocks above work unchanged. Optional -- quietly no-ops if the markup is absent.) ---- */
  const audioBtn = $('audioBtn'), audioClose = $('audioClose');
  function setAudioModal(open) {
    if (!audioModal || audioModal.classList.contains('show') === open) return;
    audioModal.classList.toggle('show', open);
    audioModal.setAttribute('aria-hidden', open ? 'false' : 'true');
    if (audioBtn) audioBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) {
      poll();   // fresh volume values the moment it opens (poll() is already guarded against overlapping runs)
      const dlg = audioModal.querySelector('.audio-card');
      if (dlg) dlg.focus();
    } else if (audioBtn) audioBtn.focus();
  }
  if (audioModal) {
    if (audioBtn) audioBtn.addEventListener('click', function (e) { e.stopPropagation(); setAudioModal(true); });
    if (audioClose) audioClose.addEventListener('click', function () { setAudioModal(false); });
    audioModal.addEventListener('click', function (e) { if (e.target.hasAttribute('data-close')) setAudioModal(false); });
    audioModal.addEventListener('keydown', function (e) {   // keep Tab focus inside the open dialog
      if (e.key !== 'Tab') return;
      const items = Array.from(audioModal.querySelectorAll('button:not(:disabled), input:not(:disabled)'));
      if (!items.length) return;
      const first = items[0], last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && audioModal.classList.contains('show')) setAudioModal(false); });
    SARA.on('page', function (p) { if (p !== 'apps') setAudioModal(false); });
  }

  async function poll() {
    if (inflight) return; inflight = true;
    try {
      syncMotion();
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
    setDragging(true); const v = parseFloat(seek.value) || 0; setFill(v); $('curTime').textContent = SARA.fmtDuration(v);
    showSeekTip(seekFrac());
  });
  seek.addEventListener('change', async function () {
    const target = Math.max(0, Math.min(parseFloat(seek.value) || 0, st.duration || parseFloat(seek.max) || 0));
    const res = await SARA.callApi('seek_media', target);
    if (res && res.ok) { st.basePos = target; st.baseTs = Date.now() / 1000; }
    setDragging(false); if (!seek.matches(':focus-visible')) hideSeekTip(); poll();
  });

  /* ---- expand / mini-player ---- */
  const chev = $('mcChev');
  function syncExpanded() {
    const open = card.classList.contains('expanded');
    if (chev) { chev.setAttribute('aria-expanded', open ? 'true' : 'false'); chev.setAttribute('aria-label', open ? 'Collapse player' : 'Expand player'); }
    if (!open) setSleepMenu(false);   // never leave the popover armed inside a collapsed body
    kickSpectrum();
  }
  $('mcHead').addEventListener('click', function (e) { if (!e.target.closest('#mcPlay')) { card.classList.toggle('expanded'); syncExpanded(); } });
  /* ---- draggable mini-player (Feature: pick it up and put it anywhere so it never covers chat text).
     Pointer events (mouse / touch / pen) with a small threshold, so a plain click still opens the Apps page.
     The position is a px offset (--mp-dx / --mp-dy) from the default spot, clamped inside the window, magnet-snapped to
     the edges on release, and remembered in localStorage as a fraction of the movable area (survives window resizes).
     A quick flick glides with friction (a requestAnimationFrame loop that exists ONLY during the glide).
     Right-click or Home resets it; arrow keys nudge it. Reduced motion: no tilt, no glide, no landing pulse. */
  const MINI_KEY = 'sara.miniPos', MINI_EDGE = 12, MINI_TOP_EDGE = 48, MINI_SNAP = 28, MINI_DRAG_PX = 5, MINI_STEP = 12;
  const MINI_FRICTION = 0.9, MINI_MIN_SPEED = 0.03, MINI_FLING_SPEED = 0.35, MINI_MAX_TILT = 7, MINI_BOUNCE = 0.45;
  const miniPos = { dx: 0, dy: 0, fx: 0, fy: 0, moved: false };
  let miniDrag = null, miniGlide = 0, miniTiltTimer = 0, miniClickGuard = false;
  const miniClamp = (v, lo, hi) => Math.min(Math.max(v, lo), Math.max(lo, hi));
  function miniBounds() {   // allowed offset range, measured from the default (undragged) position
    const w = mini.offsetWidth, h = mini.offsetHeight, baseLeft = mini.offsetLeft - w / 2, baseTop = mini.offsetTop;
    return { minX: MINI_EDGE - baseLeft, maxX: window.innerWidth - w - MINI_EDGE - baseLeft,
             minY: MINI_TOP_EDGE - baseTop, maxY: window.innerHeight - h - MINI_EDGE - baseTop };
  }
  function miniApply(dx, dy, b) {
    b = b || miniBounds();
    miniPos.dx = miniClamp(dx, b.minX, b.maxX); miniPos.dy = miniClamp(dy, b.minY, b.maxY);
    mini.style.setProperty('--mp-dx', miniPos.dx.toFixed(1) + 'px'); mini.style.setProperty('--mp-dy', miniPos.dy.toFixed(1) + 'px');
  }
  function miniSnap(b) {   // magnet to the nearest window edge when released close to it
    let x = miniPos.dx, y = miniPos.dy;
    if (x - b.minX < MINI_SNAP) x = b.minX; else if (b.maxX - x < MINI_SNAP) x = b.maxX;
    if (y - b.minY < MINI_SNAP) y = b.minY; else if (b.maxY - y < MINI_SNAP) y = b.maxY;
    miniApply(x, y, b);
  }
  function miniSave() {
    const b = miniBounds();
    miniPos.fx = b.maxX > b.minX ? (miniPos.dx - b.minX) / (b.maxX - b.minX) : 0;
    miniPos.fy = b.maxY > b.minY ? (miniPos.dy - b.minY) / (b.maxY - b.minY) : 0;
    try { localStorage.setItem(MINI_KEY, JSON.stringify({ fx: +miniPos.fx.toFixed(4), fy: +miniPos.fy.toFixed(4) })); } catch (e) { /* storage unavailable: position just won't persist */ }
  }
  function miniPlace() {   // fractions -> offsets (used on load and on window resize)
    const b = miniBounds();
    miniApply(b.minX + miniPos.fx * (b.maxX - b.minX), b.minY + miniPos.fy * (b.maxY - b.minY), b);
  }
  function miniLand() {   // one-shot shockwave
    if (SARA.reduceMotion) return;
    mini.classList.remove('landed'); void mini.offsetWidth; mini.classList.add('landed');
  }
  function miniSettle() {
    miniGlide = 0; mini.classList.remove('gliding', 'dragging');
    mini.style.setProperty('--mp-tilt', '0deg');
    miniSnap(miniBounds()); miniPos.moved = true; miniSave(); miniLand();
  }
  function miniStopGlide() {   // grabbed again mid-glide: freeze where it is
    if (!miniGlide) return;
    cancelAnimationFrame(miniGlide); miniGlide = 0; mini.classList.remove('gliding');
    mini.style.setProperty('--mp-tilt', '0deg'); miniPos.moved = true; miniSave();
  }
  function miniTilt(vx) {   // lean into the direction of travel; settles a moment after the pointer pauses
    if (SARA.reduceMotion) return;
    mini.style.setProperty('--mp-tilt', Math.max(-MINI_MAX_TILT, Math.min(MINI_MAX_TILT, vx * 4)).toFixed(2) + 'deg');
    clearTimeout(miniTiltTimer);
    miniTiltTimer = setTimeout(function () { mini.style.setProperty('--mp-tilt', '0deg'); }, 90);
  }
  function miniGlideStart(vx, vy) {
    const b = miniBounds(); let x = miniPos.dx, y = miniPos.dy, last = performance.now();
    mini.classList.add('gliding'); mini.classList.remove('dragging');
    function step(now) {
      const dt = Math.min(32, now - last); last = now;
      x += vx * dt; y += vy * dt;
      if (x < b.minX || x > b.maxX) { x = miniClamp(x, b.minX, b.maxX); vx = -vx * MINI_BOUNCE; }   // soft bounce off the window edges
      if (y < b.minY || y > b.maxY) { y = miniClamp(y, b.minY, b.maxY); vy = -vy * MINI_BOUNCE; }
      const k = Math.pow(MINI_FRICTION, dt / 16.7); vx *= k; vy *= k;
      miniApply(x, y, b); miniTilt(vx);
      if (Math.hypot(vx, vy) < MINI_MIN_SPEED) { miniSettle(); return; }
      miniGlide = requestAnimationFrame(step);
    }
    miniGlide = requestAnimationFrame(step);
  }
  function miniEnd(e) {
    const d = miniDrag;
    if (!d || e.pointerId !== d.id) return;
    miniDrag = null;
    if (!d.on) return;   // never became a drag: let the click through
    miniClickGuard = true; setTimeout(function () { miniClickGuard = false; }, 0);   // swallow the click that ends a drag
    clearTimeout(miniTiltTimer);
    const resting = e.timeStamp - d.lt > 80;   // pointer paused before release: no fling
    const vx = resting ? 0 : d.vx, vy = resting ? 0 : d.vy;
    if (!SARA.reduceMotion && Math.hypot(vx, vy) > MINI_FLING_SPEED) miniGlideStart(vx, vy); else miniSettle();
  }
  function miniReset() {
    miniStopGlide(); miniPos.moved = false; miniPos.fx = 0; miniPos.fy = 0;
    mini.style.removeProperty('--mp-dx'); mini.style.removeProperty('--mp-dy'); mini.style.setProperty('--mp-tilt', '0deg');
    miniPos.dx = 0; miniPos.dy = 0;
    try { localStorage.removeItem(MINI_KEY); } catch (e) { /* nothing stored */ }
    miniLand();
  }
  mini.addEventListener('pointerdown', function (e) {
    if (e.button !== 0 || e.target.closest('.mp-btn')) return;
    miniStopGlide();
    miniDrag = { id: e.pointerId, sx: e.clientX, sy: e.clientY, ox: miniPos.dx, oy: miniPos.dy, on: false, b: null,
                 lx: e.clientX, ly: e.clientY, lt: e.timeStamp, vx: 0, vy: 0 };
  });
  mini.addEventListener('pointermove', function (e) {
    const d = miniDrag;
    if (!d || e.pointerId !== d.id) return;
    if (!(e.buttons & 1)) { miniDrag = null; return; }   // button already released elsewhere
    if (!d.on) {
      if (Math.hypot(e.clientX - d.sx, e.clientY - d.sy) < MINI_DRAG_PX) return;
      d.on = true; d.b = miniBounds(); mini.classList.add('dragging');
      try { mini.setPointerCapture(e.pointerId); } catch (err) { /* pointer already gone */ }
    }
    miniApply(d.ox + e.clientX - d.sx, d.oy + e.clientY - d.sy, d.b);
    const dt = Math.max(1, e.timeStamp - d.lt);
    d.vx = d.vx * 0.6 + ((e.clientX - d.lx) / dt) * 0.4; d.vy = d.vy * 0.6 + ((e.clientY - d.ly) / dt) * 0.4;   // px/ms, smoothed
    d.lx = e.clientX; d.ly = e.clientY; d.lt = e.timeStamp;
    miniTilt(d.vx);
  });
  mini.addEventListener('pointerup', miniEnd);
  mini.addEventListener('pointercancel', miniEnd);
  mini.addEventListener('contextmenu', function (e) { e.preventDefault(); miniReset(); });
  mini.addEventListener('animationend', function (e) { if (e.target === mini) mini.classList.remove('landed'); });
  mini.tabIndex = 0; mini.setAttribute('role', 'group');
  mini.setAttribute('aria-label', 'Mini player. Drag to move, Enter to open, arrow keys to nudge, Home to reset');
  mini.title = 'Drag to move · Right-click to reset';
  mini.addEventListener('keydown', function (e) {
    if (e.target !== mini) return;   // the play button keeps its own keys
    const step = e.shiftKey ? MINI_STEP * 4 : MINI_STEP;
    const nudge = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key];
    if (nudge) { e.preventDefault(); miniStopGlide(); miniApply(miniPos.dx + nudge[0], miniPos.dy + nudge[1]); miniPos.moved = true; miniSave(); }
    else if (e.key === 'Home') { e.preventDefault(); miniReset(); }
    else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); mini.click(); }
  });
  window.addEventListener('resize', function () { if (miniPos.moved) miniPlace(); });
  (function miniRestore() {
    let s = null;
    try { s = JSON.parse(localStorage.getItem(MINI_KEY) || 'null'); } catch (e) { s = null; }
    if (!s || typeof s.fx !== 'number' || typeof s.fy !== 'number') return;
    miniPos.fx = s.fx; miniPos.fy = s.fy; miniPos.moved = true; miniPlace();
  })();
  let morph = null;
  function morphClear() {
    const m = morph; if (!m) return; morph = null;
    m.anims.forEach(function (a) { try { a.cancel(); } catch (e) { /* animation already gone */ } });
    m.shell.remove(); document.body.classList.remove('mp-morphing');
  }
  SARA.on('page', function (p) { if (p !== 'apps') morphClear(); });
  window.addEventListener('resize', morphClear);
  function openFromMini() {
    if (morph) return;
    const from = (!SARA.reduceMotion && !document.hidden && typeof card.animate === 'function' && SARA.current !== 'apps') ? mini.getBoundingClientRect() : null;
    const mcBody = card.querySelector('.mc-body');
    if (from && mcBody) mcBody.style.transition = 'none';
    SARA.skipSharedOnce = true; SARA.gotoPage('apps'); SARA.skipSharedOnce = false;
    card.classList.add('expanded'); syncExpanded();
    if (!from || !mcBody) return;
    const to = SARA.restRect(card);
    mcBody.style.transition = '';
    if (!(from.width > 0) || !(to.width > 0)) return;
    const MORPH_MS = 460, EASE = 'cubic-bezier(.22,1,.36,1)';
    const shell = document.createElement('div'); shell.className = 'mp-morph'; shell.setAttribute('aria-hidden', 'true'); shell.setAttribute('inert', '');
    const fade = document.createElement('div'); fade.className = 'mp-morph-fade';
    const cl = mini.cloneNode(true);
    ['id', 'tabindex', 'title', 'role', 'aria-label'].forEach(function (a) { cl.removeAttribute(a); });
    cl.querySelectorAll('[id]').forEach(function (n) { n.removeAttribute('id'); });
    cl.classList.remove('dragging', 'gliding', 'landed'); cl.classList.add('mp-morph-clone');
    cl.style.width = from.width + 'px'; cl.style.height = from.height + 'px';
    fade.appendChild(cl); shell.appendChild(fade);
    const r0 = window.getComputedStyle(mini).borderRadius || '12px', r1 = window.getComputedStyle(card).borderRadius || '14px';
    const s0 = { left: from.left + 'px', top: from.top + 'px', width: from.width + 'px', height: from.height + 'px', borderRadius: r0 };
    const s1 = { left: to.left + 'px', top: to.top + 'px', width: to.width + 'px', height: to.height + 'px', borderRadius: r1 };
    shell.style.left = s0.left; shell.style.top = s0.top; shell.style.width = s0.width; shell.style.height = s0.height; shell.style.borderRadius = s0.borderRadius;
    document.body.appendChild(shell); document.body.classList.add('mp-morphing');
    const a1 = shell.animate([s0, s1], { duration: MORPH_MS, easing: EASE, fill: 'forwards' });
    const a2 = fade.animate([{ opacity: 1 }, { opacity: 0, offset: 0.45 }, { opacity: 0 }], { duration: MORPH_MS, easing: 'linear', fill: 'forwards' });
    const a3 = card.animate([{ opacity: 0 }, { opacity: 0, offset: 0.3 }, { opacity: 1 }], { duration: MORPH_MS, easing: 'ease-out' });
    morph = { shell: shell, anims: [a1, a2, a3] };
    a1.onfinish = function () { if (morph && morph.shell === shell) morphClear(); };
  }
  mini.addEventListener('click', function () {
    if (miniClickGuard) return;   // this click is just the end of a drag
    openFromMini();
  });

  syncMotion();
  SARA.onBoot(poll);
  SARA.on('visible', poll);
  SARA.every(2000, poll);
})();