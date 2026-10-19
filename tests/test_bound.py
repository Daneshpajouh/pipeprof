from __future__ import annotations

from pipeprof.bound import (
    CPU_BOUND_THRESHOLD,
    CPU_WAIT_THRESHOLD,
    IO_BOUND_BYTES_PER_S,
    MEM_BOUND_FRACTION,
    label_step,
    label_steps,
)
from pipeprof.timeline import Task


def task(**kwargs: object) -> Task:
    base = dict(
        name="t", step="t", submit=None, start=0.0, end=10.0, attempt=1, cpus=1,
        status="COMPLETED",
    )
    base.update(kwargs)
    return Task(**base)  # type: ignore[arg-type]


def test_cpu_bound() -> None:
    t = task(cpu_percent=(CPU_BOUND_THRESHOLD + 0.05) * 100, cpus=1)
    label = label_step("step", [t])
    assert label.label == "cpu-bound"
    assert "CPU use" in label.evidence[0]


def test_io_bound_when_cpu_is_low_and_io_is_high() -> None:
    t = task(
        cpu_percent=10.0,
        cpus=1,
        start=0.0,
        end=1.0,
        read_bytes=IO_BOUND_BYTES_PER_S,
        write_bytes=IO_BOUND_BYTES_PER_S,
    )
    label = label_step("step", [t])
    assert label.label == "io-bound"


def test_memory_bound_takes_priority() -> None:
    t = task(
        cpu_percent=(CPU_BOUND_THRESHOLD + 0.1) * 100,
        cpus=1,
        max_rss_bytes=int(MEM_BOUND_FRACTION * 1000) + 10,
        mem_request_bytes=1000,
    )
    label = label_step("step", [t])
    assert label.label == "memory-bound"


def test_mostly_waiting_when_cpu_is_very_low() -> None:
    t = task(cpu_percent=(CPU_WAIT_THRESHOLD - 0.1) * 100, cpus=1)
    label = label_step("step", [t])
    assert label.label == "mostly waiting"


def test_unknown_with_no_measurements() -> None:
    t = task(cpu_percent=None, max_rss_bytes=None, read_bytes=None, write_bytes=None)
    label = label_step("step", [t])
    assert label.label == "unknown"
    assert "no CPU" in label.evidence[0]


def test_unknown_in_the_middle_band() -> None:
    # Between the waiting and cpu-bound thresholds, with no I/O or memory data.
    mid = (CPU_WAIT_THRESHOLD + CPU_BOUND_THRESHOLD) / 2
    t = task(cpu_percent=mid * 100, cpus=1)
    label = label_step("step", [t])
    assert label.label == "unknown"
    assert "no threshold crossed" in label.evidence[-1]


def test_label_steps_sorts_by_name() -> None:
    labels = label_steps({"b": [task()], "a": [task()]})
    assert [l.step for l in labels] == ["a", "b"]


def test_io_threshold_both_sides() -> None:
    # Low CPU and a high data rate: the step is limited by moving data.
    above = task(cpu_percent=10.0, start=0.0, end=1.0, read_bytes=IO_BOUND_BYTES_PER_S + 1)
    assert label_step("step", [above]).label == "io-bound"
    # Low CPU and little data moved: not I/O-bound; it is waiting on something
    # else (a slow or shared filesystem, a lock, the queue).
    below = task(cpu_percent=10.0, start=0.0, end=1.0, read_bytes=IO_BOUND_BYTES_PER_S - 1)
    label = label_step("step", [below])
    assert label.label == "mostly waiting"
    assert any("not spent moving data" in e for e in label.evidence)


def test_waiting_is_a_share_of_one_core() -> None:
    from pipeprof.advise import bound_thread_advice

    # A single-threaded tool given 4 CPUs, keeping its one core fully busy:
    # 25% of the request, but not waiting. It is CPU-bound on one core, and
    # more threads will not help because it uses one.
    single = task(cpu_percent=100.0, cpus=4)
    label = label_step("step", [single])
    assert label.label == "cpu-bound"
    assert any("1.00 cores busy" in e for e in label.evidence)
    advice = bound_thread_advice(label)
    assert advice is not None and "more threads will not help" in advice.basis
    assert "1.0 of 4" in advice.basis

    # Under 0.3 of one core busy is waiting whatever the request.
    assert label_step("step", [task(cpu_percent=20.0, cpus=4)]).label == "mostly waiting"
    assert label_step("step", [task(cpu_percent=20.0, cpus=1)]).label == "mostly waiting"
    # A tool using all four cores it asked for is CPU-bound, and gets no thread advice.
    full = label_step("step", [task(cpu_percent=390.0, cpus=4)])
    assert full.label == "cpu-bound"
    assert bound_thread_advice(full) is None


def test_waiting_matches_calibrate() -> None:
    from pipeprof.calibrate import RunPoint, waiting_runs
    from pipeprof.measure import Measurement

    # The same run read by both commands: 10 s wall, 2.5 s CPU, 4 threads given.
    m = Measurement("x", 0, 10.0, 2.5, 0.0, 1, 1, 1, None, None, 0, 0, 1, 0.05)
    run = RunPoint(0.1, 1, "p", 1, 1, m)
    assert waiting_runs([run]) == [run]
    as_task = task(cpu_percent=25.0, cpus=4)
    assert label_step("step", [as_task]).label == "mostly waiting"
    m_busy = Measurement("x", 0, 10.0, 4.0, 0.0, 1, 1, 1, None, None, 0, 0, 1, 0.05)
    assert waiting_runs([RunPoint(0.1, 1, "p", 1, 1, m_busy)]) == []
    assert label_step("step", [task(cpu_percent=40.0, cpus=4)]).label != "mostly waiting"
