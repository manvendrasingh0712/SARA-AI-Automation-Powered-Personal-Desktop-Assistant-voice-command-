"""
sara.core.intent.patterns
The regex pattern table and substring pre-filter gates for every fast-path
intent. Split into its own file since it is pure data (~650 lines) and
changes far more often than the matching logic in engine.py.
"""
_INTENT_PATTERNS = [
    # ── Modes / Personas (voice-triggerable, NEW) ─────────────────────
    # "switch to study mode" / "study mode on karo" -- applies a fixed
    # bundle of EXISTING preference toggles (focus_mode, assistant_active,
    # and -- Gaming Mode only -- mic_sensitivity) via
    # sara/orchestrator/intent_handlers.py's _h_switch_mode(). Placed
    # FIRST in this list, deliberately, because two other broad
    # patterns further down would otherwise swallow phrases like
    # "switch to study mode" or "start gaming mode" before this group
    # ever gets a chance to run:
    #   - switch_to_application's r"switch to (?:the )?(.+?)..." (Window
    #     Management section below) would capture "study mode" as an
    #     app name.
    #   - open_app's catch-all r"start (?!https?://)([a-zA-Z0-9][\w\s]
    #     {1,40}?)(?:\s+app)?$" (Apps section, near the bottom) would
    #     capture "gaming mode" the same way.
    # Being first in this list guarantees switch_mode is always tried,
    # and always wins, before either of those.
    ("switch_mode", [
        r"switch to (?:the )?(study|work|gaming|home|normal|default) mode",
        r"go to (?:the )?(study|work|gaming|home|normal|default) mode",
        r"(?:turn on|enable|activate) (?:the )?(study|work|gaming|home|normal|default) mode",
        r"(study|work|gaming|home|normal|default) mode (?:on karo|on kro|chalu karo|shuru karo)",
        r"(study|work|gaming|home|normal|default) mode (?:mein jao|mein switch karo|activate karo)",
        r"^(study|work|gaming|home|normal|default) mode$",
    ]),

    # ── Undo Setting Change (voice-triggerable, NEW) ──────────────────
    # "undo my last change" / "revert my last setting" / Hindi equivalents
    # -- reverts the most recent sara/core/memory.py decision_log entry
    # back to its old_value via db.set_preference(), same mechanism as
    # switch_mode above. Placed early in this list (same defense-in-depth
    # reasoning as switch_mode's placement comment above) so it is always
    # tried, and always wins, before it could ever reach the EXISTING,
    # UNRELATED "undo" intent much further down (Keyboard / Typing
    # section) -- r"undo(?: that)?", which sends a keyboard Ctrl+Z to
    # whatever app is currently focused. That intent is untouched by this
    # feature and must keep matching bare "undo" / "undo that" exactly as
    # before.
    #
    # None of the phrasings below are bare "undo" or "undo that" -- every
    # one requires "last change" / "last setting" (or the Hindi
    # equivalent), so a bare "undo that" never matches here and falls
    # through, completely unaffected, to the old Ctrl+Z "undo" intent
    # further down the list, exactly as it did before this feature
    # existed. Verified by inspection: "undo that" does not contain
    # "last", "wapas", or "pichla"/"pichli", so none of these patterns
    # can match it.
    ("undo_setting_change", [
        r"undo my last (?:change|setting|preference)",
        r"revert my last (?:change|setting|preference)",
        r"undo (?:the )?last (?:change|setting|preference)",
        r"revert (?:the )?last (?:change|setting|preference)",
        r"meri last setting wapas karo",
        r"meri last change wapas karo",
        r"pichla change undo karo",
        r"pichli setting wapas karo",
        r"wapas karo jo maine (?:change|badla) kiya(?: tha)?",
    ]),

    # ── Reminders ──────────────────────────────────────────────────────
    ("reminder_add", [
        r"remind me to (.+) (?:at|on|in) (.+)",
        r"set (?:a )?reminder (?:to|for) (.+) (?:at|on|in) (.+)",
        r"alert me (?:to )?(.+) (?:at|on|in) (.+)",
    ]),
    ("reminder_list", [
        r"(?:what are|show|list|do i have) (?:my )?reminders?",
        r"any reminders?(?: for me)?",
        r"upcoming reminders?",
    ]),
    ("reminder_cancel", [
        r"(?:cancel|clear|delete|remove) (?:all )?(?:my )?reminders?",
        r"(?:stop|disable) (?:all )?reminders?",
    ]),

    # ── Timer ──────────────────────────────────────────────────────────
    ("set_timer", [
        r"set (?:a )?timer (?:for|of) (.+)",
        r"start (?:a )?timer (?:for|of) (.+)",
        r"timer (?:for|of) (.+)",
        r"countdown (?:for|of) (.+)",
    ]),

    # ── Notes ──────────────────────────────────────────────────────────
    ("take_note", [
        r"take (?:a )?note[:\s]+(.+)",
        r"note (?:this )?down[:\s]+(.+)",
        r"remember this[:\s]+(.+)",
        r"save (?:a )?note[:\s]+(.+)",
        r"write (?:this )?down[:\s]+(.+)",
        r"jot (?:this )?down[:\s]+(.+)",
    ]),
    ("read_notes", [
        r"(?:read|show|list|get) (?:my )?notes?",
        r"what (?:are|did i write in) (?:my )?notes?",
        r"any notes?(?: for me)?",
    ]),
    ("clear_notes", [
        r"(?:clear|delete|remove|wipe) (?:all )?(?:my )?notes?",
    ]),

    # ── Clipboard ──────────────────────────────────────────────────────
    ("clipboard_read", [
        r"what'?s (?:on |in )?(?:my )?clipboard",
        r"read (?:my |the )?clipboard",
        r"what did i copy",
        r"show (?:me )?(?:my )?clipboard",
    ]),
    ("clipboard_write", [
        r"copy (?:this|that)(?: for me)?:?\s+(.+)",
        r"copy to clipboard:?\s+(.+)",
        r"save to clipboard:?\s+(.+)",
    ]),

    # ── Vision / Screen ────────────────────────────────────────────────
    ("screenshot_describe", [
        r"what'?s (?:on|showing on) (?:my |the )?screen",
        r"describe (?:my |the )?screen",
        r"take a screenshot",
        r"what (?:is|am i looking at) (?:on screen|on my screen|here)",
        r"look at (?:my )?screen",
    ]),

    # ── Weather ────────────────────────────────────────────────────────
    ("weather", [
        r"(?:what'?s|how'?s|get|check|give)(?: me)? (?:the )?(?:weather|whether)(?: report)? (?:like )?(?:in|at|for|near|of) (.+)",
        r"(?:weather|whether)(?: report)? (?:in|at|for|near|of) (.+)",
        r"(?:will it|is it going to) rain (?:in|at) (.+)",
        r"temperature (?:in|at|of) (.+)",
        r"forecast (?:for|in|at) (.+)",
    ]),

    # ── News ───────────────────────────────────────────────────────────
    ("news", [
        r"(?:latest |recent |breaking )?news (?:about|on|regarding) (.+)",
        r"what'?s (?:happening|going on) (?:with|in|around) (.+)",
        r"headlines? (?:about|on|for) (.+)",
        r"(?:top |latest |breaking )?news",
        r"what'?s (?:in the news|happening today|the news today)",
        r"headlines? today",
    ]),

    # ── Context Follow-ups ─────────────────────────────────────────────
    ("followup_query", [
        r"^(?:and |aur )?what about (.+?)\??$",
        r"^(?:and |aur )?how about (.+?)\??$",
        r"^aur (.+?) (?:ka|ki|mein|me) (?:kya|kaisa|kaisi)(?:\s+hai)?\??$",
    ]),

    # ── YouTube — MUST come before web_search and open_url ────────────
    ("play_youtube", [
        r"play (.+?) on youtube",
        r"youtube (.+)",
        r"search youtube (?:for )?(.+)",
        r"play (?:the )?(?:song|video|music) (?:called |named )?(.+?) on youtube",
        r"open youtube and play (.+)",
        r"play (.+?) (?:song|video|music) on youtube",
        r"play (?:the )?(?:song|video|music) (?:called |named )?(?!.*\bspotify\b)(.+)",
        r"play (.+?) (?:song|video|music)$",
        r"^play (?!.*\bspotify\b)(.+?)$",
    ]),

    # ── Spotify ────────────────────────────────────────────────────────
    ("play_spotify", [
        r"play (.+?) on spotify",
        r"^spotify (.+)$",
        r"play (.+?) (?:on |using )?spotify",
        r"open spotify and play (.+)",
    ]),
    
    # ── Web ────────────────────────────────────────────────────────────
    ("web_search", [
        r"(?:search|google|look up|find|search for) (.+?)(?:\s+for me)?$",
        r"(?:search the web|search online) (?:for )?(.+)",
    ]),
    ("summarize_url", [
        r"summarize (?:this |that |the )?(?:article|page|link|url)?:?\s*(\S+\.\S+)",
        r"(?:read|tell me about) (?:this |that |the )?(?:article|page|link):?\s*(\S+\.\S+)",
        r"what does (?:this |that |the )?(?:article|page|link) (?:say|about):?\s*(\S+\.\S+)",
        r"tl;?dr:?\s*(\S+\.\S+)",
    ]),
    ("open_url", [
        r"open (?:the )?(?:website |site )?([a-zA-Z0-9][-a-zA-Z0-9]*(?:\.[a-zA-Z]{2,})+(?:/\S*)?)",
        r"(?:go to|visit|navigate to) ([a-zA-Z0-9][-a-zA-Z0-9]*(?:\.[a-zA-Z]{2,})+(?:/\S*)?)",
        r"open (?:https?://\S+)",
    ]),

    # ── Calculator ─────────────────────────────────────────────────────
    ("calculator", [
        r"(?:what is|calculate|compute|solve|evaluate)(?: the)? (\d[\d\s\+\-\*\/\(\)\.\^%]+)",
        r"(\d+(?:\s*[\+\-\*\/\^%]\s*\d+)+)",
        r"(?:what'?s) (\d[\d\s\+\-\*\/\(\)\.\^%]+)",
        r"open calculator",
        r"open calc",
    ]),

    # ── System ─────────────────────────────────────────────────────────
    ("system_info", [
        r"system (?:status|info|information|report)",
        r"how'?s my (?:pc|computer|laptop|system)",
        r"(?:battery|cpu|ram|disk|memory) (?:status|level|usage|info)",
        r"give me a status report",
        r"check my (?:pc|computer|laptop|system)",
    ]),
    ("set_volume", [
        r"(?:set|change|put) (?:the )?volume (?:to |at )?(\d{1,3})%?",
        r"volume (?:to |at )?(\d{1,3})%?",
        r"(?:increase|raise|turn up) (?:the )?volume(?: by (\d+)%?)?",
        r"(?:decrease|lower|turn down|reduce) (?:the )?volume(?: by (\d+)%?)?",
    ]),
    ("mute", [
        r"(?:mute|silence)(?: (?:the )?(?:volume|sound|audio|mic|microphone))?",
    ]),
    ("unmute", [
        r"(?:unmute|restore|enable)(?: (?:the )?(?:volume|sound|audio|mic|microphone))?",
    ]),

    # ════════════════════════════════════════════════════════════════════
    # ── SYSTEM CONTROL — Power & Session ──────────────────────────────
    # ════════════════════════════════════════════════════════════════════
    ("lock_pc", [
        r"lock (?:the |my )?(?:pc|computer|laptop|screen|system)",
    ]),
    ("sleep_system", [
        r"(?:put|send) (?:the |my )?(?:pc|computer|laptop|system) to sleep",
        r"sleep (?:the |my )?(?:pc|computer|laptop|system)",
    ]),
    ("hibernate_system", [
        r"hibernate (?:the |my )?(?:pc|computer|laptop|system)",
        r"put (?:the |my )?(?:pc|computer|laptop|system) (?:in|into) hibernation",
    ]),
    ("log_off", [
        r"log\s?off(?: (?:the |my )?(?:pc|computer|laptop|system|user|account))?",
        r"sign out(?: (?:of|from) (?:the |my )?(?:pc|computer|laptop|system|account))?",
    ]),
    ("shutdown_system", [
        r"shut\s?down (?:the |my )?(?:pc|computer|laptop|system)",
        r"turn off (?:the |my )?(?:pc|computer|laptop|system)",
        r"power off (?:the |my )?(?:pc|computer|laptop|system)",
    ]),
    ("restart_system", [
        r"restart (?:the |my )?(?:pc|computer|laptop|system)",
        r"reboot (?:the |my )?(?:pc|computer|laptop|system)",
    ]),
    ("cancel_shutdown", [
        r"cancel (?:the )?shutdown",
        r"cancel (?:the )?restart",
        r"abort (?:the )?shutdown",
        r"stop (?:the )?shutdown",
    ]),

    # ── Volume (extremes) ─────────────────────────────────────────────
    ("max_volume", [
        r"(?:max(?:imum)?|full) volume",
        r"set (?:the )?volume to (?:max(?:imum)?|100%?|full)",
    ]),
    ("min_volume", [
        r"min(?:imum)? volume",
        r"set (?:the )?volume to (?:min(?:imum)?|0%?|zero)",
    ]),

    # ── Brightness ────────────────────────────────────────────────────
    ("set_brightness", [
        r"(?:set|change|put) (?:the )?(?:screen )?brightness (?:to |at )?(\d{1,3})%?",
        r"brightness (?:to |at )?(\d{1,3})%?",
    ]),
    ("increase_brightness", [
        r"(?:increase|raise|turn up) (?:the )?(?:screen )?brightness$",
        r"brighten (?:the )?screen",
        r"brightness up$",
    ]),
    ("decrease_brightness", [
        r"(?:decrease|lower|turn down|reduce|dim) (?:the )?(?:screen )?brightness$",
        r"dim (?:the )?screen",
        r"brightness down$",
    ]),
    ("max_brightness", [
        r"(?:max(?:imum)?|full) brightness",
    ]),
    ("min_brightness", [
        r"min(?:imum)? brightness",
        r"dim(?:mest)? brightness",
    ]),
    ("get_brightness_status", [
        r"what'?s (?:the )?(?:current )?(?:screen )?brightness",
        r"(?:check|show) (?:the )?(?:screen )?brightness",
    ]),

    # ── Window Management ─────────────────────────────────────────────
    ("show_desktop", [
        r"show (?:me )?(?:the )?desktop",
        r"minimize everything",
        r"go to (?:the )?desktop",
    ]),
    ("minimize_all_windows", [
        r"minimize all windows",
    ]),
    ("restore_windows", [
        r"restore (?:my |all )?(?:minimized )?windows",
        r"bring back (?:my |all )?windows",
    ]),
    ("maximize_active_window", [
        r"maximize (?:this|the|current) window",
        r"maximize (?:my )?window",
    ]),
    ("minimize_active_window", [
        r"minimize (?:this|the|current) window",
    ]),
    ("close_active_window", [
        r"close (?:this|the|current) window",
    ]),
    ("snap_window_left", [
        r"snap (?:this |the |current )?window (?:to the )?left",
        r"move (?:this |the |current )?window (?:to the )?left",
    ]),
    ("snap_window_right", [
        r"snap (?:this |the |current )?window (?:to the )?right",
        r"move (?:this |the |current )?window (?:to the )?right",
    ]),
    ("switch_window", [
        r"switch (?:to (?:the )?(?:next|other) )?window",
        r"alt tab",
    ]),
    ("restart_application", [
        r"restart (?:the )?(.+?)(?:\s+app(?:lication)?)?$",
        r"(.+?) (?:ko )?restart (?:karo|kro)",
    ]),
    ("switch_to_application", [
        r"switch to (?:the )?(.+?)(?:\s+app(?:lication)?)?$",
        r"(.+?) (?:pe|par) switch karo",
        r"(.+?) (?:pe|par) jao",
    ]),
    ("move_window", [
        r"move (.+?) (?:window )?to (?:the )?(left half|right half|top half|bottom half|top left|top right|bottom left|bottom right|center|full screen)$",
        r"(.+?) (?:ki window )?ko (left half|right half|top half|bottom half|top left|top right|bottom left|bottom right|center|full screen) (?:mein )?(?:le jao|move karo|kro)",
    ]),
    ("resize_window", [
        r"resize (.+?) (?:window )?to (?:the )?(left half|right half|top half|bottom half|top left|top right|bottom left|bottom right|center|full screen)$",
        r"(.+?) (?:ki window )?(?:ka size|resize)\s*(left half|right half|top half|bottom half|top left|top right|bottom left|bottom right|center|full screen) (?:kro|karo)",
    ]),
    ("always_on_top", [
        r"(?:make |set )?(.+?) always[- ]on[- ]top$",
        r"pin (.+?) (?:window )?on top$",
        r"toggle always on top for (.+)",
        r"(.+?) ko hamesha (?:upar|top pe) rakho",
        r"(.+?) (?:ko )?pin (?:kro|karo)",
    ]),
    ("toggle_fullscreen", [
        r"(?:make |set )?(.+?) fullscreen$",
        r"fullscreen (.+)",
        r"(.+?) ko fullscreen (?:kro|karo)",
        r"go fullscreen",
        r"toggle fullscreen",
        r"full ?screen (?:kro|karo|mode)?$",
    ]),

    # ── Media Controls ────────────────────────────────────────────────
    ("play_pause_media", [
        r"(?:play|pause)(?: the)? (?:music|media|song)",
        r"play(?:/| or )?pause",
        r"(?:resume|continue) (?:music|media|playback)",
    ]),
    ("next_track", [
        r"next (?:song|track)",
        r"skip (?:this |the )?(?:song|track)",
    ]),
    ("previous_track", [
        r"(?:previous|last|go back a) (?:song|track)",
    ]),
    ("stop_media", [
        r"stop (?:the )?(?:music|media|song|playback)",
    ]),

    # ── Keyboard / Typing ─────────────────────────────────────────────
    ("typing_text", [
        r"type (?:this )?(?:for me)?[:\s]+(.+)",
        r"type out[:\s]+(.+)",
        r"write (?:this )?(?:for me)?[:\s]+(.+)",
    ]),
    ("press_key", [
        r"press (?:the )?(.+?) key",
        r"hit (?:the )?(.+?) key",
        r"press (.+)",
    ]),
    ("copy_selection", [
        r"copy (?:this|that|selection|selected)(?: text)?$",
        r"copy (?:the )?selected (?:text|content)",
    ]),
    ("paste_clipboard", [
        r"paste(?: (?:it|this|that|here))?$",
        r"paste (?:from )?clipboard",
    ]),
    ("select_all", [
        r"select all(?: text)?",
        r"ctrl ?a",
        r"select everything",
    ]),
    ("undo", [
        r"undo(?: that)?",
        r"go back(?: one step)?",
        r"ctrl ?z",
    ]),
    ("redo", [
        r"redo(?: that)?",
        r"ctrl ?y",
    ]),

    # ── Browser Tab Controls ──────────────────────────────────────────
    ("new_tab", [
        r"(?:open|create) (?:a )?new tab",
        r"new tab",
    ]),
    ("close_tab", [
        r"close (?:this |current |the )?tab",
    ]),
    ("next_tab", [
        r"(?:go to |switch to )?next tab",
        r"tab right",
    ]),
    ("prev_tab", [
        r"(?:go to |switch to )?(?:previous|prev|last) tab",
        r"tab left",
    ]),
    ("reload_page", [
        r"(?:reload|refresh)(?: (?:this |the )?page)?",
        r"f5",
    ]),

    # ── Zoom ──────────────────────────────────────────────────────────
    ("zoom_in", [
        r"zoom in",
        r"(?:increase|make) (?:the )?(?:text|font|page) (?:larger|bigger)",
    ]),
    ("zoom_out", [
        r"zoom out",
        r"(?:decrease|make) (?:the )?(?:text|font|page) (?:smaller|tinier)",
    ]),
    ("zoom_reset", [
        r"reset zoom",
        r"(?:default|normal) zoom",
        r"zoom (?:to )?100%?",
    ]),

    # ── Scroll ────────────────────────────────────────────────────────
    ("scroll_up", [
        r"scroll up(?: (?:a bit|a little|more))?",
        r"go up (?:the )?page",
        r"page up",
    ]),
    ("scroll_down", [
        r"scroll down(?: (?:a bit|a little|more))?",
        r"go down (?:the )?page",
        r"page down",
    ]),
    ("scroll_top", [
        r"(?:go to |scroll to )?(?:the )?\btop\b(?: of (?:the )?page)?",
        r"beginning of (?:the )?page",
    ]),
    ("scroll_bottom", [
        r"(?:go to |scroll to )?(?:the )?\bbottom\b(?: of (?:the )?page)?",
        r"end of (?:the )?page",
    ]),
    # ── Network Controls ──────────────────────────────────────────────
    ("wifi_on", [
        r"(?:turn on|enable|connect) (?:the )?wi-?fi",
        r"wi-?fi on",
    ]),
    ("wifi_off", [
        r"(?:turn off|disable|disconnect) (?:the )?wi-?fi",
        r"wi-?fi off",
    ]),
    ("bluetooth_on", [
        r"(?:turn on|enable) (?:the )?bluetooth",
        r"bluetooth on",
    ]),
    ("bluetooth_off", [
        r"(?:turn off|disable) (?:the )?bluetooth",
        r"bluetooth off",
    ]),

    # ── Display ───────────────────────────────────────────────────────
    ("dark_mode", [
        r"(?:enable|turn on|switch to|use) dark mode",
        r"dark (?:theme|mode)",
    ]),
    ("light_mode", [
        r"(?:enable|turn on|switch to|use) light mode",
        r"light (?:theme|mode)",
    ]),

    # ── File Operations ───────────────────────────────────────────────
    ("find_file", [
        r"find (?:the )?file (?:called |named )?(.+)",
        r"search (?:for )?(?:a )?file (?:called |named )?(.+)",
        r"where is (?:the )?file (?:called |named )?(.+)",
        r"locate (?:the )?file (?:called |named )?(.+)",
    ]),
    ("empty_recycle_bin", [
        r"empty (?:the )?recycle bin",
        r"clear (?:the )?recycle bin",
        r"delete (?:all )?(?:the )?(?:items in (?:the )?)?recycle bin",
    ]),
    
    ("notify_on_file", [
        r"tell me when (?:the |a |my )?download(?:s)? (?:is |are )?(?:finished|done|complete|ready)",
        r"let me know when (?:the |a |my )?download(?:s)? (?:is |are )?(?:finished|done|complete|ready)",
        r"notify me when (?:the |a |my )?download(?:s)? (?:is |are )?(?:finished|done|complete|ready)",
        r"tell me when (?:the |a |my )?file (?:is |has )?(?:finished|done|ready|downloaded)",
        r"let me know when (?:a |the |my )?(?:new )?file (?:shows up|appears|arrives|is ready)",
        r"notify me when (?:a |the |my )?(?:new )?file (?:shows up|appears|arrives|is ready)",
        r"jab download (?:complete|khatam|poora|done) ho jaye(?: to)? (?:mujhe )?batana",
        r"download (?:complete|khatam|poora) hone (?:par|pe) (?:mujhe )?batana",
        r"jab file (?:aa jaye|complete ho jaye)(?: to)? (?:mujhe )?batana",
        r"file (?:aane|complete hone) (?:par|pe) (?:mujhe )?batana",
    ]),

    ("open_downloads", [
        r"open (?:my )?downloads?(?: folder)?",
    ]),
    ("open_documents", [
        r"open (?:my )?documents?(?: folder)?",
    ]),
    ("open_desktop_folder", [
        r"open (?:my )?desktop folder",
    ]),
    ("open_pictures", [
        r"open (?:my )?pictures?(?: folder)?",
    ]),
    ("open_music", [
        r"open (?:my )?music(?: folder)?",
    ]),
    ("open_videos", [
        r"open (?:my )?videos?(?: folder)?",
    ]),
    ("open_this_pc", [
        r"open this pc",
        r"open my computer",
    ]),
    ("open_recycle_bin", [
        r"open (?:the )?recycle bin",
        r"show (?:me )?(?:the )?recycle bin",
    ]),
    ("open_file_explorer", [
        r"open file explorer",
        r"open (?:windows )?explorer$",
    ]),
    ("open_control_panel", [
        r"open control panel",
    ]),
    ("open_task_manager", [
        r"open task manager",
    ]),

    ("open_display_settings", [
        r"open display settings",
        r"(?:change|adjust) (?:the )?display settings",
    ]),
    ("open_sound_settings", [
        r"open sound settings",
        r"(?:change|adjust) (?:the )?sound settings",
    ]),
    ("open_bluetooth_settings", [
        r"open bluetooth settings",
    ]),
    ("open_network_settings", [
        r"open network settings",
        r"open wifi settings",
    ]),
    ("open_update_settings", [
        r"open (?:windows )?update settings",
        r"check for (?:windows )?updates",
    ]),
    ("open_apps_settings", [
        r"open apps (?:and features )?settings",
        r"(?:uninstall|manage) (?:a |an )?(?:program|app)",
    ]),
    ("open_personalization_settings", [
        r"open personalization settings",
        r"change (?:my )?wallpaper",
        r"change (?:my )?background",
    ]),
    ("open_privacy_settings", [
        r"open privacy settings",
    ]),
    ("open_storage_settings", [
        r"open storage settings",
    ]),
    ("open_power_settings", [
        r"open power settings",
        r"open power (?:and|&) sleep settings",
    ]),
    ("open_about_settings", [
        r"open about settings",
        r"what windows version (?:am i running|do i have)",
    ]),
    
    ("open_nightlight_settings", [
        r"(?:turn on|enable|open|show) (?:the )?night ?light(?: settings)?",
        r"night ?light (?:on karo|chalu karo|kholo|on kro)",
        r"night ?light (?:setting|settings) (?:kholo|dikhao)",
    ]),
    ("open_airplane_mode_settings", [
        r"(?:turn on|enable|open|show) (?:the )?(?:airplane|flight) mode(?: settings)?",
        r"(?:airplane|flight) mode (?:on karo|chalu karo|kholo|on kro)",
        r"(?:airplane|flight) mode (?:setting|settings) (?:kholo|dikhao)",
    ]),

    ("disk_usage", [
        r"(?:disk|storage) (?:usage|space|status)",
        r"how much (?:disk space|storage) (?:do i have|is left|is free)",
    ]),
    ("uptime", [
        r"(?:system |pc |computer )?uptime",
        r"how long has my (?:pc|computer|laptop|system) been (?:on|running)",
    ]),
    ("local_ip", [
        r"(?:what'?s|show me|tell me) (?:my )?(?:local )?ip address",
        r"my ip address",
    ]),
    
    ("gpu_status", [
        r"gpu (?:usage|status|info|report)",
        r"how'?s (?:my )?gpu",
        r"graphics card (?:usage|status|info)",
        r"gpu (?:ka status|kaisa hai|batao)",
        r"graphics card (?:ka status|kaisa hai|batao)",
    ]),
    ("temperature_status", [
        r"(?:cpu |gpu |system |pc )?temperature(?: check| status)?$",
        r"how hot is (?:my )?(?:pc|computer|laptop|system|cpu|gpu)",
        r"temperature (?:kya hai|kitna hai|batao)",
        r"(?:pc|laptop|computer|system) kitna garam hai",
    ]),
    ("process_list", [
        r"(?:show|list) (?:running |top )?processes",
        r"top processes",
        r"what(?:'?s| is) running(?: on my (?:pc|computer|laptop|system))?",
        r"processes (?:dikhao|batao|ki list dikhao)",
        r"kya kya chal raha hai",
    ]),
    ("list_services", [
        r"(?:list|show) (?:running |windows )?services",
        r"what services are running",
        r"services (?:dikhao|batao|ki list dikhao)",
        r"kaunsi services chal rahi (?:hai|hain)",
    ]),
    ("start_service", [
        r"start (?:the )?(.+?) service",
        r"start service (.+)",
        r"(.+?) service (?:start karo|chalu karo|chalao)",
        r"service (.+?) (?:start karo|chalu karo)",
    ]),
    ("stop_service", [
        r"stop (?:the )?(.+?) service",
        r"stop service (.+)",
        r"(.+?) service (?:band karo|bnd karo|roko)",
        r"service (.+?) (?:band karo|roko)",
    ]),

    ("time_query", [
        r"what(?:'?s| is) (?:the )?(?:current )?time",
        r"what time is it",
        r"tell me the time",
    ]),
    ("date_query", [
        r"what(?:'?s| is) (?:today'?s )?date",
        r"what day is (?:it|today)",
        r"tell me (?:today'?s )?date",
        r"what(?:'?s| is) (?:the )?day today",
    ]),

    ("calendar_today", [
        r"(?:what'?s|show|tell me) (?:on )?(?:my )?(?:calendar|schedule) (?:for )?today",
        r"(?:what'?s|show|tell me) (?:my )?(?:calendar|schedule)$",
        r"aaj (?:ka|ke) (?:schedule|calendar) (?:batao|dikhao)",
        r"aaj kya (?:kya )?hai",
        r"do i have (?:any )?meetings? today",
        r"what meetings? do i have today",
    ]),
    ("calendar_create", [
        r"(?:set|schedule|create) (?:a |an )?meeting (?:called|named) (?P<title>.+?) (?:for|at|on) (?P<when>.+)",
        r"(?:create|add|schedule) (?:a |an )?(?:calendar )?event (?:called|named) (?P<title>.+?) (?:for|at|on) (?P<when>.+)",
        r"(?:set|schedule|create) (?:a |an )?meeting (?:for|at|on )?(?P<when>.+?) called (?P<title>.+)",
        r"(?:create|add|schedule) (?:a |an )?(?:calendar )?event (?:for|at|on )?(?P<when>.+?) called (?P<title>.+)",
        r"(?:set|schedule|create) (?:a |an )?meeting (?:for|at|on) (?P<when>.+)",
        r"(?:create|add|schedule) (?:a |an )?(?:calendar )?event (?:for|at|on) (?P<when>.+)",
        r"(?P<when>.+?) (?:baje|ko) meeting (?:set|schedule) karo(?: (?:naam|called) (?P<title>.+))?",
        r"meeting (?:set|schedule) karo (?P<when>.+? baje)",
    ]),

    ("why_proactive", [
        r"why did you say (?:that|this)",
        r"why did you (?:tell|mention) (?:me )?(?:that|this)",
        r"why were you proactive",
        r"why (?:was|is) that\b",
        r"explain (?:that|your) (?:message|suggestion|notification)",
        r"what made you say (?:that|this)",
        r"kyu(?:n)? bola",
        r"kyu(?:n)? kaha",
        r"aisa kyu(?:n)? bola",
        r"vo kyu(?:n)? bola tha",
    ]),

    ("why_decision", [
        r"why did i change (.+)",
        r"why did i (?:set|update|adjust) (.+)",
        r"maine (.+?) kyu(?:n)? change kiya (?:tha)?$",
        r"maine (.+?) kyu(?:n)? badla (?:tha)?$",
    ]),

    ("memory_forget_all", [
        r"forget everything you know about me",
        r"forget all (?:my )?(?:memories|long[- ]term memories)",
        r"wipe all (?:my )?memories",
        r"(?:delete|clear|erase) all (?:my )?(?:memories|long[- ]term memories)",
        r"saari yaad(?:ein|en)? (?:delete|mita|khatam) (?:kar do|karo)",
        r"mujhe (?:puri tarah|bilkul) bhool jao",
        r"sab kuch bhool jao jo tumhe mere baare mein pata hai",
    ]),
    ("memory_forget_specific", [
        r"forget that i (?:like|said|told you|mentioned) (.+)",
        r"forget (?:the fact )?that (.+)",
        r"please forget (.+)",
        r"ye bhool jao ki (.+)",
        r"(.+?) (?:wali baat|wala fact) bhool jao",
        r"ye yaad mat rakho ki (.+)",
    ]),
    ("memory_recall", [
        r"what do you remember about me",
        r"what (?:do you|can you) recall about me",
        r"tell me what you (?:know|remember) about me",
        r"mere baare mein kya yaad hai",
        r"tumhe mere baare mein kya pata hai",
        r"mere baare mein kya (?:pata|yaad) hai tumhe",
    ]),

    ("run_routine", [
        r"run (?:my |the )?(.+?) routine\b",
        r"start (?:my |the )?(.+?) routine\b",
        r"kick off (?:my |the )?(.+?) routine\b",
        r"trigger (?:my |the )?(.+?) routine\b",
        r"(.+?) routine chalao",
        r"routine (.+?) chalao",
        r"chalao (?:mera )?(.+?) routine",
    ]),

    ("open_app", [
        r"open (?!https?://)([a-zA-Z0-9][\w\s]{1,40}?)(?:\s+app)?$",
        r"launch (?!https?://)([a-zA-Z0-9][\w\s]{1,40}?)(?:\s+app)?$",
        r"start (?!https?://)([a-zA-Z0-9][\w\s]{1,40}?)(?:\s+app)?$",
        r"run (?!https?://)([a-zA-Z0-9][\w\s]{1,40}?)(?:\s+app)?$",
    ]),
    ("close_app", [
        r"(?:close|quit|exit|kill|end)(?: the)? ([a-zA-Z0-9][\w\s]{1,40}?)(?:\s+app)?$",
        r"(?:force quit|force close)(?: the)? ([a-zA-Z0-9][\w\s]{1,40}?)(?:\s+app)?$",
    ]),
]

# ── Cheap pre-filter gates ──────────────────────────────────────────────
_INTENT_GATES = {
    "switch_mode": ("mode",),
    "undo_setting_change": ("undo", "revert", "wapas", "badla"),
    "run_routine": ("routine",),
    "reminder_add": ("remind", "reminder", "alert"),
    "reminder_list": ("reminder",),
    "reminder_cancel": ("reminder",),
    "set_timer": ("timer", "countdown"),
    "take_note": ("note", "remember", "write", "jot"),
    "read_notes": ("note",),
    "clear_notes": ("note",),
    "clipboard_read": ("clipboard", "copy"),
    "clipboard_write": ("copy", "clipboard"),
    "screenshot_describe": ("screen",),
    "weather": ("weather", "whether", "rain", "temperature", "forecast"),
    "news": ("news", "headline", "happening"),
    "followup_query": ("what about", "how about", "aur "),
    "play_youtube": ("play", "youtube"),
    "play_spotify": ("play", "spotify"),
    "web_search": ("search", "google", "look up", "find"),
    "summarize_url": ("summar", "tl", "article", "page", "link"),
    "open_url": ("open", "go to", "visit", "navigate"),
    "system_info": ("system", "pc", "computer", "laptop", "status",
                     "battery", "cpu", "ram", "disk", "memory"),
    "set_volume": ("volume",),
    "mute": ("mute", "silence"),
    "unmute": ("unmute", "restore", "enable"),
    "lock_pc": ("lock",),
    "sleep_system": ("sleep",),
    "hibernate_system": ("hibernat",),
    "log_off": ("log off", "logoff", "sign out"),
    "shutdown_system": ("shut", "turn off", "power off"),
    "restart_system": ("restart", "reboot"),
    "cancel_shutdown": ("cancel", "abort", "stop"),
    "max_volume": ("volume", "max", "full"),
    "min_volume": ("volume", "min", "zero"),
    "set_brightness": ("brightness",),
    "increase_brightness": ("brightness", "brighten"),
    "decrease_brightness": ("brightness", "dim"),
    "max_brightness": ("brightness",),
    "min_brightness": ("brightness", "dim"),
    "get_brightness_status": ("brightness",),
    "show_desktop": ("desktop", "minimize"),
    "minimize_all_windows": ("minimize",),
    "restore_windows": ("restore", "window"),
    "maximize_active_window": ("maximize",),
    "minimize_active_window": ("minimize",),
    "close_active_window": ("close", "window"),
    "snap_window_left": ("snap", "window", "left"),
    "snap_window_right": ("snap", "window", "right"),
    "switch_window": ("switch", "window", "alt tab"),
    "restart_application": ("restart",),
    "switch_to_application": ("switch", "pe jao", "par jao"),
    "move_window": ("half", "center", "full screen", "top left", "top right", "bottom left", "bottom right"),
    "resize_window": ("half", "center", "full screen", "top left", "top right", "bottom left", "bottom right"),
    "always_on_top": ("always on top", "pin", "upar"),
    "toggle_fullscreen": ("fullscreen", "full screen"),
    "play_pause_media": ("play", "pause", "music", "media", "resume", "continue"),
    "next_track": ("next", "skip"),
    "previous_track": ("previous", "last", "back"),
    "stop_media": ("stop",),
    "typing_text": ("type", "write"),
    "press_key": ("press", "hit", "key"),
    "copy_selection": ("copy",),
    "paste_clipboard": ("paste",),
    "select_all": ("select", "ctrl"),
    "undo": ("undo", "back", "ctrl"),
    "redo": ("redo", "ctrl"),
    "new_tab": ("tab",),
    "close_tab": ("tab",),
    "next_tab": ("tab",),
    "prev_tab": ("tab",),
    "reload_page": ("reload", "refresh", "f5"),
    "zoom_in": ("zoom", "larger", "bigger"),
    "zoom_out": ("zoom", "smaller", "tinier"),
    "zoom_reset": ("zoom", "default", "normal"),
    "scroll_up": ("scroll", "up", "page up"),
    "scroll_down": ("scroll", "down", "page down"),
    "scroll_top": ("top", "beginning"),
    "scroll_bottom": ("bottom", "end"),
    "wifi_on": ("wifi", "wi-fi", "wi fi"),
    "wifi_off": ("wifi", "wi-fi", "wi fi"),
    "bluetooth_on": ("bluetooth",),
    "bluetooth_off": ("bluetooth",),
    "dark_mode": ("dark",),
    "light_mode": ("light",),
    "find_file": ("file", "find", "locate", "where"),
    "empty_recycle_bin": ("recycle",),
    "notify_on_file": ("download", "file", "batana", "notify", "let me know", "tell me when"),
    "open_downloads": ("download",),
    "open_documents": ("document",),
    "open_desktop_folder": ("desktop",),
    "open_pictures": ("picture",),
    "open_music": ("music",),
    "open_videos": ("video",),
    "open_this_pc": ("this pc", "my computer"),
    "open_recycle_bin": ("recycle",),
    "open_file_explorer": ("explorer",),
    "open_control_panel": ("control panel",),
    "open_task_manager": ("task manager",),
    "open_display_settings": ("display",),
    "open_sound_settings": ("sound",),
    "open_bluetooth_settings": ("bluetooth",),
    "open_network_settings": ("network", "wifi", "wi-fi"),
    "open_update_settings": ("update",),
    "open_apps_settings": ("apps", "program", "uninstall", "manage"),
    "open_personalization_settings": ("personalization", "wallpaper", "background"),
    "open_privacy_settings": ("privacy",),
    "open_storage_settings": ("storage",),
    "open_power_settings": ("power",),
    "open_about_settings": ("about", "windows version"),
    "open_nightlight_settings": ("night light", "nightlight"),
    "open_airplane_mode_settings": ("airplane", "flight mode"),
    "disk_usage": ("disk", "storage"),
    "uptime": ("uptime", "how long"),
    "local_ip": ("ip",),
    "gpu_status": ("gpu", "graphics card"),
    "temperature_status": ("temperature", "hot", "garam"),
    "process_list": ("process", "running", "chal raha"),
    "list_services": ("service", "services"),
    "start_service": ("service",),
    "stop_service": ("service",),
    "time_query": ("time",),
    "date_query": ("date", "day"),
    "why_proactive": ("why", "kyu", "kyun", "made you say", "explain"),
    "why_decision": ("why", "kyu", "kyun", "change", "badla", "badli"),
    "memory_forget_all": ("forget", "wipe", "delete", "erase", "clear", "bhool", "yaad", "mita"),
    "memory_forget_specific": ("forget", "bhool", "yaad"),
    "memory_recall": ("remember", "recall", "yaad", "pata"),
    "calendar_today": ("calendar", "schedule", "aaj", "meeting"),
    "calendar_create": ("meeting", "event", "baje", "calendar"),
    # calculator, open_app, close_app: no safe substring gate — always run.
}