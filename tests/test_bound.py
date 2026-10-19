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
