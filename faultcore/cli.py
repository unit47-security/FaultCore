"""FaultCore CLI: run, reproduce, minimize, reports (M4/M5)."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import tempfile
from pathlib import Path
from typing import Optional

from faultcore.minimize import MinimizeConfig, minimize
from faultcore.report import (
    WriteError,
    build_minimize_report,
    build_reproduce_report,
    build_run_report,
    ensure_not_protected,
    human_summary_minimize,
    human_summary_reproduce,
    human_summary_run,
    same_path,
    write_json_report,
    write_text_summary,
    atomic_write_bytes,
    _sha256_bytes,
)
from faultcore.branding import banner
from faultcore.reproduce import reproduce
from faultcore.runner import Outcome, RunnerConfig, run_command
from faultcore.signature import make_signature


def _load_input(path: Optional[str], stdin_flag: bool) -> bytes:
    if stdin_flag:
        return sys.stdin.buffer.read()
    if path:
        return Path(path).read_bytes()
    return b""


def _parse_cmd_string(raw: str) -> list[str]:
    raw = raw.strip()
    if not raw:
        raise SystemExit("empty --cmd")
    try:
        parts = shlex.split(raw, posix=True)
    except ValueError as e:
        raise SystemExit(f"invalid --cmd quoting: {e}") from e
    if not parts:
        raise SystemExit("empty --cmd")
    return parts


def _build_argv(args: argparse.Namespace) -> list[str]:
    if getattr(args, "arg", None):
        return list(args.arg)
    if getattr(args, "cmd", None):
        return _parse_cmd_string(args.cmd)
    raise SystemExit("provide --arg (repeatable) or --cmd")


def _runner_cfg(args: argparse.Namespace) -> RunnerConfig:
    return RunnerConfig(
        timeout_s=args.timeout,
        max_output_bytes=args.max_output,
    )


def _input_meta(path: Optional[str], data: bytes) -> dict:
    meta = {"size": len(data), "sha256": _sha256_bytes(data)}
    if path:
        meta["path"] = path
    else:
        meta["path"] = None
        meta["source"] = "stdin_or_empty"
    return meta


def _protected_paths(
    args: argparse.Namespace, argv: list[str]
) -> list[Path]:
    """Paths that must never be written: input, program artifacts that exist."""
    protected: list[Path] = []
    if getattr(args, "input_file", None):
        protected.append(Path(args.input_file))
    for part in argv:
        p = Path(part)
        try:
            if p.is_file():
                protected.append(p)
        except OSError:
            pass
    return protected


def _collect_outputs(args: argparse.Namespace) -> dict[str, Optional[Path]]:
    return {
        "report_json": Path(args.report_json) if getattr(args, "report_json", None) else None,
        "report_text": Path(args.report_text) if getattr(args, "report_text", None) else None,
        "output": Path(args.output) if getattr(args, "output", None) else None,
    }


def _preflight_paths(
    args: argparse.Namespace, argv: list[str]
) -> Optional[str]:
    """Return error message if output destinations collide or hit protected paths."""
    outs = _collect_outputs(args)
    protected = _protected_paths(args, argv)
    # mutual collisions among outputs
    named = [(k, v) for k, v in outs.items() if v is not None]
    for i in range(len(named)):
        for j in range(i + 1, len(named)):
            if same_path(named[i][1], named[j][1]):
                return (
                    f"output collision: {named[i][0]} and {named[j][0]} "
                    f"resolve to the same path ({named[i][1]})"
                )
    overwrite = bool(getattr(args, "overwrite", False))
    for label, dest in named:
        for prot in protected:
            if same_path(dest, prot):
                return (
                    f"refusing to write {label} over protected path {prot} "
                    f"(input or program artifact); --overwrite cannot authorize this"
                )
        if not overwrite and dest.exists():
            return f"refusing to overwrite existing file without --overwrite: {dest}"
    return None


def _write_reports(
    args: argparse.Namespace,
    payload: dict,
    summary_lines: list[str],
    protected: list[Path],
    saved: list[str],
) -> int:
    overwrite = bool(getattr(args, "overwrite", False))
    try:
        if getattr(args, "report_json", None):
            out = Path(args.report_json)
            ensure_not_protected(out, protected)
            write_json_report(out, payload, overwrite=overwrite)
            saved.append(str(out))
        if getattr(args, "report_text", None):
            out = Path(args.report_text)
            ensure_not_protected(out, protected)
            write_text_summary(out, summary_lines, overwrite=overwrite)
            saved.append(str(out))
    except WriteError as e:
        print(f"report_error: {e}", file=sys.stderr)
        if saved:
            print(f"artifacts_saved: {saved}", file=sys.stderr)
        return 4
    except OSError as e:
        print(f"report_write_error: {e}", file=sys.stderr)
        if saved:
            print(f"artifacts_saved: {saved}", file=sys.stderr)
        return 4
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    cmd = _build_argv(args)
    err = _preflight_paths(args, cmd)
    if err:
        print(f"path_error: {err}", file=sys.stderr)
        return 4
    input_data = _load_input(args.input_file, args.stdin)
    cfg = _runner_cfg(args)
    result = run_command(cmd, input_data=input_data, config=cfg)
    sig = make_signature(result)
    config = {"timeout_s": cfg.timeout_s, "max_output_bytes": cfg.max_output_bytes}
    payload = build_run_report(
        argv=cmd,
        result=result,
        signature=sig,
        config=config,
        input_meta=_input_meta(args.input_file, input_data),
    )
    protected = _protected_paths(args, cmd)
    saved: list[str] = []
    wr = _write_reports(args, payload, human_summary_run(payload), protected, saved)
    if wr != 0:
        return wr

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(result.summary())
        print(f"signature: {sig.key()}")
        if sig.evidence_insufficient:
            print("evidence: insufficient; not usable alone for automatic minimization")
        if saved:
            print(f"artifacts_saved: {saved}")

    if result.outcome == Outcome.PREP_ERROR:
        return 2
    if result.outcome == Outcome.TIMEOUT:
        return 3
    if result.outcome in (Outcome.SIGNAL_CRASH, Outcome.EXIT_NONZERO):
        return 1
    return 0


def cmd_reproduce(args: argparse.Namespace) -> int:
    cmd = _build_argv(args)
    err = _preflight_paths(args, cmd)
    if err:
        print(f"path_error: {err}", file=sys.stderr)
        return 4
    input_data = _load_input(args.input_file, args.stdin)
    cfg = _runner_cfg(args)
    report = reproduce(
        cmd,
        input_data,
        runs=args.runs,
        config=cfg,
        require_all_crash=not args.allow_partial,
    )
    config = {
        "timeout_s": cfg.timeout_s,
        "max_output_bytes": cfg.max_output_bytes,
        "runs": args.runs,
    }
    payload = build_reproduce_report(
        argv=cmd,
        report=report,
        config=config,
        input_meta=_input_meta(args.input_file, input_data),
    )
    protected = _protected_paths(args, cmd)
    saved: list[str] = []
    wr = _write_reports(
        args, payload, human_summary_reproduce(payload), protected, saved
    )
    if wr != 0:
        return wr

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        for line in report.summary_lines():
            print(line)
        if saved:
            print(f"artifacts_saved: {saved}")
    return 0 if report.stable else 1


def cmd_minimize(args: argparse.Namespace) -> int:
    cmd = _build_argv(args)
    err = _preflight_paths(args, cmd)
    if err:
        print(f"path_error: {err}", file=sys.stderr)
        return 4
    input_data = _load_input(args.input_file, args.stdin)
    cfg = _runner_cfg(args)
    mcfg = MinimizeConfig(
        runner=cfg,
        verify_runs=args.verify_runs,
        max_attempts=args.max_attempts,
        max_seconds=args.max_seconds,
        min_block=args.min_block,
    )
    report = minimize(cmd, input_data, config=mcfg)
    protected = _protected_paths(args, cmd)
    saved: list[str] = []
    overwrite = bool(getattr(args, "overwrite", False))

    if args.output:
        outp = Path(args.output)
        try:
            ensure_not_protected(outp, protected)
            atomic_write_bytes(outp, report.best_input, overwrite=overwrite)
            saved.append(str(outp))
        except WriteError as e:
            print(f"output_error: {e}", file=sys.stderr)
            if saved:
                print(f"artifacts_saved: {saved}", file=sys.stderr)
            return 4
        except OSError as e:
            print(f"output_write_error: {e}", file=sys.stderr)
            if saved:
                print(f"artifacts_saved: {saved}", file=sys.stderr)
            return 4

    payload = build_minimize_report(
        argv=cmd,
        report=report,
        config={
            "timeout_s": cfg.timeout_s,
            "max_output_bytes": cfg.max_output_bytes,
            "verify_runs": args.verify_runs,
            "max_attempts": args.max_attempts,
            "max_seconds": args.max_seconds,
        },
        original=input_data,
        best=report.best_input,
    )
    wr = _write_reports(
        args, payload, human_summary_minimize(payload), protected, saved
    )
    if wr != 0:
        return wr

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        for line in report.summary_lines():
            print(line)
        if saved:
            print(f"artifacts_saved: {saved}")

    if report.status in ("failed", "refused", "final_failed"):
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="faultcore",
        description=banner() + "\n\nControlled execution, reproducibility, minimization and reports.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version="faultcore 0.1.0")
    p.add_argument("--banner", action="store_true", help="Show the ASCII logo and exit")
    p.add_argument("--no-banner", action="store_true", help="Hide the interactive startup logo")
    sub = p.add_subparsers(dest="command")

    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--arg", action="append", default=None, help="argv element (repeatable)")
        sp.add_argument("--cmd", default=None, help="legacy shlex-parsed command string")
        g = sp.add_mutually_exclusive_group()
        g.add_argument("--input-file", help="Input file path")
        g.add_argument("--stdin", action="store_true")
        sp.add_argument("--timeout", type=float, default=5.0)
        sp.add_argument("--max-output", type=int, default=65536)
        sp.add_argument("--json", action="store_true")
        sp.add_argument("--report-json", help="Write versioned JSON report (no overwrite by default)")
        sp.add_argument("--report-text", help="Write human summary (no overwrite by default)")
        sp.add_argument(
            "--overwrite",
            action="store_true",
            help="Allow replacing existing report/output files; never the input or program",
        )

    run_p = sub.add_parser("run")
    add_common(run_p)
    run_p.set_defaults(func=cmd_run)

    rep_p = sub.add_parser("reproduce")
    add_common(rep_p)
    rep_p.add_argument("--runs", type=int, default=5)
    rep_p.add_argument("--allow-partial", action="store_true")
    rep_p.set_defaults(func=cmd_reproduce)

    min_p = sub.add_parser("minimize")
    add_common(min_p)
    min_p.add_argument("--output", help="Write minimized input (must differ from input/program)")
    min_p.add_argument("--verify-runs", type=int, default=3)
    min_p.add_argument("--max-attempts", type=int, default=200)
    min_p.add_argument("--max-seconds", type=float, default=60.0)
    min_p.add_argument("--min-block", type=int, default=1)
    min_p.set_defaults(func=cmd_minimize)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if args.banner:
            print(banner())
            return 0
        if not hasattr(args, "func"):
            parser.print_help()
            return 0
        if sys.stdout.isatty() and not args.no_banner and not getattr(args, "json", False):
            print(banner())
            print()
        return args.func(args)
    except SystemExit as e:
        if isinstance(e.code, int):
            return e.code
        return 2
