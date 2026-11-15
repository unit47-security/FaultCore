"""M4 report generation, schema 1.1, overwrite safety."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest import mock

from faultcore.cli import main
from faultcore.report import REPORT_SCHEMA_VERSION, WriteError, atomic_write_bytes, same_path
from faultcore.runner import Outcome, RunResult

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures"
PY = sys.executable


def test_run_report_json_fields(tmp_path):
    inp = tmp_path / "in.bin"
    inp.write_bytes(b"CRASHME")
    rj = tmp_path / "r.json"
    rt = tmp_path / "r.txt"
    rc = main([
        "run", "--arg", PY, "--arg", str(FIX / "crash_on_pattern.py"),
        "--input-file", str(inp), "--timeout", "2",
        "--report-json", str(rj), "--report-text", str(rt),
    ])
    assert rc == 1
    data = json.loads(rj.read_text())
    assert data["schema_version"] == REPORT_SCHEMA_VERSION
    assert data["schema_version"] == "1.1.0"
    assert data["result"]["outcome"] == "exit_nonzero"
    assert "ressembly_export" in data


def test_refuse_overwrite_existing_report(tmp_path):
    inp = tmp_path / "in.bin"
    inp.write_bytes(b"CRASHME")
    rj = tmp_path / "r.json"
    rj.write_text("OLD")
    rc = main([
        "run", "--arg", PY, "--arg", str(FIX / "crash_on_pattern.py"),
        "--input-file", str(inp), "--timeout", "2",
        "--report-json", str(rj),
    ])
    assert rc == 4
    assert rj.read_text() == "OLD"


def test_overwrite_flag_allows_report(tmp_path):
    inp = tmp_path / "in.bin"
    inp.write_bytes(b"CRASHME")
    rj = tmp_path / "r.json"
    rj.write_text("OLD")
    rc = main([
        "run", "--arg", PY, "--arg", str(FIX / "crash_on_pattern.py"),
        "--input-file", str(inp), "--timeout", "2",
        "--report-json", str(rj), "--overwrite",
    ])
    assert rc == 1
    data = json.loads(rj.read_text())
    assert data["schema_version"] == "1.1.0"


def test_refuse_overwrite_input_even_with_flag(tmp_path):
    inp = tmp_path / "in.bin"
    inp.write_bytes(b"CRASHME")
    rc = main([
        "run", "--arg", PY, "--arg", str(FIX / "crash_on_pattern.py"),
        "--input-file", str(inp), "--timeout", "2",
        "--report-json", str(inp), "--overwrite",
    ])
    assert rc == 4
    assert inp.read_bytes() == b"CRASHME"


def test_refuse_overwrite_program_script(tmp_path):
    inp = tmp_path / "in.bin"
    inp.write_bytes(b"CRASHME")
    # cannot write report over the fixture script
    script = FIX / "crash_on_pattern.py"
    original = script.read_bytes()
    rc = main([
        "run", "--arg", PY, "--arg", str(script),
        "--input-file", str(inp), "--timeout", "2",
        "--report-json", str(script), "--overwrite",
    ])
    assert rc == 4
    assert script.read_bytes() == original


def test_output_collision_json_text(tmp_path):
    inp = tmp_path / "in.bin"
    inp.write_bytes(b"CRASHME")
    same = tmp_path / "same.out"
    rc = main([
        "run", "--arg", PY, "--arg", str(FIX / "crash_on_pattern.py"),
        "--input-file", str(inp), "--timeout", "2",
        "--report-json", str(same), "--report-text", str(same),
    ])
    assert rc == 4
    assert not same.exists()


def test_symlink_alias_protected(tmp_path):
    inp = tmp_path / "in.bin"
    inp.write_bytes(b"CRASHME")
    link = tmp_path / "alias.bin"
    try:
        link.symlink_to(inp)
    except OSError:
        import pytest
        pytest.skip("symlink not supported")
    rc = main([
        "minimize", "--arg", PY, "--arg", str(FIX / "crash_on_pattern.py"),
        "--input-file", str(inp), "--output", str(link),
        "--timeout", "2", "--verify-runs", "1", "--max-attempts", "10",
        "--overwrite",
    ])
    assert rc == 4
    assert inp.read_bytes() == b"CRASHME"


def test_concurrent_destination_refused(tmp_path):
    """If destination appears before exclusive commit, refuse."""
    dest = tmp_path / "race.json"
    # Simulate: atomic_write checks not exists, then file appears
    data = b'{"ok":true}\n'
    dest.write_bytes(b"intruder")
    try:
        atomic_write_bytes(dest, data, overwrite=False)
        assert False, "should raise"
    except WriteError:
        pass
    assert dest.read_bytes() == b"intruder"


def test_reproduce_runs_detail(tmp_path):
    inp = tmp_path / "in.bin"
    inp.write_bytes(b"CRASHME")
    rj = tmp_path / "repro.json"
    rc = main([
        "reproduce", "--arg", PY, "--arg", str(FIX / "crash_on_pattern.py"),
        "--input-file", str(inp), "--runs", "3", "--timeout", "2",
        "--report-json", str(rj),
    ])
    assert rc == 0
    data = json.loads(rj.read_text())
    assert data["schema_version"] == "1.1.0"
    detail = data["reproduce"]["runs_detail"]
    assert len(detail) == 3
    assert data["reproduce"]["detail_count"] == 3
    assert data["reproduce"]["interesting_count"] == 3
    for i, row in enumerate(detail):
        assert row["index"] == i
        assert row["outcome"] == "exit_nonzero"
        assert "signature_key" in row
        assert row["normalization"] == "v2-conservative"
        assert "stdout" not in row and "stderr" not in row
    # aggregates match
    assert sum(1 for r in detail if r["outcome"] == "exit_nonzero") == data["reproduce"]["exit_nonzero_count"]


def test_cli_final_failed_exit_code(tmp_path):
    from faultcore.minimize import MinimizeConfig
    seq = {"v": 0}

    def fake_verify(cmd, data, required, cfg, budget):
        seq["v"] += 1
        for _ in range(cfg.verify_runs):
            if budget.can_start_one():
                budget.charge_started()
        if seq["v"] == 1:
            return True, "ok", cfg.verify_runs
        return False, "signature_mismatch", cfg.verify_runs

    def always_ok(cmd, input_data=None, config=None, **kw):
        return RunResult(
            outcome=Outcome.EXIT_NONZERO,
            exit_code=77,
            signal=None,
            stdout=b"",
            stderr=b"FAULTCORE_MARKER=pattern_abort\n",
            duration_s=0.01,
            timed_out=False,
            output_truncated=False,
        )

    inp = tmp_path / "in.bin"
    inp.write_bytes(b"CRASHME")
    with mock.patch("faultcore.minimize.run_command", side_effect=always_ok), \
         mock.patch("faultcore.minimize._verify_full", side_effect=fake_verify):
        rc = main([
            "minimize", "--arg", PY, "--arg", str(FIX / "crash_on_pattern.py"),
            "--input-file", str(inp), "--timeout", "2",
            "--verify-runs", "2", "--max-attempts", "40", "--json",
        ])
    assert rc == 1


def test_final_started_false_when_no_budget():
    from faultcore.minimize import MinimizeConfig, minimize
    from faultcore.runner import RunnerConfig

    report = minimize(
        [PY, str(FIX / "crash_on_pattern.py")],
        b"CRASHME",
        config=MinimizeConfig(
            runner=RunnerConfig(timeout_s=2),
            verify_runs=3,
            max_attempts=1,
            max_seconds=30,
            cleanup_reserve_s=0.1,
        ),
    )
    assert report.final_verification_started is False


def test_minimize_report_coherence(tmp_path):
    import hashlib
    inp = tmp_path / "in.bin"
    out = tmp_path / "out.bin"
    rj = tmp_path / "m.json"
    original = b"PAD" * 20 + b"CRASHME" + b"ZZ"
    inp.write_bytes(original)
    rc = main([
        "minimize", "--arg", PY, "--arg", str(FIX / "crash_on_pattern.py"),
        "--input-file", str(inp), "--output", str(out),
        "--timeout", "2", "--verify-runs", "2", "--max-attempts", "40",
        "--report-json", str(rj),
    ])
    assert rc == 0
    data = json.loads(rj.read_text())
    assert data["input_minimized"]["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    assert data["minimize"]["global_minimum_claimed"] is False
