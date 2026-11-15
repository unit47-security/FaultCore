"""Controlled process runner with timeout, streaming output limits and process-group cleanup.

Limits imposed (Linux primary):
- wall-clock timeout (SIGTERM then SIGKILL on the process group)
- max bytes *retained* from stdout and stderr; streams drained continuously
- process group termination via os.killpg when possible

Outcome classification:
- NORMAL: exit code 0, no signal
- EXIT_NONZERO: process exited with code != 0, no fatal signal
- SIGNAL_CRASH: terminated by a fatal signal (SIGSEGV, SIGABRT, ...)
- TIMEOUT: wall-clock budget exceeded
- PREP_ERROR: executable could not be started

EXIT_NONZERO is NOT treated as an automatic minimization baseline. Lab fixtures
that use conventional exit codes must opt in via an explicit interesting rule
(see signature.is_interesting_for_minimization).

This is NOT a sandbox.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Outcome(str, Enum):
    NORMAL = "normal"
    EXIT_NONZERO = "exit_nonzero"
    SIGNAL_CRASH = "signal_crash"
    TIMEOUT = "timeout"
    PREP_ERROR = "prep_error"
    # Backward-compatible alias used only in legacy comparisons; prefer the
    # specific outcomes above. is_crash_like() covers both signal and nonzero.
    CRASH = "crash"  # deprecated synonym — not produced by _classify anymore


@dataclass
class RunResult:
    outcome: Outcome
    exit_code: Optional[int]
    signal: Optional[int]
    stdout: bytes
    stderr: bytes
    duration_s: float
    timed_out: bool
    output_truncated: bool
    stdout_discarded: int = 0
    stderr_discarded: int = 0
    collectors_abandoned: bool = False
    prep_error: Optional[str] = None
    pid: Optional[int] = None
    pgid: Optional[int] = None

    def summary(self) -> str:
        if self.outcome == Outcome.PREP_ERROR:
            return f"prep_error: {self.prep_error}"
        parts = [self.outcome.value]
        if self.signal is not None:
            parts.append(f"signal={self.signal}")
        if self.exit_code is not None:
            parts.append(f"exit={self.exit_code}")
        parts.append(f"duration={self.duration_s:.3f}s")
        if self.timed_out:
            parts.append("timed_out")
        if self.output_truncated:
            parts.append(
                f"output_truncated(out_disc={self.stdout_discarded},"
                f"err_disc={self.stderr_discarded})"
            )
        if self.collectors_abandoned:
            parts.append("collectors_abandoned")
        return " ".join(parts)

    def is_signal_crash(self) -> bool:
        return self.outcome == Outcome.SIGNAL_CRASH

    def is_nonzero_exit(self) -> bool:
        return self.outcome == Outcome.EXIT_NONZERO


@dataclass
class RunnerConfig:
    timeout_s: float = 5.0
    max_output_bytes: int = 64 * 1024
    kill_grace_s: float = 0.5
    new_session: bool = True
    read_chunk: int = 8192
    collector_join_s: float = 1.0


class _StreamCollector:
    def __init__(self, pipe, max_bytes: int, chunk: int = 8192):
        self._pipe = pipe
        self._max = max_bytes
        self._chunk = chunk
        self._buf = bytearray()
        self._discarded = 0
        self._truncated = False
        self._done = threading.Event()
        self._error: Optional[BaseException] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self._run, name="faultcore-stream", daemon=True)
        self._thread = t
        t.start()
        return t

    def _run(self) -> None:
        try:
            while True:
                try:
                    chunk = self._pipe.read(self._chunk)
                except (ValueError, OSError):
                    break
                if not chunk:
                    break
                room = self._max - len(self._buf)
                if room > 0:
                    take = min(room, len(chunk))
                    self._buf.extend(chunk[:take])
                    if take < len(chunk):
                        self._discarded += len(chunk) - take
                        self._truncated = True
                else:
                    self._discarded += len(chunk)
                    self._truncated = True
        except BaseException as e:  # noqa: BLE001
            self._error = e
        finally:
            try:
                self._pipe.close()
            except Exception:
                pass
            self._done.set()

    def wait(self, timeout: Optional[float] = None) -> bool:
        return self._done.wait(timeout=timeout)

    def force_close_pipe(self) -> None:
        try:
            self._pipe.close()
        except Exception:
            pass

    @property
    def data(self) -> bytes:
        return bytes(self._buf)

    @property
    def discarded(self) -> int:
        return self._discarded

    @property
    def truncated(self) -> bool:
        return self._truncated


def run_command(
    cmd: list[str],
    input_data: Optional[bytes] = None,
    config: Optional[RunnerConfig] = None,
    env: Optional[dict] = None,
    cwd: Optional[str] = None,
) -> RunResult:
    cfg = config or RunnerConfig()
    if not cmd:
        return RunResult(
            outcome=Outcome.PREP_ERROR,
            exit_code=None,
            signal=None,
            stdout=b"",
            stderr=b"",
            duration_s=0.0,
            timed_out=False,
            output_truncated=False,
            prep_error="empty command",
        )

    start = time.monotonic()
    proc: Optional[subprocess.Popen] = None
    out_c: Optional[_StreamCollector] = None
    err_c: Optional[_StreamCollector] = None
    threads: list[threading.Thread] = []

    try:
        kwargs: dict = dict(
            stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=cwd,
            bufsize=0,
        )
        if cfg.new_session:
            kwargs["start_new_session"] = True

        proc = subprocess.Popen(cmd, **kwargs)
        pgid = None
        try:
            if cfg.new_session:
                pgid = os.getpgid(proc.pid)
        except (OSError, ProcessLookupError):
            pgid = None

        assert proc.stdout is not None and proc.stderr is not None
        out_c = _StreamCollector(proc.stdout, cfg.max_output_bytes, cfg.read_chunk)
        err_c = _StreamCollector(proc.stderr, cfg.max_output_bytes, cfg.read_chunk)
        threads.append(out_c.start())
        threads.append(err_c.start())

        if input_data is not None and proc.stdin is not None:
            try:
                proc.stdin.write(input_data)
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                try:
                    proc.stdin.close()
                except Exception:
                    pass

        timed_out = False
        deadline = start + cfg.timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            try:
                proc.wait(timeout=min(0.05, remaining))
                break
            except subprocess.TimeoutExpired:
                continue

        if timed_out:
            _terminate(proc, pgid, cfg.kill_grace_s)
            try:
                proc.wait(timeout=cfg.kill_grace_s + 1.0)
            except subprocess.TimeoutExpired:
                _kill(proc, pgid)
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass

        collectors_abandoned = False
        for c in (out_c, err_c):
            if not c.wait(timeout=cfg.collector_join_s):
                collectors_abandoned = True
                c.force_close_pipe()
                c.wait(timeout=0.2)
        for t in threads:
            t.join(timeout=0.3)
            if t.is_alive():
                collectors_abandoned = True

        duration = time.monotonic() - start
        return_code = proc.returncode

        sig = None
        exit_code = return_code
        if return_code is not None and return_code < 0:
            sig = -return_code
            exit_code = None

        outcome = _classify(timed_out=timed_out, return_code=return_code, signal=sig)
        trunc = bool(out_c.truncated or err_c.truncated)

        return RunResult(
            outcome=outcome,
            exit_code=exit_code,
            signal=sig,
            stdout=out_c.data,
            stderr=err_c.data,
            duration_s=duration,
            timed_out=timed_out,
            output_truncated=trunc,
            stdout_discarded=out_c.discarded,
            stderr_discarded=err_c.discarded,
            collectors_abandoned=collectors_abandoned,
            pid=proc.pid,
            pgid=pgid,
        )
    except FileNotFoundError as e:
        return RunResult(
            outcome=Outcome.PREP_ERROR,
            exit_code=None,
            signal=None,
            stdout=b"",
            stderr=b"",
            duration_s=time.monotonic() - start,
            timed_out=False,
            output_truncated=False,
            prep_error=f"command not found: {e.filename or cmd[0]}",
        )
    except Exception as e:
        if proc is not None and proc.poll() is None:
            try:
                _kill(proc, None)
            except Exception:
                pass
        return RunResult(
            outcome=Outcome.PREP_ERROR,
            exit_code=None,
            signal=None,
            stdout=b"",
            stderr=b"",
            duration_s=time.monotonic() - start,
            timed_out=False,
            output_truncated=False,
            prep_error=str(e),
        )


def _terminate(proc: subprocess.Popen, pgid: Optional[int], grace_s: float) -> None:
    try:
        if pgid is not None:
            os.killpg(pgid, signal.SIGTERM)
        else:
            proc.terminate()
    except (OSError, ProcessLookupError):
        pass
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.05)
    _kill(proc, pgid)


def _kill(proc: subprocess.Popen, pgid: Optional[int]) -> None:
    try:
        if pgid is not None:
            os.killpg(pgid, signal.SIGKILL)
        else:
            proc.kill()
    except (OSError, ProcessLookupError):
        pass


def _classify(*, timed_out: bool, return_code: Optional[int], signal: Optional[int]) -> Outcome:
    if timed_out:
        return Outcome.TIMEOUT
    if signal is not None:
        return Outcome.SIGNAL_CRASH
    if return_code is None:
        return Outcome.SIGNAL_CRASH
    if return_code != 0:
        return Outcome.EXIT_NONZERO
    return Outcome.NORMAL
