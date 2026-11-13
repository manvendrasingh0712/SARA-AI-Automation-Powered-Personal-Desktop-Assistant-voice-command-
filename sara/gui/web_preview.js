/* Browser-only guardrails for the isolated Cloudflare Pages preview. */
(function () {
  'use strict';
  const SARA = window.SARA;
  if (!SARA || !SARA.webPreview) return;

  const desktopOnly = 'Unavailable in the web preview. Use the SARA Windows desktop app.';

  document.documentElement.classList.add('web-preview');
  SARA.usingMock = false;

  function appendNote(parent, text, className) {
    if (!parent || parent.querySelector(':scope > .' + className)) return;
    const note = document.createElement('p');
    note.className = className;
    note.setAttribute('role', 'note');
    note.textContent = text;
    parent.appendChild(note);
  }

  function setGridNote(parent, text) {
    if (!parent) return;
    const current = parent.querySelector(':scope > .web-preview-empty');
    if (current && current.textContent === text) return;
    const note = document.createElement('p');
    note.className = 'web-preview-empty';
    note.setAttribute('role', 'note');
    note.textContent = text;
    parent.replaceChildren(note);
  }

  function setTextIfChanged(element, text) {
    if (element && element.textContent !== text) element.textContent = text;
  }

  function keepPreviewStatus() {
    document.querySelectorAll('[data-status-text]').forEach(function (el) {
      setTextIfChanged(el, 'web preview');
      el.style.removeProperty('color');
    });
  }

  function normalizeUnavailableStates() {
    const calendarUnavailable = 'Calendar data is unavailable in this web preview.';
    const noticeUnavailable = 'Notifications are unavailable in this web preview.';
    setTextIfChanged(document.getElementById('homeCaption'), 'browser preview');

    ['#tcNext', '#scheduleList', '#noticeList'].forEach(function (selector) {
      const root = document.querySelector(selector);
      if (!root) return;
      root.querySelectorAll('*').forEach(function (el) {
        if (el.children.length) return;
        const text = el.textContent.trim();
        if (text === 'Nothing scheduled today' || text === 'Nothing scheduled today.' || text === 'Nothing else today') {
          setTextIfChanged(el, calendarUnavailable);
        } else if (text === 'Nothing to flag right now.') {
          setTextIfChanged(el, noticeUnavailable);
        }
      });
    });

    const trackArtist = document.getElementById('trackArtist');
    if (trackArtist && trackArtist.textContent.trim() === 'desktop_backend_unavailable') {
      setTextIfChanged(trackArtist, 'Open SARA AI on Windows to control local playback.');
    }

    [
      {
        selector: '#notesList',
        title: 'Notes stay on your Windows device',
        body: 'Open SARA AI on Windows to view and edit your local notes.'
      },
      {
        selector: '#reminderGroups',
        title: 'Reminders stay on your Windows device',
        body: 'Open SARA AI on Windows to view and manage your reminders.'
      },
      {
        selector: '#routinesList',
        title: 'Automations stay on your Windows device',
        body: 'Open SARA AI on Windows to view and run your routines.'
      }
    ].forEach(function (state) {
      const root = document.querySelector(state.selector);
      const error = root && root.querySelector('.es.error');
      if (!error) return;
      error.classList.remove('error');
      error.setAttribute('role', 'note');
      setTextIfChanged(error.querySelector('.es-title'), state.title);
      setTextIfChanged(error.querySelector('.es-text'), state.body);
      const retry = error.querySelector('button');
      if (retry) retry.remove();
    });

    setGridNote(document.getElementById('appsGrid'), 'Installed apps are available in the SARA Windows desktop app.');
    setGridNote(document.getElementById('toolsGrid'), 'Quick tools require the SARA Windows desktop app.');
  }

  const previewStateObserver = new MutationObserver(normalizeUnavailableStates);
  [
    '#tcNext', '#scheduleList', '#noticeList', '#trackArtist', '#homeCaption',
    '#notesList', '#reminderGroups', '#routinesList', '#appsGrid', '#toolsGrid', '#sysChips'
  ].forEach(function (selector) {
    const target = document.querySelector(selector);
    if (target) previewStateObserver.observe(target, { childList: true, characterData: true, subtree: true });
  });

  function disable(control) {
    if (!control || control.dataset.previewDisabled === 'true') return;
    control.dataset.previewDisabled = 'true';
    control.setAttribute('aria-disabled', 'true');
    if ('disabled' in control) control.disabled = true;
    if (control.hasAttribute('tabindex')) control.setAttribute('tabindex', '-1');
    const oldTitle = control.getAttribute('title');
    control.setAttribute('title', oldTitle ? oldTitle + ' — ' + desktopOnly : desktopOnly);
  }

  function browserControl(control) {
    return !!(
      control.closest('#navBar, #setRail, #themeFilters, #themeGrid, #qualityRow, #accessBody') ||
      control.matches('#setSearch, #hueSlider, #hueReset, #autoTheme, [data-open-palette], .theme-card, [data-theme], [data-q]')
    );
  }

  const paletteList = document.querySelector('.pal-list');
  if (paletteList) {
    function guardPaletteActions() {
      paletteList.querySelectorAll('.pal-item').forEach(function (item) {
        const label = item.querySelector('span');
        const text = label ? label.textContent.trim() : '';
        const browserAction = ['Home', 'Chat', 'Notes & Reminders', 'Apps', 'Automation', 'Settings', 'Take the quick tour'].indexOf(text) >= 0 ||
          text.indexOf('Settings: ') === 0 || text.indexOf('Theme: ') === 0;
        if (!browserAction) disable(item);
      });
    }
    new MutationObserver(guardPaletteActions).observe(paletteList, { childList: true, subtree: true });
    guardPaletteActions();
  }

  function applyPreviewState() {
    const strip = document.getElementById('offlineStrip');
    if (strip) {
      strip.hidden = true;
    }

    document.querySelectorAll('[data-status-text]').forEach(function (el) { el.textContent = 'web preview'; });
    const caption = document.getElementById('homeCaption');
    if (caption) caption.textContent = 'browser preview';
    const hint = document.getElementById('homeHint');
    if (hint) hint.textContent = 'Live assistant controls are available in the Windows app.';
    const weather = document.getElementById('tcCond');
    if (weather) weather.textContent = 'Desktop data unavailable';
    const weatherSub = document.getElementById('tcSub');
    if (weatherSub) weatherSub.textContent = 'Open SARA on Windows for live data';
    const todayNext = document.getElementById('tcNext');
    if (todayNext) todayNext.textContent = 'Calendar data is unavailable online';
    const trackTitle = document.getElementById('trackTitle');
    if (trackTitle) trackTitle.textContent = 'Desktop media unavailable';
    const trackArtist = document.getElementById('trackArtist');
    if (trackArtist) trackArtist.textContent = 'Open SARA AI on Windows to control local playback.';
    const chatInput = document.getElementById('chatInputField');
    if (chatInput) chatInput.placeholder = 'Chat is unavailable in the web preview';
    const orb = document.getElementById('org');
    if (orb) orb.setAttribute('aria-label', 'Sara orb — browser preview; live assistant status is unavailable.');
    const miniTitle = document.getElementById('mpTitle');
    if (miniTitle) miniTitle.textContent = 'Media controls unavailable online';
    const miniArtist = document.getElementById('mpArtist');
    if (miniArtist) miniArtist.textContent = 'Use SARA AI on Windows for local playback.';

    const setupOverlay = document.getElementById('setupWizardOverlay');
    if (setupOverlay) {
      setupOverlay.hidden = true;
      setupOverlay.setAttribute('aria-hidden', 'true');
      setupOverlay.inert = true;
    }
    const setupModal = document.getElementById('setupModal');
    if (setupModal) setupModal.setAttribute('aria-hidden', 'true');

    document.querySelectorAll('#page-home [data-open-palette], #page-home #stage, #page-home #todayCard, #page-home #usageCard, #page-home #nextCard, #page-home #pauseBtn, #page-home #stopBtn, #page-home .qc-tile, #page-chat button, #page-chat input, #page-notes input, #page-notes button:not([data-tab]), #page-apps button, #page-apps input, #page-apps select, #page-automation button, #page-automation input, #page-automation select, #page-settings button, #page-settings input, #page-settings select, #page-settings textarea, #page-settings [role="switch"], #todaySheet button:not(#todayClose), #todaySheet input, #todaySheet select, #clockSheet button:not([data-theme]):not(#clockSheetClose):not(.cs-mode-btn), #clockSheet input, #audioModal button:not(#audioClose), #audioModal input, #audioModal select, #miniPlayer button, #miniPlayer input, #miniPlayer select, #quickInputModal button:not(#qiCancel), #quickInputModal input, #routineModal button:not(#rtCancel), #routineModal input').forEach(function (control) {
      if (!browserControl(control)) disable(control);
    });

    ['general', 'voice', 'intelligence', 'security', 'system'].forEach(function (name) {
      const group = document.getElementById('sg-' + name);
      if (!group) return;
      appendNote(group.querySelector('.set-group-head'), 'These controls need the SARA Windows desktop backend.', 'web-preview-detail');
    });
    appendNote(document.querySelector('#sg-appearance .set-group-head'), 'Appearance preferences in this preview apply only to this browser.', 'web-preview-detail');
    const apps = document.getElementById('appsGrid');
    if (apps) setGridNote(apps, 'Installed apps are available in the SARA Windows desktop app.');
    const tools = document.getElementById('toolsGrid');
    if (tools) setGridNote(tools, 'Quick tools require the SARA Windows desktop app.');
    const systemControls = document.getElementById('sysChips');
    if (systemControls) setGridNote(systemControls, 'System controls require the SARA Windows desktop app.');
    const modes = document.getElementById('modeChips');
    if (modes) setGridNote(modes, 'Modes require the Windows desktop app.');
    normalizeUnavailableStates();
  }

  document.addEventListener('click', function (event) {
    const control = event.target && event.target.closest('[data-preview-disabled="true"]');
    if (!control) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Enter' && document.querySelector('.pal-overlay.open .pal-item.sel[data-preview-disabled="true"]')) {
      event.preventDefault();
      event.stopImmediatePropagation();
      return;
    }
    const control = event.target && event.target.closest('[data-preview-disabled="true"]');
    if (!control || (event.key !== 'Enter' && event.key !== ' ')) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);

  SARA.onBoot(applyPreviewState);
  SARA.on('status', keepPreviewStatus);
  SARA.on('theme', keepPreviewStatus);
  SARA.on('assistant_active', keepPreviewStatus);
  SARA.on('api:unavailable', function (method) {
    if (method === 'get_notes' || method === 'get_notes_status') {
      setTimeout(normalizeUnavailableStates, 0);
    }
    if (method === 'get_reminders') {
      setTimeout(normalizeUnavailableStates, 0);
    }
  });

  applyPreviewState();
  if (SARA.bootOverlay) SARA.bootOverlay.preview();
})();
