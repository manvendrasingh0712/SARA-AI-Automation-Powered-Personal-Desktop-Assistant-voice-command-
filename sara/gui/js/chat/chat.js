/* ==========================================================================
   chat.js -- Chat page: transcript log, live streaming reply, live voice caption, text input, mic.
   Push events handled: 'transcript' (role,text), 'transcript_chunk' (role,sentence),
   'transcript_partial' (role,text).  API calls: send_text_command + record_command_usage
   (both via SARA.sendCommand in core.js), wake_now (via SARA.wake in home.js).
   NOTE: send_text_command echoes the user's own bubble back via 'transcript', so we never append it locally.
   Polish: Sara's replies are REVEALED with a typewriter effect (the full text is already in hand -- only the
   display is paced; instant when reduced-motion is on or the window is hidden). Sent/received sounds live here.
   Styles: style/chat.css.  Markup: index.html #page-chat.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA, $ = SARA.$;
  const log = $('chatLog'), scroller = log, input = $('chatInputField');   // the log itself scrolls (style/chat.css), so the dock sits outside its edge-fade mask
  const dock = input.closest('.chat-dock');
  let streaming = null, preview = null, thinkingEl = null;

  /* ---- state-aware dock (style/chat.css): idle/hover/focus are handled by CSS alone (:hover,
     :focus-within) so they can never get "stuck". Only the states below are JS-driven, and every
     one of them reflects something that is actually true right now -- no state is faked or timed
     just for show. `paused` (assistant inactive) always wins over the live voice status. ---- */
  let liveStatus = 'idle', paused = false, transient = 0;
  dock.dataset.state = 'idle';
  function applyDockState() {
    if (transient) return;                                  // 'sending'/'error' flash owns the attribute briefly
    dock.dataset.state = paused ? 'disabled' : liveStatus;
    syncWave();
  }
  function setTransient(state, ms) {
    clearTimeout(transient);
    dock.dataset.state = state;
    syncWave();
    transient = setTimeout(function () { transient = 0; applyDockState(); }, ms);
  }

  /* ---- live mic waveform: 5 bars beside the mic button while the dock is really 'listening'.
     Data is the backend's real 'audio_level' push (source 'mic', 0..1) -- the same one the orb uses in js/home.js.
     Nothing is simulated: with no level events the bars simply rest at their minimum height. The rAF loop only
     runs while listening + the chat page is visible; it is cancelled (and the bars reset) the moment either stops. ---- */
  const wave = $('micWave'), waveBars = wave ? Array.prototype.slice.call(wave.children) : [];
  const WAVE_WEIGHT = [0.55, 0.85, 1, 0.75, 0.5], WAVE_FOLLOW = [0.42, 0.3, 0.5, 0.34, 0.26], WAVE_MIN = 0.16;
  let micLevel = 0, waveRaf = 0; const waveVals = waveBars.map(() => 0);
  SARA.on('ev:audio_level', function (source, level) {
    if (source === 'mic') micLevel = Math.max(0, Math.min(1, Number(level) || 0));
  });
  const waveActive = () => !!wave && !SARA.reduceMotion && !document.hidden && (SARA.current || 'home') === 'chat' && dock.dataset.state === 'listening';
  function resetWave() {
    micLevel = 0;
    waveBars.forEach(function (bar, i) { waveVals[i] = 0; bar.style.transform = ''; });
  }
  function waveStep() {
    waveRaf = 0;
    if (!waveActive()) { resetWave(); return; }
    micLevel *= 0.94;                                       // decays on its own, so a stalled event stream can't freeze the bars
    for (let i = 0; i < waveBars.length; i++) {
      waveVals[i] += (micLevel * WAVE_WEIGHT[i] - waveVals[i]) * WAVE_FOLLOW[i];
      waveBars[i].style.transform = 'scaleY(' + (WAVE_MIN + (1 - WAVE_MIN) * waveVals[i]).toFixed(3) + ')';
    }
    waveRaf = requestAnimationFrame(waveStep);
  }
  function syncWave() {
    if (!wave) return;
    if (waveActive()) { if (!waveRaf) waveRaf = requestAnimationFrame(waveStep); }
    else if (waveRaf) { cancelAnimationFrame(waveRaf); waveRaf = 0; resetWave(); }
  }
  document.addEventListener('visibilitychange', syncWave);

  function scrollDown(smooth) {
    if (window.SaraChatPlus && !window.SaraChatPlus.shouldStick()) return;   // chat-plus.js: don't yank the log while the user is reading older messages
    if (smooth && !SARA.reduceMotion) scroller.scrollTo({ top: scroller.scrollHeight, behavior: 'smooth' });
    else scroller.scrollTop = scroller.scrollHeight;
  }
  const canAnimate = () => !SARA.reduceMotion && !document.hidden;

  /* ---- tool-result action buttons: http(s) link in Sara's reply -> compact "Open Results" button under the bubble.
     Click reuses SARA.sendCommand('open <url>') = same path as typing/saying "open <url>" (no new backend method). ---- */
  const URL_RE = /https?:\/\/[^\s<>"']+/gi;
  function findUrls(text) {
    const out = [];
    (String(text).match(URL_RE) || []).forEach(function (u) {
      u = u.replace(/[.,;:!?)\]}]+$/, '');
      if (u.length > 8 && out.indexOf(u) < 0) out.push(u);
    });
    return out.slice(0, 3);
  }
  function attachActions(b) {
    if (!b || !b.div.classList.contains('sara')) return;
    const old = b.div.querySelector('.msg-actions'); if (old) old.remove();
    const urls = findUrls(b.full); if (!urls.length) return;
    const box = document.createElement('div'); box.className = 'msg-actions';
    urls.forEach(function (url, i) {
      const btn = document.createElement('button');
      btn.type = 'button'; btn.className = 'msg-action'; btn.title = url;
      btn.textContent = '\u2197 ' + (urls.length === 1 ? 'Open Results' : 'Open result ' + (i + 1));
      btn.addEventListener('click', async function () {
        btn.disabled = true; setTimeout(function () { btn.disabled = false; }, 1500);
        const res = await SARA.sendCommand('open ' + url);
        if (res && res.ok === false && res.reason && res.reason !== 'duplicate') {
          SARA.fail(res.reason === 'busy' ? 'Sara is still working on your last request.' : 'Could not open that link.');
        }
      });
      box.appendChild(btn);
    });
    b.div.insertBefore(box, b.div.querySelector('.ts'));
    scrollDown();
  }

  /* ---- blur-to-sharp: text the typewriter just revealed fades in from a light blur (250ms), then is merged back into
     the plain text node, so a long reply never accumulates spans. Bounded on purpose: only the newest text is wrapped,
     characters revealed within ~70ms share one span, and at most BLUR_MAX_SPANS spans exist at once (the oldest is
     merged early beyond that). Structure while typing:  msg-text = [settled text node][span.new]...[span.new]  ---- */
  const BLUR_MS = 250, BLUR_COALESCE_MS = 70, BLUR_MAX_SPANS = 4;
  function mergeOldest(b) {
    const s = b.spans.shift(); if (!s) return;
    b.settled.nodeValue += s.el.textContent; s.el.remove();
  }
  function settleDone(b) { while (b.spans.length && b.spans[0].done) mergeOldest(b); }
  function paintAll(b) {                                    // instant path: plain text, no wrapping; drops any wrapping state
    b.settled = null; b.spans = [];
    b.body.textContent = b.full.slice(0, b.shown);
  }
  function revealText(b, from, to) {
    const chunk = b.full.slice(from, to); if (!chunk) return;
    if (!b.settled) {                                       // first reveal: switch this bubble to [text node][spans] mode
      b.body.textContent = '';
      b.settled = document.createTextNode(b.full.slice(0, from)); b.body.appendChild(b.settled); b.spans = [];
    }
    const now = Date.now(), last = b.spans[b.spans.length - 1];
    if (last && now - last.t < BLUR_COALESCE_MS) { last.el.textContent += chunk; return; }
    if (b.spans.length >= BLUR_MAX_SPANS) mergeOldest(b);
    const el = document.createElement('span'); el.className = 'new'; el.textContent = chunk; b.body.appendChild(el);
    const entry = { el: el, t: now, done: false };
    b.spans.push(entry);
    const finish = function () { if (entry.done) return; entry.done = true; if (b.spans.indexOf(entry) >= 0) settleDone(b); };
    el.addEventListener('animationend', finish);
    setTimeout(finish, BLUR_MS + 120);                      // animationend can be skipped (hidden window) -- never leave a span behind
  }

  /* ---- typewriter: reveals b.full into b.body; speed adapts so long replies never drag (~3s max) ---- */
  function typerStep(b, now) {
    b.raf = 0;
    if (!b.last) b.last = now;
    const dt = Math.min(64, now - b.last); b.last = now;
    const remaining = b.full.length - b.shown;
    if (remaining > 0) {
      b.acc = (b.acc || 0) + (80 + remaining * 1.2) * dt / 1000;           // chars/sec grows with the backlog
      const n = Math.floor(b.acc);
      if (n > 0) {
        const from = b.shown;
        b.acc -= n; b.shown = Math.min(b.full.length, b.shown + n);
        const c = b.full.charCodeAt(b.shown - 1);
        if (c >= 0xD800 && c <= 0xDBFF && b.shown < b.full.length) b.shown++;  // don't split an emoji
        revealText(b, from, b.shown); scrollDown();
      }
    }
    if (b.shown < b.full.length) b.raf = requestAnimationFrame((t) => typerStep(b, t));
    else { b.div.classList.remove('typing'); attachActions(b); }
  }
  function startTyper(b) {
    if (b.raf) return;
    b.div.classList.add('typing');
    b.raf = requestAnimationFrame((t) => typerStep(b, t));
  }

  function makeBubble(role, text, animate) {
    const div = document.createElement('div');
    div.className = 'msg ' + (role === 'user' ? 'user' : 'sara');
    const body = document.createElement('span'); body.className = 'msg-text';
    const ts = document.createElement('span'); ts.className = 'ts'; ts.textContent = SARA.fmtClockTime(new Date());
    div.appendChild(body); div.appendChild(ts);
    if (thinkingEl && role === 'user') log.insertBefore(div, thinkingEl); else log.appendChild(div);   // the thinking dots always stay last
    const b = { div: div, body: body, full: String(text == null ? '' : text), shown: 0, raf: 0, acc: 0, last: 0, settled: null, spans: [] };
    if (animate && b.full.length) startTyper(b);
    else { b.shown = b.full.length; body.textContent = b.full; }
    scrollDown(true);
    return b;
  }
  function finalizeStream() {
    if (streaming) {
      streaming.div.classList.remove('streaming');   // typing (if still running) finishes on its own
      if (streaming.shown >= streaming.full.length) attachActions(streaming);   // else the typewriter adds them when done
    }
    streaming = null;
  }
  /* ---- thinking indicator: three dots in the orb's thinking colour while the REAL status is 'thinking'.
     Shown/removed only by status changes and by Sara's reply actually arriving; pure CSS animation (style/chat.css). ---- */
  function showThinking() {
    if (thinkingEl) return;
    const div = document.createElement('div');
    div.className = 'msg sara thinking'; div.setAttribute('role', 'status'); div.setAttribute('aria-label', 'Sara is thinking');
    for (let i = 0; i < 3; i++) { const d = document.createElement('span'); d.className = 'dot'; div.appendChild(d); }
    log.appendChild(div); thinkingEl = div; scrollDown(true);
  }
  function hideThinking() { if (thinkingEl) { thinkingEl.remove(); thinkingEl = null; } }
  const PREVIEW_STALE_MS = 8000;       // safety net: if the "clear" (empty partial) event is ever lost, the dim caption removes itself
  let previewTimer = 0;
  function dropPreview() {
    clearTimeout(previewTimer); previewTimer = 0;
    if (preview && preview.div.parentNode) preview.div.remove();
    preview = null;
  }

  /* ---- send fly-up (FLIP): the bubble the backend echoes back for a message typed in the dock lifts out of the
     dock and settles into its log position. The real bubble is laid out first (instant scroll, so its rect is the
     true destination), hidden, and a fixed-position ghost copy travels from the dock to that rect with
     transform + opacity only. A ghost is used because the log scrolls (overflow clips) and sits above the dock.
     Skipped whenever it cannot be measured honestly: chat page not showing, window hidden, reduced motion,
     or the echo isn't the message we just sent (e.g. a voice command) -- the bubble then just uses its normal entrance. ---- */
  const FLY_MS = 420, FLY_MAX_AGE_MS = 5000;
  let pendingFly = null;
  function takeFly(text) {
    const f = pendingFly; pendingFly = null;
    return (f && f.text === String(text == null ? '' : text).trim() && Date.now() - f.t < FLY_MAX_AGE_MS) ? f : null;
  }
  function flyUp(div, fly) {
    if (!fly || !fly.rect || !fly.rect.width || !div.animate || !canAnimate() || (SARA.current || 'home') !== 'chat') return;
    div.classList.add('flying');                                    // real bubble: no entrance animation (its transform would skew the measurement) and hidden until the ghost lands
    scroller.scrollTop = scroller.scrollHeight;                     // settle first: measure the FINAL position
    const to = div.getBoundingClientRect(); if (!to.width) { div.classList.remove('flying'); return; }
    const ghost = document.createElement('div');
    ghost.className = 'msg user msg-ghost'; ghost.innerHTML = div.innerHTML;
    ghost.style.cssText = 'left:' + to.left + 'px;top:' + to.top + 'px;width:' + to.width + 'px;';
    const dx = (fly.rect.left + fly.rect.width / 2) - (to.left + to.width / 2);
    const dy = (fly.rect.top + fly.rect.height / 2) - (to.top + to.height / 2);
    document.body.appendChild(ghost);
    let done = false;
    const land = function () { if (done) return; done = true; clearTimeout(safety); div.classList.remove('flying-wait'); ghost.remove(); };
    div.classList.add('flying-wait');
    const safety = setTimeout(land, FLY_MS + 250);
    const anim = ghost.animate(
      [{ transform: 'translate(' + dx + 'px,' + dy + 'px) scale(.96)', opacity: 0.35 }, { transform: 'translate(0,0) scale(1)', opacity: 1 }],
      { duration: FLY_MS, easing: 'cubic-bezier(.22,1,.36,1)', fill: 'both' });
    anim.onfinish = land; anim.oncancel = land;
  }

  let pendingReplyTurn = null;
  SARA.on('ev:reply_meta', function (turnId) {
    pendingReplyTurn = typeof turnId === 'string' && turnId ? turnId.slice(0, 40) : null;
  });

  function attachThumbs(bubble) {
    if (!bubble || !bubble.div || !pendingReplyTurn) return;
    const turnId = pendingReplyTurn;
    pendingReplyTurn = null;
    const bar = document.createElement('div');
    bar.className = 'msg-feedback';
    const specs = [
      { value: 1, glyph: '\u{1F44D}', label: 'Helpful answer' },
      { value: -1, glyph: '\u{1F44E}', label: 'Unhelpful answer' }
    ];
    const buttons = specs.map(function (spec) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'msg-fb-btn';
      b.textContent = spec.glyph;
      b.setAttribute('aria-label', spec.label);
      b.addEventListener('click', function () { vote(spec, b); });
      bar.appendChild(b);
      return b;
    });
    function vote(spec, chosen) {
      if (bar.dataset.done) return;
      bar.dataset.done = '1';
      buttons.forEach(function (b) {
        if (b === chosen) { b.disabled = true; b.classList.add('is-chosen'); }
        else { b.hidden = true; }
      });
      SARA.callApi('submit_feedback', turnId, spec.value).catch(function () {});
    }
    bubble.div.appendChild(bar);
  }

  SARA.on('ev:transcript', function (role, text) {
    if (role !== 'user') hideThinking();       // any non-user line means Sara's reply has arrived
    if (role === 'user') {
      finalizeStream(); dropPreview();
      const fly = takeFly(text);
      const ub = makeBubble('user', text, false); SARA.sound.sent();
      if (fly) flyUp(ub.div, fly);
    } else if (role === 'sara' && streaming) {
      const liveBubble = streaming;
      finalizeStream();                       // this reply was already streamed live -> no duplicate bubble
      attachThumbs(liveBubble);
    } else {
      // This event arrives AFTER Sara has already spoken the reply (backend pushes it once
      // _handle_command() returns), so a typewriter here only makes the UI look "still generating".
      // Render instantly; the streaming path (ev:transcript_chunk) keeps its typewriter.
      const nb = makeBubble(role, text, false); SARA.sound.received();
      attachActions(nb);
      attachThumbs(nb);
    }
  });

  SARA.on('ev:transcript_partial', function (role, text) {     // dim live caption while the user is still speaking
    if (!text || !String(text).trim()) { dropPreview(); return; }
    if (!preview) { preview = makeBubble(role === 'user' ? 'user' : 'sara', text, false); preview.div.classList.add('preview'); }
    else { preview.full = String(text); preview.shown = preview.full.length; preview.body.textContent = text; scrollDown(); }
    clearTimeout(previewTimer); previewTimer = setTimeout(dropPreview, PREVIEW_STALE_MS);
  });

  SARA.on('ev:transcript_chunk', function (role, sentence) {   // LLM reply arriving sentence by sentence
    if (!sentence) return;
    hideThinking();
    if (!streaming) {
      streaming = makeBubble('sara', sentence, canAnimate()); streaming.div.classList.add('streaming'); SARA.sound.received();
    } else {
      streaming.full += ' ' + sentence;
      if (canAnimate()) startTyper(streaming);
      else { streaming.shown = streaming.full.length; paintAll(streaming); scrollDown(); }
    }
  });

  async function send() {
    const text = input.value.trim(); if (!text) return;
    const sendBtn = $('chatSend');
    pendingFly = { text: text, rect: input.getBoundingClientRect ? input.getBoundingClientRect() : null, t: Date.now() };   // FLIP "first" rect, consumed by the echo above
    setTransient('sending', 4000);                          // cleared below the instant the real call resolves
    const res = await SARA.sendCommand(text);
    clearTimeout(transient); transient = 0;
    if (res && res.ok === false) pendingFly = null;          // rejected/duplicate -> no echo is coming, nothing to fly
    if (res && res.ok === false && res.reason && res.reason !== 'duplicate') {
      setTransient('error', 900);
      SARA.fail(res.reason === 'busy' ? 'Sara is still working on your last request.' : 'Could not send that message.');
      return;
    }
    input.value = '';
    if (!SARA.reduceMotion) {                                // quick success feedback on the send button itself
      sendBtn.classList.add('is-success');
      setTimeout(() => sendBtn.classList.remove('is-success'), 550);
    }
    applyDockState();
  }
  $('chatSend').addEventListener('click', send);
  input.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); send(); } });

  $('chatMic').addEventListener('click', () => SARA.wake());
  SARA.on('status', function (s) {
    $('chatMic').classList.toggle('on', s === 'listening' || s === 'waking');
    liveStatus = (s === 'listening' || s === 'waking') ? 'listening' : (s === 'thinking' ? 'processing' : 'idle');
    if (s === 'thinking') showThinking(); else hideThinking();
    applyDockState();
  });
  SARA.on('ev:skill_card', function (name, card) {
    if (!window.SaraSkillCards) return;
    log.appendChild(window.SaraSkillCards.render(card)); scrollDown(true);
  });
  SARA.on('ev:skill_chips', function (name, chips) {
    if (!window.SaraSkillCards) return;
    log.appendChild(window.SaraSkillCards.renderChips(chips, function (t) { SARA.callApi('send_text_command', t); })); scrollDown(true);
  });
  SARA.on('assistant_active', function (active) { paused = !active; applyDockState(); });
  SARA.on('page', function (p) { if (p === 'chat') { scrollDown(); setTimeout(() => input.focus(), 60); } syncWave(); });
})();
;

/* ######################################################################
   Merged from chat-plus.js (formerly a separate file layered on this one)
   ###################################################################### */
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
