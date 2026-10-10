/* ==========================================================================
   quality.js -- Settings > Appearance > "Visual quality" (Low / Balanced / High) + hidden developer tools.
   * SARA.quality.get() / set(tier) / allows(feature)   -- js/premium.js asks allows('aura'|'dust'|'tilt'|'magnetic'|'spark'|'decrypt'|'reveal')
   * <html data-quality="low|balanced|high"> drives style/quality.css (blur, ambient layers, decorative loops)
   * Developer mode: click the "Visual quality" label 5 times -> reveals an FPS-meter switch and per-pack effect switches
     (turn a whole effects stylesheet off live and watch the FPS number: that is how we find what is heavy). Ctrl+Shift+F toggles the meter.
   Stored in localStorage only (it is a per-device choice) -> no backend change. Load BEFORE js/premium.js.
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
    high:     { aura: 1, dust: 1, tilt: 1, magnetic: 1, spark: 1, decrypt: 1, reveal: 1 },
    balanced: { aura: 1, dust: 0, tilt: 0, magnetic: 1, spark: 1, decrypt: 1, reveal: 1 },
    low:      { aura: 0, dust: 0, tilt: 0, magnetic: 0, spark: 0, decrypt: 0, reveal: 0 }
  };
  const NOTES = {
    low: 'Fastest. No blur, aura, dust or tilt, and decorative loops are off. Best for older or busy PCs.',
    balanced: 'Recommended. Smooth, with light effects (aura, magnetic buttons, sparks).',
    high: 'Everything on: stronger blur, floating dust, 3D tilt. Needs a decent GPU.'
  };
  function detect() {
    const c = navigator.hardwareConcurrency || 4, m = navigator.deviceMemory || 4;
    return (c <= 2 || m <= 2) ? 'low' : 'balanced';
  }
  let q = ls.get(K_Q); if (TIERS.indexOf(q) < 0) q = detect();
  root.dataset.quality = q;

  const $ = function (id) { return doc.getElementById(id); };
  const qRow = $('qualityRow'), qNote = $('qualityNote');
  function paint() {
    if (qRow) qRow.querySelectorAll('.chip').forEach(function (c) {
      const on = c.dataset.q === q; c.classList.toggle('on', on); c.setAttribute('aria-checked', on ? 'true' : 'false');
    });
    if (qNote) qNote.textContent = NOTES[q];
    const h = $('fpsHudTier'); if (h) h.textContent = q.toUpperCase();
  }
  SARA.quality = {
    tiers: TIERS.slice(),
    get: function () { return q; },
    allows: function (f) { return !!(FEATURES[q] && FEATURES[q][f]); },
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
