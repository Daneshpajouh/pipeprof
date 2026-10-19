#!/usr/bin/env python3
"""Stand-in for sacct and sstat in integration tests.

It replays fixture text and simulates one running job (ID 4242) whose memory
grows on every sstat call until PIPEPROF_SHIM_STEPS calls have been answered, after
which the job counts as COMPLETED. State lives in PIPEPROF_SHIM_STATE.
"""

import os
import sys
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "data"


def calls() -> int:
    state = Path(os.environ["PIPEPROF_SHIM_STATE"])
    count = int(state.read_text()) if state.exists() else 0
    return count


def bump() -> int:
    state = Path(os.environ["PIPEPROF_SHIM_STATE"])
    count = calls() + 1
    state.write_text(str(count))
    return count


def main() -> int:
    tool = Path(sys.argv[0]).name
    args = sys.argv[1:]
    limit = int(os.environ.get("PIPEPROF_SHIM_STEPS", "4"))
    jobs = next((a.split("=", 1)[1] for a in args if a.startswith("--jobs=")), "")
    if tool == "sstat":
        if jobs != "4242":
            print(f"sstat: error: no steps running for job {jobs}", file=sys.stderr)
            return 1
        count = bump()
        if count > limit:
            return 0
        rss_mib = 1150 + 200 * count
        cpu = f"00:{count * 4:02d}:00.000"
        print(f"4242.batch|1|{cpu}|{rss_mib - 50}M|{rss_mib}M|{count * 10}M|{count * 2}M")
        return 0
    if "--allocations" in args:
        if jobs != "4242":
            return 0
        state = "COMPLETED" if calls() >= limit else "RUNNING"
        print(f"4242|{state}|4|1|2G|01:00:00|00:05:00")
        return 0
    sys.stdout.write((FIXTURES / "sacct_slurm_26_05.txt").read_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
