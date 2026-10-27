/* ==========================================================================
   chat-plus.js -- Chat upgrades layered on top of js/chat.js (chat.js itself is only touched in ONE line: scrollDown).
   1 Markdown     Sara's finished replies render safely (headings, lists, **bold**, *italic*, `code`, ``` code blocks ```, > quotes,
                  [links](https://..)); code blocks get a language tag, light syntax colours and a Copy button.
   2 Message tools  hover a message: Copy (all) / Edit (your messages -> back into the input) / Try again (Sara's latest reply).
   3 Follow mode  the log only auto-follows new text while you are at the bottom; scrolled up -> a "jump to latest" button with an unread count.
   4 Search       Ctrl+F (or the magnifier) searches this conversation: matches are highlighted, others dim, Enter / Shift+Enter jump.
   5 Starters     an empty chat shows suggestion chips.   6 ArrowUp in an empty input re-loads your last message.
   Safe by construction: all message text goes through createElement/textContent (innerHTML is used only for the static icon paths); links open through the same SARA.sendCommand('open <url>') path the
   "Open Results" buttons already use. Frontend only. Styles: style/chat-plus.css. Load AFTER js/chat.js.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA; if (!SARA) return;
  const $ = SARA.$, doc = document;
  const log = $('chatLog'), input = $('chatInputField'), page = $('page-chat');
  if (!log || !input || !page) return;
  const pbody = page.querySelector('.page-body') || page;
  const el = function (tag, cls, text) { const n = doc.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
  const ICON = {
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>',
    check: '<path d="M4 12.5l5 5L20 6.5"/>',
    edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
    redo: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
    down: '<path d="M6 9l6 6 6-6"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>',
    up: '<path d="M6 15l6-6 6 6"/>',
    close: '<path d="M6 6l12 12M18 6L6 18"/>'
  };
  function icon(name) {
    const NS = 'http://www.w3.org/2000/svg', s = doc.createElementNS(NS, 'svg');
    s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('aria-hidden', 'true');
    const tmp = doc.createElementNS(NS, 'g'); tmp.innerHTML = ICON[name]; while (tmp.firstChild) s.appendChild(tmp.firstChild);   // static icon markup only
    return s;
  }
  function iconButton(cls, label, name, act) {
    const b = el('button', cls); b.type = 'button'; b.title = label; b.setAttribute('aria-label', label); if (act) b.dataset.act = act; b.appendChild(icon(name)); return b;
  }

  /* ------------------------------------------------------------------ clipboard */
  function legacyCopy(t) {
    const a = el('textarea'); a.value = t; a.setAttribute('readonly', ''); a.style.cssText = 'position:fixed;left:-9999px;top:0;opacity:0';
    doc.body.appendChild(a); a.select(); let ok = false; try { ok = doc.execCommand('copy'); } catch (e) { ok = false; } a.remove(); return ok;
  }
  function copyText(t) {
    if (navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(t).then(function () { return true; }, function () { return legacyCopy(t); });
    return Promise.resolve(legacyCopy(t));
  }
  function flash(btn, okIcon) {
    const old = btn.firstChild; btn.classList.add('done'); btn.replaceChild(icon(okIcon || 'check'), old);
    setTimeout(function () { btn.classList.remove('done'); if (btn.firstChild !== old) btn.replaceChild(old, btn.firstChild); }, 1300);
  }

  /* ------------------------------------------------------------------ syntax colours (tiny, generic) */
  const KW = new Set(('function return const let var if else for while do switch case break continue new class extends import from export default async await try catch finally throw typeof instanceof in of this super null undefined true false ' +
    'def lambda None True False and or not is pass yield with as elif raise global nonlocal print int float str bool void static public private protected final interface enum struct impl fn mut pub use mod match loop where echo fi then done ' +
    'SELECT FROM WHERE INSERT INTO UPDATE DELETE CREATE TABLE VALUES JOIN GROUP ORDER BY LIMIT AND OR NOT NULL AS ON SET select from where insert into update delete create table values join group order by limit').split(' '));
  const TOK = /(\/\/[^\n]*|\/\*[\s\S]*?\*\/|#[^\n]*|--[^\n]*)|("(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`)|(\b\d+(?:\.\d+)?\b)|(\b[A-Za-z_][A-Za-z0-9_]*\b)/g;
  const PLAIN = { '': 1, text: 1, txt: 1, plain: 1, output: 1, log: 1 };
  function highlight(code, lang) {
    const frag = doc.createDocumentFragment();
    if (PLAIN[lang] || code.length > 6000) { frag.appendChild(doc.createTextNode(code)); return frag; }
    let last = 0, m; TOK.lastIndex = 0;
    while ((m = TOK.exec(code))) {
      if (m.index > last) frag.appendChild(doc.createTextNode(code.slice(last, m.index)));
      let cls = null;
      if (m[1]) cls = 'tk-c'; else if (m[2]) cls = 'tk-s'; else if (m[3]) cls = 'tk-n';
      else if (KW.has(m[4])) cls = 'tk-k'; else if (code.charAt(TOK.lastIndex) === '(') cls = 'tk-f';
      if (cls) frag.appendChild(el('span', cls, m[0])); else frag.appendChild(doc.createTextNode(m[0]));
      last = TOK.lastIndex;
    }
    if (last < code.length) frag.appendChild(doc.createTextNode(code.slice(last)));
    return frag;
  }
  function codeBlock(code, lang) {
    const wrap = el('div', 'md-code'), head = el('div', 'md-code-head');
    head.appendChild(el('span', 'md-lang', lang || 'code'));
    const cb = el('button', 'md-copy'); cb.type = 'button'; cb.setAttribute('aria-label', 'Copy code');
    cb.appendChild(icon('copy')); cb.appendChild(el('span', '', 'Copy'));
    cb.addEventListener('click', function () {
      copyText(code).then(function (ok) {
        const t = cb.querySelector('span'); cb.classList.toggle('done', ok); t.textContent = ok ? 'Copied' : 'Press Ctrl+C';
        setTimeout(function () { cb.classList.remove('done'); t.textContent = 'Copy'; }, 1400);
      });
    });
    head.appendChild(cb);
    const pre = el('pre'), c = el('code'); c.appendChild(highlight(code, lang)); pre.appendChild(c);
    wrap.appendChild(head); wrap.appendChild(pre); return wrap;
  }

  /* ------------------------------------------------------------------ markdown (safe DOM builder) */
  const MD_HINT = /```|^\s{0,3}#{1,4}\s+\S|^\s*[-*\u2022]\s+\S|^\s*\d+[.)]\s+\S|\*\*[^*\n]+\*\*|`[^`\n]+`|\[[^\]\n]+\]\(https?:\/\/[^\s)]+\)|^\s*>\s?\S|^\s*([-*_])\1{2,}\s*$/m;
  const INL = /(`[^`\n]+`)|(\*\*[^*\n]+?\*\*)|(__[^_\n]+?__)|(\[[^\]\n]+\]\((https?:\/\/[^\s)]+)\))|(\*[^*\s][^*\n]*?\*)|(\b_[^_\s][^_\n]*?_\b)/g;
  function link(parent, label, url) {
    const a = el('a', 'md-link', label); a.href = '#'; a.title = url; a.setAttribute('role', 'link');
    a.addEventListener('click', function (e) {
      e.preventDefault();
      SARA.sendCommand('open ' + url).then(function (res) { if (res && res.ok === false && res.reason && res.reason !== 'duplicate') SARA.fail('Could not open that link.'); });
    });
    parent.appendChild(a);
  }
  function inline(parent, s) {
    let last = 0, m; const re = new RegExp(INL.source, 'g');
    while ((m = re.exec(s))) {
      if (m.index > last) parent.appendChild(doc.createTextNode(s.slice(last, m.index)));
      if (m[1]) parent.appendChild(el('code', 'md-inline', m[1].slice(1, -1)));
      else if (m[2] || m[3]) { const b = el('strong'); inline(b, m[0].slice(2, -2)); parent.appendChild(b); }
      else if (m[4]) link(parent, m[4].slice(1, m[4].indexOf(']')), m[5]);
      else { const i = el('em'); inline(i, m[0].slice(1, -1)); parent.appendChild(i); }
      last = re.lastIndex;
    }
    if (last < s.length) parent.appendChild(doc.createTextNode(s.slice(last)));
  }
  function renderMarkdown(text) {
    const root = doc.createDocumentFragment(), lines = String(text).replace(/\r/g, '').split('\n');
    let i = 0;
    const isBlank = function (l) { return !l.trim(); };
    while (i < lines.length) {
      const l = lines[i], fence = /^\s*```\s*([\w+#.-]*)\s*$/.exec(l);
      if (fence) {
        const buf = []; i++;
        while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) buf.push(lines[i++]);
        i++; root.appendChild(codeBlock(buf.join('\n'), (fence[1] || '').toLowerCase())); continue;
      }
      if (isBlank(l)) { i++; continue; }
      let m;
      if ((m = /^\s{0,3}(#{1,4})\s+(.*)$/.exec(l))) { const h = el('div', 'md-h md-h' + m[1].length); inline(h, m[2]); root.appendChild(h); i++; continue; }
      if (/^\s*([-*_])\1{2,}\s*$/.test(l)) { root.appendChild(el('hr', 'md-hr')); i++; continue; }
      if (/^\s*>\s?/.test(l)) {
        const q = el('blockquote', 'md-quote'), buf = [];
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ''));
        inline(q, buf.join(' ')); root.appendChild(q); continue;
      }
      const ul = /^\s*[-*\u2022]\s+/, ol = /^\s*\d+[.)]\s+/;
      if (ul.test(l) || ol.test(l)) {
        const ordered = ol.test(l), list = el(ordered ? 'ol' : 'ul', 'md-list'), re = ordered ? ol : ul;
        while (i < lines.length && re.test(lines[i])) { const li = el('li'); inline(li, lines[i].replace(re, '')); list.appendChild(li); i++; }
        root.appendChild(list); continue;
      }
      const para = el('p', 'md-p'), buf = [];
      while (i < lines.length && !isBlank(lines[i]) && !/^\s*```/.test(lines[i]) && !/^\s{0,3}#{1,4}\s/.test(lines[i]) && !ul.test(lines[i]) && !ol.test(lines[i]) && !/^\s*>/.test(lines[i])) buf.push(lines[i++]);
      buf.forEach(function (ln, k) { if (k) para.appendChild(el('br')); inline(para, ln); });
      root.appendChild(para);
    }
    return root;
  }

  /* ------------------------------------------------------------------ per-message upgrade */
  const SKIP = ['thinking', 'preview', 'typing', 'streaming', 'msg-ghost'];
  const ready = function (m) { return m.classList.contains('msg') && (m.classList.contains('sara') || m.classList.contains('user')) && !SKIP.some(function (c) { return m.classList.contains(c); }); };
  const plainText = function (m) { return m._md != null ? m._md : ((m.querySelector('.msg-text') || m).textContent || ''); };
  function lastUserBefore(m) { for (let n = m.previousElementSibling; n; n = n.previousElementSibling) if (n.classList.contains('user') && n.classList.contains('msg')) return plainText(n); return ''; }
  function upgrade(m) {
    if (m._cp) return; m._cp = 1;
    const body = m.querySelector('.msg-text'); if (!body) return;
    if (m.classList.contains('sara')) {
      const t = body.textContent;
      if (t && MD_HINT.test(t)) { m._md = t; const frag = renderMarkdown(t); body.textContent = ''; body.appendChild(frag); body.classList.add('md'); }
    }
    const tools = el('div', 'msg-tools'); tools.setAttribute('role', 'toolbar'); tools.setAttribute('aria-label', 'Message actions');
    tools.appendChild(iconButton('msg-tool', 'Copy message', 'copy', 'copy'));
    if (m.classList.contains('user')) tools.appendChild(iconButton('msg-tool', 'Edit and resend', 'edit', 'edit'));
    else tools.appendChild(iconButton('msg-tool', 'Try again', 'redo', 'regen'));
    tools.addEventListener('click', function (e) {
      const b = e.target.closest && e.target.closest('.msg-tool'); if (!b) return; e.stopPropagation();
      const act = b.dataset.act;
      if (act === 'copy') copyText(plainText(m)).then(function (ok) { if (ok) flash(b); else SARA.fail('Could not copy.'); });
      else if (act === 'edit') { input.value = plainText(m); input.focus(); input.setSelectionRange(input.value.length, input.value.length); SARA.toast('ti-check', 'var(--core)', 'Edit it, then press Enter to send'); }
      else if (act === 'regen') {
        const q = lastUserBefore(m); if (!q) { SARA.fail('Nothing to retry yet.'); return; }
        b.disabled = true; setTimeout(function () { b.disabled = false; }, 1500);
        SARA.sendCommand(q).then(function (res) { if (res && res.ok === false && res.reason && res.reason !== 'duplicate') SARA.fail(res.reason === 'busy' ? 'Sara is still working on your last request.' : 'Could not retry.'); });
      }
    });
    m.appendChild(tools); markLast();
  }
  function markLast() {
    const all = log.querySelectorAll('.msg.sara'); let last = null;
    all.forEach(function (n) { n.classList.remove('is-last-sara'); if (n._cp) last = n; });
    if (last) last.classList.add('is-last-sara');
  }

  /* ------------------------------------------------------------------ follow mode + jump button */
  const NEAR = 120; let stuck = true, force = 0, unread = 0, sraf = 0;
  const nearBottom = function () { return log.scrollHeight - log.scrollTop - log.clientHeight < NEAR; };
  const jump = el('button', 'chat-jump'); jump.type = 'button'; jump.setAttribute('aria-label', 'Jump to latest message');
  jump.appendChild(icon('down')); const badge = el('span', 'chat-jump-n'); jump.appendChild(badge); pbody.appendChild(jump);
  function paintJump() { const far = !nearBottom(); jump.classList.toggle('show', far); if (!far) { unread = 0; badge.textContent = ''; badge.classList.remove('on'); } }
  function toBottom(smooth) {
    stuck = true; unread = 0; badge.textContent = ''; badge.classList.remove('on');
    if (smooth && !SARA.reduceMotion) log.scrollTo({ top: log.scrollHeight, behavior: 'smooth' }); else log.scrollTop = log.scrollHeight;
  }
  log.addEventListener('scroll', function () {
    if (sraf) return;
    sraf = requestAnimationFrame(function () { sraf = 0; stuck = nearBottom(); paintJump(); });
  }, { passive: true });
  jump.addEventListener('click', function () { toBottom(true); });
  window.SaraChatPlus = { shouldStick: function () { return stuck || Date.now() < force; }, toBottom: toBottom };

  /* ------------------------------------------------------------------ search */
  const bar = el('div', 'chat-find'), fIn = el('input'), fCount = el('span', 'chat-find-n');
  bar.hidden = true; fIn.type = 'search'; fIn.placeholder = 'Search this conversation'; fIn.autocomplete = 'off'; fIn.setAttribute('aria-label', 'Search this conversation');
  const fPrev = iconButton('chat-find-btn', 'Previous match', 'up'), fNext = iconButton('chat-find-btn', 'Next match', 'down'), fClose = iconButton('chat-find-btn', 'Close search', 'close');
  bar.appendChild(icon('search')); bar.appendChild(fIn); bar.appendChild(fCount); bar.appendChild(fPrev); bar.appendChild(fNext); bar.appendChild(fClose);
  const fOpen = iconButton('chat-find-open', 'Search conversation (Ctrl+F)', 'search');
  pbody.appendChild(bar); pbody.appendChild(fOpen);
  let matches = [], cur = -1, ft = 0;
  const HL = !!(window.CSS && CSS.highlights && window.Highlight);
  function clearFind() {
    matches = []; cur = -1; if (HL) { CSS.highlights.delete('chat-find'); CSS.highlights.delete('chat-find-cur'); }
    log.querySelectorAll('.find-miss').forEach(function (n) { n.classList.remove('find-miss'); }); fCount.textContent = '';
  }
  function showCur() {
    if (!matches.length) { fCount.textContent = fIn.value.trim() ? 'No results' : ''; return; }
    fCount.textContent = (cur + 1) + ' / ' + matches.length;
    if (HL) CSS.highlights.set('chat-find-cur', new Highlight(matches[cur].r));
    matches[cur].m.scrollIntoView({ block: 'center', behavior: SARA.reduceMotion ? 'auto' : 'smooth' });
  }
  function runFind() {
    clearFind(); const q = fIn.value.trim().toLowerCase(); if (!q) return;
    log.querySelectorAll('.msg:not(.thinking):not(.preview)').forEach(function (m) {
      let hit = false; const tw = doc.createTreeWalker(m.querySelector('.msg-text') || m, NodeFilter.SHOW_TEXT); let n;
      while ((n = tw.nextNode())) {
        const t = n.nodeValue.toLowerCase(); let i = t.indexOf(q);
        while (i >= 0) { const r = doc.createRange(); r.setStart(n, i); r.setEnd(n, i + q.length); matches.push({ r: r, m: m }); hit = true; i = t.indexOf(q, i + q.length); }
      }
      m.classList.toggle('find-miss', !hit);
    });
    if (HL && matches.length) { const h = new Highlight(); matches.forEach(function (x) { h.add(x.r); }); CSS.highlights.set('chat-find', h); }
    cur = matches.length ? 0 : -1; showCur();
  }
  function step(d) { if (!matches.length) return; cur = (cur + d + matches.length) % matches.length; showCur(); }
  function openFind() { bar.hidden = false; fOpen.hidden = true; fIn.focus(); fIn.select(); if (fIn.value) runFind(); }
  function closeFind() { bar.hidden = true; fOpen.hidden = false; clearFind(); }
  fOpen.addEventListener('click', openFind); fClose.addEventListener('click', closeFind);
  fPrev.addEventListener('click', function () { step(-1); }); fNext.addEventListener('click', function () { step(1); });
  fIn.addEventListener('input', function () { clearTimeout(ft); ft = setTimeout(runFind, 120); });
  fIn.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); step(e.shiftKey ? -1 : 1); } else if (e.key === 'Escape') { e.preventDefault(); closeFind(); input.focus(); }
  });
  doc.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && (e.key === 'f' || e.key === 'F') && (SARA.current || 'home') === 'chat') { e.preventDefault(); openFind(); }
  });
  SARA.on('page', function (p) { if (p !== 'chat' && !bar.hidden) closeFind(); });

  /* ------------------------------------------------------------------ starters (empty chat) */
  const STARTERS = ['What can you do?', "What's the weather?", 'Play some music', 'Remind me in 10 minutes', "What's on my schedule?", 'Take a note'];
  const empty = el('div', 'chat-empty'); empty.setAttribute('role', 'group'); empty.setAttribute('aria-label', 'Suggestions');
  STARTERS.forEach(function (t, i) {
    const b = el('button', 'chat-starter', t); b.type = 'button'; b.style.setProperty('--i', i);
    b.addEventListener('click', function () { SARA.sendCommand(t).then(function (res) { if (res && res.ok === false && res.reason && res.reason !== 'duplicate') SARA.fail(res.reason === 'busy' ? 'Sara is still working on your last request.' : 'Could not send that message.'); }); });
    empty.appendChild(b);
  });
  log.insertAdjacentElement('afterend', empty);

  /* ------------------------------------------------------------------ ArrowUp = bring back my last message */
  input.addEventListener('keydown', function (e) {
    if (e.key !== 'ArrowUp' || input.value) return;
    const users = log.querySelectorAll('.msg.user'); if (!users.length) return;
    e.preventDefault(); input.value = plainText(users[users.length - 1]); input.setSelectionRange(input.value.length, input.value.length);
  });

  /* ------------------------------------------------------------------ observer: upgrade finished messages, track unread */
  const mo = new MutationObserver(function (muts) {
    muts.forEach(function (mu) {
      if (mu.type === 'attributes') { if (ready(mu.target)) upgrade(mu.target); return; }
      mu.addedNodes.forEach(function (n) {
        if (n.nodeType !== 1 || !n.classList.contains('msg') || n.classList.contains('thinking') || n.classList.contains('preview')) return;
        if (n.classList.contains('user')) { force = Date.now() + 1600; toBottom(true); }
        else if (!stuck && Date.now() > force) { unread++; badge.textContent = unread > 9 ? '9+' : String(unread); badge.classList.add('on'); paintJump(); }
        if (ready(n)) upgrade(n);
      });
    });
  });
  mo.observe(log, { childList: true, attributes: true, attributeFilter: ['class'], subtree: true });
  log.querySelectorAll('.msg').forEach(function (n) { if (ready(n)) upgrade(n); });
})();
