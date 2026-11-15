"""Reproduce a case N times and assess stability of the crash signature."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from faultcore.runner import Outcome, RunResult, RunnerConfig, run_command
from faultcore.signature import (
    CrashSignature,
    is_interesting_crash,
    make_signature,
)


@dataclass
class ReproduceReport:
    runs: int
    results: list[RunResult] = field(default_factory=list)
    signatures: list[CrashSignature] = field(default_factory=list)
    primary_signature: Optional[CrashSignature] = None
    crash_count: int = 0  # interesting crashes only
    signal_crash_count: int = 0
    exit_nonzero_count: int = 0
    timeout_count: int = 0
    normal_count: int = 0
    prep_error_count: int = 0
    stable: bool = False
    unstable_reason: Optional[str] = None
    signature_counts: dict[str, int] = field(default_factory=dict)

    def summary_lines(self) -> list[str]:
        lines = [
            f"runs={self.runs}",
            f"interesting={self.crash_count} signal={self.signal_crash_count} "
            f"exit_nonzero={self.exit_nonzero_count} timeout={self.timeout_count} "
            f"normal={self.normal_count} prep_error={self.prep_error_count}",
            f"stable={self.stable}",
        ]
        if self.unstable_reason:
            lines.append(f"unstable_reason={self.unstable_reason}")
        if self.primary_signature:
            lines.append(f"primary_signature={self.primary_signature.key()}")
        if self.signature_counts:
            lines.append("signature_histogram:")
            for k, v in sorted(self.signature_counts.items(), key=lambda x: -x[1]):
                lines.append(f"  {v}x {k}")
        return lines


def reproduce(
    cmd: list[str],
    input_data: bytes,
    *,
    runs: int = 5,
    config: Optional[RunnerConfig] = None,
    require_all_crash: bool = True,
    interesting_only: bool = True,
    normalization: str = "v2-conservative",
) -> ReproduceReport:
    if runs < 1:
        raise ValueError("runs must be >= 1")

    cfg = config or RunnerConfig()
    report = ReproduceReport(runs=runs)

    for _ in range(runs):
        result = run_command(cmd, input_data=input_data, config=cfg)
        report.results.append(result)
        sig = make_signature(result, normalization=normalization)
        report.signatures.append(sig)

        if result.outcome == Outcome.SIGNAL_CRASH:
            report.signal_crash_count += 1
        elif result.outcome == Outcome.EXIT_NONZERO:
            report.exit_nonzero_count += 1
        elif result.outcome == Outcome.TIMEOUT:
            report.timeout_count += 1
        elif result.outcome == Outcome.NORMAL:
            report.normal_count += 1
        else:
            report.prep_error_count += 1

        if is_interesting_crash(result):
            report.crash_count += 1

    key_counter: Counter[str] = Counter()
    interesting_sigs: list[CrashSignature] = []
    for r, s in zip(report.results, report.signatures):
        key_counter[s.key()] += 1
        if is_interesting_crash(r):
            interesting_sigs.append(s)
    report.signature_counts = dict(key_counter)

    if not interesting_sigs:
        report.stable = False
        report.unstable_reason = "no_interesting_crash_observed"
        return report

    crash_keys = Counter(s.key() for s in interesting_sigs)
    best_key, _ = crash_keys.most_common(1)[0]
    report.primary_signature = next(s for s in interesting_sigs if s.key() == best_key)

    if require_all_crash and report.crash_count != runs:
        report.stable = False
        report.unstable_reason = (
            f"not_all_runs_interesting ({report.crash_count}/{runs})"
        )
        return report

    distinct = set(s.key() for s in interesting_sigs)
    if len(distinct) > 1:
        report.stable = False
        report.unstable_reason = f"multiple_crash_signatures ({len(distinct)})"
        return report

    report.stable = True
    report.unstable_reason = None
    return report
