/* ==========================================================================
   main.js -- starts the app. Loaded LAST so every page file has registered its onBoot/every hooks.
   pywebview injects window.pywebview asynchronously, so we listen for 'pywebviewready' AND run a
   300ms safety net; SARA.boot() (js/core.js) is safe to call more than once.
   ========================================================================== */
window.addEventListener('pywebviewready', SARA.boot);
setTimeout(SARA.boot, 300);
