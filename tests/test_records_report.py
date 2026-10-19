from __future__ import annotations

import json
from pathlib import Path

import pytest

from hpc_job_profiler.records import (
    TaskRecord,
    apply_snakemake_log,
    nextflow_process_name,
    parse_nextflow_trace,
    parse_sacct,
    parse_snakemake_benchmark,
    parse_snakemake_log,
    read_sources,
)
from hpc_job_profiler.report import Thresholds, build_report, to_html, to_json, to_markdown
from hpc_job_profiler.slurm import SlurmError, parse_parsable
from hpc_job_profiler.units import GIB, MIB


def by_id(records: list[TaskRecord]) -> dict[str, TaskRecord]:
    return {r.job_id: r for r in records}


def test_sacct_current_format(data: Path) -> None:
    records = by_id(parse_sacct((data / "sacct_slurm_26_05.txt").read_text()))
    assert set(records) == {
        "1001",
        "1002",
        "1003",
        "1004",
        "1005_1",
        "1005_2",
        "1005_3",
        "1005_[4-6]",
        "1006",
        "1007",
        "1008",
        "1009",
    }
    ok = records["1001"]
    assert ok.state == "COMPLETED" and ok.req_mem_bytes == 32 * GIB
    assert ok.max_rss_bytes == round(6.40 * GIB)  # from the batch step, not extern
    assert ok.cpu_time_s == pytest.approx(7 * 3600 + 12 * 60 + 30)
    assert ok.queue_wait_s == 250 and ok.node == "node017"
    assert records["1002"].state == "OUT_OF_MEMORY" and records["1002"].signal == 125
    assert records["1003"].state == "TIMEOUT"
    assert records["1004"].state == "NODE_FAIL" and records["1004"].max_rss_bytes is None
    assert records["1005_3"].state == "FAILED" and records["1005_3"].exit_code == 1
    assert records["1005_1"].array_job_id == "1005" and records["1005_1"].array_task_id == "1"
    assert records["1005_[4-6]"].state == "PENDING"
    assert records["1006"].state == "CANCELLED" and records["1006"].queue_wait_s is None
    assert records["1007"].max_rss_bytes is None
    assert any("MaxRSS" in n for n in records["1007"].notes)
    assert records["1008"].state == "RUNNING"
    assert records["1009"].req_mem_bytes == 16 * GIB


def test_sacct_slurm_20_11_format(data: Path) -> None:
    records = by_id(parse_sacct((data / "sacct_slurm_20_11.txt").read_text()))
    assert records["2001"].req_mem_bytes == 4 * 4000 * MIB  # 4000Mc x 4 CPUs
    assert records["2001"].max_rss_bytes == 3145728 * 1024
    assert records["2002"].state == "OUT_OF_MEMORY"  # job FAILED, batch step OOM
    assert "ran out of memory" in records["2002"].notes[0]
    assert records["2002"].req_mem_bytes == 16000 * MIB
    assert records["2003"].time_limit_s == 86400 and records["2003"].queue_wait_s == 7200


def test_sacct_edge_cases() -> None:
    assert parse_sacct("") == []
    with pytest.raises(ValueError, match="JobID"):
        parse_sacct("State|Elapsed\nCOMPLETED|00:01:00\n")
    steps_only = "JobID|State|Elapsed|MaxRSS|TotalCPU\n55.batch|COMPLETED|00:01:00|10M|00:00:30\n"
    rec = parse_sacct(steps_only)[0]
    assert rec.job_id == "55" and rec.max_rss_bytes == 10 * MIB and rec.cpu_time_s == 30
    with pytest.raises(SlurmError, match="expected 2 fields"):
        parse_parsable("A|B\n1|2|3\n")
    assert parse_parsable("A|B|\n1|2|\n") == [{"A": "1", "B": "2"}]


def test_nextflow_default_trace(data: Path) -> None:
    records = parse_nextflow_trace((data / "nextflow_trace_default.txt").read_text())
    assert nextflow_process_name("mutect2Wf:mutect2 (chr1)") == "mutect2Wf:mutect2"
    names = [r.name for r in records]
    assert names.count("markDuplicates") == 2 and "mutect2Wf:createPassVcfs" in names
    first, retry = [r for r in records if r.name == "markDuplicates"]
    assert first.state == "FAILED" and first.signal == 9 and first.attempt == 1
    assert retry.attempt == 2 and retry.req_mem_bytes is None
    assert any("not near the limit" in n for n in first.notes)
    align = records[0]
    assert align.cpu_time_s == pytest.approx(3700 * 44.025)
    assert align.job_id == "5101"


def test_nextflow_raw_trace_infers_causes(data: Path) -> None:
    records = parse_nextflow_trace((data / "nextflow_trace_raw.txt").read_text())
    oom, timeout = records[1], records[3]
    assert oom.state == "OUT_OF_MEMORY" and oom.inferred_state
    assert timeout.state == "TIMEOUT" and timeout.inferred_state
    assert records[0].queue_wait_s == 30 and records[0].time_limit_s == 1800
    assert records[2].attempt == 2 and records[2].req_mem_bytes == 4 * GIB


def test_snakemake_real_macos_run(data: Path) -> None:
    run = data / "snakemake_macos_run"
    records = read_sources(
        snakemake_benchmarks=[run / "benchmarks"], snakemake_logs=[run / "run.snakemake.log"]
    )
    assert {r.name for r in records} == {"extract_chrom", "sort_vcf", "stats"}
    # Snakemake could not sample memory on macOS: every benchmark has NA.
    assert all(r.max_rss_bytes is None for r in records)
    failed = [r for r in records if r.state == "FAILED"]
    assert [r.name for r in failed] == ["stats"]
    sort = next(r for r in records if r.name == "sort_vcf")
    assert sort.req_mem_bytes == 1000 * 10**6 and sort.alloc_cpus == 1
    report = build_report(records)
    sort_rec = next(r for r in report.recommendations if r.name == "sort_vcf")
    assert sort_rec.mem_bytes is None
    assert any("no peak RSS" in b for b in sort_rec.basis)
    assert sort_rec.time_s is not None


def test_snakemake_benchmark_with_values_and_time_limit_seconds() -> None:
    text = (
        "s\th:m:s\tmax_rss\tmax_vms\tmax_uss\tmax_pss\tio_in\tio_out\tmean_load\tcpu_time\t"
        "jobid\trule_name\twildcards\tparams\tthreads\tcpu_usage\tresources\tinput_size_mb\n"
        "120.5\t0:02:00\t2048.00\t3000\t2000\t2010\t50\t10\t95.0\t114.4\t7\tcluster\t{}\t{}\t1\t"
        "NA\t{'mem_mb': 16000, 'time_limit': 57600, 'cpus': 1}\t{}\n"
    )
    rec = parse_snakemake_benchmark(text, "benchmarks/cluster/lib1.tsv")[0]
    assert rec.name == "cluster" and rec.max_rss_bytes == 2048 * MIB
    assert rec.time_limit_s == 57600 and rec.req_mem_bytes == 16000 * 10**6
    plain = parse_snakemake_benchmark("s\th:m:s\tmax_rss\n-\t0:00:03\tNA\n", "b/x.tsv")[0]
    assert plain.elapsed_s == 3 and plain.name == "b"


def test_snakemake_log_variants() -> None:
    log = (
        "rule align:\n    jobid: 3\n    wildcards: s=a\n    threads: 4\n"
        "    resources: mem_mb=8000, runtime=30\n"
        "[Mon Oct 19 11:00:00 2026]\nFinished job 3.\n"
        "rule call:\n    jobid: 4\n    resources: mem_mb=bad\n\n"
        "Error in rule call:\n    jobid: 4\n    message: None\n"
    )
    jobs = parse_snakemake_log(log)
    assert jobs["3"].status == "COMPLETED" and jobs["3"].threads == 4
    assert jobs["4"].status == "FAILED"
    records = [TaskRecord(source="snakemake", job_id="3", name="align", state="COMPLETED")]
    apply_snakemake_log(records, jobs)
    assert records[0].req_mem_bytes == 8000 * 10**6 and records[0].time_limit_s == 1800
    assert records[-1].name == "call" and records[-1].state == "FAILED"


def test_report_on_sacct(data: Path, tmp_path: Path) -> None:
    records = read_sources(sacct=[data / "sacct_slurm_26_05.txt"])
    report = build_report(records, Thresholds(), ["fixture"])
    assert report.totals.tasks == 10  # RUNNING and PENDING are left out
    assert report.totals.failed == 5
    recs = {r.name: r for r in report.recommendations}
    assert recs["bwa_mem"].mem_bytes == 8 * GIB  # 6.4 GiB x 1.2, rounded up
    assert recs["bwa_mem"].cpus == 8
    assert recs["markdup"].mem_bytes == 12 * GIB  # OOM at 8 GiB x 1.5
    assert recs["haplotypecaller"].time_s == 4 * 3600
    assert recs["bcftools_norm"].mem_bytes is None
    flags = {m.record.job_id: m.flags for m in report.tasks}
    assert any("memory over-request" in f for f in flags["1001"])
    assert any("time limit far above use" in f for f in flags["1005_1"])
    arrays = report.arrays
    assert len(arrays) == 1 and arrays[0].tasks == 3 and arrays[0].states["FAILED"] == 1
    md = to_markdown(report)
    assert "## Job arrays" in md and "OUT_OF_MEMORY: 1 task(s)" in md
    html = to_html(report)
    assert html.startswith("<!doctype html>") and "&lt;" not in html.split("<body>")[0]
    payload = json.loads(to_json(report))
    assert payload["totals"]["tasks"] == 10
    assert report.totals.core_hours_allocated > report.totals.core_hours_used


def test_report_nextflow_cpu_flag() -> None:
    rec = TaskRecord(
        source="nextflow",
        job_id="1",
        name="p",
        state="COMPLETED",
        elapsed_s=600,
        cpu_time_s=600,
        alloc_cpus=8,
        req_mem_bytes=GIB,
        max_rss_bytes=GIB // 2,
        time_limit_s=700,
    )
    report = build_report([rec])
    assert any("CPU underuse" in f for f in report.tasks[0].flags)
    assert report.recommendations[0].cpus == 1


def test_recorded_sacct_from_slurm_20_02_container(data: Path) -> None:
    # Real output from a single node Slurm 20.02 container with filetxt accounting:
    # no steps, no MaxRSS, AllocCPUS 0, ReqMem 0n, empty Timelimit, array tasks
    # listed under their raw job IDs.
    records = by_id(parse_sacct((data / "sacct_slurm_20_02_container.txt").read_text()))
    assert set(records) == {"2", "3", "4", "5", "6", "7", "8"}
    assert records["6"].state == "TIMEOUT" and records["6"].elapsed_s == 81
    assert records["4"].state == "FAILED" and records["4"].exit_code == 3
    done = records["3"]
    assert done.alloc_cpus is None and done.cpu_time_s is None
    assert done.req_mem_bytes is None and done.max_rss_bytes is None
    assert done.time_limit_s is None
    report = build_report(list(records.values()))
    recs = {r.name: r for r in report.recommendations}
    assert recs["arr"].cpus is None and recs["arr"].mem_bytes is None
    assert recs["arr"].time_s == 300
    assert any("limit not recorded" in b for b in recs["too_long"].basis)
    assert recs["fails"].basis == ["no completed tasks with measurements; no recommendation"]
