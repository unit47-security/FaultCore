from __future__ import annotations

import json
import sys
from pathlib import Path

from faultcore.cli import main, _parse_cmd_string

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures"
PY = sys.executable


def test_cli_run_crash(tmp_path, capsys):
    inp = tmp_path / "in.bin"
    inp.write_bytes(b"CRASHME")
    rc = main([
        "run", "--arg", PY, "--arg", str(FIX / "crash_on_pattern.py"),
        "--input-file", str(inp), "--timeout", "2", "--json",
    ])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["result"]["outcome"] == "exit_nonzero"
    assert out["signature"]["usable_for_minimization"] is True


def test_cli_reproduce_stable(tmp_path, capsys):
    inp = tmp_path / "in.bin"
    inp.write_bytes(b"CRASHME")
    rc = main([
        "reproduce", "--arg", PY, "--arg", str(FIX / "crash_on_pattern.py"),
        "--input-file", str(inp), "--runs", "3", "--timeout", "2", "--json",
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["reproduce"]["stable"] is True


def test_cli_minimize(tmp_path, capsys):
    inp = tmp_path / "in.bin"
    outp = tmp_path / "out.bin"
    inp.write_bytes(b"PADPAD" + b"CRASHME" + b"PADPAD")
    rc = main([
        "minimize", "--arg", PY, "--arg", str(FIX / "crash_on_pattern.py"),
        "--input-file", str(inp), "--output", str(outp),
        "--timeout", "2", "--verify-runs", "2", "--max-attempts", "40", "--json",
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["minimize"]["status"] in ("complete", "incomplete")
    assert out["minimize"]["global_minimum_claimed"] is False


def test_shlex_quotes_and_spaces():
    parts = _parse_cmd_string(f'{PY} "{FIX / "crash_on_pattern.py"}"')
    assert parts[0] == PY
    assert "crash_on_pattern.py" in parts[1]


def test_arg_with_spaces(tmp_path, capsys):
    d = tmp_path / "dir with spaces"
    d.mkdir()
    script = d / "target.py"
    script.write_text("import sys\nsys.exit(0)\n")
    rc = main([
        "run", "--arg", PY, "--arg", str(script), "--timeout", "2", "--json",
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["result"]["outcome"] == "normal"
    assert str(script) in out["argv"]


def test_empty_cmd_rejected():
    try:
        _parse_cmd_string("")
        assert False
    except SystemExit:
        pass


def test_cmd_legacy_still_works(tmp_path, capsys):
    inp = tmp_path / "in.bin"
    inp.write_bytes(b"safe")
    rc = main([
        "run", "--cmd", f"{PY} {FIX / 'healthy.py'}",
        "--input-file", str(inp), "--timeout", "2", "--json",
    ])
    assert rc == 0
