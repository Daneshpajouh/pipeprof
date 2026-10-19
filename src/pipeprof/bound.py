"""Per-step boundness: CPU-bound, I/O-bound, memory-bound or mostly waiting.

Labels come from CPU use (cores kept busy, and their share of the CPUs
requested), read and write rate against elapsed time, and peak RSS against
the memory request. A step with none of these measurements is "unknown",
never guessed from the others.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from pipeprof.timeline import Task

# Nextflow and Snakemake (with --benchmark-extended) report CPU as a percent
# of one core; divided by 100 that is cores kept busy. A step is CPU-bound
# when it keeps this share of its requested cores busy, or, whatever it
# requested, at least this much of one core: then its time goes on computing,
# on as many cores as the tool actually uses. 80% leaves headroom for
# sampling noise around a tool that is genuinely keeping its cores busy.
CPU_BOUND_THRESHOLD = 0.8

# Below this many cores busy (a share of ONE core, not of the request),
# computing does not explain the time: the step is waiting on something else
# (another task, a lock, a slow filesystem, the scheduler). The same constant
# and the same one-core reading decide "mostly waiting" in pipeprof calibrate
# (calibrate.waiting_runs), so both commands call the same runs waiting. A
# single-threaded tool given 4 CPUs at 100% of one core is not waiting.
CPU_WAIT_THRESHOLD = 0.3

# NFS and parallel filesystems common on clusters struggle to sustain much
# more than this per stream for many small, concurrent jobs. A step at low
# CPU moving data at or above this rate is limited by I/O: its time goes on
# moving data. A step at low CPU moving less is not I/O-bound; it is waiting
# on something else (often a slow or shared filesystem's latency, a lock or
# the queue), and is labelled by its CPU use alone.
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
    #: Mean cores kept busy (percent of one core / 100), when CPU was measured.
    cores_busy: float | None = None
    #: Mean CPUs requested, when the source records them.
    cpus_requested: float | None = None


def label_step(name: str, tasks: Sequence[Task]) -> BoundLabel:
    """Label one step from its tasks' measured rates against their requests."""
    cores = [t.cpu_percent / 100.0 for t in tasks if t.cpu_percent is not None]
    cpu_shares = [
        t.cpu_percent / 100.0 / t.cpus for t in tasks if t.cpu_percent is not None and t.cpus
    ]
    requested = [float(t.cpus) for t in tasks if t.cpu_percent is not None and t.cpus]
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

    if not cores and not io_rates and not mem_fracs:
        return BoundLabel(name, "unknown", ["no CPU, I/O or memory measurements for this step"])

    avg_cores = sum(cores) / len(cores) if cores else None
    avg_share = sum(cpu_shares) / len(cpu_shares) if cpu_shares else None
    avg_cpus = sum(requested) / len(requested) if requested else None
    avg_io = sum(io_rates) / len(io_rates) if io_rates else None
    max_mem = max(mem_fracs) if mem_fracs else None

    def make(label: str, why: list[str]) -> BoundLabel:
        return BoundLabel(name, label, [*evidence, *why], avg_cores, avg_cpus)

    evidence: list[str] = []
    if avg_cores is not None:
        line = f"CPU use {avg_cores:.2f} cores busy over {len(cores)} task(s)"
        if avg_share is not None and avg_cpus is not None:
            line += f", {avg_share:.0%} of {avg_cpus:g} requested"
        evidence.append(line)
    if avg_io is not None:
        evidence.append(
            f"I/O rate {avg_io / (1024 * 1024):.1f} MiB/s over {len(io_rates)} task(s)"
        )
    if max_mem is not None:
        evidence.append(
            f"peak RSS {max_mem:.0%} of requested memory over {len(mem_fracs)} task(s)"
        )

    if max_mem is not None and max_mem >= MEM_BOUND_FRACTION:
        return make("memory-bound", [f"crossed the {MEM_BOUND_FRACTION:.0%} memory threshold"])
    if avg_share is not None and avg_share >= CPU_BOUND_THRESHOLD:
        return make("cpu-bound", [f"crossed the {CPU_BOUND_THRESHOLD:.0%} CPU threshold"])
    if avg_cores is not None and avg_cores >= CPU_BOUND_THRESHOLD:
        # Computing, on fewer cores than requested: the tool uses about this many.
        why = f"at least {CPU_BOUND_THRESHOLD:.0%} of one core busy"
        if avg_cpus is not None:
            why += f", on about {math.ceil(avg_cores - 0.15)} of {avg_cpus:g} requested cores"
        return make("cpu-bound", [why])
    if avg_io is not None and avg_io >= IO_BOUND_BYTES_PER_S:
        return make(
            "io-bound",
            [f"crossed {IO_BOUND_BYTES_PER_S / (1024 * 1024):.0f} MiB/s I/O threshold"],
        )
    if avg_cores is not None and avg_cores < CPU_WAIT_THRESHOLD:
        why = [f"under {CPU_WAIT_THRESHOLD:.0%} of one core busy, the threshold for waiting"]
        if avg_io is not None:
            why.append(
                f"under {IO_BOUND_BYTES_PER_S / (1024 * 1024):.0f} MiB/s, so the time is not "
                "spent moving data"
            )
        return make("mostly waiting", why)
    return make("unknown", ["no threshold crossed"])


def label_steps(tasks_by_step: Mapping[str, Sequence[Task]]) -> list[BoundLabel]:
    """Label every step, sorted by name."""
    return [label_step(name, group) for name, group in sorted(tasks_by_step.items())]
