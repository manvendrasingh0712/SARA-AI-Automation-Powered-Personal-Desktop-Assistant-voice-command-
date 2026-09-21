/* ==========================================================================
   music.js -- Music card (on the Apps page; click the head to expand into the full player)
   + floating mini-player above the nav + VU-style spectrum.
   API calls: get_media_status (polled every 2s), toggle_music_playback, run_action('play_music'),
   stop_music, skip_next_track, skip_previous_track, seek_media, toggle_shuffle, cycle_repeat_mode.
   The spectrum is a simulated visual flourish (smoothed layered waves while playing, gentle decay when paused);
   the backend has no audio-level data, and the UI never presents it as real audio.
   Styles: style/music.css.  Markup: index.html #musicCard, #miniPlayer.
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
     plus renderIdle() clearing it) -- never on every 2s poll. */
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
          const c1 = 'rgb(' + r1 + ',' + g1 + ',' + b1 + ')';
          const c2 = 'rgb(' + Math.round(r1 * 0.55) + ',' + Math.round(g1 * 0.55) + ',' + Math.round(b1 * 0.55) + ')';
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
    volSlider.addEventListener('input', function () { volDragging = true; setVolFill(volSlider.value); });
    volSlider.addEventListener('change', async function () {
      const level = Math.max(0, Math.min(100, parseFloat(volSlider.value) || 0)) / 100;
      await SARA.callApi('set_media_volume', level);
      volDragging = false;
    });
  }

  async function poll() {
    if (inflight) return; inflight = true;
    try { render(await SARA.callApi('get_media_status')); await pollVolume(); } finally { inflight = false; }
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