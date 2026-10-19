"""Whole-run bottleneck analysis over a :mod:`~pipeprof.timeline`.

Critical path, each step's share of wall time, parallel efficiency over
time, idle gaps, queue wait and retries. Every number comes from tasks that
carry the times it needs; a task missing a time is left out of the
calculation that needs it, never guessed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pipeprof.timeline import Task


@dataclass
class CriticalPathStep:
    """One task on the critical path, and how long it waited for its predecessor."""

    task: Task
    gap_before_s: float


@dataclass
class CriticalPath:
    """The chain of tasks whose busy time plus waiting equals the total wall time."""

    steps: list[CriticalPathStep]
    method: str
    wall_s: float
    busy_s: float
    waiting_s: float


@dataclass
class StepShare:
    """One step's (process or rule) time, on the critical path and overall."""

    step: str
    critical_path_s: float
    busy_s: float
    task_count: int


@dataclass
class EfficiencyPoint:
    """The run's concurrency level from this time until the next point."""

    at: float
    running: int
    cpus_in_use: int


@dataclass
class ParallelEfficiency:
    """Tasks running, or CPUs in use, against CPUs available, as a step function."""

    points: list[EfficiencyPoint]
    cpus_available: int
    average: float
    low_intervals: list[tuple[float, float]]
    low_total_s: float
    threshold: float


@dataclass
class IdleGap:
    """A span with no task running."""

    start: float
    end: float

    @property
    def duration_s(self) -> float:
        return self.end - self.start


@dataclass
class Retry:
    """A task name that needed more than one attempt, and the time its failures cost."""

    name: str
    attempts: int
    lost_s: float


@dataclass
class BottleneckReport:
    critical_path: CriticalPath
    step_shares: list[StepShare]
    parallel_efficiency: ParallelEfficiency | None
    idle_gaps: list[IdleGap]
    idle_total_s: float
    queue_wait_s: list[tuple[str, float]]
    queue_wait_total_s: float
    retries: list[Retry]
    retry_lost_s: float = 0.0
    #: Nextflow tasks reused with -resume, left out of every calculation above.
    cached_tasks: int = 0


# Critical path


def _infer_predecessor(task: Task, candidates: Sequence[Task]) -> Task | None:
    """The task that finished most recently before ``task`` started.

    Used when the source does not name dependencies: Nextflow traces and
    Snakemake logs record what ran and when, not a DAG edge list, so the
    conservative stand-in is "whatever last finished is what this one was
    probably waiting on or scheduled after".
    """
    before = [
        c for c in candidates if c is not task and c.end is not None and c.end <= task.start
    ]
    if not before:
        return None
    return max(before, key=lambda c: c.end)  # type: ignore[arg-type, return-value]


def critical_path(
    tasks: Sequence[Task], dependencies: Mapping[str, Sequence[str]] | None = None
) -> CriticalPath:
    """The chain of tasks explaining the run's wall time, traced back from the last to finish.

    With declared ``dependencies`` (task name to the names it depends on),
    the predecessor at each step is the named dependency that finished
    latest, since that is the one that gated the task's start. Without
    declared dependencies the predecessor is inferred: the last task to
    finish before this one started. Either way the result says which method
    was used.
    """
    timed = [t for t in tasks if t.start is not None and t.end is not None]
    if not timed:
        return CriticalPath(steps=[], method="no tasks with both a start and an end", wall_s=0.0,
                             busy_s=0.0, waiting_s=0.0)

    if dependencies is not None:
        method = "declared dependencies, the one that finished latest at each step"
        by_name: dict[str, list[Task]] = {}
        for t in timed:
            by_name.setdefault(t.name, []).append(t)

        def predecessor(t: Task) -> Task | None:
            names = dependencies.get(t.name, ())
            candidates = [c for n in names for c in by_name.get(n, []) if c is not t]
            if not candidates:
                return None
            return max(candidates, key=lambda c: c.end)  # type: ignore[arg-type, return-value]
    else:
        method = "inferred: the last task that finished before this one started"

        def predecessor(t: Task) -> Task | None:
            return _infer_predecessor(t, timed)

    sink = max(timed, key=lambda t: t.end)  # type: ignore[arg-type, return-value]
    chain: list[Task] = [sink]
    seen = {id(sink)}
    current = sink
    while True:
        prev = predecessor(current)
        if prev is None or id(prev) in seen:
            break
        chain.append(prev)
        seen.add(id(prev))
        current = prev
    chain.reverse()

    steps: list[CriticalPathStep] = []
    for i, t in enumerate(chain):
        gap = 0.0 if i == 0 else max(0.0, t.start - chain[i - 1].end)  # type: ignore[operator]
        steps.append(CriticalPathStep(task=t, gap_before_s=gap))
    busy = sum((t.end - t.start) for t in chain)  # type: ignore[operator]
    waiting = sum(s.gap_before_s for s in steps)
    wall = chain[-1].end - chain[0].start  # type: ignore[operator]
    return CriticalPath(steps=steps, method=method, wall_s=wall, busy_s=busy, waiting_s=waiting)


def step_shares(tasks: Sequence[Task], path: CriticalPath) -> list[StepShare]:
    """Each step's time on the critical path, and its busy time over every task."""
    on_path: dict[str, float] = {}
    for s in path.steps:
        busy_s = s.task.end - s.task.start  # type: ignore[operator]
        on_path[s.task.step] = on_path.get(s.task.step, 0.0) + busy_s
    busy: dict[str, float] = {}
    counts: dict[str, int] = {}
    for t in tasks:
        if t.start is None or t.end is None:
            continue
        busy[t.step] = busy.get(t.step, 0.0) + (t.end - t.start)
        counts[t.step] = counts.get(t.step, 0) + 1
    names = sorted(set(busy) | set(on_path))
    return [
        StepShare(
            step=name,
            critical_path_s=on_path.get(name, 0.0),
            busy_s=busy.get(name, 0.0),
            task_count=counts.get(name, 0),
        )
        for name in names
    ]


# Parallel efficiency


def parallel_efficiency(
    tasks: Sequence[Task], cpus_available: int, threshold: float = 0.5
) -> ParallelEfficiency | None:
    """Tasks running (weighted by CPUs where known) against CPUs available.

    Sampled at every start and end, as a step function: between two
    consecutive events the level is constant. A task with no known CPU count
    counts as one CPU. ``None`` with no timed tasks.
    """
    timed = [t for t in tasks if t.start is not None and t.end is not None]
    if not timed or cpus_available <= 0:
        return None
    events: list[tuple[float, int, int]] = []
    for t in timed:
        c = t.cpus if t.cpus else 1
        events.append((t.start, 1, c))  # type: ignore[arg-type]
        events.append((t.end, -1, -c))  # type: ignore[arg-type]
    # An end at the same instant as a start is processed first, so a task
    # that finishes exactly when another starts is never double counted.
    events.sort(key=lambda e: (e[0], e[1]))

    points: list[EfficiencyPoint] = []
    running = cpus_in_use = 0
    idx = 0
    times = sorted({e[0] for e in events})
    for ts in times:
        while idx < len(events) and events[idx][0] == ts:
            running += events[idx][1]
            cpus_in_use += events[idx][2]
            idx += 1
        points.append(EfficiencyPoint(at=ts, running=running, cpus_in_use=cpus_in_use))

    weighted = 0.0
    low: list[tuple[float, float]] = []
    low_total = 0.0
    for i in range(len(points) - 1):
        span = points[i + 1].at - points[i].at
        eff = points[i].cpus_in_use / cpus_available
        weighted += eff * span
        if eff < threshold:
            low.append((points[i].at, points[i + 1].at))
            low_total += span
    total_span = points[-1].at - points[0].at
    average = weighted / total_span if total_span > 0 else points[0].cpus_in_use / cpus_available
    return ParallelEfficiency(
        points=points,
        cpus_available=cpus_available,
        average=average,
        low_intervals=low,
        low_total_s=low_total,
        threshold=threshold,
    )


# Idle gaps


def idle_gaps(tasks: Sequence[Task]) -> tuple[list[IdleGap], float]:
    """Spans with no task running, found by merging overlapping busy intervals."""
    timed = sorted(
        (t for t in tasks if t.start is not None and t.end is not None),
        key=lambda t: t.start,  # type: ignore[arg-type, return-value]
    )
    if not timed:
        return [], 0.0
    merged: list[list[float]] = []
    for t in timed:
        if merged and t.start <= merged[-1][1]:  # type: ignore[operator]
            merged[-1][1] = max(merged[-1][1], t.end)  # type: ignore[arg-type]
        else:
            merged.append([t.start, t.end])  # type: ignore[list-item]
    gaps = [IdleGap(start=a[1], end=b[0]) for a, b in zip(merged, merged[1:]) if b[0] > a[1]]
    return gaps, sum(g.duration_s for g in gaps)


# Queue wait


def queue_waits(tasks: Sequence[Task]) -> tuple[list[tuple[str, float]], float]:
    """Start minus submit for every task that carries both."""
    waits = [(t.name, t.queue_wait_s) for t in tasks if t.queue_wait_s is not None]
    return waits, sum(w for _, w in waits)  # type: ignore[misc]


# Retries


def retries(tasks: Sequence[Task]) -> tuple[list[Retry], float]:
    """Tasks with more than one attempt, or a failure before a later success, and time lost."""
    by_name: dict[str, list[Task]] = {}
    for t in tasks:
        by_name.setdefault(t.name, []).append(t)
    out: list[Retry] = []
    lost_total = 0.0
    for name, group in sorted(by_name.items()):
        if len(group) < 2:
            continue
        has_retry = any(t.attempt > 1 for t in group) or any(
            t.status not in {"COMPLETED"} for t in group
        )
        if not has_retry:
            continue
        failed = [t for t in group if t.status != "COMPLETED"]
        lost = sum(
            (t.end - t.start) for t in failed if t.start is not None and t.end is not None
        )
        out.append(Retry(name=name, attempts=len(group), lost_s=lost))
        lost_total += lost
    return out, lost_total


def live_tasks(tasks: Sequence[Task]) -> list[Task]:
    """The tasks that ran in this run: every task but the cached ones."""
    return [t for t in tasks if t.status != "CACHED"]


def analyse(
    tasks: Sequence[Task],
    cpus_available: int | None = None,
    dependencies: Mapping[str, Sequence[str]] | None = None,
    efficiency_threshold: float = 0.5,
) -> BottleneckReport:
    """Run every bottleneck calculation and collect the result.

    Cached tasks (Nextflow ``-resume``) are left out and only counted: their
    times are from the run that computed them, maybe days earlier, and would
    stretch this run's critical path and idle time by that much.
    """
    cached = sum(1 for t in tasks if t.status == "CACHED")
    tasks = live_tasks(tasks)
    path = critical_path(tasks, dependencies)
    shares = step_shares(tasks, path)
    eff = (
        parallel_efficiency(tasks, cpus_available, efficiency_threshold)
        if cpus_available
        else None
    )
    gaps, idle_total = idle_gaps(tasks)
    waits, wait_total = queue_waits(tasks)
    retry_list, retry_lost = retries(tasks)
    return BottleneckReport(
        critical_path=path,
        step_shares=shares,
        parallel_efficiency=eff,
        idle_gaps=gaps,
        idle_total_s=idle_total,
        queue_wait_s=waits,
        queue_wait_total_s=wait_total,
        retries=retry_list,
        retry_lost_s=retry_lost,
        cached_tasks=cached,
    )
