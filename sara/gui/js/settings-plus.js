/* ==========================================================================
   settings-plus.js -- Settings upgrades (frontend only; uses the existing setters, no new backend calls).
   1 Custom theme   Appearance > "Custom theme": pick an accent + a background colour. Everything else (panels, lines, text, secondary accents,
                    light/dark mode, readable contrast) is derived. Needs the "custom" entry in theme.js LIST + style/theme-custom.css.
   2 Presets        Settings > "Presets & backup": Performance / Balanced / Showcase (visual quality + home effects) and Focus
                    (focus mode on, notifications + proactive nudges off). Focus remembers your previous values and can restore them.
   3 Backup         Export settings -> a JSON you can copy or download; Import settings -> paste or choose the file, review the changes, apply.
                    Only known settings are read, values are validated, and nothing is changed until you press Apply.
   Styles: style/settings-plus.css.  Load AFTER js/settings.js, js/quality.js and js/home-plus.js.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA; if (!SARA) return;
  const doc = document, $ = SARA.$, root = doc.documentElement;
  const el = function (tag, cls, text) { const n = doc.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
  const clamp = function (v, a, b) { return Math.min(b, Math.max(a, v)); };
  const mem = {};
  const store = {
    get: function (k) { try { const v = localStorage.getItem(k); if (v !== null) return v; } catch (e) { /* blocked */ } return (k in mem) ? mem[k] : null; },
    set: function (k, v) { mem[k] = v; try { localStorage.setItem(k, v); } catch (e) { /* blocked */ } }
  };
  const readJson = function (k, fb) { try { const v = JSON.parse(store.get(k) || 'null'); return v == null ? fb : v; } catch (e) { return fb; } };

  /* ====================================================================== color math */
  const hex2rgb = function (h) { h = String(h).replace('#', ''); if (h.length === 3) h = h.split('').map(function (c) { return c + c; }).join(''); const n = parseInt(h, 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; };
  const rgb2hex = function (c) { return '#' + c.map(function (v) { return Math.round(clamp(v, 0, 255)).toString(16).padStart(2, '0'); }).join(''); };
  function rgb2hsl(c) {
    const r = c[0] / 255, g = c[1] / 255, b = c[2] / 255, mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2; let h = 0, s = 0;
    if (mx !== mn) { const d = mx - mn; s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn); h = mx === r ? (g - b) / d + (g < b ? 6 : 0) : mx === g ? (b - r) / d + 2 : (r - g) / d + 4; h *= 60; }
    return [h, s, l];
  }
  function hsl2rgb(h, s, l) {
    h = ((h % 360) + 360) % 360; const a = s * Math.min(l, 1 - l), f = function (n) { const k = (n + h / 30) % 12; return l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1)); };
    return [f(0) * 255, f(8) * 255, f(4) * 255];
  }
  const lum = function (c) { const f = function (v) { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const contrast = function (a, b) { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const trip = function (c) { return Math.round(c[0]) + ' ' + Math.round(c[1]) + ' ' + Math.round(c[2]); };

  /* ====================================================================== custom theme */
  function computeTheme(accentHex, bgHex) {
    const bg = hex2rgb(bgHex), ac = hex2rgb(accentHex), hs = rgb2hsl(bg), bh = hs[0], bs = Math.min(hs[1], 0.6), dark = lum(bg) < 0.35;
    const H = function (h, s, l) { return hsl2rgb(h, clamp(s, 0, 1), clamp(l, 0, 1)); };
    let deep, vd, panel, panel2, line, fg, text, soft, muted, faint;
    if (dark) {
      const L = clamp(hs[2], 0.02, 0.3);
      deep = H(bh, bs, L); vd = H(bh, bs, L * 0.55); panel = H(bh, bs, L + 0.035); panel2 = H(bh, bs, L + 0.07); line = H(bh, bs * 0.9, L + 0.13);
      fg = [255, 255, 255]; text = H(bh, 0.25, 0.94); soft = H(bh, 0.15, 0.74); muted = H(bh, 0.1, 0.57); faint = H(bh, 0.1, 0.34);
    } else {
      const L = clamp(hs[2], 0.78, 0.97);
      vd = H(bh, bs, L - 0.03); deep = H(bh, bs, L + 0.01); panel = H(bh, bs, Math.min(0.995, L + 0.04)); panel2 = H(bh, bs, L - 0.02); line = H(bh, bs, L - 0.13);
      fg = H(bh, 0.3, 0.12); text = H(bh, 0.25, 0.12); soft = H(bh, 0.15, 0.3); muted = H(bh, 0.1, 0.42); faint = H(bh, 0.1, 0.65);
    }
    let ah = rgb2hsl(ac), adjusted = false, acc = ac, guard = 0;
    while (contrast(acc, panel) < 3.2 && guard++ < 14) { adjusted = true; ah = [ah[0], ah[1], clamp(ah[2] + (dark ? 0.04 : -0.04), 0.05, 0.95)]; acc = hsl2rgb(ah[0], ah[1], ah[2]); }
    const think = hsl2rgb(ah[0] - 28, ah[1], ah[2]), speak = hsl2rgb(ah[0] + 38, ah[1], clamp(ah[2] + 0.05, 0, 0.95));
    const onCore = contrast(acc, [10, 10, 10]) >= contrast(acc, [255, 255, 255]) ? '#0a0a0a' : '#ffffff';
    const danger = dark ? [255, 90, 106] : [198, 47, 59];
    const vars = {
      '--cx-void': rgb2hex(vd), '--cx-deep': rgb2hex(deep), '--cx-panel': rgb2hex(panel), '--cx-panel-rgb': trip(panel), '--cx-panel-2': rgb2hex(panel2), '--cx-line': rgb2hex(line),
      '--cx-core-rgb': trip(acc), '--cx-think-rgb': trip(think), '--cx-speak-rgb': trip(speak),
      '--cx-text': rgb2hex(text), '--cx-text-soft': rgb2hex(soft), '--cx-muted': rgb2hex(muted), '--cx-faint': rgb2hex(faint),
      '--cx-danger': rgb2hex(danger), '--cx-danger-rgb': trip(danger), '--cx-on-core': onCore, '--cx-fg-rgb': trip(fg), '--cx-void-rgb': trip(vd)
    };
    return { vars: vars, mode: dark ? 'dark' : 'light', adjusted: adjusted, accent: rgb2hex(acc), contrast: Math.round(contrast(acc, panel) * 10) / 10 };
  }
  const CK = 'sara_custom_theme', DEF_CT = { accent: '#00b7ff', bg: '#07090d' };
  let ct = Object.assign({}, DEF_CT, readJson(CK, {}));
  const okHex = function (h) { return typeof h === 'string' && /^#[0-9a-f]{6}$/i.test(h); };
  if (!okHex(ct.accent)) ct.accent = DEF_CT.accent; if (!okHex(ct.bg)) ct.bg = DEF_CT.bg;
  let ctInfo = null;
  function applyCustom(save) {
    ctInfo = computeTheme(ct.accent, ct.bg);
    Object.keys(ctInfo.vars).forEach(function (k) { root.style.setProperty(k, ctInfo.vars[k]); });
    root.dataset.cxMode = ctInfo.mode;
    if (save) store.set(CK, JSON.stringify(ct));
    if (SARA.theme && SARA.theme.current() === 'custom') SARA.emit('theme');   // canvases re-read their colours
  }
  applyCustom(false);

  const labels = Array.prototype.slice.call(doc.querySelectorAll('#page-settings .section-label'));
  const hueLabel = labels.find(function (l) { return l.textContent.trim() === 'Accent hue'; });
  if (hueLabel) {
    const lab = el('div', 'section-label', 'Custom theme');
    const box = el('div', 'cx-box');
    const mk = function (name, key) {
      const w = el('label', 'cx-field'); w.appendChild(el('span', 'cx-lbl', name));
      const i = el('input'); i.type = 'color'; i.value = ct[key]; i.setAttribute('aria-label', name + ' colour'); w.appendChild(i);
      const v = el('span', 'cx-hex', ct[key]); w.appendChild(v);
      let raf = 0;
      i.addEventListener('input', function () { ct[key] = i.value.toLowerCase(); v.textContent = ct[key]; cancelAnimationFrame(raf); raf = requestAnimationFrame(function () { applyCustom(true); paintBox(); }); });
      w.setValue = function (x) { i.value = x; v.textContent = x; };
      return w;
    };
    const fA = mk('Accent', 'accent'), fB = mk('Background', 'bg');
    const sw = el('div', 'cx-sw'); const swBg = el('i'), swPanel = el('i'), swAcc = el('i'), swTxt = el('i'); [swBg, swPanel, swAcc, swTxt].forEach(function (n) { sw.appendChild(n); });
    const note = el('div', 'cx-note'), row = el('div', 'btn-row');
    const use = el('button', 'pill-btn primary', 'Use custom theme'); use.type = 'button';
    const reset = el('button', 'pill-btn', 'Reset'); reset.type = 'button';
    row.appendChild(use); row.appendChild(reset);
    [fA, fB, sw, note, row].forEach(function (n) { box.appendChild(n); });
    hueLabel.parentNode.insertBefore(lab, hueLabel); hueLabel.parentNode.insertBefore(box, hueLabel);
    function paintBox() {
      const v = ctInfo.vars; swBg.style.background = v['--cx-void']; swPanel.style.background = v['--cx-panel']; swAcc.style.background = 'rgb(' + v['--cx-core-rgb'] + ')'; swTxt.style.background = v['--cx-text'];
      note.textContent = ctInfo.adjusted ? 'Accent lightened a little so it stays readable on this background (contrast ' + ctInfo.contrast + ':1).'
        : (ctInfo.mode === 'light' ? 'Light theme \u00b7 contrast ' : 'Dark theme \u00b7 contrast ') + ctInfo.contrast + ':1';
    }
    paintBox();
    use.addEventListener('click', function () { if (SARA.sound && SARA.sound.tap) SARA.sound.tap(); SARA.theme.apply('custom'); SARA.ok('Custom theme applied'); });
    reset.addEventListener('click', function () { ct = Object.assign({}, DEF_CT); fA.setValue(ct.accent); fB.setValue(ct.bg); applyCustom(true); paintBox(); });
    SARA.on('pref', function (k) { if (k === 'theme') use.classList.toggle('on', SARA.theme.current() === 'custom'); });
    ct._ui = { fA: fA, fB: fB, paint: paintBox };
  }
  function setCustom(c) {
    if (!c || !okHex(c.accent) || !okHex(c.bg)) return false; ct.accent = c.accent.toLowerCase(); ct.bg = c.bg.toLowerCase();
    if (ct._ui) { ct._ui.fA.setValue(ct.accent); ct._ui.fB.setValue(ct.bg); } applyCustom(true); if (ct._ui) ct._ui.paint(); return true;
  }

  /* ====================================================================== reading the current settings */
  const toggleEls = function () { return Array.prototype.slice.call(doc.querySelectorAll('[data-setting]')); };
  const isOn = function (n) { return !!n && n.classList.contains('on'); };
  const rowLabel = function (n) { const r = n.closest('.set-row, .info-row, .qc-tile'); const s = r && r.querySelector('.s-name, .qc-name'); return (s && s.textContent.trim()) || n.dataset.setting; };
  function setSetting(key, on) {
    const n = doc.querySelector('[data-setting="' + key + '"]'); if (!n || isOn(n) === !!on) return false;
    SARA.setToggle(n, !!on); SARA.callApi('update_setting', key, !!on); return true;
  }
  function collect() {
    const toggles = {}; toggleEls().forEach(function (n) { toggles[n.dataset.setting] = isOn(n); }); toggles.sound_effects = isOn($('setSounds'));
    const chip = doc.querySelector('#langChips .chip.on');
    return {
      app: 'sara-settings', version: 1, exported_at: new Date().toISOString(),
      ui: {
        theme: SARA.theme.current(), accent_hue: SARA.theme.hue() === null || SARA.theme.hue() === undefined ? null : Math.round(SARA.theme.hue()),
        clock_theme: SARA.prefs.get('clock_theme', ''), quality: SARA.quality ? SARA.quality.get() : 'balanced',
        home: SARA.homeLayout ? SARA.homeLayout.get() : null, custom_theme: { accent: ct.accent, bg: ct.bg }
      },
      assistant: {
        display_name: ($('setName') || {}).value || '', muted: isOn($('setMute')), focus: isOn($('setFocus')), language: chip ? chip.dataset.lang : null,
        mic_sensitivity: $('micSlider') ? parseInt($('micSlider').value, 10) : null, speech_speed: $('speedSlider') ? parseInt($('speedSlider').value, 10) : null, toggles: toggles
      }
    };
  }

  /* ====================================================================== dialogs */
  function dialog(title, bodyNode, actions) {
    const back = el('div', 'sx-back'), box = el('div', 'sx-dialog'); box.setAttribute('role', 'dialog'); box.setAttribute('aria-modal', 'true'); box.setAttribute('aria-label', title);
    const head = el('div', 'sx-dhead'); head.appendChild(el('div', 'sx-dtitle', title)); const x = el('button', 'sx-x', '\u00d7'); x.type = 'button'; x.setAttribute('aria-label', 'Close'); head.appendChild(x);
    const foot = el('div', 'sx-dfoot'); (actions || []).forEach(function (a) { foot.appendChild(a); });
    box.appendChild(head); box.appendChild(bodyNode); box.appendChild(foot); back.appendChild(box); doc.body.appendChild(back);
    const prev = doc.activeElement; requestAnimationFrame(function () { back.classList.add('open'); });
    const close = function () { back.classList.remove('open'); doc.removeEventListener('keydown', onKey, true); setTimeout(function () { back.remove(); }, 260); if (prev && prev.focus) prev.focus({ preventScroll: true }); };
    const onKey = function (e) { if (e.key === 'Escape') { e.stopPropagation(); close(); } };
    doc.addEventListener('keydown', onKey, true);
    x.addEventListener('click', close); back.addEventListener('pointerdown', function (e) { if (e.target === back) close(); });
    return { close: close, box: box };
  }
  const pbtn = function (txt, cls) { const b = el('button', 'pill-btn' + (cls ? ' ' + cls : ''), txt); b.type = 'button'; return b; };
  function copyText(t) {
    if (navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(t).then(function () { return true; }, function () { return legacy(t); });
    return Promise.resolve(legacy(t));
  }
  function legacy(t) { const a = el('textarea'); a.value = t; a.style.cssText = 'position:fixed;left:-9999px;opacity:0'; doc.body.appendChild(a); a.select(); let ok = false; try { ok = doc.execCommand('copy'); } catch (e) { ok = false; } a.remove(); return ok; }

  function exportDialog() {
    const json = JSON.stringify(collect(), null, 2), body = el('div', 'sx-dbody');
    body.appendChild(el('p', 'sx-p', 'Your settings as a JSON file. It holds only preferences (no notes, reminders or history). Keep it, or paste it into Import on another device.'));
    const ta = el('textarea', 'sx-ta'); ta.readOnly = true; ta.value = json; ta.setAttribute('aria-label', 'Exported settings'); ta.rows = 12; body.appendChild(ta);
    const copy = pbtn('Copy', 'primary'), dl = pbtn('Download .json');
    const d = dialog('Export settings', body, [dl, copy]);
    copy.addEventListener('click', function () { copyText(json).then(function (ok) { if (ok) SARA.ok('Settings copied'); else { ta.focus(); ta.select(); SARA.fail('Press Ctrl+C to copy'); } }); });
    dl.addEventListener('click', function () {
      try { const u = URL.createObjectURL(new Blob([json], { type: 'application/json' })), a = el('a'); a.href = u; a.download = 'sara-settings.json'; doc.body.appendChild(a); a.click(); a.remove(); setTimeout(function () { URL.revokeObjectURL(u); }, 2000); SARA.ok('If nothing was saved, use Copy instead'); }
      catch (e) { SARA.fail('Download is not available here \u2014 use Copy.'); }
    });
    ta.focus(); ta.select(); return d;
  }

  /* ---- import: validate -> list the differences -> apply ---- */
  function plan(data) {
    const cur = collect(), changes = [], skipped = [];
    const add = function (label, from, to, run) { changes.push({ label: label, from: from, to: to, run: run }); };
    if (!data || data.app !== 'sara-settings' || typeof data.version !== 'number') return { error: 'This is not a SARA settings file.' };
    const ui = data.ui || {}, as = data.assistant || {};
    if (ui.theme !== undefined) {
      if (typeof ui.theme === 'string' && SARA.theme.list.some(function (t) { return t.id === ui.theme; })) { if (ui.theme !== cur.ui.theme) add('Theme', cur.ui.theme, ui.theme, function () { SARA.theme.apply(ui.theme); }); }
      else skipped.push('theme (unknown)');
    }
    if (ui.custom_theme && okHex(ui.custom_theme.accent) && okHex(ui.custom_theme.bg)) {
      if (ui.custom_theme.accent.toLowerCase() !== ct.accent || ui.custom_theme.bg.toLowerCase() !== ct.bg) add('Custom theme colours', ct.accent + ' / ' + ct.bg, ui.custom_theme.accent + ' / ' + ui.custom_theme.bg, function () { setCustom(ui.custom_theme); });
    }
    if (ui.accent_hue !== undefined) {
      const h = ui.accent_hue === null ? null : Number(ui.accent_hue);
      if (h === null || (isFinite(h) && h >= 0 && h <= 360)) { if (h !== cur.ui.accent_hue) add('Accent hue', cur.ui.accent_hue === null ? 'theme default' : cur.ui.accent_hue, h === null ? 'theme default' : h, function () { SARA.theme.setHue(h); }); }
      else skipped.push('accent hue (out of range)');
    }
    if (typeof ui.clock_theme === 'string' && ui.clock_theme.length < 40 && ui.clock_theme !== cur.ui.clock_theme) add('Clock style', cur.ui.clock_theme || 'default', ui.clock_theme, function () { SARA.prefs.set('clock_theme', ui.clock_theme); });
    if (ui.quality !== undefined) {
      if (SARA.quality && SARA.quality.tiers.indexOf(ui.quality) >= 0) { if (ui.quality !== cur.ui.quality) add('Visual quality', cur.ui.quality, ui.quality, function () { SARA.quality.set(ui.quality); }); }
      else skipped.push('visual quality (unknown)');
    }
    if (ui.home && typeof ui.home === 'object' && SARA.homeLayout) {
      const h = SARA.homeLayout.get(), diff = Object.keys(h).filter(function (k) { return k in ui.home && !!ui.home[k] !== !!h[k]; });
      if (diff.length) add('Home widgets', diff.length + ' differ', 'from file', function () { SARA.homeLayout.merge(ui.home); });
    }
    if (typeof as.display_name === 'string' && as.display_name.trim() && as.display_name.length <= 40 && as.display_name !== cur.assistant.display_name) add('Display name', cur.assistant.display_name || '\u2014', as.display_name, function () { const n = $('setName'); n.value = as.display_name; n.dispatchEvent(new Event('change')); });
    if (typeof as.muted === 'boolean' && as.muted !== cur.assistant.muted) add('Mute', cur.assistant.muted ? 'on' : 'off', as.muted ? 'on' : 'off', function () { SARA.setMute(as.muted, true); });
    if (typeof as.focus === 'boolean' && as.focus !== cur.assistant.focus) add('Focus mode', cur.assistant.focus ? 'on' : 'off', as.focus ? 'on' : 'off', function () { SARA.setFocus(as.focus, true); });
    if (as.language !== undefined && as.language !== null) {
      const chip = doc.querySelector('#langChips .chip[data-lang="' + String(as.language).replace(/[^a-z-]/gi, '') + '"]');
      if (chip) { if (as.language !== cur.assistant.language) add('Language', cur.assistant.language || '\u2014', as.language, function () { chip.click(); }); } else skipped.push('language (unknown)');
    }
    [['mic_sensitivity', 'micSlider', 'Microphone sensitivity'], ['speech_speed', 'speedSlider', 'Speech speed']].forEach(function (s) {
      const v = as[s[0]]; if (v === undefined || v === null) return; const sl = $(s[1]); if (!sl) return;
      const n = Math.round(Number(v)), lo = Number(sl.min || 0), hi = Number(sl.max || 100);
      if (!isFinite(n) || n < lo || n > hi) { skipped.push(s[2] + ' (out of range)'); return; }
      if (n !== cur.assistant[s[0]]) add(s[2], cur.assistant[s[0]], n, function () { sl.value = n; sl.dispatchEvent(new Event('input', { bubbles: true })); });
    });
    const tg = as.toggles && typeof as.toggles === 'object' ? as.toggles : {};
    Object.keys(tg).forEach(function (k) {
      if (typeof tg[k] !== 'boolean') { skipped.push(k + ' (not on/off)'); return; }
      if (k === 'sound_effects') { if (tg[k] !== isOn($('setSounds'))) add('Sound effects', isOn($('setSounds')) ? 'on' : 'off', tg[k] ? 'on' : 'off', function () { SARA.sound.set(tg[k]); SARA.setToggle($('setSounds'), tg[k]); SARA.callApi('update_setting', 'sound_effects', tg[k]); }); return; }
      const n = doc.querySelector('[data-setting="' + k.replace(/[^a-z_]/gi, '') + '"]'); if (!n) { skipped.push(k + ' (not in this version)'); return; }
      if (tg[k] !== isOn(n)) add(rowLabel(n), isOn(n) ? 'on' : 'off', tg[k] ? 'on' : 'off', function () { setSetting(k, tg[k]); });
    });
    return { changes: changes, skipped: skipped };
  }
  function importDialog() {
    const body = el('div', 'sx-dbody'); body.appendChild(el('p', 'sx-p', 'Paste a SARA settings JSON or choose the file. You will see exactly what changes before anything is applied.'));
    const ta = el('textarea', 'sx-ta'); ta.rows = 8; ta.placeholder = '{ "app": "sara-settings", \u2026 }'; ta.setAttribute('aria-label', 'Settings JSON'); body.appendChild(ta);
    const file = el('input'); file.type = 'file'; file.accept = '.json,application/json'; file.className = 'sx-file'; file.setAttribute('aria-label', 'Choose settings file'); body.appendChild(file);
    const out = el('div', 'sx-out'); body.appendChild(out);
    const check = pbtn('Review changes'), apply = pbtn('Apply', 'primary'); apply.disabled = true;
    const d = dialog('Import settings', body, [check, apply]); let current = null;
    function review() {
      out.textContent = ''; apply.disabled = true; current = null; const raw = ta.value.trim(); if (!raw) { out.appendChild(el('div', 'sx-msg bad', 'Paste or choose a file first.')); return; }
      if (raw.length > 200000) { out.appendChild(el('div', 'sx-msg bad', 'That is too large to be a settings file.')); return; }
      let data; try { data = JSON.parse(raw); } catch (e) { out.appendChild(el('div', 'sx-msg bad', 'That is not valid JSON.')); return; }
      const p = plan(data); if (p.error) { out.appendChild(el('div', 'sx-msg bad', p.error)); return; }
      if (!p.changes.length) out.appendChild(el('div', 'sx-msg', 'Nothing to change \u2014 these settings already match.'));
      else {
        out.appendChild(el('div', 'sx-msg', p.changes.length + ' change' + (p.changes.length === 1 ? '' : 's') + ' will be applied:'));
        const ul = el('ul', 'sx-list'); p.changes.slice(0, 40).forEach(function (c) { const li = el('li'); li.appendChild(el('b', '', c.label)); li.appendChild(doc.createTextNode(' ' + c.from + ' \u2192 ' + c.to)); ul.appendChild(li); }); out.appendChild(ul);
        apply.disabled = false; current = p;
      }
      if (p.skipped.length) out.appendChild(el('div', 'sx-skip', 'Ignored: ' + p.skipped.slice(0, 8).join(', ')));
    }
    check.addEventListener('click', review);
    file.addEventListener('change', function () { const f = file.files && file.files[0]; if (!f) return; if (f.size > 200000) { out.textContent = ''; out.appendChild(el('div', 'sx-msg bad', 'That file is too large.')); return; } f.text().then(function (t) { ta.value = t; review(); }, function () { SARA.fail('Could not read that file.'); }); });
    apply.addEventListener('click', function () {
      if (!current) return; let n = 0; current.changes.forEach(function (c) { try { c.run(); n++; } catch (e) { /* keep going */ } });
      d.close(); SARA.ok('Applied ' + n + ' setting' + (n === 1 ? '' : 's')); if (SARA.sound && SARA.sound.saved) SARA.sound.saved();
    });
    ta.focus(); return d;
  }

  /* ====================================================================== presets */
  const FOCUS_KEYS = { show_notifications: false, proactive_mode: false, late_night_nudges: false, proactive_idle: false, proactive_streak: false };
  const SNAP = 'sara_focus_prev';
  function applyFocusPreset() {
    if (store.get(SNAP)) return;                                                // already in focus preset
    const prev = { focus: isOn($('setFocus')), toggles: {} };
    Object.keys(FOCUS_KEYS).forEach(function (k) { const n = doc.querySelector('[data-setting="' + k + '"]'); if (n) prev.toggles[k] = isOn(n); });
    store.set(SNAP, JSON.stringify(prev));
    SARA.setFocus(true, true); Object.keys(FOCUS_KEYS).forEach(function (k) { setSetting(k, FOCUS_KEYS[k]); });
  }
  function restoreFocusPreset() {
    const prev = readJson(SNAP, null); store.set(SNAP, '');
    if (!prev) { SARA.setFocus(false, true); return; }
    SARA.setFocus(!!prev.focus, true); Object.keys(prev.toggles || {}).forEach(function (k) { setSetting(k, !!prev.toggles[k]); });
  }
  const PRESETS = [
    { id: 'performance', name: 'Performance', desc: 'Lowest visual load: no blur, aura or sky light. Best for older PCs.', apply: function () { SARA.quality.set('low'); SARA.homeLayout && SARA.homeLayout.merge({ sky: 0, fx: 0 }); }, active: function () { return SARA.quality.get() === 'low'; } },
    { id: 'balanced', name: 'Balanced', desc: 'Smooth with light effects. The recommended default.', apply: function () { SARA.quality.set('balanced'); SARA.homeLayout && SARA.homeLayout.merge({ sky: 1, fx: 1, ring: 1 }); }, active: function () { return SARA.quality.get() === 'balanced'; } },
    { id: 'showcase', name: 'Showcase', desc: 'Everything on: stronger glass, dust, 3D tilt, stars at night.', apply: function () { SARA.quality.set('high'); SARA.homeLayout && SARA.homeLayout.merge({ sky: 1, fx: 1, ring: 1 }); }, active: function () { return SARA.quality.get() === 'high'; } },
    { id: 'focus', name: 'Focus', desc: 'Focus mode on, notifications and proactive nudges off. Remembers what you had and can restore it.', apply: applyFocusPreset, active: function () { return !!store.get(SNAP); } }
  ];
  const body = $('presetsBody');
  if (body) {
    body.appendChild(el('div', 'section-label', 'Presets'));
    const grid = el('div', 'sx-presets'); grid.setAttribute('role', 'group'); grid.setAttribute('aria-label', 'Presets'); body.appendChild(grid);
    const cards = PRESETS.map(function (p) {
      const c = el('div', 'sx-preset'), tx = el('div', 'sx-ptx'); tx.appendChild(el('div', 'sx-pname', p.name)); tx.appendChild(el('div', 'sx-pdesc', p.desc)); c.appendChild(tx);
      const b = pbtn('Apply', 'small'); c.appendChild(b); grid.appendChild(c);
      b.addEventListener('click', function () {
        if (p.id === 'focus' && p.active()) { restoreFocusPreset(); SARA.ok('Focus preset off \u2014 previous settings restored'); } else { p.apply(); SARA.ok(p.name + ' preset applied'); }
        if (SARA.sound && SARA.sound.tap) SARA.sound.tap(); paintPresets();
      });
      return { p: p, c: c, b: b };
    });
    function paintPresets() { cards.forEach(function (x) { const on = x.p.active(); x.c.classList.toggle('on', on); x.b.textContent = x.p.id === 'focus' ? (on ? 'Restore previous' : 'Apply') : (on ? 'Active' : 'Apply'); x.b.disabled = on && x.p.id !== 'focus'; }); }
    paintPresets(); SARA.on('quality', paintPresets);
    body.appendChild(el('div', 'section-label', 'Backup'));
    body.appendChild(el('div', 'sx-note', 'Export your settings to a file, or restore them from one. Notes, reminders and chat history are not included.'));
    const row = el('div', 'btn-row'); const ex = pbtn('Export settings\u2026'), im = pbtn('Import settings\u2026'); row.appendChild(ex); row.appendChild(im); body.appendChild(row);
    ex.addEventListener('click', exportDialog); im.addEventListener('click', importDialog);
  }
  window.SaraSettingsPlus = { computeTheme: computeTheme, collect: collect, plan: plan };
})();
