/* ==========================================================================
   prefs.js -- ONE source of truth for UI preferences: the backend DB (get_ui_prefs / set_ui_pref).
   localStorage is only a CACHE so the first paint is right before the backend answers (no theme flash).
   Keys used today: 'theme', 'accent_hue', 'clock_theme'.  (Display name is backend-owned via
   get_display_name/set_display_name; js/home.js keeps only a cache of it.)
   API: SARA.prefs.get(key, fallback) -> cached value | SARA.prefs.set(key, value) -> saves cache + backend
        SARA.prefs.load() -> pulls the backend values (runs on every boot); emits 'pref' (key, value) on change
   Migration: a value that exists only in the old localStorage keys is pushed to the backend once.
   Backend: sara/gui/app/ui_prefs.py.  Loaded right after js/core.js.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA;
  const CACHE = 'sara_pref_', LEGACY = { clock_theme: 'sara_clock_theme' };
  const cache = {};
  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }

  ['theme', 'accent_hue', 'clock_theme'].forEach(function (k) {
    const v = lsGet(CACHE + k); if (v !== null) cache[k] = v;
    else if (LEGACY[k] && lsGet(LEGACY[k]) !== null) cache[k] = lsGet(LEGACY[k]);
  });

  SARA.prefs = {
    get: function (k, fallback) { return (k in cache && cache[k] !== '') ? cache[k] : fallback; },
    set: function (k, v) {
      v = (v === null || v === undefined) ? '' : String(v);
      cache[k] = v; lsSet(CACHE + k, v);
      SARA.emit('pref', k, v);
      return SARA.callApi('set_ui_pref', k, v);
    },
    load: async function () {
      const res = await SARA.callApi('get_ui_prefs');
      const data = (res && res.ok && res.data) || null; if (!data) return;
      Object.keys(data).forEach(function (k) {
        const remote = data[k];
        if (remote === null || remote === undefined || remote === '') {
          if (cache[k]) SARA.callApi('set_ui_pref', k, cache[k]);   // migrate old localStorage-only value up to the backend
          return;
        }
        if (cache[k] !== remote) { cache[k] = remote; lsSet(CACHE + k, remote); SARA.emit('pref', k, remote); }
      });
    }
  };
  SARA.onBoot(function () { SARA.prefs.load(); });
})();
