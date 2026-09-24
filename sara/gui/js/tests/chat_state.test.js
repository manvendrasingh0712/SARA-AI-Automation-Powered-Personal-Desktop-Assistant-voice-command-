/* ==========================================================================
   chat_state.test.js -- plain, dependency-free tests for chat.js's event
   handlers (doc item 19). No framework, no build step, no DOM/browser
   needed: chat.js is executed in a Node `vm` context against a minimal
   fake DOM + a small fake SARA event bus (reimplementing just the on/emit
   shape core.js exposes), then real chat.js logic is driven via
   SARA.emit('ev:...', ...) exactly as core.js's window.saraEvent does.

   RUN:  node sara/gui/js/tests/chat_state.test.js
   (exits non-zero on any failed assertion, for CI use)

   Covers:
   1. transcript_chunk (x2) followed by a matching transcript (finalize)
      does not create a duplicate bubble -- regression test for the
      behavior already commented in chat.js
      ("this reply was already streamed live -> no duplicate bubble").
   2. findUrls(), exercised indirectly via attachActions()'s real
      '.msg-action' buttons: no duplicate URLs, capped at 3.
   3. dropPreview(): an 'ev:transcript_partial' caption is created, then
      removed (not left lingering) once the partial text goes empty.
   4. A non-streaming 'ev:transcript' sara reply renders its full text
      immediately, with nothing left pending to animate -- regression
      test for the Part A audit finding above.
   ========================================================================== */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const CHAT_JS_PATH = path.join(__dirname, '..', 'chat.js');
const chatSource = fs.readFileSync(CHAT_JS_PATH, 'utf8');

/* ---- minimal fake DOM: just enough surface for chat.js's makeBubble/
   attachActions/dropPreview to run (createElement, classList, textContent,
   appendChild/insertBefore/removeChild/remove, a tiny '.class' selector
   query). Not a real DOM -- deliberately the lightest thing that lets
   real chat.js code run and be inspected. ---- */
function makeEl(tag) {
  const classSet = new Set();
  let text = '';
  const el = {
    tagName: tag,
    children: [],
    parentNode: null,
    style: {},
    dataset: {},
    classList: {
      add() { Array.prototype.forEach.call(arguments, (n) => classSet.add(n)); },
      remove() { Array.prototype.forEach.call(arguments, (n) => classSet.delete(n)); },
      toggle(name, force) {
        if (force === undefined) { if (classSet.has(name)) classSet.delete(name); else classSet.add(name); }
        else if (force) classSet.add(name); else classSet.delete(name);
      },
      contains(name) { return classSet.has(name); }
    },
    get className() { return Array.from(classSet).join(' '); },
    set className(v) { classSet.clear(); String(v).split(/\s+/).filter(Boolean).forEach((n) => classSet.add(n)); },
    get textContent() { return text; },
    set textContent(v) { text = v; },
    appendChild(child) { child.parentNode = el; el.children.push(child); return child; },
    insertBefore(child, ref) {
      child.parentNode = el;
      const idx = el.children.indexOf(ref);
      if (idx < 0) el.children.push(child); else el.children.splice(idx, 0, child);
      return child;
    },
    removeChild(child) {
      const idx = el.children.indexOf(child);
      if (idx >= 0) el.children.splice(idx, 1);
      child.parentNode = null;
      return child;
    },
    remove() { if (el.parentNode) el.parentNode.removeChild(el); },
    setAttribute(k, v) { el[k] = v; },
    addEventListener() { /* no-op: nothing under test needs to fire these */ },
    querySelector(sel) { return queryAll(el, sel)[0] || null; },
    querySelectorAll(sel) { return queryAll(el, sel); }
  };
  return el;
}
function matchesClass(el, sel) { return sel[0] === '.' && el.classList && el.classList.contains(sel.slice(1)); }
function queryAll(root, sel, acc) {
  acc = acc || [];
  (root.children || []).forEach((c) => { if (matchesClass(c, sel)) acc.push(c); queryAll(c, sel, acc); });
  return acc;
}

/* ---- minimal fake SARA event bus, matching core.js's SARA.on/SARA.emit shape ---- */
function makeFakeSara(elements) {
  const listeners = {};
  return {
    $: (id) => elements[id],
    on(name, fn) { (listeners[name] = listeners[name] || []).push(fn); },
    emit(name) {
      const args = Array.prototype.slice.call(arguments, 1);
      (listeners[name] || []).slice().forEach((fn) => fn.apply(null, args));
    },
    sendCommand() { return Promise.resolve({ ok: true }); },
    wake() {},
    fmtClockTime() { return '12:00 PM'; },
    reduceMotion: true,   // forces canAnimate() false everywhere -> deterministic, synchronous
                           // reveals in every test, with no need to fake requestAnimationFrame.
    sound: { sent() {}, received() {} }
  };
}

/* ---- loads a FRESH instance of chat.js's IIFE (fresh `streaming`/`preview`
   closure state) into its own vm context, per test. ---- */
function loadChatModule() {
  const scroller = { scrollTop: 0, scrollHeight: 0 };
  const log = makeEl('div');
  log.parentElement = scroller;

  const elements = {
    chatLog: log,
    chatInputField: Object.assign(makeEl('input'), { value: '' }),
    chatSend: makeEl('button'),
    chatMic: makeEl('button')
  };

  const SARA = makeFakeSara(elements);

  const sandbox = {
    window: { SARA },
    document: { hidden: false, createElement: makeEl },
    console,
    requestAnimationFrame: () => 0,
    cancelAnimationFrame() {},
    setTimeout,
    clearTimeout
  };
  vm.createContext(sandbox);
  vm.runInContext(chatSource, sandbox, { filename: 'chat.js' });

  return { SARA, log, elements };
}

/* ---- tiny assert/test runner ---- */
let passed = 0, failed = 0;
function assert(cond, msg) {
  if (cond) { passed++; console.log('  ok   - ' + msg); }
  else { failed++; console.error('  FAIL - ' + msg); }
}
function test(name, fn) {
  console.log('* ' + name);
  try { fn(); } catch (e) { failed++; console.error('  FAIL (threw) - ' + name + ': ' + (e && e.stack || e)); }
}

test('transcript_chunk x2 + matching transcript (finalize) does not duplicate the bubble', () => {
  const { SARA, log } = loadChatModule();
  SARA.emit('ev:transcript_chunk', 'sara', 'Hello');
  SARA.emit('ev:transcript_chunk', 'sara', 'world.');
  assert(log.children.length === 1, 'exactly one bubble exists after two streamed chunks');

  SARA.emit('ev:transcript', 'sara', 'Hello world.');
  assert(log.children.length === 1, 'finalize (ev:transcript for the same streamed reply) must not add a second bubble');

  const body = log.children[0].querySelector('.msg-text');
  assert(body && body.textContent === 'Hello world.', 'streamed text is the concatenation of both chunks');
});

test('findUrls(): duplicate URLs collapsed, result capped at 3 action buttons', () => {
  const { SARA, log } = loadChatModule();
  const text = 'Check http://a.com and http://b.com and http://a.com again, ' +
               'also http://c.com, http://d.com, http://e.com';
  SARA.emit('ev:transcript', 'sara', text);

  const bubble = log.children[log.children.length - 1];
  const buttons = bubble.querySelectorAll('.msg-action');
  assert(buttons.length === 3, 'at most 3 action buttons are rendered (findUrls out.slice(0, 3))');

  const titles = buttons.map((b) => b.title);
  assert(new Set(titles).size === titles.length, 'no duplicate URL appears twice among the action buttons');
  assert(titles.indexOf('http://a.com') === titles.lastIndexOf('http://a.com'), 'the repeated http://a.com only produced one button');
});

test('transcript_partial: dropPreview() removes the caption once the text goes empty', () => {
  const { SARA, log } = loadChatModule();
  SARA.emit('ev:transcript_partial', 'user', 'thinking about');
  assert(log.children.length === 1, 'a live caption bubble is created while partial text is non-empty');

  SARA.emit('ev:transcript_partial', 'user', '');
  assert(log.children.length === 0, 'dropPreview() removes the caption bubble instead of leaving it lingering');
});

test('ev:transcript (non-streaming) sara reply renders instantly, nothing left to animate', () => {
  const { SARA, log } = loadChatModule();
  const fullText = 'This is the complete reply already spoken by TTS.';
  SARA.emit('ev:transcript', 'sara', fullText);

  assert(log.children.length === 1, 'a single bubble is created for the reply');
  const bubble = log.children[0];
  const body = bubble.querySelector('.msg-text');
  assert(body && body.textContent === fullText, 'the full text is shown immediately, not partially revealed');
  assert(!bubble.classList.contains('typing'), 'no "typing" class is left pending -- nothing queued to animate');
});

console.log('\n' + passed + ' passed, ' + failed + ' failed');
process.exitCode = failed ? 1 : 0;