from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from pipeprof.cli import app
from pipeprof.timeline import Task, apply_usage, tasks_from_nextflow_trace
from pipeprof.tools.usage import StepUsage

runner = CliRunner()


def _task(name: str, **kw: float | int | None) -> Task:
    return Task(
        name=name, step=name.split(" ")[0], submit=None, start=0.0, end=10.0,
        attempt=1, cpus=1, status="COMPLETED", **kw,  # type: ignore[arg-type]
    )


def test_apply_usage_fills_only_missing_fields() -> None:
    tasks = [_task("A (1)", max_rss_bytes=100), _task("B (1)")]
    usage = {
        "A (1)": StepUsage(source="bsd_time", step="A (1)", wall_s=10.0, user_s=4.0,
                           sys_s=1.0, max_rss_bytes=999),
        "C (1)": StepUsage(source="bsd_time", step="C (1)", max_rss_bytes=5),
    }
    assert apply_usage(tasks, usage) == 1
    # The runner's own RSS wins; the CPU share comes from user plus sys over wall.
    assert tasks[0].max_rss_bytes == 100
    assert tasks[0].cpu_percent == 50.0
    assert tasks[1].max_rss_bytes is None


def test_apply_usage_prefers_reported_cpu_percent() -> None:
    tasks = [_task("A (1)")]
    usage = {"A (1)": StepUsage(source="gnu_time", step="A (1)", wall_s=10.0, user_s=1.0,
                                sys_s=0.0, cpu_percent=73.0)}
    apply_usage(tasks, usage)
    assert tasks[0].cpu_percent == 73.0


def test_apply_usage_no_wall_time_leaves_cpu_unknown() -> None:
    # Neither the task nor the tool has a wall time: the share stays unknown.
    tasks = [_task("A (1)")]
    tasks[0].start = tasks[0].end = None
    usage = {"A (1)": StepUsage(source="bsd_time", step="A (1)", user_s=1.0, sys_s=0.0)}
    apply_usage(tasks, usage)
    assert tasks[0].cpu_percent is None
    # The task's own 10 s wall time is enough when the tool has none.
    timed = [_task("A (1)")]
    apply_usage(timed, usage)
    assert timed[0].cpu_percent == 10.0


def test_macos_trace_has_times_but_no_metrics(data: Path) -> None:
    tasks = tasks_from_nextflow_trace((data / "nextflow_macos_run" / "trace.txt").read_text())
    assert len(tasks) == 25
    assert all(t.start is not None and t.end is not None for t in tasks)
    assert all(t.cpu_percent is None and t.max_rss_bytes is None for t in tasks)


def test_usage_command_reads_real_bsd_time(data: Path) -> None:
    path = data / "nextflow_macos_run" / "usage" / "split_c1.time"
    result = runner.invoke(app, ["usage", "bsd-time", str(path), "--step", "SPLIT (c1)"])
    assert result.exit_code == 0, result.output
    line = json.loads(result.output)
    assert line["step"] == "SPLIT (c1)"
    assert line["source"] == "bsd_time"
    assert line["max_rss_bytes"] > 0


def test_usage_command_rejects_unknown_tool_and_wrong_text(data: Path) -> None:
    result = runner.invoke(app, ["usage", "perf", str(data / "empty.txt"), "--step", "x"])
    assert result.exit_code == 2
    result = runner.invoke(app, ["usage", "bsd-time", str(data / "empty.txt"), "--step", "x"])
    assert result.exit_code == 1


def test_analyze_real_macos_run_with_tool_readings(data: Path, tmp_path: Path) -> None:
    run = data / "nextflow_macos_run"
    lines = []
    for path in sorted((run / "usage").glob("*.time")):
        base = path.stem
        if "_" in base:
            step, tag = base.split("_", 1)
            name = f"{step.upper()} ({tag})"
        else:
            name = base.upper()
        result = runner.invoke(app, ["usage", "bsd-time", str(path), "--step", name])
        assert result.exit_code == 0, result.output
        lines.append(result.output.strip())
    usage_file = tmp_path / "usage.jsonl"
    usage_file.write_text("\n".join(lines) + "\n")
    out = tmp_path / "an"
    result = runner.invoke(app, [
        "analyze", "--nextflow-trace", str(run / "trace.txt"), "--usage", str(usage_file),
        "--cpus", "4", "--outdir", str(out),
    ])
    assert result.exit_code == 0, result.output
    report = json.loads((out / "analysis.json").read_text())
    labels = {b["step"]: b["label"] for b in report["bound_labels"]}
    # Every bcftools step here runs one thread near full use; without the readings
    # the macOS trace alone would leave every label unknown.
    assert labels["SPLIT"] == "cpu-bound"
    assert report["advice"]["first_step"]["step"] == "SPLIT"


def test_apply_usage_cpu_share_over_the_task_wall_time() -> None:
    # The task ran 20 s by the runner's clock; the tool saw 10 s of it. The
    # docstring's rule: the share is over the task's wall time when it has one.
    tasks = [_task("A (1)")]
    tasks[0].end = 20.0
    usage = {"A (1)": StepUsage(source="bsd_time", step="A (1)", wall_s=10.0, user_s=4.0,
                                sys_s=1.0)}
    apply_usage(tasks, usage)
    assert tasks[0].cpu_percent == 25.0
    # No times on the task: the tool's own wall time is used.
    untimed = [Task(name="A (1)", step="A", submit=None, start=None, end=None, attempt=1,
                    cpus=1, status="COMPLETED")]
    apply_usage(untimed, usage)
    assert untimed[0].cpu_percent == 50.0


def test_usage_command_missing_file_is_one_line(tmp_path: Path) -> None:
    missing = tmp_path / "nope.time"
    result = runner.invoke(app, ["usage", "bsd-time", str(missing), "--step", "x"])
    assert result.exit_code == 2
    assert not isinstance(result.exception, FileNotFoundError)
    assert result.stderr.strip() == f"pipeprof: input not found: {missing}"


def test_analyze_tsv_derives_cpu_share_from_tool_times(tmp_path: Path) -> None:
    # The tool reading gives user plus sys time and no cpu_percent: the share
    # must be derived over the task's wall time, as for every other source.
    tsv = tmp_path / "run.tsv"
    tsv.write_text("name\tstart\tend\nalign\t0\t100\n")
    usage_file = tmp_path / "usage.jsonl"
    reading = {"source": "time", "step": "align", "user_s": 80.0, "sys_s": 10.0}
    usage_file.write_text(json.dumps(reading) + "\n")
    out = tmp_path / "an"
    result = runner.invoke(app, [
        "analyze", "--tsv", str(tsv), "--usage", str(usage_file), "--outdir", str(out),
    ])
    assert result.exit_code == 0, result.output
    assert "Tool readings matched 1 of 1 tasks" in result.output
    report = json.loads((out / "analysis.json").read_text())
    (label,) = report["bound_labels"]
    assert label["label"] == "cpu-bound"
    assert any("0.90 cores busy" in e for e in label["evidence"])
