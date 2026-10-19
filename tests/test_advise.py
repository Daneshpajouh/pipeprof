from __future__ import annotations

from pipeprof.advise import (
    REQUEST_TOO_LARGE_FRACTION,
    REQUEST_TOO_SMALL_FRACTION,
    THREAD_SCALING_THRESHOLD,
    advise,
    bound_thread_advice,
    pick_first_step,
    request_advice,
    thread_scaling_advice,
)
from pipeprof.bottleneck import critical_path
from pipeprof.bound import BoundLabel, label_step
from pipeprof.timeline import Task


def task(**kwargs: object) -> Task:
    base = dict(
        name="t", step="t", submit=None, start=0.0, end=10.0, attempt=1, cpus=1,
        status="COMPLETED",
    )
    base.update(kwargs)
    return Task(**base)  # type: ignore[arg-type]


def test_pick_first_step_is_the_biggest_critical_path_holder() -> None:
    a = task(name="a", step="a", start=0, end=10)
    b = task(name="b", step="b", start=10, end=40)  # 30s, the bigger share
    path = critical_path([a, b])
    advice = pick_first_step(path, factor=2.0)
    assert advice is not None
    assert advice.step == "b"
    assert advice.current_wall_s == 40.0
    assert advice.saving_s == 15.0  # 30s halved
    assert advice.expected_wall_s == 25.0


def test_pick_first_step_empty_path_is_none() -> None:
    path = critical_path([])
    assert pick_first_step(path) is None


def test_thread_scaling_below_threshold_flags_advice() -> None:
    # 2 -> 4 threads only goes from 100s to 95s: a 1.05x speed-up.
    advice = thread_scaling_advice("align", {1: 180.0, 2: 100.0, 4: 95.0})
    assert advice is not None
    assert advice.speedup < THREAD_SCALING_THRESHOLD
    assert advice.from_threads == 2 and advice.to_threads == 4


def test_thread_scaling_above_threshold_is_none() -> None:
    assert thread_scaling_advice("align", {2: 100.0, 4: 40.0}) is None


def test_thread_scaling_needs_both_points() -> None:
    assert thread_scaling_advice("align", {1: 100.0, 2: 60.0}) is None


def test_bound_thread_advice_for_io_bound_and_waiting() -> None:
    io = BoundLabel(step="copy", label="io-bound", evidence=["x"])
    waiting = BoundLabel(step="lock", label="mostly waiting", evidence=["y"])
    cpu = BoundLabel(step="compute", label="cpu-bound", evidence=["z"])
    assert bound_thread_advice(io) is not None
    assert bound_thread_advice(waiting) is not None
    assert bound_thread_advice(cpu) is None


def test_request_too_large() -> None:
    t = task(max_rss_bytes=int(1000 * (REQUEST_TOO_LARGE_FRACTION - 0.1)), mem_request_bytes=1000)
    advice = request_advice("step", [t])
    assert any(a.kind == "too large" for a in advice)


def test_request_too_small_near_oom() -> None:
    t = task(
        status="OUT_OF_MEMORY",
        max_rss_bytes=int(1000 * REQUEST_TOO_SMALL_FRACTION) + 1,
        mem_request_bytes=1000,
    )
    advice = request_advice("step", [t])
    assert any(a.kind == "too small" for a in advice)


def test_request_advice_silent_when_within_bounds() -> None:
    t = task(max_rss_bytes=700, mem_request_bytes=1000)  # 70%, fine
    assert request_advice("step", [t]) == []


def test_advise_end_to_end() -> None:
    a = task(name="a", step="a", start=0, end=10, cpu_percent=90.0, cpus=1)
    b = task(name="b", step="b", start=10, end=40, cpu_percent=10.0, cpus=1)
    tasks = [a, b]
    path = critical_path(tasks)
    labels = [label_step("a", [a]), label_step("b", [b])]
    result = advise(tasks, path, labels)
    assert result.first_step is not None and result.first_step.step == "b"
    assert any(adv.step == "b" for adv in result.thread_advice)  # b is "mostly waiting"
