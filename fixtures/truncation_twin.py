#!/usr/bin/env python3
"""Two crash modes that share a long identical stderr prefix, then diverge.

Used to show that a signature over a truncated prefix is NOT sufficient to
declare two crashes equivalent when the distinguishing text lies past the
capture limit.

Usage:
  echo MODE_A | python truncation_twin.py
  echo MODE_B | python truncation_twin.py

Both write PREFIX_LEN bytes of the same filler, then a mode-specific marker.
"""
import sys

PREFIX_LEN = 20000
PREFIX = b"IDENTICAL_PREFIX_" * (PREFIX_LEN // 16 + 1)
PREFIX = PREFIX[:PREFIX_LEN]

data = sys.stdin.buffer.read()
mode = b"A"
if b"MODE_B" in data:
    mode = b"B"
elif b"MODE_A" in data:
    mode = b"A"

sys.stderr.buffer.write(PREFIX)
sys.stderr.buffer.write(b"\nFAULTCORE_MARKER=tail_" + mode + b"\n")
sys.stderr.buffer.flush()
raise SystemExit(90 if mode == b"A" else 91)
