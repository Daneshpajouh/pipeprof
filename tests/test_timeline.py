from __future__ import annotations

from pathlib import Path

from pipeprof.timeline import (
    Task,
    load_usage,
    tasks_from_nextflow_trace,
    tasks_from_snakemake,
    tasks_from_tsv,
)
from pipeprof.tools.usage import StepUsage


def by_name(tasks: list[Task]) -> dict[str, Task]:
    return {t.name: t for t in tasks}


def test_nextflow_default_trace_derives_start_and_complete(data: Path) -> None:
    tasks = by_name(tasks_from_nextflow_trace((data / "nextflow_trace_default.txt").read_text()))
    align = tasks["alignReads (S1)"]
    # submit 09:00:01.120, duration 1h2m5s (3725s), realtime 1h1m40s (3700s):
    # start = submit + (duration - realtime) = submit + 25s.
    assert align.start is not None and align.submit is not None
    assert align.start - align.submit == 25.0
    assert align.end is not None and align.end - align.start == 3700.0
    assert align.step == "alignReads"
    assert align.status == "COMPLETED"
    assert align.cpu_percent == 4402.5
    assert align.max_rss_bytes == round(38.2 * 1024**3)

    mutect = tasks["mutect2Wf:mutect2 (chr1)"]
    assert mutect.step == "mutect2Wf:mutect2"


def test_nextflow_raw_trace_uses_start_column_directly(data: Path) -> None:
    tasks = tasks_from_nextflow_trace((data / "nextflow_trace_raw.txt").read_text())
    by = by_name(tasks)
    chr1 = by["SORT_VCF (chr1)"]
    assert chr1.submit == 1760889600.0
    assert chr1.start == 1760889630.0
    assert chr1.end == 1760889630.0 + 245.0
    assert chr1.cpus == 1
    assert chr1.mem_request_bytes == 2 * 1024**3

    retries = [t for t in tasks if t.name == "SORT_VCF (chr2)"]
    assert [t.attempt for t in retries] == [1, 2]
    assert [t.status for t in retries] == ["FAILED", "COMPLETED"]


def test_nextflow_macos_dashes_stay_none() -> None:
    text = (
        "task_id\tprocess\tname\tstatus\texit\tattempt\tsubmit\tstart\trealtime\t%cpu\t"
        "peak_rss\tcpus\tmemory\ttime\n"
        "1\tSORT\tSORT (1)\tCOMPLETED\t0\t1\t1700000000000\t1700000001000\t2000\t-\t-\t1\t-\t-\n"
    )
    task = tasks_from_nextflow_trace(text)[0]
    assert task.cpu_percent is None
    assert task.max_rss_bytes is None
    assert task.mem_request_bytes is None
    assert task.start == 1700000001.0 and task.end == 1700000003.0


def test_snakemake_real_macos_run(data: Path) -> None:
    run = data / "snakemake_macos_run"
    tasks = tasks_from_snakemake(run / "benchmarks", run / "run.snakemake.log")
    assert {t.step for t in tasks} == {"extract_chrom", "sort_vcf", "stats"}
    assert len(tasks) == 18  # 17 benchmarked + the one that failed before writing one

    failed = [t for t in tasks if t.status == "FAILED"]
    assert len(failed) == 1 and failed[0].step == "stats"
    # No benchmark row for the failed job: times come from the log alone.
    assert failed[0].start is not None and failed[0].end is not None
    assert failed[0].end >= failed[0].start

    completed = [t for t in tasks if t.step == "sort_vcf"][0]
    assert completed.start is not None and completed.end is not None
    assert completed.mem_request_bytes == 1000 * 10**6
    assert completed.cpus == 1
    # macOS could not sample memory: every benchmark row has NA for max_rss.
    assert all(t.max_rss_bytes is None for t in tasks if t.status == "COMPLETED")


def test_snakemake_without_log_has_no_times(tmp_path: Path) -> None:
    bench_dir = tmp_path / "benchmarks" / "align"
    bench_dir.mkdir(parents=True)
    (bench_dir / "s1.tsv").write_text(
        "s\th:m:s\tmax_rss\tjobid\trule_name\n5.0\t0:00:05\t100\t1\talign\n"
    )
    tasks = tasks_from_snakemake(tmp_path / "benchmarks")
    assert len(tasks) == 1
    assert tasks[0].start is None and tasks[0].end is None
    assert tasks[0].submit is None


def test_tsv_builder_with_usage() -> None:
    text = "name\tstart\tend\nalign\t100\t160\nsort\t160\t190\n"
    usage = {"align": StepUsage(source="time", step="align", cpu_percent=85.0, max_rss_bytes=2048)}
    tasks = tasks_from_tsv(text, usage)
    align, sort = tasks
    assert align.start == 100.0 and align.end == 160.0 and align.cpu_percent == 85.0
    assert sort.cpu_percent is None


def test_tsv_builder_missing_times_stay_none() -> None:
    text = "name\tstart\tend\nqueued\t-\t-\n"
    task = tasks_from_tsv(text)[0]
    assert task.start is None and task.end is None


def test_load_usage_reads_json_lines(tmp_path: Path) -> None:
    path = tmp_path / "usage.jsonl"
    path.write_text(
        '{"source": "gnutime", "step": "sort_vcf", "max_rss_bytes": 123, "extra": "ignored"}\n'
        "\n"
    )
    usage = load_usage([path])
    assert usage["sort_vcf"].max_rss_bytes == 123
    assert usage["sort_vcf"].source == "gnutime"


def test_snakemake_standard_benchmarks_match_by_path(tmp_path: Path) -> None:
    # Standard benchmarks (no --benchmark-extended): no jobid column, so the
    # file stem is the only ID, and here the stems 1, 2, 3 are also the job
    # IDs of another rule. Times and threads must come from the jobs whose
    # benchmark: line names each file.
    bench = tmp_path / "benchmarks" / "sort"
    bench.mkdir(parents=True)
    for sample in (1, 2, 3):
        (bench / f"{sample}.tsv").write_text(
            "s\th:m:s\tmax_rss\tmax_vms\tmax_uss\tmax_pss\tio_in\tio_out\tmean_load\tcpu_time\n"
            f"{10 * sample}.0\t0:00:{10 * sample}\t100\t200\t90\t95\t1\t2\t50\t{5 * sample}.0\n"
        )
    lines = []
    for jobid in (1, 2, 3):
        lines += [
            f"[Mon Oct 19 10:0{jobid}:00 2026]",
            "rule index:",
            f"    output: idx/{jobid}.idx",
            f"    jobid: {jobid}",
            "    threads: 1",
            "",
            f"[Mon Oct 19 10:0{jobid}:30 2026]",
            f"Finished job {jobid}.",
        ]
    for sample, jobid in ((1, 4), (2, 5), (3, 6)):
        lines += [
            f"[Mon Oct 19 11:0{sample}:00 2026]",
            "rule sort:",
            f"    output: sorted/{sample}.vcf.gz",
            f"    benchmark: benchmarks/sort/{sample}.tsv",
            f"    jobid: {jobid}",
            "    threads: 4",
            "    resources: mem_mb=2000",
            "",
            f"[Mon Oct 19 11:0{sample}:59 2026]",
            f"Finished job {jobid}.",
        ]
    log = tmp_path / "run.log"
    log.write_text("\n".join(lines) + "\n")
    tasks = [t for t in tasks_from_snakemake(tmp_path / "benchmarks", log) if t.step == "sort"]
    assert len(tasks) == 3
    import datetime as dt

    for t in sorted(tasks, key=lambda t: t.start or 0):
        assert t.start is not None, t.name
    starts = sorted(t.start for t in tasks if t.start is not None)
    expected = [
        dt.datetime(2026, 10, 19, 11, minute, 0, tzinfo=dt.UTC).timestamp() for minute in (1, 2, 3)
    ]
    assert starts == expected
    by = {t.start: t for t in tasks}
    # Each file's own elapsed time follows its own job's start.
    assert by[expected[1]].wall_s == 20.0
    assert all(t.cpus == 4 for t in tasks)
    assert all(t.mem_request_bytes == 2000 * 10**6 for t in tasks)
    assert {t.name for t in tasks} == {"sort (job 4)", "sort (job 5)", "sort (job 6)"}
