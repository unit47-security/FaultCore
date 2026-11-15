#!/usr/bin/env python3
"""Lab fixture: spawn child processes that hang; parent may exit or hang.

Used to verify process-group cleanup on timeout.
"""
import os
import sys
import time


def main() -> None:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "hang").lower()
    children = []
    for i in range(3):
        pid = os.fork()
        if pid == 0:
            # Child: ignore common signals and sleep
            try:
                import signal as sig

                sig.signal(sig.SIGTERM, sig.SIG_IGN)
            except Exception:
                pass
            while True:
                time.sleep(30)
            os._exit(0)
        children.append(pid)

    sys.stderr.buffer.write(f"parent_pid={os.getpid()} children={children}\n".encode())
    sys.stderr.flush()

    if mode == "exit":
        # Parent exits immediately; orphans would remain without group kill.
        raise SystemExit(0)
    # mode == hang: parent also hangs
    while True:
        time.sleep(30)


if __name__ == "__main__":
    main()
