"""Parser edge cases from tests/data and property-based checks."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hpc_job_profiler.fit import ModelKind, fit_linear, fit_power, select_model
from hpc_job_profiler.records import (
    parse_nextflow_trace,
    parse_sacct,
    parse_snakemake_benchmark,
    read_sources,
)
from hpc_job_profiler.slurm import SlurmError
from hpc_job_profiler.units import (
    GIB,
    MIB,
    format_memory,
    format_slurm_duration,
    parse_memory,
    parse_slurm_duration,
)


@pytest.mark.parametrize(
    ("job", "req", "rss"),
    [
        ("3001", int(1.5 * GIB), 1536 * MIB),  # 1.5G request, 1536M peak
        ("3002", 1536 * MIB, 1572864 * 1024),  # 1536M request, K peak
        ("3003", 4 * 512 * MIB, int(0.75 * GIB)),  # 512Mc x 4 CPUs
        ("3004", 2 * 3 * GIB, 2 * GIB),  # 3Gn x 2 nodes
    ],
)
def test_memory_unit_spellings(data: Path, job: str, req: int, rss: int) -> None:
    records = {r.job_id: r for r in parse_sacct((data / "sacct_memory_units.txt").read_text())}
    assert records[job].req_mem_bytes == req
    assert records[job].max_rss_bytes == rss


def test_same_amount_in_three_spellings(data: Path) -> None:
    records = {r.job_id: r for r in parse_sacct((data / "sacct_memory_units.txt").read_text())}
    assert records["3001"].req_mem_bytes == records["3002"].req_mem_bytes
    assert records["3001"].max_rss_bytes == records["3002"].max_rss_bytes
    # The reduced column set has no Submit or Start, so no queue wait.
    assert records["3001"].queue_wait_s is None


def test_truncated_sacct_raises(data: Path) -> None:
    with pytest.raises(SlurmError, match="expected 17 fields"):
        parse_sacct((data / "sacct_truncated.txt").read_text())


@pytest.mark.parametrize(
    "reader",
    [
        lambda p: read_sources(sacct=[p]),
        lambda p: read_sources(nextflow=[p]),
        lambda p: read_sources(snakemake_benchmarks=[p]),
    ],
)
def test_empty_file_gives_no_records(data: Path, reader: object) -> None:
    assert reader(data / "empty.txt") == []  # type: ignore[operator]


def test_linux_benchmark_values(data: Path) -> None:
    path = data / "snakemake_linux" / "sort_vcf_chr1.tsv"
    rec = parse_snakemake_benchmark(path.read_text(), path)[0]
    assert rec.name == "snakemake_linux"
    assert rec.elapsed_s == pytest.approx(61.2345)
    assert rec.max_rss_bytes == int(812.53 * MIB)
    assert rec.cpu_time_s == pytest.approx(60.43)


def test_trace_missing_columns() -> None:
    rec = parse_nextflow_trace("name\tstatus\nfoo (1)\tCOMPLETED\n")[0]
    assert rec.name == "foo" and rec.elapsed_s is None and rec.max_rss_bytes is None


# Property-based checks.


@given(st.integers(min_value=0, max_value=30 * 86400))
def test_duration_round_trip(seconds: int) -> None:
    assert parse_slurm_duration(format_slurm_duration(seconds)) == seconds


@given(st.integers(min_value=1, max_value=2**50), st.sampled_from(["K", "M", "G", "T"]))
def test_memory_suffixes_scale_by_1024(value: int, suffix: str) -> None:
    factor = {"K": 1024, "M": MIB, "G": GIB, "T": 1024**4}[suffix]
    assert parse_memory(f"{value}{suffix}") == value * factor


@given(st.floats(min_value=1, max_value=1e15, allow_nan=False))
def test_format_then_parse_is_close(num_bytes: float) -> None:
    parsed = parse_memory(format_memory(num_bytes))
    assert parsed is not None
    assert abs(parsed - num_bytes) <= 0.05 * num_bytes + 1


positive = st.floats(min_value=0.01, max_value=1e4, allow_nan=False, allow_infinity=False)


@settings(max_examples=60, deadline=None)
@given(a=positive, b=positive, k=st.floats(min_value=0.3, max_value=2.0))
def test_fits_recover_exact_models(a: float, b: float, k: float) -> None:
    xs = [1.0, 2.0, 4.0, 8.0, 16.0]
    lin = fit_linear(xs, [a + b * x for x in xs])
    assert lin.predict(100.0) == pytest.approx(a + b * 100.0, rel=1e-6)
    pw = fit_power(xs, [a * x**k for x in xs])
    assert pw.slope == pytest.approx(k, rel=1e-6)


@settings(max_examples=60, deadline=None)
@given(a=positive, b=positive, x0=st.floats(min_value=20, max_value=1e3))
def test_interval_contains_point(a: float, b: float, x0: float) -> None:
    xs = [1.0, 2.0, 3.0, 5.0, 8.0]
    ys = [a + b * x * (1.0 + 0.03 * ((i % 2) * 2 - 1)) for i, x in enumerate(xs)]
    sel = select_model(xs, ys)
    lo, hi = sel.chosen.interval(x0)
    assert lo <= sel.chosen.predict(x0) <= hi
    assert sel.chosen.kind in {ModelKind.LINEAR, ModelKind.POWER}


def test_bare_reqmem_on_two_nodes_is_whole_job(data: Path) -> None:
    records = {r.job_id: r for r in parse_sacct((data / "sacct_memory_units.txt").read_text())}
    assert records["3005"].req_mem_bytes == 2000 * MIB
    assert records["3005"].max_rss_bytes == 900 * MIB
