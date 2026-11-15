"""Signature normalization tests."""
from __future__ import annotations

import sys
from pathlib import Path

from faultcore.runner import Outcome, RunResult, RunnerConfig, run_command
from faultcore.signature import (
    NORMALIZATION_ADDR,
    NORMALIZATION_VERSION,
    is_interesting_crash,
    make_signature,
    normalize_output,
)

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures"
PY = sys.executable


def _fake(**kw) -> RunResult:
    defaults = dict(
        outcome=Outcome.EXIT_NONZERO,
        exit_code=77,
        signal=None,
        stdout=b"",
        stderr=b"",
        duration_s=0.01,
        timed_out=False,
        output_truncated=False,
    )
    defaults.update(kw)
    return RunResult(**defaults)


def test_conservative_keeps_0x_data():
    a = normalize_output(b"token=0xDEADBEEF")
    b = normalize_output(b"token=0xCAFEBABE")
    assert a != b
    assert b"0xDEADBEEF" in a
    assert b"0xCAFEBABE" in b


def test_addr_mode_collapses_0x():
    a = normalize_output(b"at 0x7fff1111", mode=NORMALIZATION_ADDR)
    b = normalize_output(b"at 0x7fff2222", mode=NORMALIZATION_ADDR)
    assert a == b
    assert b"0xADDR" in a


def test_same_signal_different_hex_remain_distinct():
    a = _fake(
        outcome=Outcome.SIGNAL_CRASH,
        signal=6,
        exit_code=None,
        stderr=b"FAULTCORE_MARKER=x\nval=0xAAAA1111\n",
    )
    b = _fake(
        outcome=Outcome.SIGNAL_CRASH,
        signal=6,
        exit_code=None,
        stderr=b"FAULTCORE_MARKER=x\nval=0xBBBB2222\n",
    )
    sa = make_signature(a)  # conservative default
    sb = make_signature(b)
    assert not sa.matches(sb)


def test_pid_still_normalized_conservatively():
    raw = b"pid=12345 process 999\n"
    norm = normalize_output(raw)
    assert b"pid=PID" in norm
    assert b"process PID" in norm


def test_exit_nonzero_without_marker_not_usable():
    r = _fake(stderr=b"just an error\n")
    sig = make_signature(r)
    assert not sig.usable_for_minimization()
    assert not is_interesting_crash(r)


def test_exit_nonzero_with_marker_usable():
    r = _fake(stderr=b"FAULTCORE_MARKER=lab\n")
    assert is_interesting_crash(r)
    assert make_signature(r).usable_for_minimization()


def test_signal_crash_usable():
    r = _fake(outcome=Outcome.SIGNAL_CRASH, signal=11, exit_code=None, stderr=b"")
    assert is_interesting_crash(r)
    assert make_signature(r).usable_for_minimization()


def test_truncation_insufficient():
    r = _fake(stderr=b"FAULTCORE_MARKER=z\n", output_truncated=True, stderr_discarded=100)
    assert not make_signature(r).usable_for_minimization()


def test_live_pattern_interesting():
    r = run_command([PY, str(FIX / "crash_on_pattern.py")], b"CRASHME", RunnerConfig(timeout_s=2))
    assert r.outcome == Outcome.EXIT_NONZERO
    assert is_interesting_crash(r)
    assert make_signature(r).marker == "pattern_abort"
