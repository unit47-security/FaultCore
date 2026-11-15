"""Deterministic tests for per-execution budget and final_failed semantics."""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest import mock

from faultcore.minimize import MinimizeConfig, MinimizeReport, minimize, _Budget, _verify_full
from faultcore.runner import Outcome, RunResult, RunnerConfig, run_command
from faultcore.signature import make_signature, is_interesting_crash
from faultcore.cli import main

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures"
PY = sys.executable


def _interesting_result(marker: bytes = b"FAULTCORE_MARKER=pattern_abort\n", exit_code: int = 77):
    return RunResult(
        outcome=Outcome.EXIT_NONZERO,
        exit_code=exit_code,
        signal=None,
        stdout=b"",
        stderr=marker,
        duration_s=0.01,
        timed_out=False,
        output_truncated=False,
    )


def test_budget_stops_mid_verify_group_no_partial_accept():
    """Exhaust attempt budget during verify repetitions; no extra runs start."""
    cfg = MinimizeConfig(
        runner=RunnerConfig(timeout_s=2.0),
        verify_runs=5,
        max_attempts=3,  # baseline 1 + only 2 verify runs possible
        max_seconds=30.0,
        cleanup_reserve_s=0.1,
    )
    calls = {"n": 0}
    real = run_command

    def counting_run(cmd, input_data=None, config=None, **kw):
        calls["n"] += 1
        return _interesting_result()

    with mock.patch("faultcore.minimize.run_command", side_effect=counting_run):
        report = minimize(
            [PY, str(FIX / "crash_on_pattern.py")],
            b"CRASHME",
            config=cfg,
        )
    # attempts charged == started runs; cannot exceed max_attempts
    assert report.attempts <= cfg.max_attempts
    assert calls["n"] == report.attempts
    assert calls["n"] <= 3
    # baseline verify needs 5 runs but only 2 slots left after baseline → incomplete/failed
    assert report.status in ("incomplete", "failed")
    assert report.status != "complete"


def test_time_budget_recalculated_between_runs():
    """Wall budget expires mid-verify; subsequent runs do not start."""
    cfg = MinimizeConfig(
        runner=RunnerConfig(timeout_s=0.5),
        verify_runs=10,
        max_attempts=100,
        max_seconds=0.35,  # tight wall budget
        cleanup_reserve_s=0.05,
    )
    started = []

    def slow_run(cmd, input_data=None, config=None, **kw):
        started.append(time.monotonic())
        time.sleep(0.12)
        return _interesting_result()

    with mock.patch("faultcore.minimize.run_command", side_effect=slow_run):
        report = minimize(
            [PY, str(FIX / "crash_on_pattern.py")],
            b"CRASHME",
            config=cfg,
        )
    assert report.attempts == len(started)
    # With 0.35s and 0.12s sleeps, only a few runs can start
    assert len(started) < 10
    assert report.status != "complete"



def test_final_failed_not_complete(tmp_path, capsys):
    """When final verify returns incompatible outcome → final_failed, not complete."""
    cfg = MinimizeConfig(
        runner=RunnerConfig(timeout_s=2.0),
        verify_runs=2,
        max_attempts=80,
        max_seconds=30.0,
        cleanup_reserve_s=0.1,
    )
    calls = {"verify": 0}

    def fake_verify(cmd, data, required, cfg, budget):
        calls["verify"] += 1
        # Charge the planned runs so budget stays consistent
        for _ in range(cfg.verify_runs):
            if budget.can_start_one():
                budget.charge_started()
        if calls["verify"] == 1:
            # baseline ok
            return True, "ok", cfg.verify_runs
        # any further full verifies during search succeed
        if calls["verify"] < 100:
            # Detect final: we cannot know easily; use: after first, succeed until
            # we see empty reductions then... better: fail only when data equals
            # current best after search — simplest approach: fail on call >= 2
            # if no intermediate candidate verifies needed.
            # With short input that cannot shrink, only baseline + final.
            return True, "ok", cfg.verify_runs
        return False, "signature_mismatch", cfg.verify_runs

    # Use input that is already minimal so search does no successful reduction
    # baseline verify = call 1, final verify = call 2
    seq = {"v": 0}

    def fake_verify2(cmd, data, required, cfg, budget):
        seq["v"] += 1
        for _ in range(cfg.verify_runs):
            if budget.can_start_one():
                budget.charge_started()
        if seq["v"] == 1:
            return True, "ok", cfg.verify_runs
        # final
        return False, "signature_mismatch", cfg.verify_runs

    def always_interesting(cmd, input_data=None, config=None, **kw):
        return _interesting_result()

    with mock.patch("faultcore.minimize.run_command", side_effect=always_interesting), \
         mock.patch("faultcore.minimize._verify_full", side_effect=fake_verify2):
        report = minimize(
            [PY, str(FIX / "crash_on_pattern.py")],
            b"CRASHME",  # minimal token; may or may not shrink
            config=cfg,
        )
    assert report.status == "final_failed", report
    assert report.status != "complete"
    assert report.final_verification_done is True
    assert report.final_verification_ok is False
    assert report.best_currently_confirmed is False
    assert "final_verify" in report.reason
    rc = 1 if report.status in ("failed", "refused", "final_failed") else 0
    assert rc == 1


def test_exact_attempt_count_charges_only_started():
    budget = _Budget(max_attempts=5, max_seconds=60, base_timeout=2, cleanup_reserve_s=0.1)
    assert budget.attempts == 0
    assert budget.can_start_one()
    budget.charge_started()
    assert budget.attempts == 1
    for _ in range(4):
        assert budget.can_start_one()
        budget.charge_started()
    assert budget.attempts == 5
    assert not budget.can_start_one()
