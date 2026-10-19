"""Read macOS (BSD) ``time -l`` reports.

Format: observed from a real run of ``/usr/bin/time -l`` on macOS 26 (Darwin
27), 2026-10-19 (see ``tests/data/README.md``). The first line is
"N real N user N sys"; every later line is "VALUE  label". Unlike GNU
``time -v``, macOS ``time -l`` reports "maximum resident set size" directly in
bytes, and never prints the timed command's exit status.
"""

from __future__ import annotations

import re

from pipeprof.tools.usage import StepUsage

_TIMES_RE = re.compile(
    r"^\s*([0-9.]+)\s+real\s+([0-9.]+)\s+user\s+([0-9.]+)\s+sys\s*$", re.MULTILINE
)
_MAXRSS_RE = re.compile(r"^\s*([0-9]+)\s+maximum resident set size\s*$", re.MULTILINE)
_BLOCK_IN_RE = re.compile(r"^\s*([0-9]+)\s+block input operations\s*$", re.MULTILINE)
_BLOCK_OUT_RE = re.compile(r"^\s*([0-9]+)\s+block output operations\s*$", re.MULTILINE)


def parse_bsd_time(text: str, step: str | None = None) -> StepUsage:
    """Parse one macOS ``/usr/bin/time -l`` report.

    Raises ``ValueError`` if ``text`` has no "N real N user N sys" line or no
    "maximum resident set size" line, the two markers every ``-l`` report
    carries.
    """
    times_match = _TIMES_RE.search(text)
    maxrss_match = _MAXRSS_RE.search(text)
    if not times_match or not maxrss_match:
        raise ValueError("not a macOS time -l report: missing the real/user/sys or rss line")

    notes: list[str] = []
    # Block input/output operation counts have no documented byte size on
    # macOS either, so they stay in notes rather than feeding read/write bytes.
    block_in_match = _BLOCK_IN_RE.search(text)
    block_out_match = _BLOCK_OUT_RE.search(text)
    if block_in_match:
        notes.append(f"block input operations: {block_in_match.group(1)}")
    if block_out_match:
        notes.append(f"block output operations: {block_out_match.group(1)}")

    return StepUsage(
        source="bsd_time",
        step=step,
        wall_s=float(times_match.group(1)),
        user_s=float(times_match.group(2)),
        sys_s=float(times_match.group(3)),
        # macOS time -l never prints an exit status; the shell's own $? has it.
        max_rss_bytes=int(maxrss_match.group(1)),
        notes=notes,
    )
