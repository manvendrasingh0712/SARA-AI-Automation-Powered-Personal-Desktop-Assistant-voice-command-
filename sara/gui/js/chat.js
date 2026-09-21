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
  const log = $('chatLog'), scroller = log.parentElement, input = $('chatInputField');
  let streaming = null, preview = null;

  function scrollDown() { scroller.scrollTop = scroller.scrollHeight; }
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
        b.acc -= n; b.shown = Math.min(b.full.length, b.shown + n);
        const c = b.full.charCodeAt(b.shown - 1);
        if (c >= 0xD800 && c <= 0xDBFF && b.shown < b.full.length) b.shown++;  // don't split an emoji
        b.body.textContent = b.full.slice(0, b.shown); scrollDown();
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
    div.appendChild(body); div.appendChild(ts); log.appendChild(div);
    const b = { div: div, body: body, full: String(text == null ? '' : text), shown: 0, raf: 0, acc: 0, last: 0 };
    if (animate && b.full.length) startTyper(b);
    else { b.shown = b.full.length; body.textContent = b.full; }
    scrollDown();
    return b;
  }
  function finalizeStream() {
    if (streaming) {
      streaming.div.classList.remove('streaming');   // typing (if still running) finishes on its own
      if (streaming.shown >= streaming.full.length) attachActions(streaming);   // else the typewriter adds them when done
    }
    streaming = null;
  }
  const PREVIEW_STALE_MS = 8000;       // safety net: if the "clear" (empty partial) event is ever lost, the dim caption removes itself
  let previewTimer = 0;
  function dropPreview() {
    clearTimeout(previewTimer); previewTimer = 0;
    if (preview && preview.div.parentNode) preview.div.remove();
    preview = null;
  }

  SARA.on('ev:transcript', function (role, text) {
    if (role === 'user') {
      finalizeStream(); dropPreview();
      makeBubble('user', text, false); SARA.sound.sent();
    } else if (role === 'sara' && streaming) {
      finalizeStream();                       // this reply was already streamed live -> no duplicate bubble
    } else {
      const nb = makeBubble(role, text, canAnimate()); SARA.sound.received();
      if (!canAnimate()) attachActions(nb);   // animated bubbles get buttons when the typewriter finishes
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
    if (!streaming) {
      streaming = makeBubble('sara', sentence, canAnimate()); streaming.div.classList.add('streaming'); SARA.sound.received();
    } else {
      streaming.full += ' ' + sentence;
      if (canAnimate()) startTyper(streaming);
      else { streaming.shown = streaming.full.length; streaming.body.textContent = streaming.full; scrollDown(); }
    }
  });

  async function send() {
    const text = input.value.trim(); if (!text) return;
    const res = await SARA.sendCommand(text);
    if (res && res.ok === false && res.reason && res.reason !== 'duplicate') {
      SARA.fail(res.reason === 'busy' ? 'Sara is still working on your last request.' : 'Could not send that message.');
      return;
    }
    input.value = '';
  }
  $('chatSend').addEventListener('click', send);
  input.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); send(); } });

  $('chatMic').addEventListener('click', () => SARA.wake());
  SARA.on('status', (s) => $('chatMic').classList.toggle('on', s === 'listening' || s === 'waking'));
  SARA.on('page', function (p) { if (p === 'chat') { scrollDown(); setTimeout(() => input.focus(), 60); } });
})();