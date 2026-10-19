from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from pipeprof.cli import app
from pipeprof.tail import render, sparkline, tail
from pipeprof.watch import (
    AlertConfig,
    AlertEngine,
    JsonlSink,
    Point,
    read_watch_log,
    watch_pid,
    watch_slurm,
)
from tests.conftest import write_vcf

runner = CliRunner()
PY = sys.executable


def test_alert_engine_rules() -> None:
    engine = AlertEngine(AlertConfig(mem_limit_bytes=1000, cpus=4, window_s=10, stall_s=20))
    events = engine.feed(Point(0, 950, 0.0, 0))
    assert [e["alert"] for e in events] == ["memory_near_limit"]
    events = engine.feed(Point(11, 100, 2.0, 5))
    names = {(e["alert"], e["status"]) for e in events}
    assert ("memory_near_limit", "cleared") in names and ("cpu_underuse", "started") in names
    engine.feed(Point(21, 100, 2.0, 5))
    events = engine.feed(Point(32, 100, 2.0, 5))
    assert ("stalled", "started") in {(e["alert"], e["status"]) for e in events}
    events = engine.feed(Point(45, 100, 200.0, 50))
    assert ("stalled", "cleared") in {(e["alert"], e["status"]) for e in events}


def test_watch_pid_writes_samples(tmp_path: Path) -> None:
    proc = subprocess.Popen([PY, "-c", "import time; x = bytearray(30_000_000); time.sleep(1)"])
    out = tmp_path / "w.jsonl"
    with out.open("w") as handle:
        n = watch_pid(proc.pid, JsonlSink(handle), AlertConfig(mem_limit_bytes=10), 0.1)
    proc.wait()
    records = read_watch_log(out)
    assert n >= 3 and records[0]["type"] == "start" and records[1]["type"] == "sample"
    assert any(r.get("alert") == "memory_near_limit" for r in records)


def test_watch_slurm_with_shim(slurm_shim: Path) -> None:
    out = slurm_shim / "slurm.jsonl"
    with out.open("w") as handle:
        n = watch_slurm("4242", JsonlSink(handle), AlertConfig(), interval=0, sleep=lambda s: None)
    records = read_watch_log(out)
    assert n == 4
    samples = [r for r in records if r["type"] == "sample"]
    assert samples[-1]["rss_bytes"] == 1950 * 1024 * 1024
    assert records[-1] == {**records[-1], "type": "end", "state": "COMPLETED"}
    assert any(r.get("alert") == "memory_near_limit" for r in records)


def test_tail_render(tmp_path: Path) -> None:
    log = tmp_path / "w.jsonl"
    log.write_text(
        json.dumps(
            {"type": "sample", "elapsed_s": 1, "rss_bytes": 10, "cpu_seconds": 1, "num_procs": 2}
        )
        + "\n"
        + json.dumps({"type": "alert", "alert": "stalled", "status": "started", "message": "x"})
        + "\n"
        + json.dumps({"type": "end"})
        + "\n{partial"
    )
    console = Console(record=True, width=100)
    tail(log, follow=False, console=console)
    assert "stalled" in console.export_text()
    with Path("/dev/null").open("w") as devnull:
        tail(log, follow=True, refresh_s=0, console=Console(file=devnull))
    assert sparkline([]) == "" and len(sparkline([1, 2, 3])) == 3
    assert render([]) is not None


def test_cli_version_and_provenance(tmp_path: Path) -> None:
    assert runner.invoke(app, ["--version"]).output.startswith("pipeprof ")
    f = tmp_path / "a.txt"
    f.write_text("abc")
    result = runner.invoke(app, ["provenance", str(f)])
    assert result.exit_code == 0 and json.loads(result.output)["inputs"][0]["size_bytes"] == 3


def test_cli_calibrate(tmp_path: Path) -> None:
    vcf = write_vcf(tmp_path / "in.vcf", 3000)
    out = tmp_path / "cal"
    result = runner.invoke(
        app,
        [
            "calibrate",
            str(vcf),
            "--outdir",
            str(out),
            "--fractions",
            "0.1,0.2,0.4",
            "--job-name",
            "count",
            "--validate-full",
            "--shell",
            "--",
            "grep -c . {input} > {output}",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (out / "calibration.json").exists() and (out / "sbatch_header.sh").exists()
    assert (out / "pipeprof.log.jsonl").exists()
    bad = runner.invoke(app, ["calibrate", str(vcf), "--outdir", str(out), "--", "false"])
    assert bad.exit_code == 2
    worse = runner.invoke(
        app, ["calibrate", str(vcf), "--outdir", str(out), "--fractions", "a,b", "--", "true"]
    )
    assert worse.exit_code != 0


def test_cli_report_files_and_slurm(data: Path, tmp_path: Path, slurm_shim: Path) -> None:
    out = tmp_path / "rep"
    result = runner.invoke(
        app,
        [
            "report",
            "--sacct",
            str(data / "sacct_slurm_20_11.txt"),
            "--nextflow-trace",
            str(data / "nextflow_trace_raw.txt"),
            "--snakemake-benchmarks",
            str(data / "snakemake_macos_run" / "benchmarks"),
            "--snakemake-log",
            str(data / "snakemake_macos_run" / "run.snakemake.log"),
            "--outdir",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    assert {p.name for p in out.iterdir()} == {"report.md", "report.html", "report.json"}
    live = runner.invoke(app, ["report", "--from-slurm", "1001", "--outdir", str(tmp_path / "l")])
    assert live.exit_code == 0 and (tmp_path / "l" / "sacct.txt").exists()
    show = runner.invoke(app, ["report", "--show-sacct"])
    assert "--parsable2" in show.output
    empty = tmp_path / "empty.txt"
    empty.write_text("")
    assert runner.invoke(app, ["report", "--sacct", str(empty)]).exit_code == 2


def test_cli_watch_modes(tmp_path: Path, slurm_shim: Path) -> None:
    out = tmp_path / "w.jsonl"
    r = runner.invoke(app, ["watch", "--out", str(out), "--slurm-job", "4242", "--interval", "0"])
    assert r.exit_code == 0, r.output
    r = runner.invoke(
        app,
        [
            "watch",
            "--out",
            str(out),
            "--interval",
            "0.1",
            "--mem-limit",
            "2G",
            "--",
            PY,
            "-c",
            "import time; time.sleep(0.5)",
        ],
    )
    assert r.exit_code == 0
    assert read_watch_log(out)[-1]["type"] == "end"
    proc = subprocess.Popen([PY, "-c", "import time; time.sleep(5)"])
    r = runner.invoke(
        app,
        [
            "watch",
            "--out",
            str(out),
            "--pid",
            str(proc.pid),
            "--interval",
            "0.1",
            "--max-seconds",
            "0.3",
        ],
    )
    proc.kill()
    proc.wait()
    assert r.exit_code == 0
    assert runner.invoke(app, ["watch", "--out", str(out)]).exit_code == 2
    assert runner.invoke(app, ["watch", "--out", str(out), "--slurm-job", "9"]).exit_code == 2
    assert runner.invoke(app, ["tail", str(out), "--once"]).exit_code == 0
    assert (
        runner.invoke(
            app, ["report", "--from-slurm", "1"], env={"PIPEPROF_SACCT": "/nonexistent/sacct"}
        ).exit_code
        == 2
    )


def test_run_id_links_calibrate_watch_and_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PIPEPROF_RUN_ID", "job-42")
    vcf = write_vcf(tmp_path / "in.vcf", 2000)
    cal = runner.invoke(
        app,
        [
            "calibrate",
            str(vcf),
            "--outdir",
            str(tmp_path / "cal"),
            "--fractions",
            "0.2,0.4,0.6",
            "--shell",
            "--",
            "grep -c . {input} > {output}",
        ],
    )
    assert cal.exit_code == 0, cal.output
    assert json.loads((tmp_path / "cal" / "calibration.json").read_text())["run_id"] == "job-42"
    watch_log = tmp_path / "w.jsonl"
    w = runner.invoke(
        app,
        [
            "watch",
            "--out",
            str(watch_log),
            "--interval",
            "0.1",
            "--mem-limit",
            "1G",
            "--cpus",
            "2",
            "--",
            PY,
            "-c",
            "import time; time.sleep(1.5)",
        ],
    )
    assert w.exit_code == 0
    lines = read_watch_log(watch_log)
    assert {r["run_id"] for r in lines} == {"job-42"}
    assert lines[0]["type"] == "start" and lines[0]["mem_limit_bytes"] == 1024**3
    rep = runner.invoke(
        app, ["report", "--watch-log", str(watch_log), "--outdir", str(tmp_path / "rep")]
    )
    assert rep.exit_code == 0, rep.output
    report = json.loads((tmp_path / "rep" / "report.json").read_text())
    assert report["run_id"] == "job-42"
    task = report["tasks"][0]["record"]
    assert task["source"] == "watch" and task["alloc_cpus"] == 2 and task["state"] == "COMPLETED"


def test_watch_log_without_samples_gives_no_record() -> None:
    from pipeprof.records import parse_watch_log

    assert parse_watch_log([{"type": "start"}]) == []
    rec = parse_watch_log(
        [
            {"type": "sample", "rss_bytes": 5, "elapsed_s": 2, "cpu_seconds": 1, "pid": 7},
            {"type": "end", "returncode": 3},
        ]
    )[0]
    assert rec.state == "FAILED" and rec.exit_code == 3 and rec.job_id == "7"
