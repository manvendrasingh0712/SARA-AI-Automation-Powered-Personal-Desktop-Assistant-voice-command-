/* ==========================================================================
   apps.js -- Apps page (everything except the Music card, which is js/music.js):
   web-search bar, app tiles, quick-tool tiles (with the input modal), system chips.
   API calls: send_text_command + record_command_usage (SARA.sendCommand), run_action.
   Every tile just sends the same phrases the old GUI sent, so Sara's replies show up on the Chat page.
   To add/remove a tile, edit the OPEN / TOOLS / SYSTEM tables below -- nothing else needs to change.
   Styles: style/apps.css, style/overlays.css.  Markup: index.html #page-apps, #quickInputModal.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA, $ = SARA.$;

  const ICON = {
    notes: '<path d="M6 4h9l3 3v13H6z"/><path d="M9 9h6M9 13h6M9 17h4"/>',
    calc: '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 7h8M8 12h2M12 12h2M8 16h2M12 16h2"/>',
    calendar: '<rect x="4" y="5" width="16" height="15" rx="1"/><path d="M4 9h16M8 3v4M16 3v4"/>',
    folder: '<path d="M4 6h5l2 2h9v11H4z"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    music: '<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>',
    chat: '<path d="M4 20l1.5-4A8 8 0 1 1 8 18.5z"/>',
    pulse: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
    shot: '<path d="M4 8V5h3M17 5h3v3M20 16v3h-3M7 19H4v-3"/><circle cx="12" cy="12" r="3"/>',
    eye: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-5-5"/>',
    cloud: '<path d="M7 16a4 4 0 1 1 1.2-7.8A5 5 0 0 1 18 10a3.5 3.5 0 0 1-.5 7H7z"/>',
    play: '<path d="M8 5v14l11-7z"/>',
    pen: '<path d="M4 20h4L19 9l-4-4L4 16z"/>'
  };
  // OPEN: { label, icon, cmd }  -> sends the phrase | { label, icon, action } -> run_action(key)
  const OPEN = [
    { label: 'Notepad', icon: 'notes', cmd: 'open notepad' },
    { label: 'Calculator', icon: 'calc', cmd: 'open calculator' },
    { label: 'Calendar', icon: 'calendar', cmd: 'open calendar' },
    { label: 'Files', icon: 'folder', cmd: 'open file explorer' },
    { label: 'Chrome', icon: 'globe', action: 'open_chrome' },
    { label: 'Spotify', icon: 'music', cmd: 'open spotify' },
    { label: 'WhatsApp', icon: 'chat', cmd: 'open whatsapp' },
    { label: 'Task Manager', icon: 'pulse', cmd: 'open task manager' },
    { label: 'Screenshot', icon: 'shot', cmd: 'take a screenshot' },
    { label: 'Screen vision', icon: 'eye', cmd: 'screen vision' }
  ];
  // TOOLS: tiles that first ask for a value; {value} is substituted into tpl
  const TOOLS = [
    { label: 'Web search', icon: 'search', tpl: 'search for {value}', title: 'Web Search', ask: 'What do you want to search?', ph: 'e.g. best pizza near me' },
    { label: 'Calculate', icon: 'calc', tpl: 'calculate {value}', title: 'Quick Calculation', ask: 'What should I calculate?', ph: 'e.g. 45 * 12 + 8' },
    { label: 'Open website', icon: 'globe', tpl: 'open {value}', title: 'Open Website', ask: 'Which website? (full domain)', ph: 'e.g. google.com' },
    { label: 'Weather in…', icon: 'cloud', tpl: "what's the weather in {value}", title: 'Check Weather', ask: 'Which city?', ph: 'e.g. Mumbai' },
    { label: 'YouTube', icon: 'play', tpl: 'play {value} on youtube', title: 'Play on YouTube', ask: 'Song or video name?', ph: 'e.g. Arijit Singh songs' },
    { label: 'Take a note', icon: 'pen', tpl: 'take a note: {value}', title: 'Take a Note', ask: 'What should I write down?', ph: 'e.g. Buy milk tomorrow' }
  ];
  const SYSTEM = [
    { label: 'System status', cmd: 'system status' }, { label: 'System info', action: 'system_info' },
    { label: 'Disk usage', cmd: 'disk usage' }, { label: 'Uptime', cmd: 'system uptime' }, { label: 'My IP', cmd: "what's my ip address" },
    { label: 'Read my notes', cmd: 'read my notes' }, { label: 'Search AI news', action: 'search_web' },
    { label: 'Dark mode', cmd: 'dark mode' }, { label: 'Brightness up', cmd: 'increase brightness' }, { label: 'Max volume', cmd: 'max volume' },
    { label: 'Bluetooth on', cmd: 'bluetooth on' }, { label: 'Airplane mode', cmd: 'turn on airplane mode' }, { label: 'Empty recycle bin', cmd: 'empty the recycle bin' },
    { label: 'Lock PC', cmd: 'lock the pc', danger: true }, { label: 'Restart PC', cmd: 'restart the pc', danger: true }, { label: 'Shutdown PC', cmd: 'shutdown the pc', danger: true }
  ];

  function runItem(item) {
    SARA.sound.tap();
    if (item.action) { SARA.callApi('run_action', item.action); SARA.toast('ti-check', '#3FD8C4', item.label + '…'); return; }
    SARA.gotoPage('chat'); SARA.sendCommand(item.cmd);
  }
  function tile(item) {
    const d = document.createElement('div'); d.className = 'app-tile'; d.tabIndex = 0;
    d.innerHTML = '<svg viewBox="0 0 24 24">' + ICON[item.icon] + '</svg><span>' + SARA.escapeHtml(item.label) + '</span>';
    const go = () => (item.tpl ? openAsk(item) : runItem(item));
    d.addEventListener('click', go);
    d.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
    return d;
  }
  OPEN.forEach((i) => $('appsGrid').appendChild(tile(i)));
  TOOLS.forEach((i) => $('toolsGrid').appendChild(tile(i)));
  SYSTEM.forEach(function (i) {
    const c = document.createElement('span'); c.className = 'chip' + (i.danger ? ' danger' : ''); c.textContent = i.label;
    c.addEventListener('click', () => runItem(i)); $('sysChips').appendChild(c);
  });

  /* ---- quick-input modal ---- */
  let current = null;
  function openAsk(tool) {
    current = tool; $('qiTitle').textContent = tool.title; $('qiLabel').textContent = tool.ask;
    $('qiField').placeholder = tool.ph || ''; $('qiField').value = ''; SARA.openOverlay('quickInputModal');
    setTimeout(() => $('qiField').focus(), 60);
  }
  function submitAsk() {
    const v = $('qiField').value.trim(); if (!v || !current) return;
    SARA.closeOverlay('quickInputModal'); SARA.gotoPage('chat'); SARA.sendCommand(current.tpl.replace('{value}', v));
  }
  $('qiSubmit').addEventListener('click', submitAsk);
  $('qiCancel').addEventListener('click', () => SARA.closeOverlay('quickInputModal'));
  $('qiField').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); submitAsk(); } });

  /* ---- web search bar ---- */
  function doSearch() {
    const q = $('searchInput').value.trim(); if (!q) return;
    $('searchInput').value = ''; SARA.gotoPage('chat'); SARA.sendCommand('search the web for ' + q);
  }
  $('searchBtn').addEventListener('click', doSearch);
  $('searchInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') doSearch(); });
})();
