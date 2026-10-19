"""Read GNU ``time -v`` reports.

Format: the GNU time info manual (https://www.gnu.org/software/time/, read
2026-10-19), "Full documentation of resource-usage information" and the
`-v`/`--verbose` section. The report is a fixed set of ``Label: value`` lines,
each on its own line and usually indented with a tab; it is written after the
timed command, so a program's own stderr can appear above it. A command
killed by a signal prints "Command terminated by signal N" instead of the
usual "Exit status" line.
"""

from __future__ import annotations

import re

from pipeprof.tools.usage import StepUsage

_COMMAND_RE = re.compile(r'^\s*Command being timed:\s*"(.*)"\s*$', re.MULTILINE)
_USER_RE = re.compile(r"^\s*User time \(seconds\):\s*([0-9.]+)\s*$", re.MULTILINE)
_SYS_RE = re.compile(r"^\s*System time \(seconds\):\s*([0-9.]+)\s*$", re.MULTILINE)
_CPU_RE = re.compile(r"^\s*Percent of CPU this job got:\s*(\?|[0-9]+)%?\s*$", re.MULTILINE)
_ELAPSED_RE = re.compile(
    r"^\s*Elapsed \(wall clock\) time \(h:mm:ss or m:ss\):\s*([0-9:.]+)\s*$", re.MULTILINE
)
_MAXRSS_RE = re.compile(r"^\s*Maximum resident set size \(kbytes\):\s*([0-9]+)\s*$", re.MULTILINE)
_FS_IN_RE = re.compile(r"^\s*File system inputs:\s*([0-9]+)\s*$", re.MULTILINE)
_FS_OUT_RE = re.compile(r"^\s*File system outputs:\s*([0-9]+)\s*$", re.MULTILINE)
_EXIT_RE = re.compile(r"^\s*Exit status:\s*(-?[0-9]+)\s*$", re.MULTILINE)
_SIGNAL_RE = re.compile(r"^\s*Command terminated by signal ([0-9]+)\s*$", re.MULTILINE)


def _parse_elapsed(text: str) -> float:
    """Return seconds for ``h:mm:ss`` or ``m:ss.ss`` (GNU time never uses days)."""
    parts = text.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = int(parts[0]), int(parts[1]), float(parts[2])
        return hours * 3600 + minutes * 60 + seconds
    if len(parts) == 2:
        minutes, seconds = int(parts[0]), float(parts[1])
        return minutes * 60 + seconds
    raise ValueError(f"bad GNU time elapsed value: {text!r}")


def parse_gnu_time(text: str, step: str | None = None) -> StepUsage:
    """Parse one GNU ``/usr/bin/time -v`` report, ignoring any text above it.

    Raises ``ValueError`` if ``text`` has no "Command being timed" line, the
    one marker every ``-v`` report carries.
    """
    if not _COMMAND_RE.search(text):
        raise ValueError("not a GNU time -v report: no 'Command being timed' line")

    notes: list[str] = []

    user_match = _USER_RE.search(text)
    sys_match = _SYS_RE.search(text)
    elapsed_match = _ELAPSED_RE.search(text)
    maxrss_match = _MAXRSS_RE.search(text)
    cpu_match = _CPU_RE.search(text)

    cpu_percent: float | None = None
    if cpu_match:
        cpu_percent = None if cpu_match.group(1) == "?" else float(cpu_match.group(1))

    exit_code: int | None = None
    exit_match = _EXIT_RE.search(text)
    signal_match = _SIGNAL_RE.search(text)
    if exit_match:
        exit_code = int(exit_match.group(1))
    elif signal_match:
        signal = int(signal_match.group(1))
        # Follow the POSIX/subprocess convention of a negative return code for a
        # command a signal killed, since GNU time prints no exit status here.
        exit_code = -signal
        notes.append(f"terminated by signal {signal}")

    # File system inputs/outputs are counts of 512-byte blocks per the GNU time
    # manual; keep them as raw counts rather than guessing at a byte total.
    fs_in_match = _FS_IN_RE.search(text)
    fs_out_match = _FS_OUT_RE.search(text)
    if fs_in_match:
        notes.append(f"file system inputs: {fs_in_match.group(1)} (512-byte blocks)")
    if fs_out_match:
        notes.append(f"file system outputs: {fs_out_match.group(1)} (512-byte blocks)")

    return StepUsage(
        source="gnu_time",
        step=step,
        wall_s=_parse_elapsed(elapsed_match.group(1)) if elapsed_match else None,
        user_s=float(user_match.group(1)) if user_match else None,
        sys_s=float(sys_match.group(1)) if sys_match else None,
        cpu_percent=cpu_percent,
        max_rss_bytes=int(maxrss_match.group(1)) * 1024 if maxrss_match else None,
        exit_code=exit_code,
        notes=notes,
    )
