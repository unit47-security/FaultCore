#!/usr/bin/env python3
"""Lab fixture: crash deterministically when input contains CRASHME."""
import sys

data = sys.stdin.buffer.read()
if b"CRASHME" in data:
    sys.stderr.buffer.write(b"FAULTCORE_MARKER=pattern_abort\n")
    sys.stderr.buffer.write(b"CRASH_REASON=found_CRASHME\n")
    sys.stderr.flush()
    # Non-zero exit without signal still classified as crash by runner.
    raise SystemExit(77)
sys.stdout.buffer.write(b"ok\n")
raise SystemExit(0)
