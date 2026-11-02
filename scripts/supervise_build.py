#!/usr/bin/env python3
"""Double-fork supervisor: keep rebuilding until data/build.log contains DONE."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "data" / "build.log"
SUP_LOG = ROOT / "data" / "supervisor.log"
PID = ROOT / "data" / "supervisor.pid"


def log(msg: str) -> None:
    SUP_LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}\n"
    with SUP_LOG.open("a") as f:
        f.write(line)


def has_done() -> bool:
    if not LOG.is_file():
        return False
    return any(line.strip() == "DONE" for line in LOG.read_text(errors="ignore").splitlines())


def daemonize() -> None:
    if os.fork() > 0:
        sys.exit(0)
    os.setsid()
    if os.fork() > 0:
        sys.exit(0)
    sys.stdin = open(os.devnull, "r")
    # keep writing to supervisor log
    sys.stdout = open(SUP_LOG, "a", buffering=1)
    sys.stderr = sys.stdout


def main() -> None:
    os.chdir(ROOT)
    os.environ.setdefault("PI_SKIP_TEAMS", "SAN")
    os.environ["PYTHONUNBUFFERED"] = "1"
    PID.write_text(str(os.getpid()) + "\n")
    log(f"supervisor start pid={os.getpid()} skip={os.environ.get('PI_SKIP_TEAMS')}")
    while True:
        if has_done():
            log("DONE seen — exiting")
            return
        log("launch build_catalog.py")
        with LOG.open("a") as out:
            out.write(f"SUP: launch {time.strftime('%H:%M:%SZ', time.gmtime())}\n")
            out.flush()
            proc = subprocess.Popen(
                [sys.executable, "-u", str(ROOT / "scripts" / "build_catalog.py")],
                cwd=str(ROOT),
                stdout=out,
                stderr=subprocess.STDOUT,
                env=os.environ.copy(),
                start_new_session=True,
            )
            code = proc.wait()
            out.write(f"SUP: exit={code} {time.strftime('%H:%M:%SZ', time.gmtime())}\n")
            out.flush()
        log(f"build exit={code}")
        if has_done():
            log("DONE after build — exiting")
            return
        time.sleep(5)


if __name__ == "__main__":
    if "--foreground" not in sys.argv:
        daemonize()
    try:
        main()
    except Exception as exc:
        log(f"FATAL {exc!r}")
        raise
