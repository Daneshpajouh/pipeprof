"""Recommendations with numbers, from the critical path and the bound labels.

Which step to work on first, where more threads will not help, and where a
resource request is too large or too small. Every recommendation states the
numbers and the rule it rests on.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pipeprof.bottleneck import CriticalPath
from pipeprof.bound import CPU_BOUND_THRESHOLD, BoundLabel, cores_used
from pipeprof.timeline import Task

# Below this, doubling the threads is not worth it: going from 2 to 4 threads
# for under 20% more throughput rarely pays for the extra cores on a shared
# cluster, where another job could use them.
THREAD_SCALING_THRESHOLD = 1.2

# Peak RSS under half the request wastes memory the scheduler could give to
# another job; the same 0.5 threshold report.py already flags as a
# "memory over-request".
REQUEST_TOO_LARGE_FRACTION = 0.5

# Within 5% of the memory limit is close enough that the next run, with
# slightly more input or on a different node, can tip into an out-of-memory
# kill.
REQUEST_TOO_SMALL_FRACTION = 0.95


@dataclass
class FirstStepAdvice:
    """Which critical-path step to speed up first, and the expected saving."""

    step: str
    current_wall_s: float
    speedup: float
    expected_wall_s: float
    saving_s: float
    basis: str


@dataclass
class ThreadAdvice:
    """Where more threads do, or do not, pay off."""

    step: str
    from_threads: int
    to_threads: int
    speedup: float
    basis: str


@dataclass
class RequestAdvice:
    """A resource request that is too large or too small, with the evidence."""

    step: str
    kind: str
    basis: str


@dataclass
class Advice:
    first_step: FirstStepAdvice | None
    thread_advice: list[ThreadAdvice]
    request_advice: list[RequestAdvice]


def pick_first_step(path: CriticalPath, factor: float = 2.0) -> FirstStepAdvice | None:
    """Which critical-path step to speed up first, and by how much wall time.

    The step chosen is whichever holds the most time on the critical path;
    the expected saving is that time's share recovered by ``factor``. This
    assumes the freed time is not absorbed by waiting elsewhere, so it is an
    approximation of recomputing the whole schedule, not a simulation of
    one; the basis string says so.
    """
    if not path.steps or factor <= 0:
        return None
    on_path: dict[str, float] = {}
    for s in path.steps:
        if s.task.start is not None and s.task.end is not None:
            on_path[s.task.step] = on_path.get(s.task.step, 0.0) + (s.task.end - s.task.start)
    if not on_path:
        return None
    step, busy = max(on_path.items(), key=lambda kv: kv[1])
    saving = busy * (1 - 1 / factor)
    return FirstStepAdvice(
        step=step,
        current_wall_s=path.wall_s,
        speedup=factor,
        expected_wall_s=max(0.0, path.wall_s - saving),
        saving_s=saving,
        basis=(
            f"{step} holds {busy:.1f}s of the critical path's {path.wall_s:.1f}s; a "
            f"{factor:g}x speed-up saves about {saving:.1f}s, assuming nothing else "
            "becomes the new bottleneck"
        ),
    )


def thread_scaling_advice(
    step: str, wall_s_by_threads: Mapping[int, float]
) -> ThreadAdvice | None:
    """Flag a step where going from 2 to 4 threads is not worth it.

    ``wall_s_by_threads`` maps thread count to wall time, for example the
    ``predictions["wall_s"].point`` of three ``pipeprof calibrate`` runs at
    ``--threads 1``, ``2`` and ``4`` (see ``calibrate.CalibrationResult``).
    """
    if 2 not in wall_s_by_threads or 4 not in wall_s_by_threads:
        return None
    w2, w4 = wall_s_by_threads[2], wall_s_by_threads[4]
    if w4 <= 0:
        return None
    speedup = w2 / w4
    if speedup >= THREAD_SCALING_THRESHOLD:
        return None
    return ThreadAdvice(
        step=step,
        from_threads=2,
        to_threads=4,
        speedup=speedup,
        basis=(
            f"{step}: 2 to 4 threads only sped it up {speedup:.2f}x (wall time {w2:.1f}s "
            f"to {w4:.1f}s); under the {THREAD_SCALING_THRESHOLD:g}x worth doubling the cores"
        ),
    )


def bound_thread_advice(label: BoundLabel) -> ThreadAdvice | None:
    """A step that will not go faster with more threads.

    I/O-bound and mostly-waiting steps are not limited by compute. A CPU-bound
    step that keeps well under its requested cores busy (a single-threaded
    tool given four CPUs) already has cores it does not use.
    """
    if (
        label.label == "cpu-bound"
        and label.cores_busy is not None
        and label.cpus_requested
        and label.cores_busy / label.cpus_requested < CPU_BOUND_THRESHOLD
    ):
        return ThreadAdvice(
            step=label.step,
            from_threads=0,
            to_threads=0,
            speedup=1.0,
            basis=f"{label.step} keeps {label.cores_busy:.1f} of {label.cpus_requested:g} "
            "requested cores busy; more threads will not help, because it uses about "
            f"{cores_used(label.cores_busy)}",
        )
    if label.label not in {"io-bound", "mostly waiting"}:
        return None
    return ThreadAdvice(
        step=label.step,
        from_threads=0,
        to_threads=0,
        speedup=1.0,
        basis=f"{label.step} is {label.label} ({'; '.join(label.evidence)}); more threads "
        "will not help",
    )


def request_advice(step: str, tasks: Sequence[Task]) -> list[RequestAdvice]:
    """A step's memory request, flagged as too large or too small, with the worst case."""
    out: list[RequestAdvice] = []
    completed = [
        t
        for t in tasks
        if t.status == "COMPLETED" and t.max_rss_bytes is not None and t.mem_request_bytes
    ]
    if completed:

        def _frac(t: Task) -> float:
            return t.max_rss_bytes / t.mem_request_bytes  # type: ignore[operator]

        worst = max(completed, key=_frac)
        frac = _frac(worst)
        if frac < REQUEST_TOO_LARGE_FRACTION:
            out.append(
                RequestAdvice(
                    step=step,
                    kind="too large",
                    basis=f"peak RSS {frac:.0%} of the request at most over "
                    f"{len(completed)} completed task(s); the request can come down",
                )
            )
    # Not-completed tasks whose peak RSS was already close to the request:
    # Task carries no exit code, so a failure close to its memory limit is
    # read as the resource cause, the same way an OOM kill would be.
    for t in tasks:
        # A cached task completed in an earlier run; it is not a failure.
        if t.status in {"COMPLETED", "CACHED"}:
            continue
        if (
            t.mem_request_bytes
            and t.max_rss_bytes is not None
            and t.max_rss_bytes / t.mem_request_bytes >= REQUEST_TOO_SMALL_FRACTION
        ):
            out.append(
                RequestAdvice(
                    step=step,
                    kind="too small",
                    basis=f"{t.name} ({t.status}): peak RSS was within "
                    f"{(1 - REQUEST_TOO_SMALL_FRACTION):.0%} of the "
                    f"{t.mem_request_bytes} byte request; raise it",
                )
            )
    return out


def advise(
    tasks: Sequence[Task],
    path: CriticalPath,
    bound_labels: Sequence[BoundLabel],
    thread_calibrations: Mapping[str, Mapping[int, float]] | None = None,
    speedup_factor: float = 2.0,
) -> Advice:
    """Run every recommendation and collect the result."""
    by_step: dict[str, list[Task]] = {}
    for t in tasks:
        by_step.setdefault(t.step, []).append(t)

    first = pick_first_step(path, speedup_factor)

    thread_advice: list[ThreadAdvice] = []
    for label in bound_labels:
        a = bound_thread_advice(label)
        if a:
            thread_advice.append(a)
    for step, by_threads in (thread_calibrations or {}).items():
        a = thread_scaling_advice(step, by_threads)
        if a:
            thread_advice.append(a)

    requests: list[RequestAdvice] = []
    for step, group in sorted(by_step.items()):
        requests += request_advice(step, group)

    return Advice(first_step=first, thread_advice=thread_advice, request_advice=requests)
