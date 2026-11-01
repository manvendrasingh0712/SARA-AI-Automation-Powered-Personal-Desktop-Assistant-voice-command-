"""Part 3 / P3: the home orb must not run at the display's full refresh rate (144 Hz measured 2x the idle CPU)."""
from pathlib import Path

GUI = Path(__file__).resolve().parent.parent / "sara" / "gui"


def test_orb_loop_is_frame_capped_in_source_and_bundle():
    for rel in ("js/home/home.js", "bundle/app.js"):
        text = (GUI / rel).read_text(encoding="utf-8")
        assert "ORB_MIN_GAP_MS = 12.4" in text, rel
        assert "now - lastFrameTs < ORB_MIN_GAP_MS" in text, rel


def test_cap_keeps_60hz_displays_unchanged():
    # a 60 Hz frame is 16.7 ms and a 75 Hz frame 13.3 ms: both are longer than the gap, so every frame still draws
    gap = 12.4
    assert 1000 / 60 > gap and 1000 / 75 > gap
    # 144 Hz (6.94 ms): the second refresh (13.9 ms) is the first one past the gap -> 72 fps
    assert 1000 / 144 < gap < 2 * 1000 / 144
