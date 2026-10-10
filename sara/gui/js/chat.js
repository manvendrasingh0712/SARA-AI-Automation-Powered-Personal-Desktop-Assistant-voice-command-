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

  SARA.on('ev:transcript', function (role, text) {
    if (role !== 'user') hideThinking();       // any non-user line means Sara's reply has arrived
    if (role === 'user') {
      finalizeStream(); dropPreview();
      const fly = takeFly(text);
      const ub = makeBubble('user', text, false); SARA.sound.sent();
      if (fly) flyUp(ub.div, fly);
    } else if (role === 'sara' && streaming) {
      finalizeStream();                       // this reply was already streamed live -> no duplicate bubble
    } else {
      // This event arrives AFTER Sara has already spoken the reply (backend pushes it once
      // _handle_command() returns), so a typewriter here only makes the UI look "still generating".
      // Render instantly; the streaming path (ev:transcript_chunk) keeps its typewriter.
      const nb = makeBubble(role, text, false); SARA.sound.received();
      attachActions(nb);
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