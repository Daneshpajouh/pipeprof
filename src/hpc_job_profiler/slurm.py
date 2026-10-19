"""Call and parse ``sacct`` and ``sstat`` in parsable form.

Formats follow the Slurm manual pages (https://slurm.schedmd.com/sacct.html
and https://slurm.schedmd.com/sstat.html, version 26.05 read on 2026-10-19):
``--parsable2`` separates fields with ``|`` and has no trailing ``|``. The
first line is a header unless ``--noheader`` is given. ``hjp`` always asks for
an explicit ``--format`` so the column order is known.

The binaries can be replaced with ``HJP_SACCT`` and ``HJP_SSTAT``, which the
tests use to point at a shim that replays recorded output.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

from hpc_job_profiler.units import parse_memory, parse_reqmem, parse_slurm_duration

SACCT_FIELDS = (
    "JobID",
    "JobIDRaw",
    "JobName",
    "Partition",
    "State",
    "ExitCode",
    "Submit",
    "Start",
    "End",
    "Elapsed",
    "Timelimit",
    "AllocCPUS",
    "NNodes",
    "ReqMem",
    "TotalCPU",
    "MaxRSS",
    "NodeList",
)

SSTAT_FIELDS = (
    "JobID",
    "NTasks",
    "AveCPU",
    "AveRSS",
    "MaxRSS",
    "MaxDiskRead",
    "MaxDiskWrite",
)

TERMINAL_STATES = {
    "BOOT_FAIL",
    "CANCELLED",
    "COMPLETED",
    "DEADLINE",
    "FAILED",
    "NODE_FAIL",
    "OUT_OF_MEMORY",
    "PREEMPTED",
    "REVOKED",
    "TIMEOUT",
}


class SlurmError(RuntimeError):
    """Raised when a Slurm command fails or prints something unexpected."""


def sacct_command(jobs: Sequence[str] = (), start: str | None = None) -> list[str]:
    """The sacct call ``hjp report`` expects, also printed by ``hjp report --show-sacct``."""
    cmd = [binary("sacct"), "--parsable2", f"--format={','.join(SACCT_FIELDS)}"]
    if jobs:
        cmd.append(f"--jobs={','.join(jobs)}")
    if start:
        cmd.append(f"--starttime={start}")
    return cmd


def binary(name: str) -> str:
    """Path of ``sacct`` or ``sstat``, honouring ``HJP_SACCT`` and ``HJP_SSTAT``."""
    override = os.environ.get(f"HJP_{name.upper()}")
    if override:
        return override
    found = shutil.which(name)
    return found or name


def run_text(cmd: Sequence[str], timeout: float = 60.0) -> str:
    try:
        result = subprocess.run(
            list(cmd), capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError as exc:
        raise SlurmError(f"{cmd[0]} not found; is this a Slurm login node?") from exc
    except subprocess.TimeoutExpired as exc:
        raise SlurmError(f"{cmd[0]} did not answer within {timeout:.0f} s") from exc
    if result.returncode != 0:
        raise SlurmError(
            f"{shlex.join(cmd)} failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


def parse_parsable(
    text: str, fields: Sequence[str] | None = None, delimiter: str = "|"
) -> list[dict[str, str]]:
    """Parse ``--parsable2`` (or ``--parsable``) output into dictionaries.

    When ``fields`` is ``None`` the first non-empty line is the header. A
    trailing delimiter (``--parsable``) is tolerated.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    if fields is None:
        header = lines[0].rstrip(delimiter).split(delimiter)
        body = lines[1:]
    else:
        header = list(fields)
        body = lines
    rows: list[dict[str, str]] = []
    for number, line in enumerate(body, start=1):
        parts = line.split(delimiter)
        if len(parts) == len(header) + 1 and parts[-1] == "":
            parts = parts[:-1]
        if len(parts) != len(header):
            raise SlurmError(
                f"line {number}: expected {len(header)} fields, found {len(parts)}: {line!r}"
            )
        rows.append(dict(zip(header, parts, strict=True)))
    return rows


@dataclass
class StepUsage:
    """Live usage of one job step from sstat."""

    step: str
    ntasks: int
    ave_cpu_s: float
    ave_rss_bytes: int
    max_rss_bytes: int
    max_disk_read_bytes: int | None
    max_disk_write_bytes: int | None

    @property
    def cpu_s(self) -> float:
        """Total CPU seconds over all tasks of the step (AveCPU times NTasks)."""
        return self.ave_cpu_s * max(1, self.ntasks)


def parse_sstat(text: str) -> list[StepUsage]:
    """Parse ``sstat --parsable2 --noheader --format=<SSTAT_FIELDS>`` output."""
    steps: list[StepUsage] = []
    for row in parse_parsable(text, SSTAT_FIELDS):
        steps.append(
            StepUsage(
                step=row["JobID"],
                ntasks=int(row["NTasks"] or 1),
                ave_cpu_s=parse_slurm_duration(row["AveCPU"]) or 0.0,
                ave_rss_bytes=parse_memory(row["AveRSS"]) or 0,
                max_rss_bytes=parse_memory(row["MaxRSS"]) or 0,
                max_disk_read_bytes=parse_memory(row["MaxDiskRead"]),
                max_disk_write_bytes=parse_memory(row["MaxDiskWrite"]),
            )
        )
    return steps


def query_sstat(job_id: str, timeout: float = 30.0) -> list[StepUsage]:
    cmd = [
        binary("sstat"),
        "--parsable2",
        "--noheader",
        "--allsteps",
        f"--format={','.join(SSTAT_FIELDS)}",
        f"--jobs={job_id}",
    ]
    return parse_sstat(run_text(cmd, timeout))


@dataclass
class JobLimits:
    """What a job asked for, and where it is now."""

    job_id: str
    state: str
    alloc_cpus: int
    req_mem_bytes: int | None
    time_limit_s: float | None
    elapsed_s: float | None


def query_limits(job_id: str, timeout: float = 30.0) -> JobLimits:
    """Read a job's state, allocated CPUs, requested memory and time limit with sacct."""
    fields = ("JobID", "State", "AllocCPUS", "NNodes", "ReqMem", "Timelimit", "Elapsed")
    cmd = [
        binary("sacct"),
        "--parsable2",
        "--noheader",
        "--allocations",
        f"--format={','.join(fields)}",
        f"--jobs={job_id}",
    ]
    rows = parse_parsable(run_text(cmd, timeout), fields)
    if not rows:
        raise SlurmError(f"sacct knows no job {job_id}")
    row = rows[0]
    cpus = int(row["AllocCPUS"] or 0)
    nodes = int(row["NNodes"] or 1)
    return JobLimits(
        job_id=row["JobID"],
        state=normalise_state(row["State"]),
        alloc_cpus=cpus,
        req_mem_bytes=parse_reqmem(row["ReqMem"], cpus, nodes),
        time_limit_s=parse_slurm_duration(row["Timelimit"]),
        elapsed_s=parse_slurm_duration(row["Elapsed"]),
    )


def normalise_state(state: str) -> str:
    """``CANCELLED by 1000`` becomes ``CANCELLED``; the first word is the state."""
    word = state.strip().split(" ")[0] if state.strip() else "UNKNOWN"
    return word.rstrip("+").upper()
