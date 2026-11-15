"""Tests for controlled runner."""
from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

from faultcore.runner import Outcome, RunnerConfig, run_command

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures"
PY = sys.executable


def test_healthy_normal():
    r = run_command([PY, str(FIX / "healthy.py")], b"hello", RunnerConfig(timeout_s=2))
    assert r.outcome == Outcome.NORMAL
    assert r.exit_code == 0
    assert b"len=5" in r.stdout


def test_exit_nonzero_not_signal_crash():
    r = run_command([PY, str(FIX / "crash_on_pattern.py")], b"CRASHME", RunnerConfig(timeout_s=2))
    assert r.outcome == Outcome.EXIT_NONZERO
    assert r.exit_code == 77
    assert r.signal is None


def test_healthy_input_no_crash():
    r = run_command([PY, str(FIX / "crash_on_pattern.py")], b"safe", RunnerConfig(timeout_s=2))
    assert r.outcome == Outcome.NORMAL


def test_timeout():
    r = run_command([PY, str(FIX / "hang.py")], b"", RunnerConfig(timeout_s=0.4, kill_grace_s=0.2))
    assert r.outcome == Outcome.TIMEOUT
    assert r.timed_out


def test_prep_error_missing_binary():
    r = run_command(["/nonexistent/faultcore-binary-xyz"], b"", RunnerConfig(timeout_s=1))
    assert r.outcome == Outcome.PREP_ERROR


def test_python_missing_script_is_exit_nonzero_not_baseline():
    """python nonexistent.py finds the interpreter; exit nonzero, no lab marker."""
    r = run_command([PY, "/tmp/definitely_missing_fc_script_xyz.py"], b"", RunnerConfig(timeout_s=2))
    assert r.outcome == Outcome.EXIT_NONZERO
    assert r.signal is None


def test_signal_abort():
    r = run_command([PY, str(FIX / "abort_signal.py")], b"ABORT", RunnerConfig(timeout_s=2))
    assert r.outcome == Outcome.SIGNAL_CRASH
    assert r.signal == signal.SIGABRT


def test_output_limit_truncation():
    code = "import sys\nsys.stdout.buffer.write(b'A'*200000)\nsys.stderr.buffer.write(b'B'*50000)"
    r = run_command([PY, "-c", code], b"", RunnerConfig(timeout_s=3, max_output_bytes=4096))
    assert r.outcome == Outcome.NORMAL
    assert r.output_truncated
    assert len(r.stdout) == 4096
    assert len(r.stderr) == 4096


def test_streaming_drain():
    code = "import sys\nchunk=b'X'*4096\nfor _ in range(512):\n sys.stdout.buffer.write(chunk)\n sys.stdout.buffer.flush()"
    r = run_command([PY, "-c", code], b"", RunnerConfig(timeout_s=5, max_output_bytes=8192))
    assert r.outcome == Outcome.NORMAL
    assert len(r.stdout) == 8192
    assert r.output_truncated


def test_child_processes_killed_on_timeout():
    r = run_command([PY, str(FIX / "spawn_children.py"), "hang"], b"", RunnerConfig(timeout_s=0.5, kill_grace_s=0.2))
    assert r.outcome == Outcome.TIMEOUT
    time.sleep(0.2)
    try:
        os.kill(r.pid, 0)
        alive = True
    except OSError:
        alive = False
    assert not alive


def test_orphan_writer_does_not_block():
    t0 = time.monotonic()
    r = run_command(
        [PY, str(FIX / "orphan_writer.py")],
        b"",
        RunnerConfig(timeout_s=1.5, collector_join_s=0.4, kill_grace_s=0.2),
    )
    assert time.monotonic() - t0 < 4.0
    assert r.duration_s < 4.0
