"""Input minimization with per-execution budget accounting.

Every *started* process execution counts against max_attempts:
baseline single run, quick gates, each verify repetition, final verify runs.

Before each execution:
- check remaining attempts and wall-clock budget
- set run timeout to min(configured timeout, remaining_operational_time)

Operational time budget is separate from process-cleanup grace: kill_grace and
collector_join are not charged as "attempts" but may consume wall clock after
a timed-out child. Remaining timeout for the *next* run is always derived from
the wall clock at the start of that run.

Partial verification (budget exhausted mid-group) never accepts a candidate.
On budget exhaustion, return the last fully verified candidate as incomplete.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Optional

from faultcore.runner import Outcome, RunnerConfig, run_command
from faultcore.signature import (
    CrashSignature,
    is_interesting_crash,
    make_signature,
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class MinimizeConfig:
    runner: RunnerConfig = field(default_factory=RunnerConfig)
    verify_runs: int = 3
    max_attempts: int = 200
    max_seconds: float = 60.0
    min_block: int = 1
    stop_when_stable: bool = True
    normalization: str = "v2-conservative"
    # Wall-clock reserved per timed-out run for SIGTERM/SIGKILL + collector join.
    # Not an "attempt"; documented separately from operational exec timeout.
    cleanup_reserve_s: float = 0.8


@dataclass
class MinimizeReport:
    status: str
    # complete | incomplete | failed | refused | final_failed
    reason: str
    original_size: int
    original_hash: str
    best_input: bytes
    best_size: int
    best_hash: str
    required_signature: Optional[CrashSignature]
    attempts: int
    elapsed_s: float
    reductions: int
    size_trace: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # Final verification lifecycle
    final_verification_started: bool = False
    final_verification_done: bool = False
    final_verification_ok: Optional[bool] = None
    # True only if best is fully multi-run verified and final check succeeded
    # (or final was the verification that established it).
    best_currently_confirmed: bool = False
    # Historical: a full verify succeeded for best at acceptance time
    best_was_verified_when_accepted: bool = False

    def summary_lines(self) -> list[str]:
        lines = [
            f"status={self.status}",
            f"reason={self.reason}",
            f"original_size={self.original_size} hash={self.original_hash[:16]}",
            f"best_size={self.best_size} hash={self.best_hash[:16]}",
            f"attempts={self.attempts} reductions={self.reductions} "
            f"elapsed={self.elapsed_s:.3f}s",
            f"best_was_verified_when_accepted={self.best_was_verified_when_accepted}",
            f"best_currently_confirmed={self.best_currently_confirmed}",
            f"final_verification_started={self.final_verification_started}",
            f"final_verification_done={self.final_verification_done}",
            f"final_verification_ok={self.final_verification_ok}",
        ]
        if self.required_signature:
            lines.append(f"required_signature={self.required_signature.key()}")
        for n in self.notes:
            lines.append(f"note={n}")
        return lines


class _Budget:
    """Tracks started executions and operational wall-clock.

    cleanup_reserve_s is subtracted when computing the next run timeout so that
    SIGTERM/KILL + collector join still fit inside the overall max_seconds
    without starting a run that cannot finish cleanly. Cleanup time itself is
    not counted as an attempt.
    """

    def __init__(
        self,
        max_attempts: int,
        max_seconds: float,
        base_timeout: float,
        cleanup_reserve_s: float,
    ):
        self.max_attempts = max_attempts
        self.max_seconds = max_seconds
        self.base_timeout = base_timeout
        self.cleanup_reserve_s = cleanup_reserve_s
        self.attempts = 0
        self.t0 = time.monotonic()

    def elapsed(self) -> float:
        return time.monotonic() - self.t0

    def remaining_seconds(self) -> float:
        return max(0.0, self.max_seconds - self.elapsed())

    def can_start_one(self) -> bool:
        if self.attempts >= self.max_attempts:
            return False
        # Need a minimal operational slice after reserving cleanup headroom
        if self.remaining_seconds() <= self.cleanup_reserve_s + 0.05:
            return False
        return True

    def runner_config(self, base: RunnerConfig) -> RunnerConfig:
        remaining = self.remaining_seconds()
        # Operational timeout: leave cleanup_reserve for kill/join if the child
        # hits the timeout path.
        op = max(0.05, remaining - self.cleanup_reserve_s)
        t = min(base.timeout_s, op)
        grace = min(base.kill_grace_s, max(0.05, self.cleanup_reserve_s * 0.5))
        return RunnerConfig(
            timeout_s=t,
            max_output_bytes=base.max_output_bytes,
            kill_grace_s=grace,
            new_session=base.new_session,
            read_chunk=base.read_chunk,
            collector_join_s=min(base.collector_join_s, max(0.1, self.cleanup_reserve_s * 0.4)),
        )

    def charge_started(self) -> None:
        self.attempts += 1


def _run_one(
    cmd: list[str],
    data: bytes,
    cfg: MinimizeConfig,
    budget: _Budget,
) -> tuple[Optional[object], str]:
    """Start one execution if budget allows. Returns (RunResult|None, reason)."""
    if not budget.can_start_one():
        return None, "budget_exhausted"
    rcfg = budget.runner_config(cfg.runner)
    budget.charge_started()
    result = run_command(cmd, input_data=data, config=rcfg)
    return result, "ok"


def _verify_full(
    cmd: list[str],
    data: bytes,
    required: CrashSignature,
    cfg: MinimizeConfig,
    budget: _Budget,
) -> tuple[bool, str, int]:
    """Run verify_runs executions one-by-one with per-run budget checks.

    Returns (ok, reason, runs_started).
    Partial groups (budget mid-way) → ok=False, reason=partial_verify.
    """
    needed = cfg.verify_runs
    started = 0
    for i in range(needed):
        result, why = _run_one(cmd, data, cfg, budget)
        if result is None:
            if started == 0:
                return False, "insufficient_budget_for_verify", started
            return False, "partial_verify", started
        started += 1
        if not is_interesting_crash(result):  # type: ignore[arg-type]
            return False, f"not_interesting:{result.outcome.value}", started  # type: ignore[union-attr]
        sig = make_signature(result, normalization=cfg.normalization)  # type: ignore[arg-type]
        if sig.evidence_insufficient:
            return False, "candidate_evidence_insufficient", started
        if not sig.matches(required):
            return False, "signature_mismatch", started
    return True, "ok", started


def minimize(
    cmd: list[str],
    original: bytes,
    *,
    config: Optional[MinimizeConfig] = None,
    required_signature: Optional[CrashSignature] = None,
) -> MinimizeReport:
    cfg = config or MinimizeConfig()
    budget = _Budget(
        cfg.max_attempts,
        cfg.max_seconds,
        cfg.runner.timeout_s,
        cfg.cleanup_reserve_s,
    )
    reductions = 0
    notes: list[str] = [
        f"cleanup_reserve_s={cfg.cleanup_reserve_s} "
        "(wall-clock headroom for kill/join; not counted as attempts)",
        "no_global_minimum_claim: best under attempt/time budget only",
    ]
    original_hash = _sha256(original)
    best = original
    best_was_verified = False
    best_confirmed = False
    size_trace = [len(original)]
    final_started = False
    final_done = False
    final_ok: Optional[bool] = None
    incomplete = False

    def finish(
        status: str,
        reason: str,
        *,
        sig: Optional[CrashSignature] = None,
        extra: Optional[list[str]] = None,
    ) -> MinimizeReport:
        ns = list(notes)
        if extra:
            ns.extend(extra)
        return MinimizeReport(
            status=status,
            reason=reason,
            original_size=len(original),
            original_hash=original_hash,
            best_input=best,
            best_size=len(best),
            best_hash=_sha256(best),
            required_signature=sig if sig is not None else required_signature,
            attempts=budget.attempts,
            elapsed_s=budget.elapsed(),
            reductions=reductions,
            size_trace=list(size_trace),
            notes=ns,
            final_verification_started=final_started,
            final_verification_done=final_done,
            final_verification_ok=final_ok,
            best_currently_confirmed=best_confirmed,
            best_was_verified_when_accepted=best_was_verified,
        )

    # --- Baseline single ---
    if required_signature is None:
        result, why = _run_one(cmd, original, cfg, budget)
        if result is None:
            return finish("failed", "no_budget_for_baseline")
        if result.outcome in (Outcome.TIMEOUT, Outcome.PREP_ERROR, Outcome.NORMAL):
            return finish(
                "failed",
                f"baseline_not_interesting:{result.outcome.value}",
                extra=["baseline did not produce an interesting crash"],
            )
        if result.outcome == Outcome.EXIT_NONZERO and not is_interesting_crash(result):
            return finish(
                "failed",
                "baseline_exit_nonzero_without_lab_marker",
                extra=[
                    "exit_nonzero without lab marker is not a valid minimization baseline"
                ],
            )
        if not is_interesting_crash(result):
            return finish(
                "failed",
                f"baseline_not_interesting:{result.outcome.value}",
            )
        required_signature = make_signature(result, normalization=cfg.normalization)

    if not required_signature.usable_for_minimization():
        return finish(
            "refused",
            "required_signature_not_usable_for_minimization",
            sig=required_signature,
        )

    ok, why, _ = _verify_full(cmd, original, required_signature, cfg, budget)
    if not ok:
        if why in ("insufficient_budget_for_verify", "partial_verify"):
            return finish(
                "incomplete",
                f"budget_during_baseline_verify:{why}",
                sig=required_signature,
                extra=["final_verification_missing=true"],
            )
        return finish(
            "failed",
            f"baseline_unstable:{why}",
            sig=required_signature,
        )
    best_was_verified = True
    best_confirmed = True

    # --- Block deletion ---
    n = len(best)
    block = max(cfg.min_block, n // 2) if n > 0 else cfg.min_block

    while block >= cfg.min_block and n > 0:
        if not budget.can_start_one():
            incomplete = True
            notes.append("budget_exhausted")
            break

        progressed = False
        i = 0
        while i < len(best):
            if not budget.can_start_one():
                incomplete = True
                notes.append("budget_exhausted")
                break

            end = min(i + block, len(best))
            if end <= i:
                break
            candidate = best[:i] + best[end:]
            if candidate == best:
                i = end
                continue

            # Quick gate: one run
            result, why = _run_one(cmd, candidate, cfg, budget)
            if result is None:
                incomplete = True
                notes.append("budget_exhausted")
                break
            if not is_interesting_crash(result):
                i = end
                continue
            quick_sig = make_signature(result, normalization=cfg.normalization)
            if quick_sig.evidence_insufficient or not quick_sig.matches(
                required_signature
            ):
                i = end
                continue

            # Full verify — must complete all repetitions or reject
            ok, why, started = _verify_full(
                cmd, candidate, required_signature, cfg, budget
            )
            if why in ("insufficient_budget_for_verify", "partial_verify"):
                incomplete = True
                notes.append(f"budget_during_candidate_verify:{why}:started={started}")
                # do not accept partial
                break
            if ok:
                best = candidate
                best_was_verified = True
                best_confirmed = True
                n = len(best)
                reductions += 1
                size_trace.append(n)
                progressed = True
            else:
                i = end

        if incomplete:
            break
        if not progressed:
            if block == cfg.min_block:
                break
            block = max(cfg.min_block, block // 2)

    # --- Final verification (required for status=complete) ---
    # final_started becomes True only when the first final-verify execution starts
    if not budget.can_start_one():
        incomplete = True
        final_done = False
        final_ok = None
        best_confirmed = False
        notes.append("final_verification_not_started_budget")
        return finish(
            "incomplete",
            "budget_exhausted_before_final_verify",
            sig=required_signature,
        )

    ok, why, started = _verify_full(cmd, best, required_signature, cfg, budget)
    if started > 0:
        final_started = True
    if why in ("insufficient_budget_for_verify", "partial_verify"):
        final_done = False
        final_ok = None
        best_confirmed = False
        notes.append(f"final_verification_partial:{why}:started={started}")
        return finish(
            "incomplete",
            f"budget_during_final_verify:{why}",
            sig=required_signature,
        )

    final_done = True
    if not ok:
        final_ok = False
        best_confirmed = False
        notes.append(
            "final_verification_failed: candidate kept for history but "
            "not currently confirmed"
        )
        return finish(
            "final_failed",
            f"final_verify_incompatible:{why}",
            sig=required_signature,
        )

    final_ok = True
    best_confirmed = True

    if incomplete:
        # reductions may have stopped early but final still succeeded
        return finish(
            "incomplete",
            "budget_exhausted_during_search",
            sig=required_signature,
        )
    if len(best) < len(original):
        return finish("complete", "local_minimum_under_budget", sig=required_signature)
    return finish("complete", "no_reduction_found", sig=required_signature)
