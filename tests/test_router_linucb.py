"""LinUCB: convergence, Sherman-Morrison accuracy, persistence and mismatches."""
from __future__ import annotations

import json

import numpy as np

from sara.core.router.linucb import LinUCB


def _ctx(c: int) -> np.ndarray:
    return np.array([1.0, float(c), 0.0])


def test_online_bandit_converges():
    rng = np.random.default_rng(0)
    model = LinUCB(["a", "b"], 3, alpha=0.5)
    rewards = []
    for _ in range(400):
        c = int(rng.integers(0, 2))
        x = _ctx(c)
        arm = model.choose(x, ["a", "b"])
        reward = 1.0 if arm == ("a" if c == 0 else "b") else 0.0
        model.update(arm, x, reward)
        rewards.append(reward)
    assert sum(rewards[-100:]) >= 80


def test_greedy_choice_after_training():
    model = LinUCB(["a", "b"], 3, alpha=0.0)
    for i in range(300):
        c = i % 2
        model.update("a", _ctx(c), 1.0 - c)
        model.update("b", _ctx(c), float(c))
    assert model.choose(_ctx(0), ["a", "b"]) == "a"
    assert model.choose(_ctx(1), ["a", "b"]) == "b"
    assert model.choose(_ctx(0), ["b"]) == "b"


def test_sherman_morrison_matches_direct_inverse():
    rng = np.random.default_rng(1)
    model = LinUCB(["a"], 4)
    for _ in range(120):
        model.update("a", rng.random(4), float(rng.random()))
    assert np.allclose(model._inv["a"], np.linalg.inv(model._a["a"]), atol=1e-8)
    for _ in range(530):
        model.update("a", rng.random(4), float(rng.random()))
    assert model.counts["a"] == 650
    assert np.allclose(model._inv["a"], np.linalg.inv(model._a["a"]), atol=1e-8)


def test_bad_rewards_are_ignored():
    model = LinUCB(["a"], 3)
    model.update("a", _ctx(1), float("nan"))
    model.update("zzz", _ctx(1), 1.0)
    assert model.counts == {"a": 0}


def test_atomic_save_and_load(tmp_path):
    model = LinUCB(["a", "b"], 3, alpha=0.8)
    for i in range(30):
        model.update("a" if i % 2 else "b", _ctx(i % 2), 0.5)
    path = tmp_path / "state.json"
    model.save(path)
    assert not (tmp_path / "state.json.tmp").exists()
    loaded = LinUCB.load(path, arms=["a", "b"], dim=3, schema=1)
    assert loaded is not None
    assert loaded.counts == model.counts
    x = _ctx(1)
    for arm, value in model.scores(x).items():
        assert loaded.scores(x)[arm] == np.float64(value) or abs(loaded.scores(x)[arm] - value) < 1e-9


def test_missing_file_is_none(tmp_path):
    assert LinUCB.load(tmp_path / "nope.json") is None


def test_corrupt_file_is_renamed(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    assert LinUCB.load(path) is None
    assert (tmp_path / "state.json.bad").exists()
    assert not path.exists()


def test_schema_dim_and_arm_mismatch(tmp_path):
    model = LinUCB(["a", "b"], 3)
    model.update("a", _ctx(0), 1.0)
    path = tmp_path / "state.json"
    model.save(path)
    assert LinUCB.load(path, schema=2) is None
    assert LinUCB.load(path, dim=4) is None
    assert LinUCB.load(path, arms=["a", "c"]) is None
    assert LinUCB.load(path, arms=["a", "b"], dim=3, schema=1) is not None
    assert path.exists()


def test_from_state_rejects_garbage():
    assert LinUCB.from_state({}) is None
    state = LinUCB(["a"], 2).to_state()
    state["version"] = 99
    assert LinUCB.from_state(state) is None
    state = LinUCB(["a"], 2).to_state()
    state["arms"]["a"]["A"] = [[1.0]]
    assert LinUCB.from_state(json.loads(json.dumps(state))) is None