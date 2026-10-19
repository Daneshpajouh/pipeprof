"""Parse and format the time and memory strings used by Slurm, Nextflow and Snakemake.

Slurm formats follow the sacct manual (https://slurm.schedmd.com/sacct.html):
elapsed times are ``[DD-[HH:]]MM:SS`` with optional fractional seconds on CPU
fields, and memory values carry a K, M, G, T or P suffix in binary units.

Nextflow trace files use human readable values such as ``1m 5s``, ``352ms``,
``29.8 MB`` and ``94.3%`` unless ``trace.raw = true`` is set, in which case
durations are milliseconds and memory is bytes.
"""

from __future__ import annotations

import math
import re

KIB = 1024
MIB = 1024**2
GIB = 1024**3
TIB = 1024**4

_BINARY_SUFFIX = {
    "": 1,
    "B": 1,
    "K": KIB,
    "M": MIB,
    "G": GIB,
    "T": TIB,
    "P": 1024**5,
}

_SPECIAL_TIMELIMITS = {"UNLIMITED", "PARTITION_LIMIT", "INVALID", ""}


class UnitError(ValueError):
    """Raised when a time or memory string cannot be parsed."""


def parse_slurm_duration(text: str) -> float | None:
    """Return seconds for a Slurm duration, or ``None`` for empty or unlimited values.

    Accepted forms: ``SS``, ``MM:SS``, ``HH:MM:SS``, ``D-HH``, ``D-HH:MM``,
    ``D-HH:MM:SS``, each with optional ``.fraction`` on the seconds.

    >>> parse_slurm_duration("1-02:03:04")
    93784.0
    >>> parse_slurm_duration("05:02.500")
    302.5
    """
    value = text.strip()
    if value.upper() in _SPECIAL_TIMELIMITS:
        return None
    days = 0
    if "-" in value:
        day_part, value = value.split("-", 1)
        if not day_part.isdigit():
            raise UnitError(f"bad day field in Slurm duration: {text!r}")
        days = int(day_part)
        parts = value.split(":")
        # With a day prefix the first field is hours: D-HH, D-HH:MM, D-HH:MM:SS.
        while len(parts) < 3:
            parts.append("0")
    else:
        parts = value.split(":")
        while len(parts) < 3:
            parts.insert(0, "0")
    if len(parts) != 3:
        raise UnitError(f"too many fields in Slurm duration: {text!r}")
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = float(parts[2])
    except ValueError as exc:
        raise UnitError(f"bad Slurm duration: {text!r}") from exc
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def format_slurm_duration(seconds: float) -> str:
    """Format seconds as a Slurm time limit, ``D-HH:MM:SS`` or ``HH:MM:SS``.

    Seconds are rounded up so the limit is never shorter than asked.

    >>> format_slurm_duration(93784)
    '1-02:03:04'
    >>> format_slurm_duration(59.2)
    '00:01:00'
    """
    total = max(0, math.ceil(seconds))
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days}-{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


_MEM_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([KMGTPB]?)(i?B)?\s*([cn]?)\s*$", re.IGNORECASE)


def parse_memory(text: str, default_unit: str = "B") -> int | None:
    """Return bytes for a memory string such as ``1.5G``, ``4000M``, ``123K`` or ``29.8 MB``.

    All suffixes are binary (1K = 1024 bytes), as in Slurm and Nextflow.
    A bare number uses ``default_unit``. A trailing ``c`` or ``n`` (older
    Slurm ReqMem per CPU or per node marker) is accepted and ignored here;
    use :func:`parse_reqmem` when the marker matters. Returns ``None`` for
    empty values.

    >>> parse_memory("1.5G")
    1610612736
    >>> parse_memory("29.8 MB")
    31247565
    """
    value = text.strip()
    if value in {"", "-", "NA", "N/A"}:
        return None
    match = _MEM_RE.match(value)
    if not match:
        raise UnitError(f"bad memory value: {text!r}")
    number = float(match.group(1))
    suffix = match.group(2).upper()
    if not suffix and match.group(3) is None:
        suffix = default_unit.upper()
    if suffix not in _BINARY_SUFFIX:
        raise UnitError(f"unknown memory unit in {text!r}")
    return round(number * _BINARY_SUFFIX[suffix])


def parse_reqmem(text: str, alloc_cpus: int, nnodes: int) -> int | None:
    """Return the total requested memory in bytes for a sacct ``ReqMem`` value.

    Slurm releases before 21.08 appended ``c`` (per CPU) or ``n`` (per node).
    Newer releases print the amount without a marker. A bare number is in
    megabytes, as the sacct manual states for ReqMem.
    """
    value = text.strip()
    if value in {"", "0", "0n", "0c"}:
        return None
    per_cpu = value.endswith("c")
    per_node = value.endswith("n")
    amount = parse_memory(value.rstrip("cn"), default_unit="M")
    if amount is None:
        return None
    if per_cpu:
        return amount * max(1, alloc_cpus)
    if per_node:
        return amount * max(1, nnodes)
    return amount


def format_memory(num_bytes: float, unit: str | None = None) -> str:
    """Format bytes with a binary suffix, for example ``3.5G``.

    >>> format_memory(3.5 * 1024**3)
    '3.5G'
    """
    if unit is not None:
        factor = _BINARY_SUFFIX[unit.upper()]
        return f"{num_bytes / factor:.1f}{unit.upper()}"
    for suffix in ("P", "T", "G", "M", "K"):
        factor = _BINARY_SUFFIX[suffix]
        if abs(num_bytes) >= factor:
            return f"{num_bytes / factor:.1f}{suffix}"
    return f"{int(num_bytes)}B"


_NF_DURATION_RE = re.compile(r"([0-9]*\.?[0-9]+)\s*(ms|d|h|m|s)")
_NF_UNITS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}


def parse_nextflow_duration(text: str) -> float | None:
    """Return seconds for a Nextflow trace duration (``1m 5s``, ``352ms``) or raw milliseconds.

    >>> parse_nextflow_duration("1h 2m 3s")
    3723.0
    >>> parse_nextflow_duration("352ms")
    0.352
    >>> parse_nextflow_duration("65000")
    65.0
    """
    value = text.strip()
    if value in {"", "-", "NA"}:
        return None
    if re.fullmatch(r"[0-9]+", value):
        return int(value) / 1000.0
    pieces = _NF_DURATION_RE.findall(value)
    rebuilt = "".join(f"{n}{u}" for n, u in pieces)
    if not pieces or rebuilt != value.replace(" ", ""):
        raise UnitError(f"bad Nextflow duration: {text!r}")
    return sum(float(n) * _NF_UNITS[u] for n, u in pieces)


def parse_nextflow_memory(text: str) -> int | None:
    """Return bytes for a Nextflow trace memory value (``29.8 MB``) or raw bytes."""
    value = text.strip()
    if value in {"", "-", "NA"}:
        return None
    if re.fullmatch(r"[0-9]+", value):
        return int(value)
    return parse_memory(value)


def parse_percent(text: str) -> float | None:
    """Return a percentage as a float, for example ``94.3%`` gives 94.3."""
    value = text.strip().rstrip("%")
    if value in {"", "-", "NA"}:
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise UnitError(f"bad percentage: {text!r}") from exc


def parse_hms(text: str) -> float | None:
    """Return seconds for ``H:MM:SS`` or ``D days, H:MM:SS`` as written by Python timedelta."""
    value = text.strip()
    if value in {"", "-", "NA"}:
        return None
    days = 0
    match = re.match(r"^(\d+) days?, (.*)$", value)
    if match:
        days = int(match.group(1))
        value = match.group(2)
    parts = value.split(":")
    if len(parts) != 3:
        raise UnitError(f"bad h:m:s value: {text!r}")
    return days * 86400 + int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])


def round_up_memory(num_bytes: float) -> int:
    """Round a memory request up to a scheduler friendly step.

    Below 1 GiB the step is 256 MiB; up to 16 GiB it is 1 GiB; above that
    it is 4 GiB. Nothing is rounded below 256 MiB.
    """
    if num_bytes <= 0:
        return 256 * MIB
    step = 256 * MIB if num_bytes <= GIB else (GIB if num_bytes <= 16 * GIB else 4 * GIB)
    return max(256 * MIB, math.ceil(num_bytes / step) * step)


def round_up_minutes(seconds: float) -> int:
    """Round a time request up to whole minutes in friendly steps.

    Up to 1 hour the step is 5 minutes; up to 12 hours it is 15 minutes;
    above that it is 1 hour. The minimum is 5 minutes.
    """
    minutes = max(seconds, 1.0) / 60.0
    step = 5 if minutes <= 60 else (15 if minutes <= 720 else 60)
    return max(5, math.ceil(minutes / step) * step)
