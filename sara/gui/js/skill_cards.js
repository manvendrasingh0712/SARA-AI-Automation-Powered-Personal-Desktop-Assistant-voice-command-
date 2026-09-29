/*
 * skill_cards.js — renders the rich cards and suggestion chips that Sara's
 * skills emit (sara/skills/_framework.py):
 *
 *     ui_update("skill_card",  skillName, cardDict)
 *     ui_update("skill_chips", skillName, ["chip", ...])
 *
 * Dependency-free. Include this file in the GUI page, then, wherever the page
 * handles a ui_update event, add:
 *
 *     if (kind === "skill_card")  chatLog.appendChild(SaraSkillCards.render(arg2));
 *     if (kind === "skill_chips") chatLog.appendChild(SaraSkillCards.renderChips(arg2, txt => sendUserText(txt)));
 *
 * (`chatLog` / `sendUserText` are whatever your page uses to append a chat
 * element / send text as if typed.) Colours come from CSS variables with
 * fallbacks (--sara-accent, --sara-card-bg, --sara-text, --sara-muted).
 * Card types: streak, briefing, notes_answer, notes_summary, quiz, joke,
 * timeline, health, notes_status.
 */
(function (root) {
  "use strict";
  var SVG_NS = "http://www.w3.org/2000/svg";

  var CSS = [
    ".sk-card{background:var(--sara-card-bg,#1e1e2a);color:var(--sara-text,#eee);border-radius:14px;padding:14px 16px;margin:8px 0;max-width:420px;font:14px/1.4 system-ui,sans-serif;box-shadow:0 2px 10px rgba(0,0,0,.25);animation:sk-in .35s ease both}",
    "@keyframes sk-in{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}",
    ".sk-title{font-weight:600;margin-bottom:8px}.sk-muted{color:var(--sara-muted,#9aa)}",
    ".sk-row{display:flex;align-items:center;gap:10px;margin:6px 0}",
    ".sk-week{display:flex;gap:6px;margin-top:8px}.sk-day{width:22px;height:22px;border-radius:6px;background:rgba(255,255,255,.1)}.sk-day.on{background:var(--sara-accent,#ff8a3d)}",
    ".sk-chips{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0}.sk-chip{border:1px solid var(--sara-accent,#7c9cff);background:transparent;color:var(--sara-accent,#7c9cff);border-radius:999px;padding:5px 12px;cursor:pointer;font:13px system-ui}.sk-chip:hover{background:var(--sara-accent,#7c9cff);color:#111}",
    ".sk-src{display:inline-block;background:rgba(255,255,255,.1);border-radius:8px;padding:2px 8px;margin:2px 4px 2px 0;font-size:12px}",
    ".sk-dot{width:10px;height:10px;border-radius:50%;flex:none}.sk-ok{background:#3ecf6e}.sk-warn{background:#f5c542}.sk-fail{background:#ef5350}.sk-skip,.sk-unknown{background:#8894a5}",
    ".sk-flip{cursor:pointer;background:rgba(255,255,255,.07);border-radius:10px;padding:10px;margin:6px 0}",
    ".sk-punch{transition:opacity .6s ease;opacity:0;margin-top:8px;font-weight:600}.sk-punch.show{opacity:1}"
  ].join("\n");

  function ensureStyle() {
    var d = root.document;
    if (!d || d.__skStyle) return;
    var s = d.createElement("style");
    s.textContent = CSS;
    (d.head || d.body).appendChild(s);
    d.__skStyle = true;
  }

  function el(tag, cls, text) {
    var e = root.document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }
  function add(parent) {
    for (var i = 1; i < arguments.length; i++) if (arguments[i]) parent.appendChild(arguments[i]);
    return parent;
  }

  function ring(progress, label) {
    var size = 64, r = 26, c = 2 * Math.PI * r;
    var svg = root.document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("width", size); svg.setAttribute("height", size);
    svg.setAttribute("viewBox", "0 0 64 64");
    [["rgba(255,255,255,.12)", c], ["var(--sara-accent,#ff8a3d)", c * (1 - Math.max(0, Math.min(1, progress)))]].forEach(function (p) {
      var circle = root.document.createElementNS(SVG_NS, "circle");
      circle.setAttribute("cx", 32); circle.setAttribute("cy", 32); circle.setAttribute("r", r);
      circle.setAttribute("fill", "none"); circle.setAttribute("stroke", p[0]); circle.setAttribute("stroke-width", 6);
      circle.setAttribute("stroke-dasharray", c); circle.setAttribute("stroke-dashoffset", p[1]);
      circle.setAttribute("transform", "rotate(-90 32 32)");
      svg.appendChild(circle);
    });
    var t = root.document.createElementNS(SVG_NS, "text");
    t.setAttribute("x", 32); t.setAttribute("y", 37); t.setAttribute("text-anchor", "middle");
    t.setAttribute("fill", "currentColor"); t.setAttribute("font-size", 16);
    t.textContent = String(label);
    svg.appendChild(t);
    return svg;
  }

  var R = {};

  R.streak = function (c) {
    var card = el("div", "sk-card");
    add(card, el("div", "sk-title", "🔥 " + (c.title || "Talk streak")));
    var row = el("div", "sk-row");
    add(row, ring(c.progress || 0, c.count),
      add(el("div"),
        el("div", null, c.count + " day" + (c.count === 1 ? "" : "s") + " in a row"),
        el("div", "sk-muted", "Longest: " + c.longest + (c.next_milestone ? "  ·  Next: " + c.next_milestone + " (" + c.days_to_next + " to go)" : ""))));
    add(card, row);
    var week = el("div", "sk-week");
    (c.week || []).forEach(function (on) { add(week, el("div", "sk-day" + (on ? " on" : ""))); });
    add(card, week);
    if (c.badges && c.badges.length) add(card, el("div", "sk-muted", "Badges: " + c.badges.join(" · ")));
    return card;
  };

  R.briefing = function (c) {
    var card = el("div", "sk-card");
    if (c.greeting) add(card, el("div", "sk-title", c.greeting));
    if (c.weather) add(card, el("div", "sk-row", "☀️ " + c.weather));
    if (c.exam) add(card, el("div", "sk-row", "🎯 " + c.exam.name + ": " + (c.exam.days === 0 ? "today!" : c.exam.days + " days left")));
    (c.reminders || []).forEach(function (r) {
      add(card, el("div", "sk-row", "⏰ " + (r.time ? r.time + " — " : "") + r.text));
    });
    (c.news || []).forEach(function (n) { add(card, el("div", "sk-row sk-muted", "📰 " + n)); });
    return card;
  };

  function sources(list) {
    var box = el("div");
    (list || []).forEach(function (s) { add(box, el("span", "sk-src", "📄 " + (typeof s === "string" ? s : s.label))); });
    return box;
  }

  R.notes_answer = function (c) {
    var card = el("div", "sk-card");
    add(card, el("div", "sk-muted", c.question), el("div", "sk-row", c.answer), sources(c.sources));
    return card;
  };
  R.notes_summary = function (c) {
    var card = el("div", "sk-card");
    add(card, el("div", "sk-title", "📝 " + c.topic), el("div", "sk-row", c.summary), sources(c.sources));
    return card;
  };

  R.quiz = function (c) {
    var card = el("div", "sk-card");
    add(card, el("div", "sk-title", "🧠 Quiz: " + c.topic));
    (c.cards || []).forEach(function (qa, i) {
      var box = el("div", "sk-flip", (i + 1) + ". " + qa.question);
      var shown = false;
      box.addEventListener("click", function () {
        shown = !shown;
        box.textContent = shown ? "✔ " + qa.answer : (i + 1) + ". " + qa.question;
      });
      add(card, box);
    });
    add(card, el("div", "sk-muted", "Tap a question to flip it."));
    return card;
  };

  R.joke = function (c) {
    var card = el("div", "sk-card");
    var punch = el("div", "sk-punch", c.punchline || "");
    add(card, el("div", null, "😄 " + c.setup), punch);
    if (c.punchline) root.setTimeout(function () { punch.className = "sk-punch show"; }, c.reveal_ms || 1500);
    else punch.className = "sk-punch show";
    return card;
  };

  R.timeline = function (c) {
    var card = el("div", "sk-card");
    add(card, el("div", "sk-title", c.title || "Recent actions"));
    (c.items || []).forEach(function (it) {
      var row = el("div", "sk-row");
      add(row, el("span", "sk-dot sk-" + (it.state === "ok" ? "ok" : it.state === "fail" ? "fail" : it.state === "skip" ? "skip" : "unknown")),
        el("span", null, it.text), el("span", "sk-muted", it.when || ""));
      add(card, row);
    });
    return card;
  };

  R.health = function (c) {
    var card = el("div", "sk-card");
    add(card, el("div", "sk-title", (c.overall === "ok" ? "✅ " : c.overall === "warn" ? "⚠️ " : "❌ ") + (c.title || "System health")));
    (c.rows || []).forEach(function (r) {
      var row = el("div", "sk-row");
      add(row, el("span", "sk-dot sk-" + (r.severity || "unknown")), el("span", null, r.name),
        el("span", "sk-muted", r.latency_ms + " ms"));
      add(card, row);
      if (r.severity !== "ok" && (r.detail || r.fix)) add(card, el("div", "sk-muted", (r.detail || "") + (r.fix ? " → " + r.fix : "")));
    });
    return card;
  };

  R.notes_status = function (c) {
    var card = el("div", "sk-card");
    add(card, el("div", "sk-title", "📚 Notes index"),
      el("div", null, c.count + " notes indexed" + (c.queued ? "  ·  " + c.queued + " queued" : "")),
      el("div", "sk-muted", "Last synced: " + (c.last_synced || "never")));
    return card;
  };

  function render(payload) {
    ensureStyle();
    var fn = payload && R[payload.type];
    if (!fn) return el("div");  // unknown card type: render nothing
    try { return fn(payload); } catch (e) { return el("div"); }
  }

  function renderChips(chips, onPick) {
    ensureStyle();
    var box = el("div", "sk-chips");
    (chips || []).forEach(function (text) {
      var b = el("button", "sk-chip", text);
      b.addEventListener("click", function () { if (onPick) onPick(text); });
      box.appendChild(b);
    });
    return box;
  }

  var api = { render: render, renderChips: renderChips, types: Object.keys(R) };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.SaraSkillCards = api;
})(typeof window !== "undefined" ? window : globalThis);
