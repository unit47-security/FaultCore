#!/usr/bin/env python3
"""Parent exits quickly; child leaves the process group and keeps writing briefly.

Demonstrates that the runner must not wait forever on pipe readers when a
descendant detaches (setsid) and holds stdout open.

Self-cleanup: child exits after a short write budget so lab tests do not
leave processes around. killpg still does not reach setsid children — that
limit remains.
"""
import os
import sys
import time


def main() -> None:
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(r)
        try:
            os.setsid()
        except Exception:
            pass
        try:
            for _ in range(30):  # bounded ~6s of noise max
                try:
                    os.write(1, b"orphan-chunk\n")
                except Exception:
                    break
                time.sleep(0.2)
        finally:
            os.close(w)
            os._exit(0)

    os.close(w)
    sys.stderr.buffer.write(f"parent_exit child={pid}\n".encode())
    sys.stderr.flush()
    os.close(r)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
