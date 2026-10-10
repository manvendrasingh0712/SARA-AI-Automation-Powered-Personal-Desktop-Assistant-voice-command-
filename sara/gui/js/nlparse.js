/* ==========================================================================
   nlparse.js -- tiny natural-language date/time parser for reminders (English + Hinglish). Pure functions, no DOM, no dependencies.
   SaraNL.parseWhen("kal 5 baje dentist", now) -> { date:'2026-10-11', time:'17:00', text:'dentist', display:'Tomorrow · 5:00 PM', assumed:true, kinds:['day','time'] }
   Returns null when the text contains no date/time words. Understands:
     relative   "in 30 minutes", "after 1 hour", "in half an hour", "10 min mein", "2 ghante baad"
     day        today/aaj, tomorrow/kal, day after tomorrow/parso, tonight, next week, monday..sunday / somvar..ravivar (next occurrence)
     date       "12 nov", "nov 12", "25/12", "12-11-2026"
     time       "5pm", "5:30 pm", "17:45", "at 5", "5 baje", "5:30 baje"      dayparts: morning/subah, afternoon/dopahar, evening/sham, night/raat, noon, midnight
   Ambiguous "5 baje" is resolved to the next sensible AM/PM and flagged assumed:true so the UI can show it and offer a swap.
   ========================================================================== */
(function (root) {
  'use strict';
  const MONTHS = { jan: 0, feb: 1, mar: 2, apr: 3, may: 4, jun: 5, jul: 6, aug: 7, sep: 8, sept: 8, oct: 9, nov: 10, dec: 11 };
  const WD_FULL = { sunday: 0, monday: 1, tuesday: 2, wednesday: 3, thursday: 4, friday: 5, saturday: 6, ravivar: 0, somvar: 1, mangalvar: 2, budhvar: 3, guruvar: 4, brihaspativar: 4, shukravar: 5, shanivar: 6 };
  const WD_ABBR = { sun: 0, mon: 1, tue: 2, tues: 2, wed: 3, thu: 4, thur: 4, thurs: 4, fri: 5, sat: 6 };
  const NUMW = { a: 1, an: 1, one: 1, ek: 1, two: 2, do: 2, three: 3, teen: 3, half: 0.5, 'half an': 0.5, aadha: 0.5, adha: 0.5 };
  const pad = function (n) { return String(n).padStart(2, '0'); };
  const ymd = function (d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); };
  const midnight = function (d) { return new Date(d.getFullYear(), d.getMonth(), d.getDate()); };
  const plusDays = function (d, n) { return new Date(d.getFullYear(), d.getMonth(), d.getDate() + n); };
  const DAYN = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'], MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function label(dateStr, time, now) {
    const t = midnight(now), d = new Date(dateStr + 'T00:00:00'), diff = Math.round((d - t) / 86400000);
    const day = diff === 0 ? 'Today' : diff === 1 ? 'Tomorrow' : (diff > 1 && diff < 7 ? DAYN[d.getDay()] : DAYN[d.getDay()] + ', ' + d.getDate() + ' ' + MON[d.getMonth()]);
    const h = parseInt(time.slice(0, 2), 10), m = time.slice(3), h12 = h % 12 === 0 ? 12 : h % 12;
    return day + ' \u00b7 ' + h12 + ':' + m + ' ' + (h < 12 ? 'AM' : 'PM');
  }
  function clean(text) {
    let s = String(text).replace(/\s+/g, ' ').trim();
    s = s.replace(/^(?:please\s+)?(?:remind me(?:\s+to)?|set (?:a )?reminder(?:\s+to)?|add (?:a )?reminder(?:\s+to)?|reminder(?:\s+to)?|remember to|mujhe yaad dilana|yaad dila dena|yaad dilana|yaad dilao)\b[:\s,-]*/i, '');
    for (let i = 0; i < 3; i++) s = s.replace(/^(?:on|at|by|for|to|ko|pe|par|me|mein|tak)\s+/i, '').replace(/\s+(?:on|at|by|for|ko|pe|par|me|mein|tak)$/i, '');
    return s.replace(/^[\s,;:.-]+|[\s,;:.-]+$/g, '').replace(/\s{2,}/g, ' ');
  }
  function parseWhen(input, nowIn) {
    const original = String(input == null ? '' : input);
    if (!original.trim()) return null;
    const now = nowIn instanceof Date ? nowIn : new Date();
    let rest = original; const kinds = [];
    const take = function (re, fn) { const m = re.exec(rest); if (!m) return false; rest = rest.slice(0, m.index) + ' ' + rest.slice(m.index + m[0].length); fn(m); return true; };
    const unit = function (u) { u = u.toLowerCase(); return /^min/.test(u) ? 60000 : /^(h|ghant)/.test(u) ? 3600000 : /^(d|din)/.test(u) ? 86400000 : 604800000; };
    const val = function (v) { v = v.toLowerCase(); return v in NUMW ? NUMW[v] : parseFloat(v); };

    /* ---- relative durations ---- */
    let rel = null;
    take(/\b(?:in|after)\s+(\d+(?:\.\d+)?|half(?:\s+an)?|an?|one|two|three)\s*(minutes?|mins?|hours?|hrs?|hr|days?|weeks?)\b/i, function (m) { rel = val(m[1]) * unit(m[2]); });
    if (rel == null) take(/\b(\d+|ek|do|teen|aadha|adha)\s*(minutes?|mins?|ghante|ghanta|ghantey|hours?|din|days?|hafte|hafta|weeks?)\s+(?:mein|me|baad|bad)\b/i, function (m) { rel = val(m[1]) * unit(m[2]); });
    if (rel != null && rel > 0) {
      const at = new Date(now.getTime() + Math.round(rel)), date = ymd(at), time = pad(at.getHours()) + ':' + pad(at.getMinutes());
      return { date: date, time: time, text: clean(rest) || original.trim(), display: label(date, time, now), assumed: false, kinds: ['relative'] };
    }

    /* ---- dates ---- */
    const today = midnight(now); let day = null, explicitYear = false, part = null, hh = null, mm = 0, mer = null, nightTwelve = false;
    const mkDate = function (y, mo, d) { const x = new Date(y, mo, d); return (x.getMonth() === mo && x.getDate() === d) ? x : null; };
    if (take(/\b(\d{1,2})(?:st|nd|rd|th)?\s*(?:of\s+)?(jan|feb|mar|apr|may|jun|jul|aug|sept|sep|oct|nov|dec)[a-z]*\.?(?:\s*,?\s*(\d{4}))?\b/i, function (m) {
      const y = m[3] ? parseInt(m[3], 10) : now.getFullYear(); explicitYear = !!m[3]; day = mkDate(y, MONTHS[m[2].toLowerCase()], parseInt(m[1], 10)); })) kinds.push('date');
    else if (take(/\b(jan|feb|mar|apr|may|jun|jul|aug|sept|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?:\s*,?\s*(\d{4}))?\b/i, function (m) {
      const y = m[3] ? parseInt(m[3], 10) : now.getFullYear(); explicitYear = !!m[3]; day = mkDate(y, MONTHS[m[1].toLowerCase()], parseInt(m[2], 10)); })) kinds.push('date');
    else if (take(/\b(\d{1,2})[\/-](\d{1,2})(?:[\/-](\d{2,4}))?\b/, function (m) {
      let y = m[3] ? parseInt(m[3], 10) : now.getFullYear(); if (m[3] && m[3].length === 2) y += 2000; explicitYear = !!m[3]; day = mkDate(y, parseInt(m[2], 10) - 1, parseInt(m[1], 10)); })) kinds.push('date');
    if (day && !explicitYear && day < today) day = new Date(day.getFullYear() + 1, day.getMonth(), day.getDate());

    if (!day) {
      if (take(/\b(?:day after tomorrow|parso|parson)\b/i, function () { day = plusDays(today, 2); })) kinds.push('day');
      else if (take(/\b(?:tomorrow|tmrw|tmr|kal)\b/i, function () { day = plusDays(today, 1); })) kinds.push('day');
      else if (take(/\b(?:next week|agle hafte|agle hafta)\b/i, function () { day = plusDays(today, 7); })) kinds.push('day');
      else if (take(/\b(?:today|aaj)\b/i, function () { day = today; })) kinds.push('day');
      else if (take(/\b(?:(?:next|this|coming)\s+)?(sunday|monday|tuesday|wednesday|thursday|friday|saturday|ravivar|somvar|mangalvar|budhvar|guruvar|brihaspativar|shukravar|shanivar)\b/i, function (m) {
        let d = (WD_FULL[m[1].toLowerCase()] - today.getDay() + 7) % 7; if (d === 0) d = 7; day = plusDays(today, d); })) kinds.push('day');
      else if (take(/\b(?:next|this|on|coming)\s+(sun|mon|tues|tue|wed|thurs|thur|thu|fri|sat)\b/i, function (m) {
        let d = (WD_ABBR[m[1].toLowerCase()] - today.getDay() + 7) % 7; if (d === 0) d = 7; day = plusDays(today, d); })) kinds.push('day');
    }

    /* ---- dayparts ---- */
    take(/\b(tonight|morning|subah|subha|savere|afternoon|dopahar|dophar|evening|sham|shaam|night|raat|noon|midnight)\b/i, function (m) {
      const w = m[1].toLowerCase();
      part = /^(morning|subah|subha|savere)$/.test(w) ? 'morning' : /^(afternoon|dopahar|dophar)$/.test(w) ? 'afternoon' : /^(evening|sham|shaam)$/.test(w) ? 'evening' : w === 'noon' ? 'noon' : w === 'midnight' ? 'midnight' : 'night';
      if (w === 'tonight' && !day) { day = today; kinds.push('day'); }
    });

    /* ---- clock time ---- */
    const setT = function (h, m, ap) { hh = parseInt(h, 10); mm = m ? parseInt(m, 10) : 0; mer = ap ? ap.toLowerCase() : null; };
    let timed = take(/\b(?:at\s+)?(\d{1,2}):(\d{2})\s*([ap])\.?m\.?(?![a-z])/i, function (m) { setT(m[1], m[2], m[3]); });
    if (!timed) timed = take(/\b(?:at\s+)?(\d{1,2}):(\d{2})\b/, function (m) { setT(m[1], m[2], null); });
    if (!timed) timed = take(/\b(?:at\s+)?(\d{1,2})\s*([ap])\.?m\.?(?![a-z])/i, function (m) { setT(m[1], 0, m[2]); });
    if (!timed) timed = take(/\b(\d{1,2})(?:[:.](\d{2}))?\s*(?:baje|bje|o'?clock)\b/i, function (m) { setT(m[1], m[2], null); });
    if (!timed) timed = take(/\bat\s+(\d{1,2})\b/i, function (m) { setT(m[1], 0, null); });
    if (timed && (hh > 23 || mm > 59 || (mer && (hh < 1 || hh > 12)))) { return null; }
    if (timed) kinds.push('time');
    if (!day && !timed && !part) return null;

    /* ---- resolve ---- */
    const nowMin = now.getHours() * 60 + now.getMinutes();
    let assumed = false;
    if (timed) {
      if (mer) { if (mer === 'p' && hh < 12) hh += 12; else if (mer === 'a' && hh === 12) hh = 0; }
      else if (hh >= 13 || hh === 0) { /* 24-hour */ }
      else if (part) {
        if (part === 'morning') { if (hh === 12) hh = 0; }
        else if (part === 'afternoon' || part === 'evening') { if (hh < 12) hh += 12; }
        else if (part === 'night') { if (hh === 12) { hh = 0; nightTwelve = true; } else if (hh < 12) hh += 12; }
      } else {
        assumed = true;
        const h12 = hh % 12;
        if (!day || +day === +today) {
          const cands = [h12 * 60 + mm, (h12 + 12) * 60 + mm].filter(function (c) { return c > nowMin; });
          if (cands.length) hh = Math.floor(cands[0] / 60);
          else { day = plusDays(today, 1); hh = (hh >= 6 && hh <= 11) ? hh : h12 + 12; }
        } else hh = (hh >= 7 && hh <= 11) ? hh : (hh === 12 ? 12 : h12 + 12);
      }
    } else if (part) {
      const D = { morning: 9, afternoon: 14, evening: 18, night: 21, noon: 12, midnight: 0 }; hh = D[part]; mm = 0;
    } else { hh = 9; mm = 0; }
    if (!day) { day = today; if (hh * 60 + mm <= nowMin) day = plusDays(today, 1); }
    if (nightTwelve) day = plusDays(day, 1);
    const date = ymd(day), time = pad(hh) + ':' + pad(mm);
    return { date: date, time: time, text: clean(rest) || original.trim(), display: label(date, time, now), assumed: assumed, kinds: kinds };
  }
  const api = { parseWhen: parseWhen, label: label };
  if (typeof module !== 'undefined' && module.exports) module.exports = api; else root.SaraNL = api;
})(typeof window !== 'undefined' ? window : this);
