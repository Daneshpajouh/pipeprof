from __future__ import annotations

import doctest

import pytest

from hpc_job_profiler import units
from hpc_job_profiler.units import (
    GIB,
    MIB,
    UnitError,
    format_memory,
    format_slurm_duration,
    parse_hms,
    parse_memory,
    parse_nextflow_duration,
    parse_nextflow_memory,
    parse_percent,
    parse_reqmem,
    parse_slurm_duration,
    round_up_memory,
    round_up_minutes,
)


def test_doctests() -> None:
    result = doctest.testmod(units)
    assert result.failed == 0


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("00:01:05", 65.0),
        ("01:05", 65.0),
        ("1-02:03:04", 93784.0),
        ("2-00", 172800.0),
        ("3-04:05", 273900.0),
        ("05:02.500", 302.5),
        ("42", 42.0),
    ],
)
def test_slurm_duration(text: str, seconds: float) -> None:
    assert parse_slurm_duration(text) == seconds


@pytest.mark.parametrize("text", ["UNLIMITED", "Partition_Limit", "", "INVALID"])
def test_slurm_duration_special(text: str) -> None:
    assert parse_slurm_duration(text) is None


@pytest.mark.parametrize("text", ["x-01:00:00", "1:2:3:4", "aa:bb"])
def test_slurm_duration_bad(text: str) -> None:
    with pytest.raises(UnitError):
        parse_slurm_duration(text)


def test_format_duration_round_trip() -> None:
    assert format_slurm_duration(59.2) == "00:01:00"
    assert parse_slurm_duration(format_slurm_duration(93784)) == 93784


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.5G", int(1.5 * GIB)),
        ("4000M", 4000 * MIB),
        ("123K", 123 * 1024),
        ("0", 0),
        ("29.8 MB", round(29.8 * MIB)),
        ("1.1 TB", round(1.1 * 1024**4)),
        ("512B", 512),
        ("2GiB", 2 * GIB),
    ],
)
def test_parse_memory(text: str, expected: int) -> None:
    assert parse_memory(text) == expected


def test_parse_memory_empty_and_bad() -> None:
    assert parse_memory("") is None
    assert parse_memory("NA") is None
    assert parse_memory("16", default_unit="M") == 16 * MIB
    with pytest.raises(UnitError):
        parse_memory("lots")


def test_reqmem_versions() -> None:
    # Slurm 20.11: per CPU (c) and per node (n) markers, bare numbers in MB.
    assert parse_reqmem("4000Mc", alloc_cpus=4, nnodes=1) == 16000 * MIB
    assert parse_reqmem("16000Mn", alloc_cpus=2, nnodes=2) == 32000 * MIB
    # Slurm 21.08 and later: whole job with a unit letter.
    assert parse_reqmem("16G", alloc_cpus=8, nnodes=2) == 16 * GIB
    assert parse_reqmem("2000", alloc_cpus=1, nnodes=1) == 2000 * MIB
    assert parse_reqmem("", alloc_cpus=1, nnodes=1) is None
    assert parse_reqmem("0n", alloc_cpus=1, nnodes=1) is None


def test_nextflow_values() -> None:
    assert parse_nextflow_duration("1m 5s") == 65.0
    assert parse_nextflow_duration("1h 2m 3s") == 3723.0
    assert parse_nextflow_duration("2.4s") == 2.4
    assert parse_nextflow_duration("1d 1h") == 90000.0
    assert parse_nextflow_duration("300100") == 300.1
    assert parse_nextflow_duration("-") is None
    with pytest.raises(UnitError):
        parse_nextflow_duration("soon")
    assert parse_nextflow_memory("180.4 MB") == round(180.4 * MIB)
    assert parse_nextflow_memory("734003200") == 734003200
    assert parse_nextflow_memory("") is None
    assert parse_percent("4402.5%") == 4402.5
    assert parse_percent("-") is None
    with pytest.raises(UnitError):
        parse_percent("high")


def test_hms() -> None:
    assert parse_hms("0:00:02") == 2.0
    assert parse_hms("1 day, 2:00:00") == 93600.0
    assert parse_hms("NA") is None
    with pytest.raises(UnitError):
        parse_hms("12")


def test_format_memory() -> None:
    assert format_memory(3.5 * GIB) == "3.5G"
    assert format_memory(512) == "512B"
    assert format_memory(GIB, unit="M") == "1024.0M"


def test_rounding() -> None:
    assert round_up_memory(0) == 256 * MIB
    assert round_up_memory(300 * MIB) == 512 * MIB
    assert round_up_memory(1.2 * GIB) == 2 * GIB
    assert round_up_memory(17 * GIB) == 20 * GIB
    assert round_up_minutes(10) == 5
    assert round_up_minutes(61 * 60) == 75
    assert round_up_minutes(13 * 3600) == 780
