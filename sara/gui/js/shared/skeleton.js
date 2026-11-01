/* ==========================================================================
   skeleton.js -- shimmering placeholders while a list or card waits for its FIRST data (frontend only).
   SARA.skeleton.fill(el, n, kind, ms)   put n placeholder rows ('row' | 'line') into el; if they are still there after `ms` (default 10 s)
                                         they are replaced by a quiet "taking longer than usual" line, so a skeleton can never stay forever
   SARA.skeleton.clear(el)               remove the placeholders (a no-op when real content is already there)
   Wired here, through the API hooks in core.js, to the calls that have a visible wait:
     list_routines -> #routinesList (first load only: an already filled list is never blanked)     get_weather -> the Today card temperature
   The memory view calls fill() itself (js/settings/memory-view.js). Failed loads keep using the error cards of js/shared/empty-states.js.
   Not wired on purpose: the Apps grid (built synchronously, nothing to wait for) and the chat log (no history is fetched; it starts empty).
   Styles: .skel* in style/core/components.css, .sk-slow in style/shared/empty-states.css. Respects reduced motion (the shimmer is a plain background then).
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA; if (!SARA) return;
  const doc = document;
  const timers = new WeakMap();

  function hasSkel(el) { return !!(el && el.querySelector && el.querySelector('.skel')); }
  function drop(el) { const t = timers.get(el); if (t) { clearTimeout(t); timers.delete(el); } }

  function fill(el, n, kind, ms) {
    if (!el) return;
    drop(el);
    el.textContent = '';
    for (let i = 0; i < (n || 3); i++) {
      const s = doc.createElement('span'); s.className = 'skel ' + (kind === 'line' ? 'skel-line' : 'skel-row');
      if (kind === 'line') s.style.width = (45 + (i * 29) % 45) + '%';
      s.setAttribute('aria-hidden', 'true'); el.appendChild(s);
    }
    el.setAttribute('aria-busy', 'true');
    timers.set(el, setTimeout(function () {
      timers.delete(el);
      if (!hasSkel(el)) return;
      el.textContent = ''; el.removeAttribute('aria-busy');
      const p = doc.createElement('div'); p.className = 'sk-slow'; p.setAttribute('role', 'status');
      p.textContent = 'Still loading — this is taking longer than usual.'; el.appendChild(p);
    }, ms || 10000));
  }
  function clear(el) {
    if (!el) return;
    drop(el);
    if (hasSkel(el)) el.textContent = '';
    el.removeAttribute('aria-busy');
  }
  SARA.skeleton = { fill: fill, clear: clear };

  /* ---- routines list: only when it is empty (first load) ---- */
  if (SARA.api && SARA.api.before) {
    SARA.api.before('list_routines', function () {
      const el = doc.getElementById('routinesList'); if (el && !el.children.length) fill(el, 3, 'row');
    });
    SARA.api.after('list_routines', function () { clear(doc.getElementById('routinesList')); }, true);

    /* ---- Today card temperature: the "--°" placeholder becomes a shimmer until the weather arrives ---- */
    SARA.api.before('get_weather', function () {
      const t = doc.getElementById('tcTemp');
      if (!t || t.querySelector('.skel') || !/^\s*--/.test(t.textContent)) return;
      t.textContent = ''; const s = doc.createElement('span'); s.className = 'skel skel-inline skel-temp'; s.setAttribute('aria-hidden', 'true'); t.appendChild(s);
      timers.set(t, setTimeout(function () { timers.delete(t); if (t.querySelector('.skel')) t.textContent = '--°'; }, 10000));
    });
    SARA.api.after('get_weather', function () {
      const t = doc.getElementById('tcTemp'); if (!t) return;
      drop(t); if (t.querySelector('.skel')) t.textContent = '--°';     // renderWeather() overwrites this right after when data came
    }, true);
  }
})();
