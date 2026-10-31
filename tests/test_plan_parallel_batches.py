"""Audit part 2, O22: independent plan steps already run concurrently (executor._group_into_batches); this pins it."""
import time
from types import SimpleNamespace

from sara.core.planning.executor import _group_into_batches, execute_plan
from sara.core.planning.schema import Plan, PlanStep


def _plan(*flags):
    steps = tuple(PlanStep(tool="set_volume", arguments={"level": 30 + i}, depends_on_previous=f) for i, f in enumerate(flags))
    return Plan(steps=steps, proposed_step_count=len(steps))


def test_independent_steps_share_a_batch_and_dependent_ones_start_a_new_one():
    plan = _plan(False, False, True, False)
    batches = _group_into_batches(plan.steps)
    assert [[i for i, _ in b] for b in batches] == [[0, 1], [2, 3]]


def test_two_independent_steps_take_one_steps_time():
    plan = _plan(False, False)
    seen = []

    def dispatch(tool, args):
        seen.append(time.perf_counter())
        time.sleep(0.4)
        return "ok"

    cfg = SimpleNamespace(LLM_BACKEND="ollama", PLANNING_STEP_RETRY_ENABLED=False)
    t0 = time.perf_counter()
    execute_plan(plan, dispatch, model_name="m", cfg=cfg, step_timeout_s=3.0, total_timeout_s=6.0, retry_enabled=False)
    wall = time.perf_counter() - t0
    assert len(seen) == 2 and wall < 0.7        # sequential would be >= 0.8
