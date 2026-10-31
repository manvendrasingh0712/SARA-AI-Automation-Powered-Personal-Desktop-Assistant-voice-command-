"""Audit part 2 / Task 6: the plain range clamps of Config.validate live in one table."""
import config


def test_every_row_is_a_sane_range_and_a_real_setting():
    assert len(config._CLAMPS) >= 90
    for name, lo, hi in config._CLAMPS:
        assert lo <= hi, name
        assert hasattr(config.Config, name), name


def test_validate_clamps_out_of_range_values(monkeypatch):
    for name, lo, hi in config._CLAMPS[:40]:
        cur = getattr(config.Config, name)
        integer = isinstance(cur, int) and not isinstance(cur, bool)
        for value, expected in ((lo - 7, lo), (hi + 7, hi)):
            monkeypatch.setattr(config.Config, name, int(value) if integer else value)
            try:
                config.Config.validate(force=True)
            except Exception:       # a cross-field rule (for example a missing API key) is not what is tested here
                pass
            got = getattr(config.Config, name)
            assert got == (int(expected) if integer else expected) or got in (lo, hi), (name, got)
