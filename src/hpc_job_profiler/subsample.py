"""Make graduated, nested subsamples of VCF, BAM, FASTQ and plain text inputs.

Text formats (VCF, FASTQ, line based text) are read once. Each record draws
one uniform number ``u`` and is written to every subsample whose fraction is
above ``u``. The subsamples are therefore nested (the 1 percent file is a
subset of the 2 percent file), which keeps the measured scaling curve
monotone in content, not only in size. Header lines go to every file.

BAM and CRAM files are subsampled with ``samtools view --subsample``, which
keeps or drops whole templates by a hash of the read name. With one seed the
subsamples are nested in the same way.
"""

from __future__ import annotations

import bisect
import contextlib
import gzip
import io
import random
import shutil
import subprocess
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import IO, Any

GZIP_MAGIC = b"\x1f\x8b"


class InputKind(StrEnum):
    """Supported input formats."""

    VCF = "vcf"
    BAM = "bam"
    FASTQ = "fastq"
    TEXT = "text"


class SubsampleError(RuntimeError):
    """Raised when an input cannot be subsampled."""


@dataclass
class Subsample:
    """One subsample file and its size."""

    fraction: float
    path: Path
    records: int
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        """JSON ready form with the path as text."""
        data = asdict(self)
        data["path"] = str(self.path)
        return data


@dataclass
class FullInput:
    """Size of the complete input, used as the prediction target."""

    path: Path
    kind: InputKind
    records: int
    size_bytes: int


def strip_compression_suffix(name: str) -> str:
    """Drop ``.gz``, ``.bgz`` or ``.bgzf`` from a file name."""
    for suffix in (".gz", ".bgz", ".bgzf"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def detect_kind(path: str | Path) -> InputKind:
    """Guess the input format from the file name."""
    name = strip_compression_suffix(Path(path).name.lower())
    if name.endswith(".vcf"):
        return InputKind.VCF
    if name.endswith((".bam", ".cram", ".sam")):
        return InputKind.BAM
    if name.endswith((".fastq", ".fq")):
        return InputKind.FASTQ
    if name.endswith(".bcf"):
        raise SubsampleError("BCF input is not supported; convert with 'bcftools view -Oz' first")
    return InputKind.TEXT


def is_gzipped(path: str | Path) -> bool:
    """True when the file starts with the gzip magic bytes (gzip or BGZF)."""
    with Path(path).open("rb") as handle:
        return handle.read(2) == GZIP_MAGIC


def _open_text(path: Path) -> IO[str]:
    if is_gzipped(path):
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", newline="")
    return path.open(encoding="utf-8", newline="")


class _Writer:
    """Write text to a file, compressed with bgzip when available and asked for."""

    def __init__(self, path: Path, compress: bool) -> None:
        self.path = path
        self._proc: subprocess.Popen[bytes] | None = None
        self._handle: IO[str]
        bgzip = shutil.which("bgzip") if compress else None
        if compress and bgzip is not None:
            out = path.open("wb")
            self._out = out
            self._proc = subprocess.Popen([bgzip, "-c"], stdin=subprocess.PIPE, stdout=out)
            assert self._proc.stdin is not None
            self._handle = io.TextIOWrapper(self._proc.stdin, encoding="utf-8", newline="")
        elif compress:
            # Plain gzip still reads with htslib, but cannot be indexed with tabix.
            self._handle = io.TextIOWrapper(
                gzip.open(path, "wb", compresslevel=6),  # noqa: SIM115
                encoding="utf-8",
                newline="",
            )
        else:
            self._handle = path.open("w", encoding="utf-8", newline="")

    def write(self, text: str) -> None:
        self._handle.write(text)

    def close(self) -> None:
        self._handle.close()
        if self._proc is not None:
            code = self._proc.wait()
            self._out.close()
            if code != 0:
                raise SubsampleError(f"bgzip failed with status {code} for {self.path}")


def _records(handle: IO[str], kind: InputKind, header_prefix: str) -> Iterator[tuple[bool, str]]:
    """Yield ``(is_header, text)`` for each record; FASTQ records are four lines."""
    if kind is InputKind.FASTQ:
        while True:
            lines = [handle.readline() for _ in range(4)]
            if not lines[0]:
                return
            if not lines[3]:
                raise SubsampleError("truncated FASTQ record at end of file")
            yield False, "".join(lines)
    else:
        for line in handle:
            yield bool(header_prefix) and line.startswith(header_prefix), line


def _validate_fractions(fractions: Sequence[float]) -> list[float]:
    cleaned = sorted(set(fractions))
    if not cleaned:
        raise SubsampleError("at least one fraction is needed")
    if cleaned[0] <= 0 or cleaned[-1] > 1:
        raise SubsampleError("fractions must be in (0, 1]")
    return cleaned


def subsample_text(
    path: str | Path,
    fractions: Sequence[float],
    outdir: str | Path,
    kind: InputKind,
    seed: int = 13,
    header_prefix: str = "#",
) -> tuple[list[Subsample], FullInput]:
    """Subsample a VCF, FASTQ or text file in one pass. Returns the subsamples and full size."""
    source = Path(path)
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    fracs = _validate_fractions(fractions)
    compress = is_gzipped(source)
    stem = strip_compression_suffix(source.name)
    suffix = ".gz" if compress else ""
    paths = [out / f"sub_{frac:.4f}_{stem}{suffix}" for frac in fracs]
    writers = [_Writer(p, compress) for p in paths]
    counts = [0] * len(fracs)
    total = 0
    rng = random.Random(seed)
    prefix = "" if kind is InputKind.FASTQ else header_prefix
    try:
        with _open_text(source) as handle:
            for is_header, text in _records(handle, kind, prefix):
                if is_header:
                    for writer in writers:
                        writer.write(text)
                    continue
                total += 1
                first = bisect.bisect_right(fracs, rng.random())
                for index in range(first, len(fracs)):
                    writers[index].write(text)
                    counts[index] += 1
    finally:
        for writer in writers:
            writer.close()
    _index_like_source(source, paths, kind)
    subs = [
        Subsample(fraction=f, path=p, records=c, size_bytes=p.stat().st_size)
        for f, p, c in zip(fracs, paths, counts, strict=True)
    ]
    full = FullInput(path=source, kind=kind, records=total, size_bytes=source.stat().st_size)
    return subs, full


def _index_like_source(source: Path, paths: Sequence[Path], kind: InputKind) -> None:
    """Index subsamples with tabix when the source VCF has an index.

    Some VCFs (ClinVar's among them) have no ``##contig`` header lines;
    htslib then takes the contig list from the index, and tools such as
    ``bcftools sort`` fail on an unindexed subsample of the same file.
    """
    if kind is not InputKind.VCF:
        return
    has_tbi = Path(str(source) + ".tbi").exists()
    has_csi = Path(str(source) + ".csi").exists()
    tabix = shutil.which("tabix")
    if not (has_tbi or has_csi) or tabix is None:
        return
    for path in paths:
        args = [tabix, "-f", "-p", "vcf", str(path)]
        if has_csi and not has_tbi:
            args.insert(1, "--csi")
        subprocess.run(args, check=True)


def _samtools() -> str:
    exe = shutil.which("samtools")
    if exe is None:
        raise SubsampleError("samtools is needed for BAM and CRAM input but was not found on PATH")
    return exe


def count_alignments(path: str | Path, threads: int = 1) -> int:
    """Count records in a BAM or CRAM, from the index when one exists."""
    samtools = _samtools()
    source = Path(path)
    has_index = (
        any(Path(str(source) + ext).exists() for ext in (".bai", ".csi", ".crai"))
        or source.with_suffix(".bai").exists()
    )
    if has_index:
        result = subprocess.run(
            [samtools, "idxstats", str(source)], capture_output=True, text=True, check=False
        )
        if result.returncode == 0:
            total = 0
            for line in result.stdout.splitlines():
                fields = line.split("\t")
                if len(fields) == 4:
                    total += int(fields[2]) + int(fields[3])
            return total
    result = subprocess.run(
        [samtools, "view", "-c", "-@", str(max(1, threads)), str(source)],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(result.stdout.strip())


def subsample_bam(
    path: str | Path,
    fractions: Sequence[float],
    outdir: str | Path,
    seed: int = 13,
    threads: int = 1,
) -> tuple[list[Subsample], FullInput]:
    """Subsample a BAM or CRAM with ``samtools view --subsample``."""
    samtools = _samtools()
    source = Path(path)
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    subs: list[Subsample] = []
    for frac in _validate_fractions(fractions):
        target = out / f"sub_{frac:.4f}_{source.stem}.bam"
        subprocess.run(
            [
                samtools,
                "view",
                "-b",
                "--subsample",
                f"{frac:.6f}",
                "--subsample-seed",
                str(seed),
                "-@",
                str(max(1, threads)),
                "-o",
                str(target),
                str(source),
            ],
            check=True,
        )
        records = count_alignments(target, threads)
        subs.append(
            Subsample(
                fraction=frac, path=target, records=records, size_bytes=target.stat().st_size
            )
        )
    full = FullInput(
        path=source,
        kind=InputKind.BAM,
        records=count_alignments(source, threads),
        size_bytes=source.stat().st_size,
    )
    return subs, full


def make_subsamples(
    path: str | Path,
    fractions: Sequence[float],
    outdir: str | Path,
    kind: InputKind | None = None,
    seed: int = 13,
    threads: int = 1,
    header_prefix: str = "#",
) -> tuple[list[Subsample], FullInput]:
    """Make nested subsamples of ``path`` for each fraction."""
    source = Path(path)
    if not source.is_file():
        raise SubsampleError(f"input not found: {source}")
    chosen = kind or detect_kind(source)
    if chosen is InputKind.BAM:
        return subsample_bam(source, fractions, outdir, seed=seed, threads=threads)
    return subsample_text(
        source, fractions, outdir, chosen, seed=seed, header_prefix=header_prefix
    )


def remove_subsamples(subs: Sequence[Subsample]) -> None:
    """Delete subsample files made by this module."""
    for sub in subs:
        with contextlib.suppress(FileNotFoundError):
            sub.path.unlink()
