from __future__ import annotations

from pathlib import Path

from pipeprof.bottleneck import (
    analyse,
    critical_path,
    idle_gaps,
    parallel_efficiency,
    queue_waits,
    retries,
    step_shares,
)
from pipeprof.timeline import Task, tasks_from_snakemake


def task(
    name: str,
    start: float | None,
    end: float | None,
    *,
    step: str | None = None,
    submit: float | None = None,
    attempt: int = 1,
    cpus: int | None = None,
    status: str = "COMPLETED",
) -> Task:
    return Task(
        name=name,
        step=step or name,
        submit=submit,
        start=start,
        end=end,
        attempt=attempt,
        cpus=cpus,
        status=status,
    )


def test_chain_of_two_tasks_is_the_whole_critical_path() -> None:
    a = task("a", 0, 10)
    b = task("b", 10, 25)
    path = critical_path([a, b])
    assert [s.task.name for s in path.steps] == ["a", "b"]
    assert path.method.startswith("inferred")
    assert path.busy_s == 25.0 and path.waiting_s == 0.0 and path.wall_s == 25.0


def test_two_parallel_tasks_critical_path_is_the_longer_one() -> None:
    short = task("short", 0, 10)
    long_ = task("long", 0, 30)
    path = critical_path([short, long_])
    assert [s.task.name for s in path.steps] == ["long"]
    assert path.busy_s == 30.0 and path.wall_s == 30.0


def test_idle_gap_shows_as_waiting_on_the_critical_path() -> None:
    a = task("a", 0, 10)
    b = task("b", 20, 30)  # starts 10s after a ends: a 10s idle gap
    path = critical_path([a, b])
    assert [s.task.name for s in path.steps] == ["a", "b"]
    assert path.steps[1].gap_before_s == 10.0
    assert path.busy_s == 20.0 and path.waiting_s == 10.0 and path.wall_s == 30.0

    gaps, total = idle_gaps([a, b])
    assert total == 10.0 and len(gaps) == 1
    assert gaps[0].start == 10.0 and gaps[0].end == 20.0


def test_retry_counts_attempts_and_lost_time() -> None:
    first = task("sort", 0, 10, attempt=1, status="FAILED")
    second = task("sort", 10, 25, attempt=2, status="COMPLETED")
    found, lost = retries([first, second])
    assert len(found) == 1
    assert found[0].name == "sort" and found[0].attempts == 2
    assert lost == 10.0  # only the failed attempt's time is lost


def test_task_with_no_submit_time_has_no_queue_wait() -> None:
    t = task("a", 0, 10)
    assert t.queue_wait_s is None
    waits, total = queue_waits([t, task("b", 10, 20, submit=5)])
    assert waits == [("b", 5.0)]
    assert total == 5.0


def test_one_task_only() -> None:
    t = task("solo", 0, 10)
    path = critical_path([t])
    assert [s.task.name for s in path.steps] == ["solo"]
    assert path.busy_s == 10.0 and path.waiting_s == 0.0 and path.wall_s == 10.0
    gaps, total = idle_gaps([t])
    assert gaps == [] and total == 0.0


def test_empty_input() -> None:
    path = critical_path([])
    assert path.steps == [] and path.wall_s == 0.0
    assert step_shares([], path) == []
    gaps, total = idle_gaps([])
    assert gaps == [] and total == 0.0
    assert parallel_efficiency([], 4) is None
    found, lost = retries([])
    assert found == [] and lost == 0.0


def test_overlapping_identical_times() -> None:
    a = task("a", 0, 10)
    b = task("b", 0, 10)
    path = critical_path([a, b])
    # Both start together with no earlier finisher: either can be picked as
    # the sole step on the path, but the path must span the whole run.
    assert len(path.steps) == 1
    assert path.wall_s == 10.0 and path.busy_s == 10.0


def test_parallel_efficiency_step_function() -> None:
    # a: 2 cpus from 0-10; b: 1 cpu from 5-15; cpus available 4.
    a = task("a", 0, 10, cpus=2)
    b = task("b", 5, 15, cpus=1)
    eff = parallel_efficiency([a, b], cpus_available=4, threshold=0.5)
    assert eff is not None
    levels = [(p.at, p.cpus_in_use) for p in eff.points]
    assert levels == [(0, 2), (5, 3), (10, 1), (15, 0)]
    # 0-5 at 2/4=50% (not below), 5-10 at 3/4=75%, 10-15 at 1/4=25% (below).
    assert eff.low_intervals == [(10.0, 15.0)]
    assert eff.low_total_s == 5.0
    expected_average = (5 * 0.5 + 5 * 0.75 + 5 * 0.25) / 15
    assert abs(eff.average - expected_average) < 1e-9


def test_step_shares_group_by_step_name() -> None:
    a1 = task("a (1)", 0, 10, step="a")
    a2 = task("a (2)", 10, 15, step="a")
    b = task("b (1)", 0, 20, step="b")
    path = critical_path([a1, a2, b])  # b is the sink and spans the whole run alone
    shares = step_shares([a1, a2, b], path)
    by_step = {s.step: s for s in shares}
    assert by_step["b"].critical_path_s == 20.0
    assert by_step["a"].critical_path_s == 0.0  # not on this particular critical path
    assert by_step["a"].busy_s == 15.0 and by_step["a"].task_count == 2


def test_declared_dependencies_pick_the_latest_finishing_predecessor() -> None:
    a = task("a", 0, 10)
    b = task("b", 0, 25)  # finishes later than a, both are "roots" by time alone
    c = task("c", 25, 30)
    path = critical_path([a, b, c], dependencies={"c": ["a", "b"]})
    assert path.method.startswith("declared")
    assert [s.task.name for s in path.steps] == ["b", "c"]


def test_critical_path_on_the_real_macos_snakemake_run(data: Path) -> None:
    run = data / "snakemake_macos_run"
    tasks = tasks_from_snakemake(run / "benchmarks", run / "run.snakemake.log")
    report = analyse(tasks, cpus_available=4)
    assert report.critical_path.wall_s > 0
    assert (
        abs(
            report.critical_path.busy_s
            + report.critical_path.waiting_s
            - report.critical_path.wall_s
        )
        < 1e-6
    )
    assert report.parallel_efficiency is not None
    assert 0.0 <= report.parallel_efficiency.average <= 4.0
    names = {s.step for s in report.step_shares}
    assert names == {"extract_chrom", "sort_vcf", "stats"}
