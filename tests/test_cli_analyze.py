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
