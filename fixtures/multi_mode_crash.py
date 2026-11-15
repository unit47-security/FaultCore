#!/usr/bin/env python3
"""Two different crash signatures depending on input tokens."""
import sys

data = sys.stdin.buffer.read()
if b"CRASHME" in data:
    sys.stderr.buffer.write(b"FAULTCORE_MARKER=mode_pattern\n")
    raise SystemExit(77)
if b"OTHERBUG" in data:
    sys.stderr.buffer.write(b"FAULTCORE_MARKER=mode_other\n")
    raise SystemExit(88)
sys.stdout.buffer.write(b"ok\n")
raise SystemExit(0)
