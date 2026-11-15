"""Versioned JSON reports and human-readable summaries (M4).

Schema versioning:
- REPORT_SCHEMA_VERSION is bumped on incompatible or additive field changes.
- 1.0.0: initial run/reproduce/minimize envelopes
- 1.1.0: reproduce.runs_detail[] per-execution records; safer write policy docs

Reports never claim a crash is a confirmed vulnerability. Environment variables,
credentials and raw input/output bytes are not exported by default.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import platform
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

from faultcore import __version__ as FAULTCORE_VERSION

REPORT_SCHEMA_VERSION = "1.1.0"

RESSEMBLY_EXPORT_SCHEMA = {
    "faultcore_export_version": "0.1-predisposition",
    "note": "Placeholder schema for a future Ressembly bridge; not functional integration",
    "fields": [
        "schema_version",
        "faultcore_version",
        "command_argv",
        "artifacts[].path",
        "artifacts[].sha256",
        "artifacts[].role",
        "crash_signature",
        "normalization",
        "repro_params",
        "status",
    ],
}


def _sha256_file(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def same_path(a: Path, b: Path) -> bool:
    """True if paths refer to the same destination (resolve + samefile when possible)."""
    try:
        ar, br = a.resolve(), b.resolve()
    except OSError:
        return False
    if ar == br:
        return True
    try:
        if a.exists() and b.exists() and os.path.samefile(a, b):
            return True
    except OSError:
        pass
    return False


def path_conflicts(paths: Sequence[Optional[Path]]) -> list[tuple[str, str]]:
    """Return pairs of labels that collide (same destination)."""
    labeled = [(i, p) for i, p in enumerate(paths) if p is not None]
    names = [f"path[{i}]" for i, _ in labeled]
    # caller should pass named; this is low-level
    conflicts: list[tuple[str, str]] = []
    for i in range(len(labeled)):
        for j in range(i + 1, len(labeled)):
            if same_path(labeled[i][1], labeled[j][1]):
                conflicts.append((str(labeled[i][1]), str(labeled[j][1])))
    return conflicts


def identify_command_artifacts(argv: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, part in enumerate(argv):
        p = Path(part)
        entry: dict[str, Any] = {"index": i, "arg": part}
        try:
            if p.is_file():
                entry["identified"] = True
                entry["sha256"] = _sha256_file(p)
                entry["size"] = p.stat().st_size
                entry["role"] = "script_or_binary"
            else:
                entry["identified"] = False
                entry["reason"] = "not_an_existing_file"
        except OSError as e:
            entry["identified"] = False
            entry["reason"] = str(e)
        out.append(entry)
    return out


def platform_info() -> dict[str, Any]:
    return {
        "python_version": sys.version.split()[0],
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "system": platform.system(),
        "machine": platform.machine(),
    }


def base_envelope(
    *,
    command: str,
    argv: list[str],
    cwd: Optional[str] = None,
    config: Optional[dict] = None,
) -> dict[str, Any]:
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "faultcore_version": FAULTCORE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "command": command,
        "argv": list(argv),
        "cwd": cwd or os.getcwd(),
        "platform": platform_info(),
        "config": config or {},
        "disclaimer": (
            "A crash or exit_nonzero outcome is not a confirmed vulnerability. "
            "Lab evidence only within the stated limits."
        ),
        "ressembly_export": RESSEMBLY_EXPORT_SCHEMA,
        "command_artifacts": identify_command_artifacts(argv),
    }


class WriteError(Exception):
    """Base class for safe-write failures."""


class DestinationExistsError(WriteError):
    """Destination already exists or appeared concurrently (overwrite=False)."""


class UnsupportedWriteError(WriteError):
    """Filesystem cannot perform exclusive publish without replace."""


class FilesystemWriteError(WriteError):
    """Permissions, I/O, or other filesystem errors."""


def atomic_write_bytes(
    path: Path,
    data: bytes,
    *,
    overwrite: bool = False,
) -> None:
    """Publish bytes without unsafe concurrent overwrite.

    With overwrite=False this function **never** calls os.replace. Publication
    is os.link(temp, dest) after writing a complete temp file. If hard links are
    unavailable, raises UnsupportedWriteError instead of falling back to replace.

    overwrite=True may use os.replace (caller must still protect input/program).
    """
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise FilesystemWriteError(f"cannot create parent directory: {e}") from e

    if not overwrite and path.exists():
        raise DestinationExistsError(f"refusing to overwrite existing file: {path}")

    try:
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=".faultcore-", suffix=".tmp"
        )
    except OSError as e:
        raise FilesystemWriteError(f"cannot create temporary file: {e}") from e

    tmp_path = Path(tmp_name)

    def _cleanup_temp() -> None:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass

    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
    except OSError as e:
        _cleanup_temp()
        raise FilesystemWriteError(f"cannot write temporary file: {e}") from e

    if overwrite:
        try:
            os.replace(tmp_name, path)
        except OSError as e:
            _cleanup_temp()
            raise FilesystemWriteError(f"cannot replace destination: {e}") from e
        return

    # Exclusive publish — no os.replace path
    try:
        os.link(tmp_name, path)
    except FileExistsError as e:
        _cleanup_temp()
        raise DestinationExistsError(
            f"destination exists or appeared concurrently: {path}"
        ) from e
    except OSError as e:
        _cleanup_temp()
        unsupported_errnos = {
            getattr(errno, "EPERM", 1),
            getattr(errno, "ENOSYS", 38),
            getattr(errno, "EOPNOTSUPP", 95),
            getattr(errno, "ENOTSUP", 95),
            getattr(errno, "EXDEV", 18),
        }
        if e.errno == getattr(errno, "EEXIST", 17):
            raise DestinationExistsError(
                f"destination exists or appeared concurrently: {path}"
            ) from e
        if e.errno in unsupported_errnos:
            raise UnsupportedWriteError(
                f"exclusive publish via hard link not supported for {path}: {e}; "
                f"refusing unsafe os.replace fallback when overwrite=False"
            ) from e
        raise FilesystemWriteError(f"cannot publish file: {e}") from e

    try:
        os.unlink(tmp_name)
    except OSError:
        pass


def atomic_write_text(path: Path, text: str, *, overwrite: bool = False) -> None:
    atomic_write_bytes(path, text.encode("utf-8"), overwrite=overwrite)


def write_json_report(
    path: Path, payload: dict[str, Any], *, overwrite: bool = False
) -> None:
    atomic_write_text(
        path, json.dumps(payload, indent=2, sort_keys=False) + "\n", overwrite=overwrite
    )


def write_text_summary(
    path: Path, lines: list[str], *, overwrite: bool = False
) -> None:
    atomic_write_text(path, "\n".join(lines) + "\n", overwrite=overwrite)


def ensure_not_protected(
    dest: Path,
    protected: Sequence[Path],
) -> None:
    for p in protected:
        if same_path(dest, p):
            raise WriteError(
                f"refusing to write over protected path: {dest} (aliases {p})"
            )


def build_run_report(
    *,
    argv: list[str],
    result: Any,
    signature: Any,
    config: dict,
    input_meta: Optional[dict] = None,
) -> dict[str, Any]:
    env = base_envelope(command="run", argv=argv, config=config)
    env["result"] = {
        "outcome": result.outcome.value,
        "exit_code": result.exit_code,
        "signal": result.signal,
        "duration_s": result.duration_s,
        "timed_out": result.timed_out,
        "output_truncated": result.output_truncated,
        "stdout_discarded": result.stdout_discarded,
        "stderr_discarded": result.stderr_discarded,
        "collectors_abandoned": result.collectors_abandoned,
        "prep_error": result.prep_error,
        "stdout_len": len(result.stdout),
        "stderr_len": len(result.stderr),
    }
    env["signature"] = {
        "key": signature.key(),
        "normalization": signature.normalization,
        "marker": signature.marker,
        "evidence_insufficient": signature.evidence_insufficient,
        "usable_for_minimization": signature.usable_for_minimization(),
    }
    if input_meta:
        env["input"] = input_meta
    return env


def build_reproduce_report(
    *,
    argv: list[str],
    report: Any,
    config: dict,
    input_meta: Optional[dict] = None,
) -> dict[str, Any]:
    env = base_envelope(command="reproduce", argv=argv, config=config)
    runs_detail = []
    for i, (result, sig) in enumerate(zip(report.results, report.signatures)):
        runs_detail.append(
            {
                "index": i,
                "outcome": result.outcome.value,
                "exit_code": result.exit_code,
                "signal": result.signal,
                "duration_s": result.duration_s,
                "signature_key": sig.key(),
                "normalization": sig.normalization,
                "output_truncated": result.output_truncated,
                "collectors_abandoned": result.collectors_abandoned,
                "evidence_insufficient": sig.evidence_insufficient,
                "prep_error": result.prep_error,
            }
        )
    env["reproduce"] = {
        "runs": report.runs,
        "stable": report.stable,
        "unstable_reason": report.unstable_reason,
        "interesting_count": report.crash_count,
        "signal_crash_count": report.signal_crash_count,
        "exit_nonzero_count": report.exit_nonzero_count,
        "timeout_count": report.timeout_count,
        "normal_count": report.normal_count,
        "prep_error_count": report.prep_error_count,
        "primary_signature": report.primary_signature.key()
        if report.primary_signature
        else None,
        "signature_counts": report.signature_counts,
        "runs_detail": runs_detail,
    }
    # Aggregate coherence check embedded for consumers
    env["reproduce"]["detail_count"] = len(runs_detail)
    if input_meta:
        env["input"] = input_meta
    return env


def build_minimize_report(
    *,
    argv: list[str],
    report: Any,
    config: dict,
    original: bytes,
    best: bytes,
) -> dict[str, Any]:
    env = base_envelope(command="minimize", argv=argv, config=config)
    env["input_original"] = {
        "size": len(original),
        "sha256": _sha256_bytes(original),
    }
    env["input_minimized"] = {
        "size": len(best),
        "sha256": _sha256_bytes(best),
    }
    env["minimize"] = {
        "status": report.status,
        "reason": report.reason,
        "attempts": report.attempts,
        "elapsed_s": report.elapsed_s,
        "reductions": report.reductions,
        "size_trace": report.size_trace,
        "notes": report.notes,
        "required_signature": report.required_signature.key()
        if report.required_signature
        else None,
        "normalization": report.required_signature.normalization
        if report.required_signature
        else None,
        "best_was_verified_when_accepted": report.best_was_verified_when_accepted,
        "best_currently_confirmed": report.best_currently_confirmed,
        "final_verification_started": report.final_verification_started,
        "final_verification_done": report.final_verification_done,
        "final_verification_ok": report.final_verification_ok,
        "global_minimum_claimed": False,
    }
    return env


def human_summary_run(payload: dict) -> list[str]:
    r = payload["result"]
    s = payload["signature"]
    return [
        f"FaultCore {payload['faultcore_version']} report (schema {payload['schema_version']})",
        f"command=run argv={payload['argv']}",
        f"outcome={r['outcome']} exit={r['exit_code']} signal={r['signal']}",
        f"duration={r['duration_s']:.3f}s truncated={r['output_truncated']}",
        f"signature={s['key']}",
        f"usable_for_minimization={s['usable_for_minimization']}",
        payload["disclaimer"],
    ]


def human_summary_reproduce(payload: dict) -> list[str]:
    r = payload["reproduce"]
    lines = [
        f"FaultCore {payload['faultcore_version']} report (schema {payload['schema_version']})",
        f"command=reproduce stable={r['stable']} reason={r.get('unstable_reason')}",
        f"interesting={r['interesting_count']}/{r['runs']} detail_rows={r.get('detail_count')}",
        f"primary={r.get('primary_signature')}",
    ]
    for d in r.get("runs_detail") or []:
        lines.append(
            f"  run[{d['index']}] {d['outcome']} exit={d['exit_code']} "
            f"sig={d['signal']} dur={d['duration_s']:.3f}s trunc={d['output_truncated']}"
        )
    lines.append(payload["disclaimer"])
    return lines


def human_summary_minimize(payload: dict) -> list[str]:
    m = payload["minimize"]
    o = payload["input_original"]
    b = payload["input_minimized"]
    return [
        f"FaultCore {payload['faultcore_version']} report (schema {payload['schema_version']})",
        f"status={m['status']} reason={m['reason']}",
        f"original size={o['size']} sha256={o['sha256'][:16]}…",
        f"best size={b['size']} sha256={b['sha256'][:16]}…",
        f"attempts={m['attempts']} reductions={m['reductions']} elapsed={m['elapsed_s']:.3f}s",
        f"final_started={m['final_verification_started']} "
        f"final_done={m['final_verification_done']} final_ok={m['final_verification_ok']}",
        f"historically_verified={m['best_was_verified_when_accepted']} "
        f"currently_confirmed={m['best_currently_confirmed']}",
        f"signature={m.get('required_signature')}",
        payload["disclaimer"],
    ]
