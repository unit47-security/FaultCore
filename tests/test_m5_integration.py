"""M5 integration path on lab fixtures."""
from __future__ import annotations

import hashlib
import json
import sys
import threading
from pathlib import Path

from faultcore.cli import main
from faultcore.runner import run_command, RunnerConfig

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures"
PY = sys.executable


def test_pipeline_input_reproduce_minimize_report(tmp_path):
    inp = tmp_path / "seed.bin"
    out = tmp_path / "min.bin"
    rep_j = tmp_path / "repro.json"
    min_j = tmp_path / "min.json"
    original = b"XX" * 30 + b"CRASHME" + b"YY" * 20
    inp.write_bytes(original)

    rc = main([
        "reproduce", "--arg", PY, "--arg", str(FIX / "crash_on_pattern.py"),
        "--input-file", str(inp), "--runs", "3", "--timeout", "2",
        "--report-json", str(rep_j),
    ])
    assert rc == 0
    assert json.loads(rep_j.read_text())["reproduce"]["stable"] is True

    rc = main([
        "minimize", "--arg", PY, "--arg", str(FIX / "crash_on_pattern.py"),
        "--input-file", str(inp), "--output", str(out),
        "--timeout", "2", "--verify-runs", "2", "--max-attempts", "50",
        "--report-json", str(min_j),
    ])
    assert rc == 0
    data = json.loads(min_j.read_text())
    assert data["input_original"]["sha256"] == hashlib.sha256(original).hexdigest()
    assert data["input_minimized"]["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    assert b"CRASHME" in out.read_bytes()
    assert inp.read_bytes() == original  # original intact


def test_healthy_and_intermittent_exit_codes(tmp_path):
    inp = tmp_path / "i.bin"
    inp.write_bytes(b"x")
    rc = main([
        "run", "--arg", PY, "--arg", str(FIX / "healthy.py"),
        "--input-file", str(inp), "--timeout", "2",
    ])
    assert rc == 0
    rc = main([
        "reproduce", "--arg", PY, "--arg", str(FIX / "intermittent.py"),
        "--input-file", str(inp), "--runs", "6", "--timeout", "2",
    ])
    assert rc == 1  # unstable


def test_spaces_in_path(tmp_path):
    d = tmp_path / "my dir"
    d.mkdir()
    script = d / "ok.py"
    script.write_text("import sys\nsys.exit(0)\n")
    rc = main(["run", "--arg", PY, "--arg", str(script), "--timeout", "2"])
    assert rc == 0


def test_thread_count_stable_repeated_runs():
    before = threading.active_count()
    for _ in range(15):
        run_command([PY, str(FIX / "healthy.py")], b"z", RunnerConfig(timeout_s=1))
    after = threading.active_count()
    assert after <= before + 3


def test_invalid_args_message():
    rc = main(["run", "--timeout", "1"])  # missing cmd/arg
    assert rc != 0  # argparse error exit
