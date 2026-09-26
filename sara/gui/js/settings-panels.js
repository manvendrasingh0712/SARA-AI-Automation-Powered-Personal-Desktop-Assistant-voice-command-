/* ==========================================================================
   settings-panels.js -- accordion open/close for every collapsible Settings section, plus the
   "System" section: backend connection, CPU/RAM/disk/network, window controls, re-run setup check.
   API calls: get_system_stats (polled every 3.5s only while Settings is open), minimize_window,
   toggle_maximize, close_window.  Opens the wizard via SARA.showSetupWizard (js/setup-wizard.js).
   Styles: style/settings.css.  Markup: index.html #page-settings (System accordion).
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA, $ = SARA.$;

  document.querySelectorAll('[data-acc] .acc-header').forEach(function (h) {
    h.addEventListener('click', function () { SARA.sound.tap(); h.closest('[data-acc]').classList.toggle('open'); });
  });

  function renderConnection() {
    const el = $('sysBackend'), on = SARA.isConnected();
    el.textContent = on ? 'Connected' : 'Preview mode (not connected)'; el.className = on ? 'ok' : 'bad';
  }
  async function pollStats() {
    if (SARA.current !== 'settings') return;
    const s = await SARA.callApi('get_system_stats'); if (!s) return;
    $('sysCpuRam').textContent = Math.round(s.cpu) + '% · ' + Math.round(s.ram) + '%';
    $('sysDisk').textContent = s.disk_total_gb ? s.disk_used_gb + ' / ' + s.disk_total_gb + ' GB (' + Math.round(s.disk) + '%)' : Math.round(s.disk) + '%';
    $('sysNet').textContent = '↓ ' + s.net_down_mbps + ' Mb/s · ↑ ' + s.net_up_mbps + ' Mb/s';
  }
  SARA.on('connection', renderConnection);
  SARA.on('page', function (p) { if (p === 'settings') { renderConnection(); pollStats(); } });
  SARA.every(3500, pollStats);
  renderConnection();

  $('btnMin').addEventListener('click', () => SARA.callApi('minimize_window'));
  $('btnMax').addEventListener('click', () => SARA.callApi('toggle_maximize'));
  $('btnClose').addEventListener('click', function () { if (confirm('Close SARA?')) SARA.callApi('close_window'); });
  $('btnOpenDataFolder').addEventListener('click', () => SARA.callApi('open_data_folder'));
  $('btnWizard').addEventListener('click', () => SARA.showSetupWizard && SARA.showSetupWizard());
})();
