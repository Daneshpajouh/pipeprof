"""Read Slurm ``seff <jobid>`` text.

Format: the output of the ``seff`` script shipped with Slurm (see the "seff"
man page; read against Slurm 26.05 field names, 2026-10-19). It has no
machine-readable mode, so this reads the fixed "Label: value" lines it
prints. Durations use the same ``D-HH:MM:SS`` Slurm format as sacct; memory
values carry a K/M/G/T suffix read the same binary way.
"""

from __future__ import annotations

import re

from pipeprof.tools.usage import StepUsage
from pipeprof.units import parse_memory, parse_percent, parse_slurm_duration

_JOB_ID_RE = re.compile(r"^Job ID:\s*(\S+)\s*$", re.MULTILINE)
_ARRAY_JOB_ID_RE = re.compile(r"^Array Job ID:\s*(\S+)\s*$", re.MULTILINE)
_STATE_RE = re.compile(r"^State:\s*([^\n(]+?)\s*(?:\(exit code (-?[0-9]+)\))?\s*$", re.MULTILINE)
_CORES_RE = re.compile(r"^Cores:\s*([0-9]+)\s*$", re.MULTILINE)
_NODES_RE = re.compile(r"^Nodes:\s*([0-9]+)\s*$", re.MULTILINE)
_CORES_PER_NODE_RE = re.compile(r"^Cores per node:\s*([0-9]+)\s*$", re.MULTILINE)
_CPU_UTILIZED_RE = re.compile(r"^CPU Utilized:\s*(\S+)\s*$", re.MULTILINE)
_CPU_EFFICIENCY_RE = re.compile(
    r"^CPU Efficiency:\s*([0-9.]+)%\s*of\s*(\S+)\s*core-walltime\s*$", re.MULTILINE
)
_WALLCLOCK_RE = re.compile(r"^Job Wall-clock time:\s*(\S+)\s*$", re.MULTILINE)
_MEM_UTILIZED_RE = re.compile(
    r"^Memory Utilized:\s*([0-9.]+\s*[KMGTP]?i?B?)\s*(\(estimated maximum\))?\s*$", re.MULTILINE
)
_MEM_EFFICIENCY_RE = re.compile(
    r"^Memory Efficiency:\s*([0-9.]+)%\s*of\s*([0-9.]+\s*[KMGTP]?i?B?)\s*$", re.MULTILINE
)
_RUNNING_WARNING_RE = re.compile(
    r"^WARNING: Efficiency statistics may be misleading for RUNNING jobs\.\s*$", re.MULTILINE
)


def parse_seff(text: str, step: str | None = None) -> StepUsage:
    """Parse one ``seff`` report.

    Raises ``ValueError`` if ``text`` has no "Job ID" line, the one marker
    every ``seff`` report carries. Fields seff does not print for a job
    (RUNNING jobs have no exit code; some fields are version dependent) stay
    unset.
    """
    if not _JOB_ID_RE.search(text):
        raise ValueError("not a seff report: no 'Job ID' line")

    notes: list[str] = []

    array_job_match = _ARRAY_JOB_ID_RE.search(text)
    if array_job_match:
        notes.append(f"array job id: {array_job_match.group(1)}")

    exit_code: int | None = None
    state_match = _STATE_RE.search(text)
    if state_match:
        state_text, exit_code_text = state_match.group(1).strip(), state_match.group(2)
        notes.append(f"state: {state_text}")
        if exit_code_text is not None:
            exit_code = int(exit_code_text)

    if _RUNNING_WARNING_RE.search(text):
        notes.append("efficiency statistics may be misleading for a RUNNING job")

    cores_match = _CORES_RE.search(text)
    if cores_match:
        notes.append(f"cores: {cores_match.group(1)}")
    else:
        nodes_match = _NODES_RE.search(text)
        cores_per_node_match = _CORES_PER_NODE_RE.search(text)
        if cores_per_node_match:
            nodes = int(nodes_match.group(1)) if nodes_match else 1
            notes.append(f"cores: {nodes * int(cores_per_node_match.group(1))}")

    # Slurm reports one CPU time total, not split into user and system; keep
    # it as user_s and leave sys_s unset rather than guess at a split.
    cpu_utilized_match = _CPU_UTILIZED_RE.search(text)
    user_s = parse_slurm_duration(cpu_utilized_match.group(1)) if cpu_utilized_match else None

    # CPU Efficiency is CPU time over (cores x walltime), not GNU time's
    # (user+sys)/wall for one core, but both describe how fully the job used
    # what it was given, so it fills the same cpu_percent slot.
    cpu_percent: float | None = None
    cpu_efficiency_match = _CPU_EFFICIENCY_RE.search(text)
    if cpu_efficiency_match:
        cpu_percent = float(cpu_efficiency_match.group(1))
        notes.append(f"CPU Efficiency base: {cpu_efficiency_match.group(2)} core-walltime")

    wallclock_match = _WALLCLOCK_RE.search(text)
    wall_s = parse_slurm_duration(wallclock_match.group(1)) if wallclock_match else None

    max_rss_bytes: int | None = None
    mem_utilized_match = _MEM_UTILIZED_RE.search(text)
    if mem_utilized_match:
        max_rss_bytes = parse_memory(mem_utilized_match.group(1))
        if mem_utilized_match.group(2):
            notes.append("memory utilized is seff's estimated maximum")

    mem_efficiency_match = _MEM_EFFICIENCY_RE.search(text)
    if mem_efficiency_match:
        efficiency = parse_percent(mem_efficiency_match.group(1) + "%")
        requested = parse_memory(mem_efficiency_match.group(2))
        notes.append(f"memory efficiency: {efficiency}% of {requested} bytes requested")

    return StepUsage(
        source="seff",
        step=step,
        wall_s=wall_s,
        user_s=user_s,
        cpu_percent=cpu_percent,
        max_rss_bytes=max_rss_bytes,
        exit_code=exit_code,
        notes=notes,
    )
