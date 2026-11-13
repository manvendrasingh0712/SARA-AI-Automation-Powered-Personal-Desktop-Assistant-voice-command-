/* Browser-only guardrails for the isolated Cloudflare Pages preview. */
(function () {
  'use strict';
  const SARA = window.SARA;
  if (!SARA || !SARA.webPreview) return;

  const desktopOnly = 'Unavailable in the web preview. Use the SARA Windows desktop app.';
  const pageNotes = {
    home: 'Browser preview: the clock, navigation and themes work here. Live assistant status, microphone, calendar and device controls need the Windows app.',
    chat: 'Chat requires SARA’s local Python service and is disabled in this preview.',
    notes: 'Notes and reminders are stored by the local app. They are not available or editable on this public preview.',
    apps: 'Local app launching, media controls, web search and quick tools require the Windows app.',
    automation: 'Modes and routines require the local Windows backend and are disabled in this preview.',
    settings: 'Theme, theme filters, accent colour and visual quality are browser-local. Device, voice and system settings require the Windows app.'
  };

  document.documentElement.classList.add('web-preview');
  SARA.usingMock = false;
  if (SARA.bootOverlay) SARA.bootOverlay.preview();

  function appendNote(parent, text, className) {
    if (!parent || parent.querySelector(':scope > .' + className)) return;
    const note = document.createElement('p');
    note.className = className;
    note.setAttribute('role', 'note');
    note.textContent = text;
    parent.appendChild(note);
  }

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
      control.closest('#navBar, #setRail, #themeFilters, #themeGrid, #qualityRow') ||
      control.matches('#setSearch, #hueSlider, #hueReset, .theme-card, [data-theme], [data-q]')
    );
  }

  function applyPreviewState() {
    const strip = document.getElementById('offlineStrip');
    if (strip) {
      strip.textContent = 'Web preview — desktop backend unavailable. Navigation and browser-local appearance controls remain available.';
      strip.hidden = false;
    }

    document.querySelectorAll('[data-status-text]').forEach(function (el) { el.textContent = 'web preview'; });
    const caption = document.getElementById('homeCaption');
    if (caption) caption.textContent = 'browser preview';
    const hint = document.getElementById('homeHint');
    if (hint) hint.textContent = 'Explore the six pages using the navigation below.';
    const weather = document.getElementById('tcCond');
    if (weather) weather.textContent = 'Desktop data unavailable';
    const weatherSub = document.getElementById('tcSub');
    if (weatherSub) weatherSub.textContent = 'Open SARA on Windows for live data';
    const todayNext = document.getElementById('tcNext');
    if (todayNext) todayNext.textContent = 'Calendar data is unavailable online';
    const trackTitle = document.getElementById('trackTitle');
    if (trackTitle) trackTitle.textContent = 'Desktop media unavailable';
    const trackArtist = document.getElementById('trackArtist');
    if (trackArtist) trackArtist.textContent = 'Open SARA on Windows to control local playback';
    const chatInput = document.getElementById('chatInputField');
    if (chatInput) chatInput.placeholder = 'Chat is unavailable in the web preview';

    Object.keys(pageNotes).forEach(function (name) {
      const page = document.getElementById('page-' + name);
      const header = page && page.querySelector('.page-header');
      if (header) appendNote(header, pageNotes[name], 'web-preview-note');
    });

    document.querySelectorAll('#page-home [data-open-palette], #page-home #stage, #page-home #todayCard, #page-home #usageCard, #page-home #nextCard, #page-home #pauseBtn, #page-home #stopBtn, #page-home .qc-tile, #page-chat button, #page-chat input, #page-notes input, #page-notes button:not([data-tab]), #page-apps button, #page-apps input, #page-apps select, #page-automation button, #page-automation input, #page-automation select, #page-settings button, #page-settings input, #page-settings select, #page-settings textarea, #page-settings [role="switch"], #clockSheet button:not([data-theme]), #clockSheet input, #quickInputModal button:not(#qiCancel), #quickInputModal input, #routineModal button:not(#rtCancel), #routineModal input').forEach(function (control) {
      if (!browserControl(control)) disable(control);
    });

    ['general', 'voice', 'skills', 'security', 'system'].forEach(function (name) {
      const group = document.getElementById('sg-' + name);
      if (!group) return;
      const grid = group.querySelector('.set-group-grid');
      if (grid) grid.hidden = true;
      appendNote(group.querySelector('.set-group-head'), 'These settings need the SARA Windows desktop backend.', 'web-preview-detail');
    });

    const notes = document.getElementById('notesList');
    if (notes) notes.textContent = 'Notes require the local SARA desktop app.';
    const reminders = document.getElementById('reminderGroups');
    if (reminders) reminders.textContent = 'Reminders require the local SARA desktop app.';
    const apps = document.getElementById('appsGrid');
    if (apps) apps.textContent = 'Installed apps are available in the Windows desktop app.';
    const tools = document.getElementById('toolsGrid');
    if (tools) tools.textContent = 'Quick tools require the Windows desktop app.';
    const modes = document.getElementById('modeChips');
    if (modes) modes.textContent = 'Modes require the Windows desktop app.';
    const routines = document.getElementById('routinesList');
    if (routines) routines.textContent = 'Routines require the Windows desktop app.';
  }

  document.addEventListener('click', function (event) {
    const control = event.target && event.target.closest('[data-preview-disabled="true"]');
    if (!control) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);
  document.addEventListener('keydown', function (event) {
    const control = event.target && event.target.closest('[data-preview-disabled="true"]');
    if (!control || (event.key !== 'Enter' && event.key !== ' ')) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);

  SARA.onBoot(applyPreviewState);
  SARA.on('api:unavailable', function (method) {
    if (method === 'get_notes' || method === 'get_notes_status') {
      setTimeout(function () { const el = document.getElementById('notesList'); if (el) el.textContent = 'Notes require the local SARA desktop app.'; }, 0);
    }
    if (method === 'get_reminders') {
      setTimeout(function () { const el = document.getElementById('reminderGroups'); if (el) el.textContent = 'Reminders require the local SARA desktop app.'; }, 0);
    }
  });

  applyPreviewState();
})();
