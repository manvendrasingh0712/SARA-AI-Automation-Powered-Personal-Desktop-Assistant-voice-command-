/* ==========================================================================
   mock.js -- fake backend used ONLY when window.pywebview is missing (double-clicking index.html
   in a browser). Returns the SAME shapes as the real Api mixins in sara/gui/app/*.py, so every page
   renders and every button works for preview. Called by SARA.callApi() in js/core.js.
   Covers all 57 Api methods (see the audit table). Never used inside the real app once the bridge binds.
   ========================================================================== */
(function () {
  'use strict';
  const SARA = window.SARA;
  const today = SARA.todayStr(0), tomorrow = SARA.todayStr(1);
  const S = {
    name: 'Friend', active: true, wizardSeen: true, mode: 'normal', prefs: {},
    reminders: [
      { id: 1, date: today, time: '18:30', text: 'Gym — leg day', done: false },
      { id: 2, date: today, time: '20:00', text: 'Call Arjun about the project', done: false },
      { id: 3, date: tomorrow, time: '09:00', text: 'Submit assignment', done: false }],
    reminderId: 10,
    notes: [{ id: 1, text: 'Project ideas for SARA v2\nAdd a barge-in mode, revisit the RAG chunking strategy', timestamp: new Date(Date.now() - 7200000).toISOString() },
            { id: 2, text: 'Grocery list — milk, eggs, atta', timestamp: new Date(Date.now() - 86400000).toISOString() }],
    routines: [
      { name: 'morning', label: 'Morning routine', trigger_time: '07:00', steps: [{ type: 'simple_action', key: 'max_volume' }, { type: 'simple_action', key: 'open_downloads' }] },
      { name: 'winddown', label: 'Wind-down', trigger_time: null, steps: [{ type: 'simple_action', key: 'dark_mode' }] }],
    tracks: [{ title: 'Weightless', artist: 'Marconi Union', dur: 490 }, { title: 'An Ending (Ascent)', artist: 'Brian Eno', dur: 272 }, { title: 'Experience', artist: 'Ludovico Einaudi', dur: 315 }],
    ti: 0, playing: true, active: true, pos: 112, posTs: Date.now(), shuffle: false, repeat: 'none',
    skills: [
      { name: 'daily_briefing', intent: 'daily_briefing', description: 'Weather + reminders + a headline, spoken as one summary', enabled: true, status: 'loaded' },
      { name: 'notes_qa', intent: 'notes_qa', description: 'Answers questions from your notes via RAG', enabled: true, status: 'loaded' },
      { name: 'broken_skill', intent: null, description: null, enabled: true, status: 'error', error: 'missing INTENT_NAME' }]
  };
  const ok = (o) => Object.assign({ ok: true }, o || {});
  const ev = (kind, ...args) => window.saraEvent({ kind, args });
  function curPos() { return S.playing ? Math.min(S.tracks[S.ti].dur, S.pos + (Date.now() - S.posTs) / 1000) : S.pos; }
  function mediaStatus() {
    if (!S.active) return ok({ active: false });
    const t = S.tracks[S.ti];
    return ok({ active: true, status: S.playing ? 'playing' : 'paused', title: t.title, artist: t.artist, album: '', app: 'Preview player',
      track_id: 't' + S.ti, position_sec: curPos(), duration_sec: t.dur, playback_rate: 1, timeline_updated_at: Date.now() / 1000,
      shuffle: S.shuffle, shuffle_supported: true, repeat: S.repeat, caps: { can_next: true, can_prev: true, can_seek: true, can_shuffle: true, can_repeat: true } });
  }
  function setPos(p) { S.pos = p; S.posTs = Date.now(); }

  SARA.mockApi = function (name, a) {
    switch (name) {
      case 'get_system_stats': return { cpu: Math.round(10 + Math.random() * 40), ram: Math.round(30 + Math.random() * 30), disk: 52, disk_total_gb: 512, disk_used_gb: 198, net_down_mbps: +(Math.random() * 20).toFixed(1), net_up_mbps: +(Math.random() * 4).toFixed(1) };
      case 'get_memory_stats': return ok({ pct: 34, exchange_count: 170, max_exchanges: 500, approx_mb: 2.1 });
      case 'get_proactive_stats': return ok({ total: 3, by_trigger: { battery: 1, reminder: 1, idle_break: 1 }, recent: [
        { trigger: 'battery', message: 'Battery at 18% — plug in soon.', timestamp: new Date(Date.now() - 1800000).toISOString() },
        { trigger: 'reminder', message: 'Gym — leg day at 6:30 PM.', timestamp: new Date(Date.now() - 7200000).toISOString() }] });
      case 'get_share_card_data': return ok({ streak: 7, total_messages: 1904, days_used: 142, proactive_nudges: 3, sara_name: 'Sara' });
      case 'get_weather': return ok({ data: { ok: true, city: 'Ajmer', temp: 31, temp_max: 35, temp_min: 26, humidity: 40, condition: 'Clear', description: 'Clear Sky', aqi_label: 'Fair' } });
      case 'wake_now':
        ev('status', 'waking'); setTimeout(() => ev('status', 'listening'), 400); setTimeout(() => ev('status', 'sleeping'), 4000); return ok();
      case 'stop_sara': ev('status', 'sleeping'); return ok();
      case 'send_text_command':
        ev('transcript', 'user', a[0]); ev('status', 'thinking');
        setTimeout(() => { ev('status', 'speaking'); ev('transcript', 'sara', '(preview mode) Got it: ' + a[0]); setTimeout(() => ev('status', 'sleeping'), 1500); }, 700);
        return ok({ queued: 0 });
      case 'record_command_usage': return ok();
      case 'minimize_window': case 'toggle_maximize': case 'close_window': return ok();
      case 'run_action': if (a[0] === 'play_music') { S.active = true; S.playing = true; setPos(curPos()); return ok(); } return ok();
      case 'toggle_wifi': return ok({ message: 'Wi-Fi toggled (preview).' });
      case 'set_mute': case 'set_focus_mode': case 'update_setting': case 'set_mic_sensitivity': case 'set_speech_speed': case 'set_language':
        S.prefs[name] = a; return ok();
      case 'set_display_name': S.name = a[0]; return ok({ name: a[0] });
      case 'get_display_name': return ok({ name: S.name });
      case 'set_assistant_active': S.active = !!a[0]; return ok();
      case 'get_assistant_active': return ok({ active: S.active });
      case 'get_ui_settings': return ok({ data: {} });
      case 'get_notes_status': return ok({ enabled: true, count: 128, last_synced: new Date(Date.now() - 3600000).toISOString() });
      case 'get_skills_list': return ok({ data: S.skills });
      case 'set_skill_enabled': S.skills.forEach(s => { if (s.name === a[0]) s.enabled = !!a[1]; }); return ok();
      case 'get_notes': return ok({ data: S.notes });
      case 'save_note': S.notes.unshift({ id: Date.now(), text: a[0], timestamp: new Date().toISOString() }); return ok({ message: 'Note saved.', id: null });
      case 'export_memory': return ok({ path: 'memory_export.json' });
      case 'get_reminders': return ok({ data: S.reminders });
      case 'add_reminder': S.reminders.push({ id: ++S.reminderId, date: a[0], time: a[1], text: a[2], done: false }); return ok({ id: S.reminderId });
      case 'delete_reminder': S.reminders = S.reminders.filter(r => r.id !== a[0]); return ok();
      case 'toggle_reminder': S.reminders.forEach(r => { if (r.id === a[0]) r.done = !r.done; }); return ok();
      case 'get_calendar_status': return ok({ data: { connected: true, email: 'preview@example.com' } });
      case 'get_today_calendar_events': { const d = new Date(); d.setHours(d.getHours() + 2, 0, 0, 0); return ok({ data: [{ start: d.toISOString(), summary: 'Project sync' }] }); }
      case 'list_routines': return ok({ data: S.routines });
      case 'get_routine': { const r = S.routines.find(x => x.name === a[0]); return r ? ok({ data: r }) : { ok: false, data: null, error: 'Routine not found.' }; }
      case 'save_routine': { const d = { name: a[0], label: a[1] || a[0], steps: a[2], trigger_time: a[3] || null }; const i = S.routines.findIndex(x => x.name === a[0]); if (i >= 0) S.routines[i] = d; else S.routines.push(d); return ok(); }
      case 'delete_routine': S.routines = S.routines.filter(r => r.name !== a[0]); return ok();
      case 'run_routine_now': setTimeout(() => ev('transcript', 'sara', '(preview mode) Ran routine "' + a[0] + '".'), 500); return ok();
      case 'get_analytics_dashboard': {
        const trend = []; for (let i = 13; i >= 0; i--) trend.push({ date: SARA.todayStr(-i), count: Math.round(Math.random() * 30) });
        return ok({ data: { total_commands: 1904, top_commands: [{ name: 'set a reminder', count: 88 }, { name: 'open chrome', count: 61 }, { name: 'system status', count: 40 }], daily_trend: trend, conversation_stats: {}, proactive_stats: {} } });
      }
      case 'get_modes_status': return ok({ active_mode: S.mode, modes: ['normal', 'study', 'work', 'gaming', 'home'] });
      case 'apply_mode': S.mode = String(a[0]).toLowerCase(); return ok({ active_mode: S.mode, message: 'Switched to ' + S.mode + ' mode (preview).' });
      case 'get_setup_wizard_seen': return { seen: S.wizardSeen };
      case 'mark_setup_wizard_seen': S.wizardSeen = true; return ok();
      case 'check_setup_status': return { llm_backend: 'ollama', gemini_key_set: null, ollama_installed: true, ollama_running: true, llm_model_pulled: true, llm_model_name: 'qwen3:4b', rag_enabled: true, embedding_model_pulled: true, embedding_model_name: 'gemini-embedding-001', kokoro_model_present: true, kokoro_voices_present: true, all_ready: true };
      case 'run_setup_fix': return ok({ started: a[0] });
      case 'get_media_status': return mediaStatus();
      case 'toggle_music_playback': setPos(curPos()); S.active = true; S.playing = !!a[0]; return ok();
      case 'stop_music': S.active = false; S.playing = false; setPos(0); return ok();
      case 'skip_next_track': S.ti = (S.ti + 1) % S.tracks.length; setPos(0); return ok();
      case 'skip_previous_track': S.ti = (S.ti + S.tracks.length - 1) % S.tracks.length; setPos(0); return ok();
      case 'seek_media': setPos(a[0]); return ok();
      case 'toggle_shuffle': S.shuffle = !!a[0]; return ok({ shuffle: S.shuffle, shuffle_supported: true });
      case 'cycle_repeat_mode': { const o = ['none', 'track', 'list']; S.repeat = o[(o.indexOf(S.repeat) + 1) % 3]; return ok({ mode: S.repeat }); }
      default: return ok();
    }
  };
})();
