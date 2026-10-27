/* ==========================================================================
   automation-plus.js -- Automation page upgrades layered on js/automation.js + js/routine-builder.js (frontend only).
   1 Flow view     every routine card gets a visual flow (trigger -> step -> step ...) that opens with the "Flow" button, with a
                   "Preview flow" walkthrough. The preview is a plain animation: it says so, and nothing is executed.
   2 Run history   each Run click is logged on this device (time + success) and shown on the card ("Last run 5 min ago - 3 runs").
                   Scheduled runs happen in the backend and are not visible to the GUI, so they are not counted.
   3 Templates     6 ready-made automations built ONLY from actions the backend lists (SARA.routineActions). "Add" saves a manual
                   automation (no schedule, no destructive steps) through the normal save_routine call; tap Edit to tweak or schedule it.
   4 Builder       (needs the 1-line patch in routine-builder.js) a live flow mini-map, drag-to-reorder + up/down buttons with a smooth
                   FLIP animation, step numbers, and a searchable action picker instead of "add a default step then change it".
   Styles: style/automation-plus.css. Load AFTER js/automation.js.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA; if (!SARA || !SARA.routineActions) return;
  const doc = document, $ = SARA.$, A = SARA.routineActions;
  const list = $('routinesList'), newBtn = $('newRoutineBtn'); if (!list || !newBtn) return;
  const el = function (tag, cls, text) { const n = doc.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
  const mem = {};
  const store = {
    get: function (k) { try { const v = localStorage.getItem(k); if (v !== null) return v; } catch (e) { /* blocked */ } return (k in mem) ? mem[k] : null; },
    set: function (k, v) { mem[k] = v; try { localStorage.setItem(k, v); } catch (e) { /* blocked */ } }
  };

  /* ------------------------------------------------------------------ icons + step info */
  const ICON = {
    power: '<path d="M12 3v9"/><path d="M6.3 7.2a8 8 0 1 0 11.4 0"/>', volume: '<path d="M4 9v6h4l5 4V5L8 9z"/><path d="M16.5 8.5a5 5 0 0 1 0 7"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M5 19l1.5-1.5M17.5 6.5L19 5"/>',
    window: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 9h18"/>', play: '<path d="M8 5v14l11-7z"/>',
    keyboard: '<rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    zoom: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2M8.5 11h5M11 8.5v5"/>',
    wifi: '<path d="M2.5 9a15 15 0 0 1 19 0M5.5 12.5a10.5 10.5 0 0 1 13 0M8.8 16a6 6 0 0 1 6.4 0"/><circle cx="12" cy="19.2" r="1"/>',
    moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z"/>', folder: '<path d="M3 8a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    gear: '<circle cx="12" cy="12" r="3"/><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>', timer: '<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2M9 2.5h6"/>',
    spark: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>', clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    bolt: '<path d="M13 3L5 13h6l-1 8 8-10h-6z"/>', grip: '<circle cx="9" cy="6" r="1.2"/><circle cx="15" cy="6" r="1.2"/><circle cx="9" cy="12" r="1.2"/><circle cx="15" cy="12" r="1.2"/><circle cx="9" cy="18" r="1.2"/><circle cx="15" cy="18" r="1.2"/>',
    up: '<path d="M6 15l6-6 6 6"/>', down: '<path d="M6 9l6 6 6-6"/>', search: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>', plus: '<path d="M12 5v14M5 12h14"/>', check: '<path d="M4 12.5l5 5L20 6.5"/>'
  };
  const GROUP_ICON = { 'Power & Session': 'power', 'Volume': 'volume', 'Brightness': 'sun', 'Window Management': 'window', 'Media': 'play', 'Keyboard': 'keyboard', 'Browser Tabs': 'globe', 'Zoom & Scroll': 'zoom', 'Network': 'wifi', 'Display': 'moon', 'Files & Notes': 'folder', 'Folders': 'folder', 'Windows Settings': 'gear', 'System Info': 'info', 'Timer': 'timer' };
  const DANGER = { shutdown_system: 1, restart_system: 1, log_off: 1, hibernate_system: 1, sleep_system: 1, clear_notes: 1, empty_recycle_bin: 1, close_active_window: 1, wifi_off: 1, bluetooth_off: 1 };
  const keyGroup = {}; A.groups.forEach(function (g) { g.keys.forEach(function (k) { keyGroup[k] = g.label; }); });
  function svg(name) {
    const NS = 'http://www.w3.org/2000/svg', s = doc.createElementNS(NS, 'svg'); s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('aria-hidden', 'true');
    const g = doc.createElementNS(NS, 'g'); g.innerHTML = ICON[name] || ICON.spark; while (g.firstChild) s.appendChild(g.firstChild); return s;   // static icon paths only
  }
  function stepInfo(s) {
    if (!s) return { label: '?', icon: 'spark', danger: false };
    if (s.type === 'simple_action') return { label: A.labels[s.key] || s.key, icon: GROUP_ICON[keyGroup[s.key]] || 'spark', danger: !!DANGER[s.key] };
    return { label: (s.type || 'step') + ': ' + (s.name || '…'), icon: 'spark', danger: false };
  }
  const fmt12 = function (t) { return SARA.fmt12h ? SARA.fmt12h(t) : t; };
  function node(kind, label, icon, num, danger) {
    const n = el('div', 'rt-node' + (kind === 'trigger' ? ' trigger' : '') + (danger ? ' danger' : ''));
    const i = el('div', 'rt-ico'); i.appendChild(svg(icon)); n.appendChild(i);
    n.appendChild(el('div', 'rt-lbl', label)); if (num) n.appendChild(el('span', 'rt-num', String(num)));
    return n;
  }
  function flowOf(trigger, steps) {
    const f = el('div', 'rt-flow'), t = trigger ? { l: 'At ' + fmt12(trigger), i: 'clock' } : { l: 'Run manually', i: 'bolt' };
    f.appendChild(node('trigger', t.l, t.i));
    (steps || []).forEach(function (s, k) { const si = stepInfo(s); f.appendChild(el('span', 'rt-link')); f.appendChild(node('step', si.label, si.icon, k + 1, si.danger)); });
    return f;
  }

  /* ------------------------------------------------------------------ cache routine data + record runs (wrap callApi) */
  let cache = []; const origCall = SARA.callApi;
  const HKEY = 'sara_rt_hist';
  function readHist() { try { return JSON.parse(store.get(HKEY) || '{}') || {}; } catch (e) { return {}; } }
  function recordRun(name, ok) {
    const h = readHist(), a = h[name] || []; a.push({ t: Date.now(), ok: !!ok }); h[name] = a.slice(-20); store.set(HKEY, JSON.stringify(h));
    const card = cardOf(name); if (card) { paintHist(card); if (!SARA.reduceMotion) { card.classList.remove('rt-ran'); void card.offsetWidth; card.classList.add('rt-ran'); } }
  }
  SARA.callApi = function (name) {
    const args = arguments, p = origCall.apply(this, args);
    if (p && p.then) {
      if (name === 'list_routines') p.then(function (r) { if (r && r.data) cache = r.data; }, function () { /* ignore */ });
      else if (name === 'run_routine_now') { const rn = args[1]; p.then(function (r) { recordRun(rn, !!(r && r.ok)); }, function () { recordRun(rn, false); }); }
    }
    return p;
  };
  const cardOf = function (name) { return Array.prototype.find.call(list.querySelectorAll('.auto-card'), function (c) { return c.dataset.name === name; }); };
  function rel(ts) {
    const m = Math.round((Date.now() - ts) / 60000);
    if (m < 1) return 'just now'; if (m < 60) return m + ' min ago'; const h = Math.round(m / 60); if (h < 24) return h + ' h ago';
    const d = Math.round(h / 24); return d === 1 ? 'yesterday' : d + ' days ago';
  }
  function paintHist(card) {
    const box = card.querySelector('.rt-hist'); if (!box) return;
    const a = (readHist()[card.dataset.name] || []); box.textContent = '';
    if (!a.length) { box.textContent = 'Not run from this app yet'; box.title = 'Scheduled runs happen in the background and are not counted here.'; return; }
    const last = a[a.length - 1], bad = a.filter(function (x) { return !x.ok; }).length;
    const dot = el('span', 'rt-dot ' + (last.ok ? 'ok' : 'bad')); box.appendChild(dot);
    box.appendChild(doc.createTextNode('Last run ' + rel(last.t) + ' \u00b7 ' + a.length + ' run' + (a.length === 1 ? '' : 's') + (bad ? ' (' + bad + ' failed)' : '')));
    box.title = 'Counts runs started from this app on this device. Scheduled runs are not included.';
  }

  /* ------------------------------------------------------------------ routine cards: flow view */
  const open = new Set();
  function preview(card) {
    const flow = card.querySelector('.rt-flow'), cap = card.querySelector('.rt-pv-cap'); if (!flow || flow._pv) return;
    const nodes = Array.prototype.slice.call(flow.querySelectorAll('.rt-node')), links = Array.prototype.slice.call(flow.querySelectorAll('.rt-link'));
    const clear = function () { nodes.forEach(function (n) { n.classList.remove('lit', 'done'); }); links.forEach(function (l) { l.classList.remove('on'); }); flow._pv = false; if (cap) cap.hidden = true; };
    if (SARA.reduceMotion) { nodes.forEach(function (n) { n.classList.add('done'); }); setTimeout(clear, 1200); return; }
    flow._pv = true; if (cap) cap.hidden = false; let i = 0;
    (function step() {
      if (i > 0) { nodes[i - 1].classList.remove('lit'); nodes[i - 1].classList.add('done'); if (links[i - 1]) links[i - 1].classList.add('on'); }
      if (i < nodes.length) { nodes[i].classList.add('lit'); i++; setTimeout(step, 540); } else setTimeout(clear, 1100);
    })();
  }
  function enhance() {
    list.querySelectorAll('.auto-card:not([data-rtp])').forEach(function (card) {
      card.dataset.rtp = '1';
      const r = cache.find(function (x) { return x.name === card.dataset.name; }); if (!r) return;
      const actions = card.querySelector('.auto-actions'), info = card.querySelector('.auto-info');
      const fb = el('button', 'pill-btn small', 'Flow'); fb.type = 'button'; fb.dataset.flow = ''; fb.setAttribute('aria-expanded', 'false');
      if (actions) actions.insertBefore(fb, actions.firstChild);
      const detail = el('div', 'rt-detail'), inn = el('div', 'rt-detail-in'), pad = el('div', 'rt-detail-pad');
      pad.appendChild(flowOf(r.trigger_time, r.steps));
      const meta = el('div', 'rt-meta'), hist = el('span', 'rt-hist'), side = el('span', 'rt-pv'), cap = el('span', 'rt-pv-cap', 'Preview only \u2014 nothing is run'); cap.hidden = true;
      const pv = el('button', 'link-btn', 'Preview flow'); pv.type = 'button'; pv.dataset.preview = '';
      side.appendChild(cap); side.appendChild(pv); meta.appendChild(hist); meta.appendChild(side); pad.appendChild(meta);
      inn.appendChild(pad); detail.appendChild(inn); card.appendChild(detail); paintHist(card);
      const setOpen = function (on) { card.classList.toggle('rt-open', on); fb.setAttribute('aria-expanded', on ? 'true' : 'false'); if (on) open.add(card.dataset.name); else open.delete(card.dataset.name); };
      fb.addEventListener('click', function () { setOpen(!card.classList.contains('rt-open')); });
      if (info) info.addEventListener('click', function () { setOpen(!card.classList.contains('rt-open')); });
      pv.addEventListener('click', function () { preview(card); });
      if (open.has(card.dataset.name)) setOpen(true);
    });
  }
  new MutationObserver(enhance).observe(list, { childList: true });
  setInterval(function () { if (!doc.hidden && (SARA.current || 'home') === 'automation') list.querySelectorAll('.auto-card').forEach(paintHist); }, 30000);

  /* ------------------------------------------------------------------ templates */
  const TEMPLATES = [
    { id: 'focus', name: 'Focus time', desc: 'Dark mode, mute, clear the desktop', steps: ['dark_mode', 'min_volume', 'minimize_all_windows'] },
    { id: 'morning', name: 'Good morning', desc: 'Light mode, bright screen, read your notes', steps: ['light_mode', 'max_brightness', 'read_notes'] },
    { id: 'winddown', name: 'Wind down', desc: 'Dark mode, dim screen, mute', steps: ['dark_mode', 'min_brightness', 'min_volume'] },
    { id: 'present', name: 'Presentation', desc: 'Light mode, max brightness, show desktop', steps: ['light_mode', 'max_brightness', 'show_desktop'] },
    { id: 'tidy', name: 'Quick tidy', desc: 'Open Downloads and Recycle Bin, check the disk', steps: ['open_downloads', 'open_recycle_bin', 'disk_usage'] },
    { id: 'away', name: 'Stepping away', desc: 'Mute and lock the PC', steps: ['min_volume', 'lock_pc'] }
  ].filter(function (t) { return t.steps.every(function (k) { return !!A.labels[k]; }); });   // only actions this backend really offers
  if (TEMPLATES.length) {
    const lab = el('div', 'section-label', 'Templates'); lab.style.marginTop = '22px';
    const row = el('div', 'rt-tpl-row');
    TEMPLATES.forEach(function (t) {
      const c = el('div', 'rt-tpl'), top = el('div', 'rt-tpl-top');
      const ic = el('div', 'rt-ico'); ic.appendChild(svg(stepInfo({ type: 'simple_action', key: t.steps[0] }).icon)); top.appendChild(ic);
      const tx = el('div', 'rt-tpl-tx'); tx.appendChild(el('div', 'rt-tpl-name', t.name)); tx.appendChild(el('div', 'rt-tpl-desc', t.desc)); top.appendChild(tx); c.appendChild(top);
      const mini = el('div', 'rt-tpl-steps'); t.steps.forEach(function (k) { const m = el('span', 'rt-mini'); m.title = A.labels[k]; m.appendChild(svg(stepInfo({ type: 'simple_action', key: k }).icon)); mini.appendChild(m); });
      const add = el('button', 'pill-btn small', 'Add'); add.type = 'button'; mini.appendChild(add); c.appendChild(mini);
      add.addEventListener('click', async function () {
        add.disabled = true;
        let name = 'tpl_' + t.id, n = 2; const taken = function (x) { return cache.some(function (r) { return r.name === x; }); };
        while (taken(name)) name = 'tpl_' + t.id + '_' + (n++);
        const res = await SARA.callApi('save_routine', name, t.name + (n > 2 ? ' ' + (n - 1) : ''), t.steps.map(function (k) { return { type: 'simple_action', key: k }; }), null);
        if (res && res.ok) { SARA.sound.saved(); SARA.ok('Added "' + t.name + '" \u2014 tap Edit to schedule or tweak it'); SARA.emit('routines:changed'); }
        else SARA.fail((res && res.error) || 'Could not add that template.');
        setTimeout(function () { add.disabled = false; }, 900);
      });
      row.appendChild(c);
    });
    newBtn.insertAdjacentElement('afterend', row); newBtn.insertAdjacentElement('afterend', lab);
  }

  /* ------------------------------------------------------------------ builder: mini flow, reorder, picker */
  const ed = SARA.routineEditor, modal = $('routineModal'), box = $('rtSteps'), addBtn = $('rtAddStep'), timeIn = $('rtTime');
  if (!ed || !modal || !box || !addBtn) return;
  const mini = el('div', 'rt-flow rt-flow-mini'); box.parentNode.insertBefore(mini, box);
  function updateMini() {
    const f = flowOf(timeIn && timeIn.value, ed.get()); mini.textContent = '';
    Array.prototype.slice.call(f.children).forEach(function (c, i) { mini.appendChild(c); });
    mini.querySelectorAll('.rt-node').forEach(function (n, i) {
      if (i === 0) return; n.setAttribute('role', 'button'); n.tabIndex = 0; n.title = 'Go to step ' + i;
      const go = function () { const row = box.children[i - 1]; if (row) { row.scrollIntoView({ block: 'nearest', behavior: SARA.reduceMotion ? 'auto' : 'smooth' }); row.classList.remove('rt-flash'); void row.offsetWidth; row.classList.add('rt-flash'); } };
      n.addEventListener('click', go); n.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
    });
  }
  function decorate() {
    Array.prototype.slice.call(box.querySelectorAll('.rt-step')).forEach(function (row, i) {
      if (row.dataset.rtp) return; row.dataset.rtp = '1';
      const line = row.querySelector('.rt-line'); if (!line) return;
      const grip = el('span', 'rt-grip'); grip.title = 'Drag to reorder'; grip.appendChild(svg('grip'));
      const badge = el('span', 'rt-badge', String(i + 1));
      line.insertBefore(badge, line.firstChild); line.insertBefore(grip, line.firstChild);
      const x = line.querySelector('[data-rm]');
      [['up', -1, 'Move up'], ['down', 1, 'Move down']].forEach(function (d) {
        const b = el('button', 'rt-mv'); b.type = 'button'; b.dataset.mv = d[1]; b.title = d[2]; b.setAttribute('aria-label', d[2]); b.appendChild(svg(d[0]));
        if (d[1] === -1 && i === 0) b.disabled = true; if (d[1] === 1 && i === ed.get().length - 1) b.disabled = true;
        if (x) line.insertBefore(b, x); else line.appendChild(b);
      });
    });
    updateMini();
  }
  new MutationObserver(decorate).observe(box, { childList: true });
  if (timeIn) timeIn.addEventListener('input', updateMini);
  function flip(before, perm) {
    if (SARA.reduceMotion) return;
    Array.prototype.slice.call(box.children).forEach(function (n, k) {
      const dy = before[perm[k]] - n.getBoundingClientRect().top;
      if (Math.abs(dy) > 1 && n.animate) n.animate([{ transform: 'translateY(' + dy + 'px)' }, { transform: 'none' }], { duration: 340, easing: 'cubic-bezier(.16,1,.3,1)' });
    });
  }
  function move(i, j) {
    const s = ed.get(); if (i === j || i < 0 || j < 0 || i >= s.length || j >= s.length) return;
    const before = Array.prototype.map.call(box.children, function (n) { return n.getBoundingClientRect().top; });
    const perm = s.map(function (_, k) { return k; }); const pi = perm.splice(i, 1)[0]; perm.splice(j, 0, pi);
    const it = s.splice(i, 1)[0]; s.splice(j, 0, it); ed.render(); flip(before, perm);
  }
  box.addEventListener('click', function (e) {
    const b = e.target.closest && e.target.closest('[data-mv]'); if (!b || b.disabled) return;
    const i = parseInt(b.closest('.rt-step').dataset.idx, 10); move(i, i + parseInt(b.dataset.mv, 10));
  });
  let dragFrom = -1;
  const marks = function () { box.querySelectorAll('.drop-before,.drop-after').forEach(function (r) { r.classList.remove('drop-before', 'drop-after'); }); };
  box.addEventListener('pointerdown', function (e) { const g = e.target.closest && e.target.closest('.rt-grip'); if (g) g.closest('.rt-step').draggable = true; });
  window.addEventListener('pointerup', function () { box.querySelectorAll('.rt-step').forEach(function (r) { if (dragFrom < 0) r.draggable = false; }); });
  box.addEventListener('dragstart', function (e) {
    const row = e.target.closest && e.target.closest('.rt-step'); if (!row) return;
    dragFrom = parseInt(row.dataset.idx, 10); row.classList.add('dragging'); try { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', String(dragFrom)); } catch (err) { /* ignore */ }
  });
  box.addEventListener('dragover', function (e) {
    if (dragFrom < 0) return; e.preventDefault(); marks();
    const row = e.target.closest && e.target.closest('.rt-step'); if (!row) return;
    const r = row.getBoundingClientRect(), after = e.clientY > r.top + r.height / 2; row._after = after; row.classList.add(after ? 'drop-after' : 'drop-before');
  });
  box.addEventListener('drop', function (e) {
    e.preventDefault(); const row = e.target.closest && e.target.closest('.rt-step'); if (!row || dragFrom < 0) return;
    let to = parseInt(row.dataset.idx, 10) + (row._after ? 1 : 0); if (dragFrom < to) to--; const from = dragFrom; dragFrom = -1; marks(); move(from, to);
  });
  box.addEventListener('dragend', function () { dragFrom = -1; marks(); box.querySelectorAll('.rt-step').forEach(function (r) { r.draggable = false; r.classList.remove('dragging'); }); });

  /* action picker */
  const picker = el('div', 'rt-picker'); picker.hidden = true;
  const sbar = el('div', 'rt-pk-search'); sbar.appendChild(svg('search')); const sin = el('input'); sin.type = 'search'; sin.placeholder = 'Search actions'; sin.autocomplete = 'off'; sin.setAttribute('aria-label', 'Search actions'); sbar.appendChild(sin);
  const pk = el('div', 'rt-pk-list'), pfoot = el('div', 'rt-pk-foot'), added = el('span', 'rt-pk-added');
  const adv = el('button', 'link-btn', 'Advanced step (intent / skill)'); adv.type = 'button';
  const done = el('button', 'pill-btn small', 'Done'); done.type = 'button'; pfoot.appendChild(added); pfoot.appendChild(adv); pfoot.appendChild(done);
  A.groups.forEach(function (g) {
    const sec = el('div', 'rt-pk-group'); sec.appendChild(el('div', 'rt-pk-gl', g.label));
    g.keys.forEach(function (k) {
      const b = el('button', 'rt-pk-item'); b.type = 'button'; b.dataset.key = k; b.dataset.q = ((A.labels[k] || k) + ' ' + g.label).toLowerCase();
      const ic = el('span', 'rt-pk-ic'); ic.appendChild(svg(GROUP_ICON[g.label] || 'spark')); b.appendChild(ic); b.appendChild(el('span', 'rt-pk-name', A.labels[k] || k));
      if (DANGER[k]) b.classList.add('danger'); sec.appendChild(b);
    });
    pk.appendChild(sec);
  });
  picker.appendChild(sbar); picker.appendChild(pk); picker.appendChild(pfoot); addBtn.insertAdjacentElement('afterend', picker);
  let addedN = 0;
  function openPicker() { picker.hidden = false; addedN = 0; added.textContent = ''; sin.value = ''; filter(); requestAnimationFrame(function () { picker.classList.add('open'); sin.focus({ preventScroll: true }); }); addBtn.setAttribute('aria-expanded', 'true'); }
  function closePicker() { picker.classList.remove('open'); addBtn.setAttribute('aria-expanded', 'false'); setTimeout(function () { if (!picker.classList.contains('open')) picker.hidden = true; }, 220); }
  function filter() {
    const q = sin.value.trim().toLowerCase();
    pk.querySelectorAll('.rt-pk-item').forEach(function (b) { b.hidden = !!q && b.dataset.q.indexOf(q) < 0; });
    pk.querySelectorAll('.rt-pk-group').forEach(function (g) { g.hidden = !g.querySelector('.rt-pk-item:not([hidden])'); });
  }
  function pushStep(step, label) {
    ed.get().push(step); ed.render(); addedN++; added.textContent = addedN + ' added' + (label ? ' \u00b7 ' + label : '');
    const row = box.lastElementChild; if (row && row.scrollIntoView) { row.scrollIntoView({ block: 'nearest', behavior: SARA.reduceMotion ? 'auto' : 'smooth' }); row.classList.add('rt-flash'); }
  }
  sin.addEventListener('input', filter);
  sin.addEventListener('keydown', function (e) { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closePicker(); addBtn.focus(); } });
  pk.addEventListener('click', function (e) { const b = e.target.closest && e.target.closest('.rt-pk-item'); if (b) pushStep({ type: 'simple_action', key: b.dataset.key }, A.labels[b.dataset.key]); });
  adv.addEventListener('click', function () { pushStep({ type: 'intent', name: '' }, 'advanced'); closePicker(); });
  done.addEventListener('click', closePicker);
  modal.addEventListener('click', function (e) {                       // capture: run BEFORE routine-builder's own "add a default step" handler
    if (e.target.closest && e.target.closest('#rtAddStep')) { e.stopImmediatePropagation(); e.preventDefault(); if (picker.hidden || !picker.classList.contains('open')) openPicker(); else closePicker(); }
  }, true);
  new MutationObserver(function () { if (!modal.classList.contains('open')) { picker.classList.remove('open'); picker.hidden = true; } else updateMini(); }).observe(modal, { attributes: true, attributeFilter: ['class'] });
  updateMini();
})();
