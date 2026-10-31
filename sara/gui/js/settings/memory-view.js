/* ==========================================================================
   memory-view.js -- Settings > Memory > "Open memory viewer" (Memory 2.0).
   Backend: sara/gui/app/memory2_api.py (mem2_list, mem2_update, mem2_forget, mem2_pin, mem2_stats).
   Markup: index.html #memViewer.  Styles: style/memory-view.css.  Dynamic text via textContent only.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA;
  SARA.memoryView = SARA.memoryView || {};
  const PAGE = 50, DEBOUNCE_MS = 250, MAX_TEXT = 200, MAX_QUERY = 80;
  const TABS = ['facts', 'events', 'entities', 'archived'];
  const EMPTY = {
    facts: 'No facts yet. Sara adds them as you chat.',
    events: 'No events yet. Plans and dates you mention show up here.',
    entities: 'No people, places or things yet.',
    archived: 'Nothing archived. Old, unused memories move here.'
  };
  const OFF_TEXT = 'Memory 2.0 is off - set MEMORY2_ENABLED=True in .env and restart';
  const state = { tab: 'facts', query: '', offset: 0, token: 0, open: false, loading: false, lastFocus: null, timer: 0 };

  const root = SARA.$('memViewer');
  const card = root && root.querySelector('.mv-card');
  const list = SARA.$('mvList'), more = SARA.$('mvMore'), note = SARA.$('mvNote');
  const counts = SARA.$('mvCounts'), hint = SARA.$('mvHint'), search = SARA.$('mvSearch');
  const closeBtn = SARA.$('mvClose'), tabsEl = SARA.$('mvTabs'), openBtn = SARA.$('openMemoryViewerBtn');
  if (!root || !card || !list || !more || !note || !counts || !hint || !search || !closeBtn || !tabsEl) return;
  const tabEls = Array.prototype.slice.call(tabsEl.querySelectorAll('[role="tab"]'));

  function node(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : (many || one + 's')); }
  function relMs(ms) {
    if (!isFinite(ms)) return '';
    const sec = (Date.now() - ms) / 1000;
    if (sec < 0) return new Date(ms).toLocaleDateString([], { year: 'numeric', month: 'short', day: 'numeric' });
    if (sec < 60) return 'just now';
    const min = Math.floor(sec / 60);
    if (min < 60) return plural(min, 'minute') + ' ago';
    const hr = Math.floor(min / 60);
    if (hr < 24) return plural(hr, 'hour') + ' ago';
    const day = Math.floor(hr / 24);
    if (day < 30) return plural(day, 'day') + ' ago';
    if (day < 365) return plural(Math.floor(day / 30), 'month') + ' ago';
    return plural(Math.floor(day / 365), 'year') + ' ago';
  }
  function rel(iso) { return typeof iso === 'string' ? relMs(Date.parse(iso)) : ''; }
  function absTime(iso) {
    const ms = typeof iso === 'string' ? Date.parse(iso) : NaN;
    if (!isFinite(ms)) return '';
    return new Date(ms).toLocaleString([], { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  }
  function say(text) { note.hidden = !text; note.textContent = text || ''; }
  function failText(res) {
    const code = res && res.error;
    if (code === 'disabled') return 'Memory 2.0 is off.';
    if (code === 'not_found') return 'That memory no longer exists.';
    return 'Could not complete that. Please try again.';
  }
  async function api() {
    try { return await SARA.callApi.apply(SARA, arguments); } catch (e) { return null; }
  }
  function setBusy(row, on) {
    row.querySelectorAll('button').forEach(function (b) { b.disabled = on; });
  }
  function showStatus(text, cls) {
    list.appendChild(node('div', 'mv-status' + (cls ? ' ' + cls : ''), text));
  }
  function showEmpty() {
    showStatus(state.query ? 'No memories match that search.' : EMPTY[state.tab]);
  }
  function setOff(on) {
    root.classList.toggle('mv-off', on);
    if (!on) return;
    closeBtn.focus();
    list.textContent = '';
    showStatus(OFF_TEXT);
    counts.textContent = '';
    hint.textContent = '';
    more.hidden = true;
    say('');
  }

  function dotFor(c) {
    if (typeof c !== 'number' || !isFinite(c)) return null;
    const level = c >= 0.75 ? 'high' : c >= 0.45 ? 'medium' : 'low';
    const dot = node('span', 'mv-dot mv-dot-' + level);
    dot.setAttribute('role', 'img');
    dot.setAttribute('aria-label', 'Confidence: ' + level);
    dot.title = 'Confidence: ' + level;
    return dot;
  }

  function swap(row, item, focusIndex) {
    const fresh = buildRow(item);
    row.replaceWith(fresh);
    const buttons = fresh.querySelectorAll('.mv-act');
    if (buttons[focusIndex]) buttons[focusIndex].focus();
  }

  async function togglePin(row, item) {
    setBusy(row, true);
    const res = await api('mem2_pin', item.id, item.kind, !item.pinned);
    if (!state.open) return;
    if (res && res.ok) {
      item.pinned = !item.pinned;
      say('');
      swap(row, item, 0);
      return;
    }
    say(failText(res));
    setBusy(row, false);
    if (res && res.error === 'not_found') load(true);
  }

  async function saveEdit(row, item, input) {
    const value = input.value.replace(/\s+/g, ' ').trim();
    if (!value) { say('Text cannot be empty.'); return; }
    if (value === (item.value || item.text)) { say(''); swap(row, item, 1); return; }
    input.disabled = true;
    const patch = {};
    patch[item.kind === 'events' ? 'summary' : 'object_text'] = value;
    const res = await api('mem2_update', item.id, item.kind, patch);
    if (!state.open) return;
    if (res && res.ok) {
      say('');
      load(true);
      card.focus();
      return;
    }
    say(failText(res));
    input.disabled = false;
    input.focus();
  }

  function startEdit(row, item) {
    const textEl = row.querySelector('.mv-text');
    if (!textEl || row.classList.contains('mv-editing')) return;
    const input = node('input', 'mv-edit');
    input.type = 'text';
    input.maxLength = MAX_TEXT;
    input.value = item.value || item.text;
    input.setAttribute('aria-label', 'Edit memory text');
    row.classList.add('mv-editing');
    setBusy(row, true);
    textEl.replaceWith(input);
    input.addEventListener('keydown', function (e) {
      if (e.isComposing) return;
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); say(''); swap(row, item, 1); }
      else if (e.key === 'Enter') { e.preventDefault(); saveEdit(row, item, input); }
    });
    input.focus();
    input.select();
  }

  async function doForget(row, item) {
    setBusy(row, true);
    const res = await api('mem2_forget', item.id, item.kind);
    if (!state.open) return;
    if (res && res.ok) {
      say('');
      const next = row.nextElementSibling;
      row.remove();
      const target = next && next.querySelector('.mv-act');
      if (target) target.focus(); else card.focus();
      if (!list.querySelector('.mv-row')) { if (!more.hidden) load(true); else showEmpty(); }
      loadStats();
      return;
    }
    say(failText(res));
    swap(row, item, 2);
    if (res && res.error === 'not_found') load(true);
  }

  function askForget(row, item, actions) {
    actions.textContent = '';
    actions.appendChild(node('span', 'mv-confirm', 'Forget?'));
    const yes = node('button', 'mv-act mv-act-danger', 'Yes, forget');
    const no = node('button', 'mv-act', 'Cancel');
    yes.type = 'button';
    no.type = 'button';
    yes.addEventListener('click', function () { doForget(row, item); });
    no.addEventListener('click', function () { swap(row, item, 2); });
    actions.appendChild(yes);
    actions.appendChild(no);
    no.focus();
  }

  function buildRow(item) {
    const row = node('div', 'mv-row' + (item.pinned ? ' mv-pinned' : ''));
    const main = node('div', 'mv-main');
    main.appendChild(node('div', 'mv-text', item.text));
    const meta = node('div', 'mv-meta');
    const dot = dotFor(item.confidence);
    if (dot) meta.appendChild(dot);
    const when = rel(item.when);
    if (when) meta.appendChild(node('span', 'mv-when', when));
    if (item.inferred) meta.appendChild(node('span', 'mv-badge mv-badge-inferred', 'Inferred'));
    if (item.pinned) meta.appendChild(node('span', 'mv-badge mv-badge-pin', 'Pinned'));
    main.appendChild(meta);
    row.appendChild(main);
    if (item.kind !== 'facts' && item.kind !== 'events') return row;

    const date = absTime(item.learned || item.when);
    const why = node('div', 'mv-why',
      (date ? 'Learned ' + date : 'Date unknown') + ' \u00b7 source turn ' + (item.source_turn_id || 'unknown'));
    why.hidden = true;
    const actions = node('div', 'mv-actions');
    function act(label, cls, fn) {
      const b = node('button', 'mv-act' + (cls ? ' ' + cls : ''), label);
      b.type = 'button';
      b.addEventListener('click', fn);
      actions.appendChild(b);
      return b;
    }
    act(item.pinned ? 'Unpin' : 'Pin', '', function () { togglePin(row, item); });
    act('Edit', '', function () { startEdit(row, item); });
    act('Forget', 'mv-act-danger', function () { askForget(row, item, actions); });
    const whyBtn = act('Why do I know this?', '', function () {
      why.hidden = !why.hidden;
      whyBtn.setAttribute('aria-expanded', why.hidden ? 'false' : 'true');
    });
    whyBtn.setAttribute('aria-expanded', 'false');
    row.appendChild(actions);
    row.appendChild(why);
    return row;
  }

  async function load(reset) {
    if (reset) {
      state.offset = 0;
      list.textContent = '';
      more.hidden = true;
      showStatus('Loading\u2026', 'mv-loading');
    } else if (state.loading) {
      return;
    }
    const token = ++state.token;
    state.loading = true;
    more.disabled = true;
    list.setAttribute('aria-busy', 'true');
    const res = await api('mem2_list', state.tab, state.query, PAGE, state.offset);
    if (token !== state.token) return;
    state.loading = false;
    more.disabled = false;
    list.removeAttribute('aria-busy');
    const wait = list.querySelector('.mv-loading');
    if (wait) wait.remove();
    if (!state.open) return;
    const data = res && res.ok ? res.data : null;
    if (!data) {
      say('Could not load memories.');
      if (!list.querySelector('.mv-row')) showEmpty();
      return;
    }
    if (data.enabled === false) { setOff(true); return; }
    root.classList.remove('mv-off');
    say('');
    const items = Array.isArray(data.items) ? data.items : [];
    items.forEach(function (it) {
      if (!it || typeof it !== 'object') return;
      it.text = String(it.text == null ? '' : it.text);
      list.appendChild(buildRow(it));
    });
    state.offset += items.length;
    more.hidden = !data.has_more;
    if (!list.querySelector('.mv-row')) showEmpty();
  }

  async function loadStats() {
    const res = await api('mem2_stats');
    if (!state.open) return;
    const d = res && res.ok ? res.data : null;
    if (!d) { counts.textContent = ''; hint.textContent = ''; return; }
    if (d.enabled === false) { setOff(true); return; }
    const num = function (v) { return typeof v === 'number' && isFinite(v) ? v : 0; };
    const f = d.facts || {}, e = d.events || {};
    counts.textContent = [
      plural(num(f.active), 'fact'), plural(num(e.active), 'event'),
      plural(num(d.entities), 'entity', 'entities'), (num(f.archived) + num(e.archived)) + ' archived'
    ].join(' \u00b7 ');
    const ts = d.last_extract_ts;
    hint.textContent = typeof ts === 'number' && isFinite(ts)
      ? 'Extraction last ran ' + relMs(ts * 1000) : 'Extraction has not run yet';
  }

  function paintTabs() {
    tabEls.forEach(function (b) {
      const on = b.dataset.tab === state.tab;
      b.setAttribute('aria-selected', on ? 'true' : 'false');
      b.tabIndex = on ? 0 : -1;
    });
    list.setAttribute('aria-labelledby', 'mvTab-' + state.tab);
  }
  function selectTab(name, focus) {
    if (TABS.indexOf(name) < 0) return;
    if (name !== state.tab) {
      state.tab = name;
      paintTabs();
      say('');
      load(true);
    }
    if (focus) {
      const b = SARA.$('mvTab-' + name);
      if (b) b.focus();
    }
  }

  function open() {
    if (state.open) return;
    state.open = true;
    state.lastFocus = document.activeElement;
    state.tab = 'facts';
    state.query = '';
    search.value = '';
    say('');
    counts.textContent = '';
    hint.textContent = '';
    root.classList.remove('mv-off');
    paintTabs();
    root.hidden = false;
    loadStats();
    load(true);
    const first = SARA.$('mvTab-facts');
    (first || card).focus();
  }
  function close() {
    if (!state.open) return;
    state.open = false;
    state.token++;
    state.loading = false;
    clearTimeout(state.timer);
    root.hidden = true;
    const back = state.lastFocus;
    state.lastFocus = null;
    if (back && typeof back.focus === 'function' && document.contains(back)) back.focus();
  }

  tabEls.forEach(function (b) {
    b.addEventListener('click', function () { selectTab(b.dataset.tab, false); });
  });
  tabsEl.addEventListener('keydown', function (e) {
    const i = TABS.indexOf(state.tab);
    let n = -1;
    if (e.key === 'ArrowRight') n = (i + 1) % TABS.length;
    else if (e.key === 'ArrowLeft') n = (i + TABS.length - 1) % TABS.length;
    else if (e.key === 'Home') n = 0;
    else if (e.key === 'End') n = TABS.length - 1;
    if (n < 0) return;
    e.preventDefault();
    selectTab(TABS[n], true);
  });
  search.addEventListener('input', function () {
    clearTimeout(state.timer);
    state.timer = setTimeout(function () {
      state.query = search.value.trim().slice(0, MAX_QUERY);
      load(true);
    }, DEBOUNCE_MS);
  });
  more.addEventListener('click', function () { load(false); });
  closeBtn.addEventListener('click', close);
  root.addEventListener('click', function (e) { if (e.target === root) close(); });
  root.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { e.preventDefault(); close(); return; }
    if (e.key !== 'Tab') return;
    const stops = Array.prototype.filter.call(card.querySelectorAll('button, input'), function (el) {
      return !el.disabled && el.tabIndex >= 0 && el.getClientRects().length > 0;
    });
    if (!stops.length) { e.preventDefault(); card.focus(); return; }
    const first = stops[0], last = stops[stops.length - 1], active = document.activeElement;
    if (!card.contains(active) || active === card) { e.preventDefault(); (e.shiftKey ? last : first).focus(); }
    else if (e.shiftKey && active === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && active === last) { e.preventDefault(); first.focus(); }
  });
  if (openBtn) openBtn.addEventListener('click', open);

  SARA.memoryView.open = open;
  SARA.memoryView.close = close;
  SARA.memoryView.refresh = function () { if (state.open) { loadStats(); load(true); } };
})();