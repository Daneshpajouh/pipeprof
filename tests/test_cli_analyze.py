from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from pipeprof.cli import app

runner = CliRunner()


def test_analyze_nextflow_trace(data: Path, tmp_path: Path) -> None:
    out = tmp_path / "an"
    result = runner.invoke(
        app,
        [
            "analyze",
            "--nextflow-trace",
            str(data / "nextflow_trace_raw.txt"),
            "--cpus",
            "2",
            "--outdir",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    assert {p.name for p in out.iterdir()} == {"analysis.md", "analysis.json"}
    data_json = json.loads((out / "analysis.json").read_text())
    assert data_json["tasks"] == 4
    assert data_json["critical_path"]["wall_s"] > 0
    assert "Critical path" in result.output


def test_analyze_snakemake_benchmarks(data: Path, tmp_path: Path) -> None:
    run = data / "snakemake_macos_run"
    out = tmp_path / "an"
    result = runner.invoke(
        app,
        [
            "analyze",
            "--snakemake-benchmarks",
            str(run / "benchmarks"),
            "--snakemake-log",
            str(run / "run.snakemake.log"),
            "--cpus",
            "4",
            "--outdir",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    data_json = json.loads((out / "analysis.json").read_text())
    assert data_json["tasks"] == 18


def test_analyze_tsv(tmp_path: Path) -> None:
    tsv = tmp_path / "run.tsv"
    tsv.write_text("name\tstart\tend\na\t0\t10\nb\t10\t25\n")
    out = tmp_path / "an"
    result = runner.invoke(app, ["analyze", "--tsv", str(tsv), "--outdir", str(out)])
    assert result.exit_code == 0, result.output
    data_json = json.loads((out / "analysis.json").read_text())
    assert data_json["critical_path"]["wall_s"] == 25.0


def test_analyze_needs_exactly_one_source(tmp_path: Path) -> None:
    result = runner.invoke(app, ["analyze", "--outdir", str(tmp_path / "an")])
    assert result.exit_code == 2

    tsv = tmp_path / "run.tsv"
    tsv.write_text("name\tstart\tend\na\t0\t10\n")
    result = runner.invoke(
        app,
        [
            "analyze",
            "--tsv",
            str(tsv),
            "--nextflow-trace",
            str(tsv),
            "--outdir",
            str(tmp_path / "an"),
        ],
    )
    assert result.exit_code == 2


def test_analyze_empty_tsv_is_rejected(tmp_path: Path) -> None:
    tsv = tmp_path / "empty.tsv"
    tsv.write_text("name\tstart\tend\n")
    result = runner.invoke(app, ["analyze", "--tsv", str(tsv), "--outdir", str(tmp_path / "an")])
    assert result.exit_code == 2


def _assert_missing_reported(args: list[str], missing: Path) -> None:
    result = runner.invoke(app, args)
    assert result.exit_code == 2, result.output
    assert not isinstance(result.exception, FileNotFoundError)
    assert result.stderr.strip() == f"pipeprof: input not found: {missing}"


def test_report_names_each_missing_input(data: Path, tmp_path: Path) -> None:
    missing = tmp_path / "no_such_file.jsonl"
    out = ["--outdir", str(tmp_path / "rep")]
    for option in ("--sacct", "--nextflow-trace", "--snakemake-benchmarks", "--watch-log"):
        _assert_missing_reported(["report", option, str(missing), *out], missing)
    bench = data / "snakemake_macos_run" / "benchmarks"
    args = ["report", "--snakemake-benchmarks", str(bench), "--snakemake-log", str(missing)]
    _assert_missing_reported([*args, *out], missing)


def test_analyze_names_each_missing_input(data: Path, tmp_path: Path) -> None:
    missing = tmp_path / "no_such_file.txt"
    out = ["--outdir", str(tmp_path / "an")]
    for option in ("--nextflow-trace", "--snakemake-benchmarks", "--tsv"):
        _assert_missing_reported(["analyze", option, str(missing), *out], missing)
    bench = data / "snakemake_macos_run" / "benchmarks"
    args = ["analyze", "--snakemake-benchmarks", str(bench), "--snakemake-log", str(missing)]
    _assert_missing_reported([*args, *out], missing)
    trace = data / "nextflow_trace_raw.txt"
    args = ["analyze", "--nextflow-trace", str(trace), "--usage", str(missing)]
    _assert_missing_reported([*args, *out], missing)


def test_analyze_leaves_cached_tasks_out(tmp_path: Path) -> None:
    from pipeprof.bottleneck import analyse
    from pipeprof.timeline import tasks_from_nextflow_trace

    # A -resume run: ALIGN was cached from a run a day earlier (its times are
    # that run's), SORT and STATS ran now. The day-old times must not stretch
    # the critical path, idle time or efficiency of this run.
    day = 86_400_000
    now = 1_760_000_000_000
    header = (
        "task_id\tprocess\tname\tstatus\texit\tattempt\tsubmit\tstart\trealtime\t%cpu\t"
        "peak_rss\tcpus\tmemory\ttime\n"
    )
    rows = [
        f"1\tALIGN\tALIGN (s1)\tCACHED\t0\t1\t{now - day}\t{now - day}\t60000\t95\t1000\t1\t-\t-",
        f"2\tSORT\tSORT (s1)\tCOMPLETED\t0\t1\t{now}\t{now}\t20000\t95\t1000\t1\t-\t-",
        f"3\tSTATS\tSTATS (s1)\tCOMPLETED\t0\t1\t{now + 20000}\t{now + 20000}\t10000\t95\t"
        "1000\t1\t-\t-",
    ]
    text = header + "\n".join(rows) + "\n"
    tasks = tasks_from_nextflow_trace(text)
    assert [t.status for t in tasks] == ["CACHED", "COMPLETED", "COMPLETED"]
    report = analyse(tasks, cpus_available=1)
    assert report.cached_tasks == 1
    assert report.critical_path.wall_s == 30.0
    assert report.idle_total_s == 0.0
    assert {s.step for s in report.step_shares} == {"SORT", "STATS"}
    assert report.parallel_efficiency is not None
    assert report.parallel_efficiency.average == 1.0

    trace = tmp_path / "trace.txt"
    trace.write_text(text)
    out = tmp_path / "an"
    result = runner.invoke(
        app, ["analyze", "--nextflow-trace", str(trace), "--cpus", "1", "--outdir", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert "1 cached task(s) left out" in (out / "analysis.md").read_text()
    data_json = json.loads((out / "analysis.json").read_text())
    assert data_json["cached_tasks"] == 1
