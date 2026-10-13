/* ==========================================================================
   music-plus.js -- Music card extras layered on js/music.js (that file is NOT modified). Frontend only.
   What it adds (inside the expanded player):
   1 Lyrics   OFF by default. When you switch it on, the song title + artist + length are sent to lrclib.net (a free community lyrics database) -- that is the
              only data sent, and the card says so. Time-synced lyrics scroll with the song, the current line is highlighted, click a line to seek;
              songs without synced lyrics show plain text. Results are cached on this device (40 songs) so repeats need no network.
   2 Recent   a local history of songs this app has seen playing (30), with "Play again" (sends "play <song> by <artist>" like typing it in chat).
   Not possible (no backend data): an "Up next" queue -- Windows media sessions don't expose one -- so none is faked.
   Album-art accent colour already exists in music.js (--music-accent-1/2) and is reused here. Styles: style/music-plus.css.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA; if (!SARA) return;
  const doc = document, $ = SARA.$, card = $('musicCard'), inner = card && card.querySelector('.mc-body-inner');
  if (!card || !inner) return;
  const el = function (tag, cls, text) { const n = doc.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
  const mem = {};
  const store = {
    get: function (k) { try { const v = localStorage.getItem(k); if (v !== null) return v; } catch (e) { /* blocked */ } return (k in mem) ? mem[k] : null; },
    set: function (k, v) { mem[k] = v; try { localStorage.setItem(k, v); } catch (e) { /* blocked */ } }
  };
  const readJson = function (k, fb) { try { const v = JSON.parse(store.get(k) || 'null'); return v == null ? fb : v; } catch (e) { return fb; } };
  const K_ON = 'sara_lyrics_on', K_CACHE = 'sara_lyrics_cache', K_HIST = 'sara_music_recent', K_TAB = 'sara_mx_tab';

  /* ------------------------------------------------------------------ now-playing state (from get_media_status) */
  const now = { active: false, key: '', title: '', artist: '', album: '', dur: 0, pos: 0, ts: 0, rate: 1, playing: false, app: '' };
  const posNow = function () { return now.playing ? now.pos + (Date.now() / 1000 - now.ts) * now.rate : now.pos; };
  const origCall = SARA.callApi;
  SARA.callApi = function (name) {
    const p = origCall.apply(this, arguments);
    if (name === 'get_media_status' && p && p.then) p.then(onStatus, function () { /* ignore */ });
    return p;
  };
  function onStatus(s) {
    if (!s || !s.ok || !s.active) { if (now.active) { now.active = false; now.key = ''; paintLy(); } return; }
    const key = ((s.artist || '') + '|' + (s.title || '') + '|' + Math.round(s.duration_sec || 0)).toLowerCase();
    now.title = s.title || ''; now.artist = s.artist || ''; now.album = s.album || ''; now.app = s.app || ''; now.dur = Math.max(0, s.duration_sec || 0);
    now.pos = Math.max(0, s.position_sec || 0); now.ts = typeof s.timeline_updated_at === 'number' ? s.timeline_updated_at : Date.now() / 1000;
    now.rate = (typeof s.playback_rate === 'number' && s.playback_rate > 0) ? s.playback_rate : 1; now.playing = s.status === 'playing'; now.active = true;
    if (key !== now.key) { now.key = key; remember(); if (lyricsOn) loadLyrics(); else paintLy(); }
  }

  /* ------------------------------------------------------------------ panel shell */
  const mx = el('div', 'mx'), tabs = el('div', 'mx-tabs'); tabs.setAttribute('role', 'tablist');
  const tabLy = el('button', 'mx-tab', 'Lyrics'), tabRc = el('button', 'mx-tab', 'Recent');
  [tabLy, tabRc].forEach(function (t) { t.type = 'button'; t.setAttribute('role', 'tab'); tabs.appendChild(t); });
  const paneLy = el('div', 'mx-pane'), paneRc = el('div', 'mx-pane'); paneLy.setAttribute('role', 'tabpanel'); paneRc.setAttribute('role', 'tabpanel');
  mx.appendChild(tabs); mx.appendChild(paneLy); mx.appendChild(paneRc); inner.appendChild(mx);
  let tab = store.get(K_TAB) === 'recent' ? 'recent' : 'lyrics';
  function setTab(t) {
    tab = t; store.set(K_TAB, t);
    tabLy.classList.toggle('on', t === 'lyrics'); tabRc.classList.toggle('on', t === 'recent');
    tabLy.setAttribute('aria-selected', t === 'lyrics'); tabRc.setAttribute('aria-selected', t === 'recent');
    paneLy.hidden = t !== 'lyrics'; paneRc.hidden = t !== 'recent'; if (t === 'recent') paintRecent(); else paintLy();
  }
  tabLy.addEventListener('click', function () { setTab('lyrics'); }); tabRc.addEventListener('click', function () { setTab('recent'); });

  /* ------------------------------------------------------------------ recent */
  function remember() {
    if (!now.title) return; const h = readJson(K_HIST, []);
    if (h.length && h[0].k === now.key) return;
    h.unshift({ k: now.key, title: now.title, artist: now.artist, app: now.app, t: Date.now() }); store.set(K_HIST, JSON.stringify(h.slice(0, 30)));
    if (tab === 'recent') paintRecent();
  }
  const rel = function (ts) { const m = Math.round((Date.now() - ts) / 60000); if (m < 1) return 'just now'; if (m < 60) return m + ' min ago'; const h = Math.round(m / 60); if (h < 24) return h + ' h ago'; const d = Math.round(h / 24); return d === 1 ? 'yesterday' : d + ' days ago'; };
  function paintRecent() {
    const h = readJson(K_HIST, []); paneRc.textContent = '';
    if (!h.length) { paneRc.appendChild(el('div', 'mx-empty', 'Songs you play will show up here. This list is kept by this app on this device.')); return; }
    const list = el('div', 'mx-rc-list');
    h.forEach(function (it, i) {
      const row = el('div', 'mx-rc' + (i === 0 && now.active && it.k === now.key ? ' now' : ''));
      const tx = el('div', 'mx-rc-tx'); tx.appendChild(el('div', 'mx-rc-t', it.title)); tx.appendChild(el('div', 'mx-rc-s', [it.artist, it.app].filter(Boolean).join(' \u00b7 ') + (it.artist || it.app ? ' \u00b7 ' : '') + rel(it.t)));
      const b = el('button', 'pill-btn small', row.classList.contains('now') ? 'Playing' : 'Play again'); b.type = 'button'; b.disabled = row.classList.contains('now');
      b.addEventListener('click', function () {
        SARA.sendCommand('play ' + it.title + (it.artist ? ' by ' + it.artist : '')).then(function (res) { if (res && res.ok === false && res.reason && res.reason !== 'duplicate') SARA.fail(res.reason === 'busy' ? 'Sara is still working on your last request.' : 'Could not start that song.'); else SARA.ok('Asked Sara to play it'); });
      });
      row.appendChild(tx); row.appendChild(b); list.appendChild(row);
    });
    const foot = el('div', 'mx-foot'), clr = el('button', 'link-btn', 'Clear history'); clr.type = 'button';
    clr.addEventListener('click', function () { store.set(K_HIST, '[]'); paintRecent(); }); foot.appendChild(clr);
    paneRc.appendChild(list); paneRc.appendChild(foot);
  }

  /* ------------------------------------------------------------------ lyrics */
  let lyricsOn = store.get(K_ON) === '1', token = 0;
  let ly = { state: 'idle', lines: [], synced: false, msg: '' };   // idle | loading | synced | plain | none | instrumental | error
  let curIdx = -1, userUntil = 0, scroller = null;
  const cleanTitle = function (t) { return String(t).replace(/\s*[\(\[].*?[\)\]]/g, '').replace(/\s*-\s*(remaster(ed)?|live|version|single|radio edit|mono|stereo).*$/i, '').trim() || t; };
  function parseLrc(txt) {
    const out = [];
    String(txt).split(/\r?\n/).forEach(function (line) {
      const re = /\[(\d{1,3}):(\d{2}(?:[.:]\d{1,3})?)\]/g, tags = []; let m, last = 0;
      while ((m = re.exec(line))) { tags.push(parseInt(m[1], 10) * 60 + parseFloat(m[2].replace(':', '.'))); last = re.lastIndex; }
      if (!tags.length) return; const text = line.slice(last).trim(); tags.forEach(function (t) { out.push({ t: t, text: text }); });
    });
    return out.sort(function (a, b) { return a.t - b.t; });
  }
  async function fetchJson(url) {
    const ac = new AbortController(), timer = setTimeout(function () { ac.abort(); }, 7000);
    try { const r = await fetch(url, { signal: ac.signal }); if (r.status === 404) return null; if (!r.ok) throw new Error('http ' + r.status); return await r.json(); } finally { clearTimeout(timer); }
  }
  const qs = function (o) { return Object.keys(o).filter(function (k) { return o[k]; }).map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(o[k]); }).join('&'); };
  async function lookup() {
    const dur = Math.round(now.dur) || '';
    let r = await fetchJson('https://lrclib.net/api/get?' + qs({ artist_name: now.artist, track_name: now.title, album_name: now.album, duration: dur }));
    if (!r) r = await fetchJson('https://lrclib.net/api/get?' + qs({ artist_name: now.artist, track_name: cleanTitle(now.title), duration: dur }));
    if (!r) {
      const arr = await fetchJson('https://lrclib.net/api/search?' + qs({ track_name: cleanTitle(now.title), artist_name: now.artist }));
      if (Array.isArray(arr) && arr.length) {
        const close = arr.filter(function (x) { return !dur || Math.abs((x.duration || 0) - dur) <= 4; }); const pool = close.length ? close : arr;
        r = pool.find(function (x) { return x.syncedLyrics; }) || pool.find(function (x) { return x.plainLyrics; }) || null;
      }
    }
    return r;
  }
  function readCache() { return readJson(K_CACHE, {}); }
  function writeCache(key, val) {
    const c = readCache(); c[key] = Object.assign({ t: Date.now() }, val); const keys = Object.keys(c);
    if (keys.length > 40) keys.sort(function (a, b) { return c[a].t - c[b].t; }).slice(0, keys.length - 40).forEach(function (k) { delete c[k]; });
    store.set(K_CACHE, JSON.stringify(c));
  }
  function applyResult(v) {
    curIdx = -1;
    if (!v || v.none) ly = { state: 'none', lines: [], synced: false };
    else if (v.i) ly = { state: 'instrumental', lines: [], synced: false };
    else if (v.s) { const lines = parseLrc(v.s); ly = lines.length ? { state: 'synced', lines: lines, synced: true } : { state: 'none', lines: [], synced: false }; }
    else if (v.p) ly = { state: 'plain', lines: String(v.p).split(/\r?\n/).map(function (t) { return { t: 0, text: t.trim() }; }), synced: false };
    else ly = { state: 'none', lines: [], synced: false };
  }
  async function loadLyrics() {
    if (!lyricsOn || !now.active || !now.title) { ly = { state: 'idle', lines: [], synced: false }; paintLy(); return; }
    const my = ++token, key = now.key, c = readCache()[key];
    if (c && (!c.none || Date.now() - c.t < 6 * 3600 * 1000)) { applyResult(c); paintLy(); return; }
    ly = { state: 'loading', lines: [], synced: false }; paintLy();
    try {
      const r = await lookup(); if (my !== token) return;
      const v = r ? { s: r.syncedLyrics || '', p: r.plainLyrics || '', i: !!r.instrumental } : { none: true };
      writeCache(key, v); applyResult(v);
    } catch (e) { if (my !== token) return; ly = { state: 'error', lines: [], synced: false }; }
    paintLy();
  }
  function setOn(on) { lyricsOn = on; store.set(K_ON, on ? '1' : '0'); if (on) loadLyrics(); else { token++; ly = { state: 'idle', lines: [], synced: false }; paintLy(); } }

  function paintLy() {
    if (tab !== 'lyrics') return;
    paneLy.textContent = ''; scroller = null; curIdx = -1;
    if (!lyricsOn) {
      const box = el('div', 'mx-optin');
      box.appendChild(el('div', 'mx-optin-t', 'Show lyrics'));
      box.appendChild(el('div', 'mx-optin-d', 'Lyrics come from lrclib.net, a free community database. When this is on, the song title, artist and length are sent there \u2014 nothing else. It is off until you turn it on.'));
      const b = el('button', 'pill-btn primary small', 'Turn on lyrics'); b.type = 'button'; b.addEventListener('click', function () { setOn(true); }); box.appendChild(b); paneLy.appendChild(box); return;
    }
    const head = el('div', 'mx-ly-head'); head.appendChild(el('span', 'mx-ly-src', 'Lyrics \u00b7 lrclib.net'));
    const off = el('button', 'link-btn', 'Turn off'); off.type = 'button'; off.addEventListener('click', function () { setOn(false); }); head.appendChild(off); paneLy.appendChild(head);
    const msg = function (t, retry) { const m = el('div', 'mx-empty', t); if (retry) { const r = el('button', 'link-btn', 'Try again'); r.type = 'button'; r.addEventListener('click', loadLyrics); m.appendChild(doc.createElement('br')); m.appendChild(r); } paneLy.appendChild(m); };
    if (!now.active) { msg('Play a song to see its lyrics.'); return; }
    if (ly.state === 'loading') { const sk = el('div', 'mx-ly-skel'); for (let i = 0; i < 5; i++) { const l = el('span', 'skel skel-line'); l.style.width = (50 + (i * 37) % 45) + '%'; sk.appendChild(l); } paneLy.appendChild(sk); return; }
    if (ly.state === 'none') { msg('No lyrics found for this song.'); return; }
    if (ly.state === 'instrumental') { msg('Instrumental \u2014 no lyrics.'); return; }
    if (ly.state === 'error') { msg('Couldn\u2019t reach the lyrics service. Check your connection.', true); return; }
    if (ly.state === 'idle') return;
    const sc = el('div', 'mx-ly-scroll' + (ly.synced ? ' synced' : '')); scroller = sc;
    ly.lines.forEach(function (ln, i) {
      const d = el('div', 'ly-line' + (ln.text ? '' : ' gap'), ln.text || '\u266a'); d.dataset.i = i;
      if (ly.synced) { d.setAttribute('role', 'button'); d.tabIndex = 0; d.title = 'Jump to this line'; }
      sc.appendChild(d);
    });
    if (!ly.synced) sc.appendChild(el('div', 'ly-note', 'Not time-synced for this song.'));
    ['wheel', 'touchstart', 'pointerdown'].forEach(function (t) { sc.addEventListener(t, function () { userUntil = Date.now() + 3500; }, { passive: true }); });
    const seekTo = function (d) { const ln = ly.lines[+d.dataset.i]; if (!ln) return; now.pos = ln.t; now.ts = Date.now() / 1000; SARA.callApi('seek_media', ln.t); userUntil = 0; tick(true); };
    sc.addEventListener('click', function (e) { const d = e.target.closest && e.target.closest('.ly-line'); if (d && ly.synced) seekTo(d); });
    sc.addEventListener('keydown', function (e) { const d = e.target.closest && e.target.closest('.ly-line'); if (d && ly.synced && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); seekTo(d); } });
    paneLy.appendChild(sc); tick(true);
  }
  function tick(force) {
    if (!scroller || !ly.synced || !card.classList.contains('expanded') || tab !== 'lyrics' || doc.hidden) return;
    const pos = posNow() + 0.15; let lo = 0, hi = ly.lines.length - 1, idx = -1;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (ly.lines[mid].t <= pos) { idx = mid; lo = mid + 1; } else hi = mid - 1; }
    if (idx === curIdx && !force) return; curIdx = idx;
    const kids = scroller.children;
    for (let i = 0; i < ly.lines.length; i++) { const k = kids[i]; if (!k) break; k.classList.toggle('active', i === idx); k.classList.toggle('past', i < idx); }
    if (idx >= 0 && Date.now() > userUntil) {
      const k = kids[idx], top = k.offsetTop - scroller.clientHeight / 2 + k.offsetHeight / 2;
      if (scroller.scrollTo) scroller.scrollTo({ top: Math.max(0, top), behavior: (SARA.reduceMotion || force) ? 'auto' : 'smooth' });
    }
  }
  setInterval(tick, 160);
  new MutationObserver(function () { if (card.classList.contains('expanded')) { if (tab === 'lyrics') tick(true); } }).observe(card, { attributes: true, attributeFilter: ['class'] });
  setTab(tab);
})();
