from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "data"
SHIM = Path(__file__).parent / "shim"


@pytest.fixture
def data() -> Path:
    return FIXTURES


@pytest.fixture
def slurm_shim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point pipeprof at the fake sacct and sstat and give them a fresh state file."""
    monkeypatch.setenv("PIPEPROF_SACCT", str(SHIM / "sacct"))
    monkeypatch.setenv("PIPEPROF_SSTAT", str(SHIM / "sstat"))
    monkeypatch.setenv("PIPEPROF_SHIM_STATE", str(tmp_path / "shim_state"))
    monkeypatch.setenv("PIPEPROF_SHIM_STEPS", "4")
    # The shim runs with the same interpreter as the tests.
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    yield tmp_path


def write_vcf(path: Path, records: int, chrom: str = "1") -> Path:
    lines = [
        "##fileformat=VCFv4.2\n",
        f"##contig=<ID={chrom},length=250000000>\n",
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n",
    ]
    lines += [f"{chrom}\t{1000 + i * 10}\t.\tA\tG\t.\tPASS\tDP={i % 50}\n" for i in range(records)]
    path.write_text("".join(lines))
    return path
