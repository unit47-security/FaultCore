from __future__ import annotations

import sys
from pathlib import Path

from faultcore.reproduce import reproduce
from faultcore.runner import RunnerConfig

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures"
PY = sys.executable


def test_reproducible_crash():
    report = reproduce(
        [PY, str(FIX / "crash_on_pattern.py")],
        b"prefix CRASHME suffix",
        runs=4,
        config=RunnerConfig(timeout_s=2),
    )
    assert report.crash_count == 4
    assert report.stable is True
    assert report.primary_signature is not None
    assert "pattern_abort" in (report.primary_signature.marker or "")


def test_healthy_not_stable():
    report = reproduce(
        [PY, str(FIX / "healthy.py")],
        b"anything",
        runs=3,
        config=RunnerConfig(timeout_s=2),
    )
    assert report.normal_count == 3
    assert report.stable is False


def test_timeout_not_stable():
    report = reproduce(
        [PY, str(FIX / "hang.py")],
        b"",
        runs=2,
        config=RunnerConfig(timeout_s=0.3, kill_grace_s=0.15),
    )
    assert report.timeout_count == 2
    assert report.stable is False


def test_intermittent_unstable():
    report = reproduce(
        [PY, str(FIX / "intermittent.py")],
        b"x",
        runs=8,
        config=RunnerConfig(timeout_s=2),
    )
    assert report.crash_count + report.normal_count == 8
    if report.crash_count == 0 or report.normal_count == 0:
        import pytest
        pytest.skip("no mix this run")
    assert report.stable is False


def test_signal_crash_stable():
    report = reproduce(
        [PY, str(FIX / "abort_signal.py")],
        b"ABORT",
        runs=3,
        config=RunnerConfig(timeout_s=2),
    )
    assert report.stable is True
    assert report.primary_signature is not None
    assert report.primary_signature.signal is not None


def test_signature_differs():
    a = reproduce([PY, str(FIX / "crash_on_pattern.py")], b"CRASHME", runs=1, config=RunnerConfig(timeout_s=2))
    b = reproduce([PY, str(FIX / "abort_signal.py")], b"ABORT", runs=1, config=RunnerConfig(timeout_s=2))
    assert a.primary_signature is not None and b.primary_signature is not None
    assert not a.primary_signature.matches(b.primary_signature)
