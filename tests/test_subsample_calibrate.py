from __future__ import annotations

import gzip
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pipeprof.calibrate import (
    CalibrationError,
    RunPoint,
    calibrate,
    file_extension,
    render_command,
    write_outputs,
)
from pipeprof.subsample import (
    InputKind,
    SubsampleError,
    detect_kind,
    is_gzipped,
    make_subsamples,
    remove_subsamples,
)
from tests.conftest import write_vcf

PY = sys.executable
HAVE_BGZIP = shutil.which("bgzip") is not None and shutil.which("tabix") is not None
HAVE_SAMTOOLS = shutil.which("samtools") is not None


def test_detect_kind() -> None:
    assert detect_kind("a.vcf.gz") is InputKind.VCF
    assert detect_kind("a.bam") is InputKind.BAM
    assert detect_kind("reads_R1.fq.gz") is InputKind.FASTQ
    assert detect_kind("table.tsv") is InputKind.TEXT
    with pytest.raises(SubsampleError, match="BCF"):
        detect_kind("calls.bcf")


def test_vcf_subsamples_are_nested_and_keep_header(tmp_path: Path) -> None:
    vcf = write_vcf(tmp_path / "in.vcf", 2000)
    subs, full = make_subsamples(vcf, [0.5, 0.1, 0.25], tmp_path / "out", seed=1)
    assert full.records == 2000 and full.kind is InputKind.VCF
    assert [s.fraction for s in subs] == [0.1, 0.25, 0.5]
    bodies = []
    for sub in subs:
        lines = sub.path.read_text().splitlines()
        assert lines[0] == "##fileformat=VCFv4.2"
        body = {line for line in lines if not line.startswith("#")}
        assert len(body) == sub.records
        bodies.append(body)
    assert bodies[0] <= bodies[1] <= bodies[2]
    assert 100 < subs[0].records < 300
    remove_subsamples(subs)
    assert not subs[0].path.exists()


@pytest.mark.skipif(not HAVE_BGZIP, reason="bgzip and tabix not installed")
def test_bgzipped_indexed_vcf_gets_indexed_subsamples(tmp_path: Path) -> None:
    vcf = write_vcf(tmp_path / "in.vcf", 500)
    subprocess.run(["bgzip", "-f", str(vcf)], check=True)
    gz = tmp_path / "in.vcf.gz"
    subprocess.run(["tabix", "-p", "vcf", str(gz)], check=True)
    subs, full = make_subsamples(gz, [0.2], tmp_path / "out")
    assert is_gzipped(subs[0].path)
    assert Path(str(subs[0].path) + ".tbi").exists()
    assert full.records == 500


def test_plain_gzip_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vcf = write_vcf(tmp_path / "in.vcf", 300)
    gz = tmp_path / "in.vcf.gz"
    gz.write_bytes(gzip.compress(vcf.read_bytes()))
    monkeypatch.setattr(shutil, "which", lambda name: None)
    subs, _ = make_subsamples(gz, [0.5], tmp_path / "out")
    with gzip.open(subs[0].path, "rt") as handle:
        assert handle.readline().startswith("##fileformat")


def test_fastq_and_text(tmp_path: Path) -> None:
    fq = tmp_path / "r.fastq"
    fq.write_text("".join(f"@r{i}\nACGT\n+\nIIII\n" for i in range(400)))
    subs, full = make_subsamples(fq, [0.5], tmp_path / "o1")
    assert full.records == 400 and full.kind is InputKind.FASTQ
    assert len(subs[0].path.read_text().splitlines()) == 4 * subs[0].records
    bad = tmp_path / "bad.fq"
    bad.write_text("@r\nACGT\n+\n")
    with pytest.raises(SubsampleError, match="truncated"):
        make_subsamples(bad, [0.5], tmp_path / "o2")
    txt = tmp_path / "t.txt"
    txt.write_text("# header\n" + "".join(f"{i}\n" for i in range(100)))
    subs, full = make_subsamples(txt, [1.0], tmp_path / "o3")
    assert subs[0].records == 100 and full.kind is InputKind.TEXT


def test_subsample_errors(tmp_path: Path) -> None:
    with pytest.raises(SubsampleError, match="not found"):
        make_subsamples(tmp_path / "none.vcf", [0.1], tmp_path)
    vcf = write_vcf(tmp_path / "in.vcf", 10)
    with pytest.raises(SubsampleError, match=r"\(0, 1\]"):
        make_subsamples(vcf, [0.0, 0.5], tmp_path / "o")
    with pytest.raises(SubsampleError, match="at least one"):
        make_subsamples(vcf, [], tmp_path / "o")


@pytest.mark.skipif(not HAVE_SAMTOOLS, reason="samtools not installed")
def test_bam_subsampling(tmp_path: Path) -> None:
    sam = tmp_path / "in.sam"
    header = "@HD\tVN:1.6\tSO:coordinate\n@SQ\tSN:chr1\tLN:100000\n"
    reads = "".join(
        f"r{i}\t0\tchr1\t{100 + i}\t60\t4M\t*\t0\t0\tACGT\tIIII\n" for i in range(2000)
    )
    sam.write_text(header + reads)
    bam = tmp_path / "in.bam"
    subprocess.run(["samtools", "view", "-b", "-o", str(bam), str(sam)], check=True)
    subs, full = make_subsamples(bam, [0.1, 0.5], tmp_path / "out")
    assert full.records == 2000
    assert subs[0].records < subs[1].records < 2000
    subprocess.run(["samtools", "index", str(bam)], check=True)
    _, indexed = make_subsamples(bam, [0.1], tmp_path / "out2")
    assert indexed.records == 2000


def test_render_command_and_extension(tmp_path: Path) -> None:
    argv = render_command(
        ["cat", "{input}", ">", "{output}", "-@{threads}"],
        Path("a b.txt"),
        Path("o.txt"),
        2,
        tmp_path,
        quote=True,
    )
    assert argv[1] == "'a b.txt'" and argv[-1] == "-@2"
    with pytest.raises(CalibrationError, match="placeholder"):
        render_command(["{nope}"], Path("a"), Path("b"), 1, tmp_path)
    assert file_extension("x.vcf.gz") == ".vcf.gz"
    assert file_extension("x.BAM") == ".BAM"
    assert file_extension("x.foo.gz") == ".foo.gz"
    assert file_extension("x.csv") == ".csv"
    assert file_extension("README") == ""


MEM_SCRIPT = """
import sys
data = open(sys.argv[1]).read()
keep = [line for line in data.splitlines() for _ in range(40)]
open(sys.argv[2], "w").write(str(len(keep)))
"""


def test_calibrate_end_to_end(tmp_path: Path) -> None:
    script = tmp_path / "grow.py"
    script.write_text(MEM_SCRIPT)
    vcf = write_vcf(tmp_path / "in.vcf", 40000)
    result = calibrate(
        vcf,
        [PY, str(script), "{input}", "{output}"],
        workdir=tmp_path / "work",
        fractions=[0.05, 0.1, 0.2, 0.3, 0.4],
        job_name="grow",
        validate_full=True,
        interval=0.01,
    )
    assert len(result.points) == 5
    rss = result.predictions["peak_rss_bytes"]
    assert rss.lower <= rss.point <= rss.upper
    assert result.validation is not None
    actual = result.validation["metrics"]["peak_rss_bytes"]["actual"]
    assert 0.5 < rss.point / actual < 2.0
    assert result.plan.mem_bytes >= actual
    assert "#SBATCH --job-name=grow" in result.snippets["sbatch"]
    paths = write_outputs(result, tmp_path / "out")
    data = json.loads(paths["json"].read_text())
    assert data["validation"]["plan_covers_actual"]["memory"] is True
    assert not list((tmp_path / "work").glob("sub_*"))


def test_calibrate_shell_mode_records_and_failures(tmp_path: Path) -> None:
    vcf = write_vcf(tmp_path / "in.vcf", 3000)
    result = calibrate(
        vcf,
        ["grep -v '^#' {input} | wc -l > {output}"],
        workdir=tmp_path / "w",
        fractions=[0.1, 0.3, 0.6],
        shell=True,
        x_variable="records",
        repeats=2,
        keep_subsamples=True,
    )
    assert len(result.points) == 6 and result.x_variable == "records"
    assert list((tmp_path / "w").glob("sub_*"))
    with pytest.raises(CalibrationError, match="failed with status"):
        calibrate(
            vcf, [PY, "-c", "import sys; sys.exit(1)"], workdir=tmp_path / "f", fractions=[0.5]
        )
    with pytest.raises(CalibrationError, match="x_variable"):
        calibrate(vcf, ["true"], workdir=tmp_path, x_variable="lines")
    with pytest.raises(CalibrationError, match="repeats"):
        calibrate(vcf, ["true"], workdir=tmp_path, repeats=0)


SINGLE_THREAD = """
import sys, time
n = sum(1 for _ in open(sys.argv[1]))
start = time.process_time()
while time.process_time() - start < n * 2e-6:
    pass
time.sleep(0.02 + n * 1e-6)
open(sys.argv[2], "w").write(str(n))
"""


def test_cpu_request_comes_from_measured_runs(tmp_path: Path) -> None:
    # Single threaded: every run uses under one core, whatever models the
    # wall and CPU fits choose, so the request must be 1 CPU.
    script = tmp_path / "one_core.py"
    script.write_text(SINGLE_THREAD)
    vcf = write_vcf(tmp_path / "in.vcf", 60000)
    result = calibrate(
        vcf,
        [PY, str(script), "{input}", "{output}"],
        workdir=tmp_path / "w",
        fractions=[0.02, 0.05, 0.1, 0.2, 0.4],
        threads=4,
    )
    ratios = [p.measurement.cpu_s / p.measurement.wall_s for p in result.points]
    assert max(ratios) < 1.0
    assert result.plan.cpus == 1


def test_cpus_from_runs_ignores_extrapolated_ratio() -> None:
    from pipeprof.calibrate import RunPoint, cpus_from_runs
    from pipeprof.measure import Measurement

    def point(wall: float, cpu: float) -> RunPoint:
        m = Measurement("x", 0, wall, cpu, 0.0, 1, 1, 1, None, None, 0, 0, 1, 0.05)
        return RunPoint(0.1, 1, "p", 1, 1, m)

    # The reviewer's case: per-run CPU/wall 0.11 to 0.68.
    runs = [point(1.0, 0.11), point(1.0, 0.4), point(1.0, 0.68)]
    assert cpus_from_runs(runs, threads=8) == (1, 0.68)
    busy = [point(1.0, 3.2)]
    assert cpus_from_runs(busy, threads=8)[0] == 4
    assert cpus_from_runs(busy, threads=2)[0] == 2  # capped at --threads


def test_decreasing_series_never_predicts_below_measured(tmp_path: Path) -> None:
    # Start-up cost dominates: the smallest run is the slowest.
    from pipeprof.fit import ModelKind, select_model

    xs = [1.0, 2.0, 5.0, 10.0, 20.0]
    ys = [0.057, 0.026, 0.030, 0.038, 0.030]
    sel = select_model(xs, ys)
    assert sel.chosen.slope >= 0
    falling = select_model(xs, [5.0, 4.0, 3.0, 2.5, 2.0])
    assert falling.chosen.kind is ModelKind.CONSTANT
    assert falling.chosen.predict(100.0) == 5.0
    assert falling.chosen.interval(100.0) == (5.0, 5.0)
    assert "largest measured" in falling.chosen.formula()
    assert "rejected decreasing" in falling.reason


def _run(fraction: float, wall: float, cpu: float, rss: float, repeat: int = 1) -> RunPoint:
    from pipeprof.measure import Measurement

    peak = int(rss)
    m = Measurement("x", 0, wall, cpu, 0.0, peak, peak, peak, None, None, 0, 0, 1, 0.05)
    return RunPoint(fraction, repeat, "p", int(fraction * 1e9), 1, m)


def test_flat_memory_is_marked_and_bounded() -> None:
    from pipeprof.calibrate import predict_metrics

    # Synthetic runs: CPU-busy, memory flat near 70 MB at every size.
    runs = [_run(f, 10 * f, 10 * f, 70e6 * j) for f, j in [(0.01, 0.99), (0.05, 1.0), (0.1, 1.01)]]
    preds = predict_metrics(runs, 1e9, "bytes")
    mem = preds["peak_rss_bytes"]
    assert mem.basis == "flat"
    assert mem.upper == pytest.approx(70.7e6 * 10)
    assert mem.to_dict()["basis"] == "flat"
    assert preds["cpu_s"].basis == "fitted"


def test_waiting_runs_use_the_cpu_model() -> None:
    from pipeprof.calibrate import predict_metrics, waiting_runs

    # The two Narval runs of one bcftools sort: 15.15 s wall on 3.56 s CPU on
    # one node, 4.27 s on 3.25 s on another.
    slow = _run(0.1, 15.15, 3.56, 80e6, repeat=1)
    fast = _run(0.1, 4.27, 3.25, 80e6, repeat=2)
    assert waiting_runs([slow, fast]) == [slow]
    # Synthetic CPU-busy runs at two more sizes, so CPU can be fitted.
    runs = [_run(0.05, 2.0, 1.7, 60e6), slow, fast, _run(0.2, 8.1, 6.8, 120e6)]
    preds = predict_metrics(runs, 1e9, "bytes")
    wall, cpu = preds["wall_s"], preds["cpu_s"]
    assert wall.basis == "mostly waiting"
    assert wall.observed_range == (2.0, 15.15)
    worst = 3.56 / 15.15
    assert wall.point == pytest.approx(cpu.point / worst)
    assert wall.upper == pytest.approx(cpu.upper / worst)
    assert wall.lower <= wall.point <= wall.upper
    assert "mostly waiting" in wall.note and "15.15 s on 3.56 s CPU" in wall.note
    assert "2.00 to 15.15 s" in wall.note

    busy_runs = [_run(0.05, 2.0, 1.7, 60e6), fast, _run(0.2, 8.1, 6.8, 120e6)]
    busy = predict_metrics(busy_runs, 1e9, "bytes")
    assert busy["wall_s"].basis == "fitted"
