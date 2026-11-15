#!/usr/bin/env python3
"""Lab fixture: crash roughly half the time (based on a weak time bit).

Not cryptographically random; intended only to produce unstable runs for tests.
"""
import sys
import time

data = sys.stdin.buffer.read()
# Alternate based on milliseconds so repeated runs in the same second still vary.
tick = int(time.time() * 1000) & 1
if tick == 0:
    sys.stderr.buffer.write(b"FAULTCORE_MARKER=intermittent_abort\n")
    raise SystemExit(88)
sys.stdout.buffer.write(b"survived\n")
raise SystemExit(0)
