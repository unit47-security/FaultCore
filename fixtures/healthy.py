#!/usr/bin/env python3
"""Lab fixture: always exits 0 and echoes input length."""
import sys

data = sys.stdin.buffer.read()
sys.stdout.buffer.write(f"len={len(data)}\n".encode())
raise SystemExit(0)
