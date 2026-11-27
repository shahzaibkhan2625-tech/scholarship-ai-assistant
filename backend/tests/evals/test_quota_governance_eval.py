"""003 T304 (constitution Principle V): the scored `quota_governance` eval case.

It simulates a day of mixed traffic (students, monitoring, validation, backfill) against the admission policy
with a fake provider and a fake clock, and scores the invariants: never above the allowance, the user-facing
reserve honoured, no background request below its floor, no provider call after a quota pause. The case is a
zero-tolerance gate (threshold 1.0), and a deliberately broken policy must score below it."""

import pytest

from evals import runner
from evals.cases import quota_governance


def test_the_case_scores_one_on_the_real_policy():
    assert quota_governance.score() == 1.0


def test_the_runner_registers_the_case_with_threshold_one():
    cases = {c.name: c for c in runner.CASES}
    assert "quota_governance" in cases
    assert cases["quota_governance"].threshold == 1.0
    assert runner.score_case(cases["quota_governance"]) == 1.0


def test_the_simulation_is_deterministic():
    assert quota_governance.simulate() == quota_governance.simulate()


def test_the_day_exercises_every_class_and_a_pause():
    result = quota_governance.simulate()
    assert {"user_facing", "backfill", "monitoring", "validation"} <= set(result.requests_by_class)
    assert result.pauses >= 1
    assert result.provider_calls > 0 and result.refused_background > 0


def test_a_policy_that_ignores_the_reserve_scores_below_one(monkeypatch):
    from app.services import llm_budget

    real = llm_budget.decide

    def careless(config, request_type, priority, **kw):
        return llm_budget.Decision(True, "allowed")  # admits everything, ignoring allowance, floors and pauses

    monkeypatch.setattr(llm_budget, "decide", careless)
    assert quota_governance.score() < 1.0
    monkeypatch.setattr(llm_budget, "decide", real)
    assert quota_governance.score() == 1.0


def test_a_policy_that_lets_background_work_ignore_a_pause_scores_below_one(monkeypatch):
    from app.services import llm_budget

    real = llm_budget.decide

    def pause_blind(config, request_type, priority, **kw):
        kw["paused_until"] = None
        return real(config, request_type, priority, **kw)

    monkeypatch.setattr(llm_budget, "decide", pause_blind)
    assert quota_governance.score() < 1.0
