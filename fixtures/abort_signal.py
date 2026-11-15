#!/usr/bin/env python3
"""Lab fixture: raise SIGABRT for a real signal-based crash signature."""
import os
import signal
import sys

data = sys.stdin.buffer.read()
if b"ABORT" in data:
    sys.stderr.buffer.write(b"FAULTCORE_MARKER=sigabrt\n")
    sys.stderr.flush()
    os.kill(os.getpid(), signal.SIGABRT)
    # fallback
    raise SystemExit(1)
sys.stdout.buffer.write(b"no_abort\n")
raise SystemExit(0)
