/* ==========================================================================
   quality.js -- Settings > Appearance > "Visual quality" (Low / Balanced / High) + hidden developer tools.
   * SARA.quality.get() / set(tier) / allows(feature)   -- js/fx/motion-pack.js asks allows('aura'|'dust'|'tilt'|'magnetic'|'spark'|'decrypt'|'reveal')
   * <html data-quality="low|balanced|high"> drives style/quality.css (blur, ambient layers, decorative loops)
   * Developer mode: click the "Visual quality" label 5 times -> reveals an FPS-meter switch and per-pack effect switches
     (turn a whole effects stylesheet off live and watch the FPS number: that is how we find what is heavy). Ctrl+Shift+F toggles the meter.
   Stored in localStorage only (it is a per-device choice) -> no backend change. Loads before js/fx/motion-pack.js (order in assets.json).
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA;
  if (!SARA) return;
  const root = document.documentElement, doc = document;
  const K_Q = 'sara_quality', K_DEV = 'sara_dev', K_FPS = 'sara_fps', K_PACKS = 'sara_packs_off';
  const mem = {};   // fallback when localStorage is blocked (private mode / locked-down webview): settings still work for this session
  const ls = {
    get: function (k) { try { const v = localStorage.getItem(k); if (v !== null) return v; } catch (e) { /* blocked */ } return (k in mem) ? mem[k] : null; },
    set: function (k, v) { mem[k] = v; try { localStorage.setItem(k, v); } catch (e) { /* blocked */ } }
  };
  const TIERS = ['low', 'balanced', 'high'];
  const FEATURES = {
    high:     { aura: 1, dust: 1, tilt: 1, magnetic: 1, spark: 1, decrypt: 1, reveal: 1, numberroll: 1, progress: 1, edgepulse: 1, idle: 1, p6: 1, hoverpreview: 1, hints: 1, radial: 1 },
    balanced: { aura: 1, dust: 0, tilt: 0, magnetic: 0, spark: 0, decrypt: 1, reveal: 1, numberroll: 1, progress: 1, edgepulse: 1, idle: 1, p6: 1, hoverpreview: 1, hints: 1, radial: 1 },
    low:      { aura: 0, dust: 0, tilt: 0, magnetic: 0, spark: 0, decrypt: 0, reveal: 0, numberroll: 1, progress: 1, edgepulse: 0, idle: 0, p6: 0, hoverpreview: 0, hints: 0, radial: 1 }
  };
  const NOTES = {
    low: 'Fastest. No blur, aura, dust or tilt, and decorative loops are off. Best for older or busy PCs.',
    balanced: 'Recommended. Smooth, with light effects (cursor aura). Magnetic buttons and click sparks are High only.',
    high: 'Everything on: floating dust, 3D tilt, magnetic buttons, click sparks. Needs a decent GPU.'
  };
  /* ---- fine-tune registry: every effect has its own on/off (js = checked by js/motion-pack.js, css = <html data-fx-off="..."> gates style/motion-pack.css) ---- */
  const FX = [
    ['aura', 'Cursor aura', 'soft light that follows the pointer', 'js'], ['magnetic', 'Magnetic buttons', 'buttons lean toward the cursor', 'js'], ['tilt', '3D tilt cards', 'tiles tilt under the cursor', 'js'],
    ['spark', 'Click sparks', 'particles on primary clicks', 'js'], ['dust', 'Floating dust', 'faint drifting particles', 'js'], ['decrypt', 'Title decrypt', 'page titles resolve from glyphs', 'js'],
    ['reveal', 'Theme circle reveal', 'a theme change expands from the click', 'js'], ['numberroll', 'Number roll', 'stats count up to new values', 'js'], ['progress', 'Scroll progress line', 'hairline at the top of long pages', 'js'],
    ['edgepulse', 'Toast edge pulse', 'screen edge glows on a notification', 'js'], ['idle', 'Idle calm', 'dims the chrome after 25 s without input', 'js'],
    ['ax', 'Entrance & hover animations', 'page entrances, press and hover motion', 'css'], ['pm', 'Premium upgrades', 'smoother page, message and toast timing', 'css'],
    ['lx', 'Hover polish', 'icon springs, gloss and beams on hover', 'css'], ['au', 'Detail effects', 'small interaction details across the app', 'css'], ['p6', 'Motion polish', 'list stagger, card hover lift, orb reveal after start-up', 'css'],
    ['hoverpreview', 'Theme hover preview', 'pointing at a theme card shows it live', 'js'], ['hints', 'Rotating hints', 'the Home hint line suggests things to say when idle', 'js'], ['radial', 'Orb quick actions', 'press and hold the orb for a ring of actions', 'js']
  ];
  const off = new Set();
  try { const a = JSON.parse(ls.get('sara_fx_off') || '[]'); if (Array.isArray(a)) a.forEach(function (x) { if (FX.some(function (f) { return f[0] === x; })) off.add(x); }); } catch (e) { /* none */ }
  function applyFx() { const css = FX.filter(function (f) { return f[3] === 'css' && off.has(f[0]); }).map(function (f) { return f[0]; }); if (css.length) root.dataset.fxOff = css.join(' '); else delete root.dataset.fxOff; }
  function detect() {
    const c = navigator.hardwareConcurrency || 4, m = navigator.deviceMemory || 4;
    return (c <= 2 || m <= 2) ? 'low' : 'balanced';
  }
  let q = ls.get(K_Q); if (TIERS.indexOf(q) < 0) q = detect();
  root.dataset.quality = q; applyFx();

  const $ = function (id) { return doc.getElementById(id); };
  const qRow = $('qualityRow'), qNote = $('qualityNote');
  function paint() {
    if (qRow) qRow.querySelectorAll('.chip').forEach(function (c) {
      const on = c.dataset.q === q; c.classList.toggle('on', on); c.setAttribute('aria-checked', on ? 'true' : 'false');
    });
    if (qNote) qNote.textContent = NOTES[q];
  }
  SARA.quality = {
    tiers: TIERS.slice(),
    get: function () { return q; },
    allows: function (f) { return !!(FEATURES[q] && FEATURES[q][f]) && !off.has(f); },
    fx: {
      names: FX.map(function (f) { return f[0]; }),
      off: function () { return Array.from(off); },
      isOn: function (n) { return !off.has(n); },
      set: function (n, on) { if (!FX.some(function (f) { return f[0] === n; })) return; if (on) off.delete(n); else off.add(n); ls.set('sara_fx_off', JSON.stringify(Array.from(off))); applyFx(); SARA.emit('fx', n, !!on); },
      setOff: function (arr) { off.clear(); (arr || []).forEach(function (x) { if (FX.some(function (f) { return f[0] === x; })) off.add(x); }); ls.set('sara_fx_off', JSON.stringify(Array.from(off))); applyFx(); SARA.emit('fx'); paintFx(); }
    },
    set: function (v, persist) {
      if (TIERS.indexOf(v) < 0 || v === q) return;
      q = v; if (persist !== false) ls.set(K_Q, q);
      root.dataset.quality = q; paint(); SARA.emit('quality', q);
    }
  };
  if (qRow) qRow.addEventListener('click', function (e) {
    const c = e.target.closest && e.target.closest('.chip'); if (!c) return;
    if (SARA.sound && SARA.sound.tap) SARA.sound.tap();
    SARA.quality.set(c.dataset.q);
  });
  paint();

  /* ---------------- Settings > Appearance > Fine-tune effects ---------------- */
  const fxRows = {};
  function paintFx() { Object.keys(fxRows).forEach(function (n) { SARA.setToggle(fxRows[n], !off.has(n)); }); }
  if (qNote && qNote.parentNode) {
    const det = doc.createElement('details'); det.className = 'fx-panel';
    const sum = doc.createElement('summary'); sum.textContent = 'Fine-tune effects'; det.appendChild(sum);
    const hint = doc.createElement('div'); hint.className = 'acc-note'; hint.textContent = 'Switch single effects on or off. They also follow the quality tier above.'; det.appendChild(hint);
    ['js', 'css'].forEach(function (kind) {
      const lab = doc.createElement('div'); lab.className = 'section-label'; lab.textContent = kind === 'js' ? 'Pointer & page effects' : 'Animation groups'; det.appendChild(lab);
      FX.filter(function (f) { return f[3] === kind; }).forEach(function (f) {
        const r = doc.createElement('div'); r.className = 'set-row'; const info = doc.createElement('div'); info.className = 'set-info';
        const nm = doc.createElement('div'); nm.className = 's-name'; nm.textContent = f[1]; const sb = doc.createElement('div'); sb.className = 's-sub'; sb.textContent = f[2]; info.appendChild(nm); info.appendChild(sb);
        const t = doc.createElement('div'); t.className = 'toggle'; t.setAttribute('role', 'switch'); t.tabIndex = 0; t.setAttribute('aria-label', f[1]); r.appendChild(info); r.appendChild(t); det.appendChild(r);
        fxRows[f[0]] = t; SARA.bindToggle(t, function (on) { SARA.quality.fx.set(f[0], on); });
      });
    });
    const foot = doc.createElement('div'); foot.className = 'btn-row'; const rb = doc.createElement('button'); rb.type = 'button'; rb.className = 'pill-btn'; rb.textContent = 'Turn everything back on';
    rb.addEventListener('click', function () { SARA.quality.fx.setOff([]); }); foot.appendChild(rb); det.appendChild(foot);
    qNote.parentNode.insertBefore(det, $('devBlock') || qNote.nextSibling); paintFx();
  }

  /* ---------------- FPS meter ---------------- */
  let hud = null, hudOn = false, raf = 0, lt = 0, ltObs = null;
  const stats = { jank: 0, lt: 0 };
  function hudLoop() {
    let last = performance.now(), t0 = last, frames = 0, acc = 0, worst = 0;
    function tick(now) {
      if (!hudOn) return;
      const dt = now - last; last = now; frames++; acc += dt; if (dt > worst) worst = dt; if (dt > 33.4) stats.jank++;
      if (now - t0 >= 500) {
        const fps = frames * 1000 / (now - t0), avg = acc / frames;
        hud.className = 'fps-hud' + (fps < 40 ? ' bad' : fps < 55 ? ' warn' : '');
        hud.firstChild.textContent = Math.round(fps) + ' FPS  ·  ' + avg.toFixed(1) + ' ms';
        hud.lastChild.textContent = 'worst ' + Math.round(worst) + 'ms  ·  jank ' + stats.jank + '  ·  long ' + stats.lt + '  ·  ' + q.toUpperCase();
        frames = 0; acc = 0; worst = 0; t0 = now;
      }
      raf = requestAnimationFrame(tick);
    }
    raf = requestAnimationFrame(tick);
  }
  function setHud(on, persist) {
    on = !!on; if (on === hudOn) return; hudOn = on;
    if (persist !== false) ls.set(K_FPS, on ? '1' : '0');
    if (on) {
      hud = doc.createElement('div'); hud.className = 'fps-hud'; hud.setAttribute('aria-hidden', 'true'); hud.title = 'Click to reset counters';
      hud.appendChild(doc.createElement('div')); hud.appendChild(doc.createElement('div'));
      hud.addEventListener('click', function () { stats.jank = 0; stats.lt = 0; });
      doc.body.appendChild(hud); hudLoop();
      if (window.PerformanceObserver) { try { ltObs = new PerformanceObserver(function (l) { stats.lt += l.getEntries().length; }); ltObs.observe({ entryTypes: ['longtask'] }); } catch (e) { ltObs = null; } }
    } else {
      cancelAnimationFrame(raf); if (hud) { hud.remove(); hud = null; } if (ltObs) { ltObs.disconnect(); ltObs = null; }
    }
    const t = $('devFpsToggle'); if (t) SARA.setToggle(t, on);
  }

  /* ---------------- developer mode (hidden) ---------------- */
  const PACKS = [['animations', 'Entrance & hover animations'], ['premium', 'Premium upgrades'], ['luxe', 'Luxe pack'], ['aurum', 'Aurum pack'], ['theme-neon', 'Neon theme effects'], ['theme-minimal', 'Minimal themes effects']];
  function packLink(name) {
    return Array.prototype.find.call(doc.querySelectorAll('link[rel="stylesheet"]'), function (l) { return (l.getAttribute('href') || '').indexOf('/' + name + '.css') >= 0; });
  }
  function offList() { try { return JSON.parse(ls.get(K_PACKS) || '[]'); } catch (e) { return []; } }
  function applyPacks() { offList().forEach(function (n) { const l = packLink(n); if (l) l.disabled = true; }); }
  applyPacks();
  const devBlock = $('devBlock');
  function renderDev() {
    if (!devBlock) return;
    const dev = ls.get(K_DEV) === '1'; devBlock.hidden = !dev; if (!dev) { devBlock.textContent = ''; return; }
    const off = offList();
    let h = '<div class="section-label">Developer</div>' +
      '<div class="set-row"><div class="set-info"><div class="s-name">FPS meter</div><div class="s-sub">live FPS, frame time, jank and long tasks (Ctrl+Shift+F)</div></div><div class="toggle' + (hudOn ? ' on' : '') + '" id="devFpsToggle" role="switch" tabindex="0" aria-checked="' + hudOn + '"></div></div>';
    PACKS.forEach(function (p) {
      if (!packLink(p[0])) return;
      const on = off.indexOf(p[0]) < 0;
      h += '<div class="set-row"><div class="set-info"><div class="s-name">' + p[1] + '</div><div class="s-sub">turn the whole stylesheet off to see what it costs</div></div><div class="toggle' + (on ? ' on' : '') + '" data-pack="' + p[0] + '" role="switch" tabindex="0" aria-checked="' + on + '"></div></div>';
    });
    h += '<div class="btn-row"><button type="button" class="pill-btn" id="devExit">Leave developer mode</button></div>';
    devBlock.innerHTML = h;
    SARA.bindToggle($('devFpsToggle'), function (on) { setHud(on); });
    devBlock.querySelectorAll('[data-pack]').forEach(function (t) {
      SARA.bindToggle(t, function (on) {
        const n = t.dataset.pack, l = packLink(n), list = offList().filter(function (x) { return x !== n; });
        if (!on) list.push(n); ls.set(K_PACKS, JSON.stringify(list)); if (l) l.disabled = !on;
      });
    });
    $('devExit').addEventListener('click', function () { ls.set(K_DEV, '0'); setHud(false); renderDev(); });
  }
  const label = $('qualityLabel');
  if (label) {
    let n = 0, tm = 0;
    label.addEventListener('click', function () {
      n++; clearTimeout(tm); tm = setTimeout(function () { n = 0; }, 2500);
      if (n >= 5) {
        n = 0; const on = ls.get(K_DEV) !== '1'; ls.set(K_DEV, on ? '1' : '0'); if (!on) setHud(false);
        renderDev(); if (SARA.toast) SARA.toast('ti-check', 'var(--core)', on ? 'Developer mode on' : 'Developer mode off');
      }
    });
  }
  doc.addEventListener('keydown', function (e) {
    if (e.ctrlKey && e.shiftKey && (e.key === 'F' || e.key === 'f') && ls.get(K_DEV) === '1') { e.preventDefault(); setHud(!hudOn); }
  });
  renderDev();
  if (ls.get(K_DEV) === '1' && ls.get(K_FPS) === '1') setHud(true, false);
})();
