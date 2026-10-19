"""Per-step boundness: CPU-bound, I/O-bound, memory-bound or mostly waiting.

Labels come from CPU use against CPUs requested, read and write rate against
elapsed time, and peak RSS against the memory request. A step with none of
these measurements is "unknown", never guessed from the others.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from pipeprof.timeline import Task

# Nextflow and Snakemake (with --benchmark-extended) report CPU as a percent
# of one core; dividing by CPUs requested gives the share of the request
# actually used. 80% leaves headroom for sampling noise around a tool that is
# genuinely keeping its cores busy.
CPU_BOUND_THRESHOLD = 0.8

# Below this, CPU use alone does not explain the time: under three tenths of
# one requested core busy, the step is waiting on something else (another
# task, a lock, a scheduler) rather than computing or moving data.
CPU_WAIT_THRESHOLD = 0.3

# A single sequential stream below this on a shared HPC filesystem is the
# filesystem, not the tool: NFS and parallel filesystems common on clusters
# struggle to sustain much more than this for many small, concurrent jobs,
# so a step moving less than this while barely using its CPU is read as
# I/O-bound rather than idle.
IO_BOUND_BYTES_PER_S = 20 * 1024 * 1024

# Peak RSS within this share of the request is read as memory-bound: the
# step is sized by what it was given, and a slightly larger input next time
# risks an out-of-memory kill.
MEM_BOUND_FRACTION = 0.8


@dataclass
class BoundLabel:
    """A step's label and the evidence it rests on."""

    step: str
    label: str
    evidence: list[str] = field(default_factory=list)


def label_step(name: str, tasks: Sequence[Task]) -> BoundLabel:
    """Label one step from its tasks' measured rates against their requests."""
    cpu_shares = [
        t.cpu_percent / 100.0 / t.cpus
        for t in tasks
        if t.cpu_percent is not None and t.cpus
    ]
    io_rates = [
        ((t.read_bytes or 0) + (t.write_bytes or 0)) / t.wall_s
        for t in tasks
        if t.wall_s and (t.read_bytes is not None or t.write_bytes is not None)
    ]
    mem_fracs = [
        t.max_rss_bytes / t.mem_request_bytes
        for t in tasks
        if t.max_rss_bytes is not None and t.mem_request_bytes
    ]

    if not cpu_shares and not io_rates and not mem_fracs:
        return BoundLabel(name, "unknown", ["no CPU, I/O or memory measurements for this step"])

    avg_cpu = sum(cpu_shares) / len(cpu_shares) if cpu_shares else None
    avg_io = sum(io_rates) / len(io_rates) if io_rates else None
    max_mem = max(mem_fracs) if mem_fracs else None

    evidence: list[str] = []
    if avg_cpu is not None:
        evidence.append(f"CPU use {avg_cpu:.0%} of requested cores over {len(cpu_shares)} task(s)")
    if avg_io is not None:
        evidence.append(f"I/O rate {avg_io / (1024 * 1024):.1f} MiB/s over {len(io_rates)} task(s)")
    if max_mem is not None:
        evidence.append(f"peak RSS {max_mem:.0%} of requested memory over {len(mem_fracs)} task(s)")

    if max_mem is not None and max_mem >= MEM_BOUND_FRACTION:
        return BoundLabel(
            name, "memory-bound", [*evidence, f"crossed the {MEM_BOUND_FRACTION:.0%} memory threshold"]
        )
    if avg_cpu is not None and avg_cpu >= CPU_BOUND_THRESHOLD:
        return BoundLabel(
            name, "cpu-bound", [*evidence, f"crossed the {CPU_BOUND_THRESHOLD:.0%} CPU threshold"]
        )
    if (
        avg_io is not None
        and avg_io >= IO_BOUND_BYTES_PER_S
        and (avg_cpu is None or avg_cpu < CPU_BOUND_THRESHOLD)
    ):
        return BoundLabel(
            name,
            "io-bound",
            [*evidence, f"crossed {IO_BOUND_BYTES_PER_S / (1024 * 1024):.0f} MiB/s I/O threshold"],
        )
    if avg_cpu is not None and avg_cpu < CPU_WAIT_THRESHOLD:
        return BoundLabel(
            name,
            "mostly waiting",
            [*evidence, f"under the {CPU_WAIT_THRESHOLD:.0%} CPU threshold for waiting"],
        )
    return BoundLabel(name, "unknown", [*evidence, "no threshold crossed"])


def label_steps(tasks_by_step: Mapping[str, Sequence[Task]]) -> list[BoundLabel]:
    """Label every step, sorted by name."""
    return [label_step(name, group) for name, group in sorted(tasks_by_step.items())]
