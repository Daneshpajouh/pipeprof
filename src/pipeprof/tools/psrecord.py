"""Read ``psrecord --log`` files.

Format: psrecord (https://github.com/astrofrog/psrecord) writes a header line
starting with "# Elapsed time", then one line per sample with four
whitespace-separated numbers: elapsed seconds, CPU percent, real memory in MB
and virtual memory in MB. psrecord is not installed in this environment; the
fixtures in ``tests/data/tools`` are hand-written from that documented
format.
"""

from __future__ import annotations

from pipeprof.tools.usage import StepUsage

_HEADER_PREFIX = "# Elapsed time"

# psrecord's MB is MiB (2**20): this matches psutil's memory_info(), which
# psrecord reads directly and divides by 1024**2 for its "Real (MB)" and
# "Virtual (MB)" columns. [CONFIRM] against psrecord's source before relying
# on this in a context where the distinction from decimal MB matters.
_MIB = 1024 * 1024


def psrecord_samples(text: str) -> list[tuple[float, float, float, float]]:
    """Return each sample as ``(elapsed_s, cpu_percent, real_mb, virtual_mb)``.

    Raises ``ValueError`` if the header is missing (not a psrecord log) or a
    data line does not have exactly four numbers.
    """
    lines = text.splitlines()
    header_idx = next((i for i, line in enumerate(lines) if line.startswith(_HEADER_PREFIX)), None)
    if header_idx is None:
        raise ValueError("not a psrecord --log file: missing '# Elapsed time' header")

    samples: list[tuple[float, float, float, float]] = []
    for line in lines[header_idx + 1 :]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) != 4:
            raise ValueError(f"bad psrecord sample line: {line!r}")
        try:
            elapsed, cpu, real_mb, virtual_mb = (float(p) for p in parts)
        except ValueError as exc:
            raise ValueError(f"bad psrecord sample line: {line!r}") from exc
        samples.append((elapsed, cpu, real_mb, virtual_mb))
    return samples


def parse_psrecord(text: str, step: str | None = None) -> StepUsage:
    """Summarize a psrecord log into one ``StepUsage``.

    ``wall_s`` is the last sample's elapsed time; ``cpu_percent`` is the mean
    over all samples, since psrecord gives no single overall figure; peak
    real memory becomes ``max_rss_bytes``. ``StepUsage`` has no field for
    virtual memory, so the peak virtual figure goes into ``notes`` instead.
    """
    samples = psrecord_samples(text)
    notes: list[str] = []
    if not samples:
        return StepUsage(source="psrecord", step=step, notes=["no samples in log"])

    wall_s = samples[-1][0]
    cpu_percent = sum(sample[1] for sample in samples) / len(samples)
    peak_real_mb = max(sample[2] for sample in samples)
    peak_virtual_mb = max(sample[3] for sample in samples)
    max_rss_bytes = round(peak_real_mb * _MIB)
    notes.append(
        f"peak virtual memory: {round(peak_virtual_mb * _MIB)} bytes"
        " (StepUsage has no virtual-memory field)"
    )

    return StepUsage(
        source="psrecord",
        step=step,
        wall_s=wall_s,
        cpu_percent=cpu_percent,
        max_rss_bytes=max_rss_bytes,
        notes=notes,
    )
