from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from faultcore.minimize import MinimizeConfig, minimize
from faultcore.runner import RunnerConfig

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures"
PY = sys.executable


def _cfg(**kwargs) -> MinimizeConfig:
    base = dict(
        runner=RunnerConfig(timeout_s=2, max_output_bytes=65536),
        verify_runs=2,
        max_attempts=80,
        max_seconds=20.0,
        min_block=1,
    )
    base.update(kwargs)
    return MinimizeConfig(**base)


def test_minimize_padding():
    original = b"PAD" * 50 + b"CRASHME" + b"TAIL" * 20
    report = minimize([PY, str(FIX / "crash_on_pattern.py")], original, config=_cfg())
    assert report.status in ("complete", "incomplete")
    assert report.best_size < report.original_size
    assert b"CRASHME" in report.best_input
    assert report.best_was_verified_when_accepted or report.best_currently_confirmed
    assert report.original_hash == hashlib.sha256(original).hexdigest()


def test_minimize_healthy_fails():
    report = minimize([PY, str(FIX / "healthy.py")], b"x", config=_cfg(max_attempts=10, verify_runs=1))
    assert report.status == "failed"


def test_minimize_python_missing_script_not_baseline():
    report = minimize(
        [PY, "/tmp/no_such_fc_script_xyz.py"],
        b"data",
        config=_cfg(max_attempts=5, verify_runs=1),
    )
    assert report.status == "failed"
    assert "marker" in report.reason or "interesting" in report.reason or "baseline" in report.reason


def test_minimize_refuses_truncated():
    # With tight capture the lab marker is past the retained prefix, so
    # exit_nonzero without marker is not an interesting baseline (failed),
    # or if somehow marked insufficient → refused. Either rejects minimize.
    report = minimize(
        [PY, str(FIX / "truncation_twin.py")],
        b"MODE_A",
        config=_cfg(runner=RunnerConfig(timeout_s=2, max_output_bytes=512), max_attempts=5, verify_runs=1),
    )
    assert report.status in ("refused", "failed")


def test_minimize_budget():
    original = b"Z" * 200 + b"CRASHME" + b"Z" * 200
    report = minimize(
        [PY, str(FIX / "crash_on_pattern.py")],
        original,
        config=_cfg(max_attempts=8, verify_runs=1),
    )
    assert report.status in ("incomplete", "complete")
    assert report.attempts <= 8 or report.status == "complete"
    assert report.best_size <= report.original_size


def test_minimize_rejects_other_bug_token():
    original = b"PAD" * 20 + b"CRASHME" + b"PAD" * 10 + b"OTHERBUG" + b"PAD" * 10
    report = minimize(
        [PY, str(FIX / "multi_mode_crash.py")],
        original,
        config=_cfg(verify_runs=2, max_attempts=80),
    )
    assert report.status in ("complete", "incomplete")
    assert report.required_signature is not None
    assert report.required_signature.marker == "mode_pattern"
    assert b"CRASHME" in report.best_input


def test_minimize_intermittent_fails():
    report = minimize(
        [PY, str(FIX / "intermittent.py")],
        b"x",
        config=_cfg(verify_runs=4, max_attempts=20),
    )
    assert report.status in ("failed", "refused")
