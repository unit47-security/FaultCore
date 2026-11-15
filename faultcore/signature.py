"""Crash signature with explicit, versioned normalization.

Default normalization is CONSERVATIVE (v2-conservative):
- pid / process numbers
- absolute paths under known roots
- ISO timestamps / rough epoch
- whitespace collapse
- does NOT rewrite 0x-prefixed hex (may be payload / diagnostic data)

Optional mode "v2-addr" additionally maps 0x[0-9a-fA-F]{4,} -> 0xADDR for
ASLR-heavy stack traces. Must be selected explicitly.

"Same signature" is structural equality under the chosen rules, not proof of
the same root cause.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Optional

from faultcore.runner import Outcome, RunResult

# Default: conservative. Address-scrubbing is opt-in.
NORMALIZATION_VERSION = "v2-conservative"
NORMALIZATION_ADDR = "v2-addr"


@dataclass(frozen=True)
class CrashSignature:
    outcome: str
    signal: Optional[int]
    exit_code: Optional[int]
    stderr_hash: str
    stdout_hash: str
    marker: Optional[str] = None
    normalization: str = NORMALIZATION_VERSION
    raw_stderr_hash: str = ""
    evidence_insufficient: bool = False
    output_truncated: bool = False
    collectors_abandoned: bool = False

    def key(self) -> str:
        parts = [
            f"outcome={self.outcome}",
            f"signal={self.signal}",
            f"exit={self.exit_code}",
            f"norm={self.normalization}",
            f"stderr={self.stderr_hash[:16]}",
            f"stdout={self.stdout_hash[:16]}",
        ]
        if self.marker:
            parts.append(f"marker={self.marker}")
        if self.evidence_insufficient:
            parts.append("evidence=insufficient")
        return "|".join(parts)

    def matches(self, other: "CrashSignature") -> bool:
        return self.key() == other.key()

    def usable_for_minimization(self) -> bool:
        """Signal crashes with sufficient evidence are always usable.

        EXIT_NONZERO requires an explicit lab marker (FAULTCORE_MARKER / …)
        to be considered interesting for automatic minimization.
        """
        if self.evidence_insufficient:
            return False
        if self.outcome == Outcome.SIGNAL_CRASH.value:
            return True
        if self.outcome == Outcome.EXIT_NONZERO.value and self.marker:
            return True
        return False


_HEX_ADDR = re.compile(rb"0x[0-9a-fA-F]{4,}")
_PID_EQ = re.compile(rb"\bpid[=:][ \t]*\d+\b", re.IGNORECASE)
_PROCESS_NUM = re.compile(rb"\bprocess[ \t]+\d+\b", re.IGNORECASE)
_ABS_PATH = re.compile(
    rb"(?:/home|/tmp|/var/folders|/Users|/private/var)[^\s:\"']+",
)
_ISO_TS = re.compile(
    rb"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)
_EPOCH_ISH = re.compile(rb"\b1[6-9]\d{8}(?:\.\d+)?\b")
_WS_RUN = re.compile(rb"[ \t]+")


def normalize_output(data: bytes, *, mode: str = NORMALIZATION_VERSION) -> bytes:
    """Return a normalized copy. Does not mutate the original."""
    if not data:
        return b""
    out = data
    if mode == NORMALIZATION_ADDR:
        out = _HEX_ADDR.sub(b"0xADDR", out)
    out = _PID_EQ.sub(b"pid=PID", out)
    out = _PROCESS_NUM.sub(b"process PID", out)
    out = _ABS_PATH.sub(b"PATH", out)
    out = _ISO_TS.sub(b"TIMESTAMP", out)
    out = _EPOCH_ISH.sub(b"EPOCH", out)
    lines = out.split(b"\n")
    lines = [_WS_RUN.sub(b" ", ln).strip() for ln in lines]
    return b"\n".join(lines)


_MARKER_RE = re.compile(
    rb"(?:FAULTCORE_MARKER|CRASH_REASON|ABORT)=([^\r\n\x00]{1,64})",
    re.IGNORECASE,
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _extract_marker(stdout: bytes, stderr: bytes) -> Optional[str]:
    for blob in (stderr, stdout):
        m = _MARKER_RE.search(blob)
        if m:
            try:
                return m.group(1).decode("utf-8", errors="replace")
            except Exception:
                return m.group(1).hex()
    return None


def make_signature(
    result: RunResult,
    *,
    include_stdout: bool = True,
    normalization: str = NORMALIZATION_VERSION,
) -> CrashSignature:
    marker = _extract_marker(result.stdout, result.stderr)
    norm_err = normalize_output(result.stderr, mode=normalization)
    norm_out = normalize_output(result.stdout, mode=normalization) if include_stdout else b""
    insufficient = bool(result.output_truncated or result.collectors_abandoned)
    return CrashSignature(
        outcome=result.outcome.value,
        signal=result.signal,
        exit_code=result.exit_code,
        stderr_hash=_sha256(norm_err),
        stdout_hash=_sha256(norm_out),
        marker=marker,
        normalization=normalization,
        raw_stderr_hash=_sha256(result.stderr),
        evidence_insufficient=insufficient,
        output_truncated=result.output_truncated,
        collectors_abandoned=result.collectors_abandoned,
    )


def is_interesting_crash(result: RunResult) -> bool:
    """True for signal crashes, or lab exit_nonzero with an explicit marker."""
    if result.outcome == Outcome.SIGNAL_CRASH:
        return True
    if result.outcome == Outcome.EXIT_NONZERO:
        return _extract_marker(result.stdout, result.stderr) is not None
    return False
