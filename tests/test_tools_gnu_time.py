from __future__ import annotations

from pathlib import Path

import pytest

from pipeprof.tools.gnu_time import parse_gnu_time


def test_ok(data: Path) -> None:
    usage = parse_gnu_time((data / "tools" / "gnu_time_ok.txt").read_text(), step="align")
    assert usage.source == "gnu_time"
    assert usage.step == "align"
    assert usage.user_s == 1.23
    assert usage.sys_s == 0.45
    assert usage.cpu_percent == 98.0
    assert usage.wall_s == pytest.approx(1.72)
    assert usage.max_rss_bytes == 20480 * 1024
    assert usage.exit_code == 0
    assert usage.cpu_s == pytest.approx(1.68)
    assert "file system inputs: 128 (512-byte blocks)" in usage.notes
    assert "file system outputs: 16 (512-byte blocks)" in usage.notes


def test_hms_elapsed(data: Path) -> None:
    usage = parse_gnu_time((data / "tools" / "gnu_time_long.txt").read_text())
    assert usage.wall_s == pytest.approx(5405.0)
    assert usage.max_rss_bytes == 8388608 * 1024
    assert usage.exit_code == 1


def test_killed_by_signal_and_unknown_cpu(data: Path) -> None:
    text = (data / "tools" / "gnu_time_killed.txt").read_text()
    usage = parse_gnu_time(text)
    assert usage.cpu_percent is None
    assert usage.exit_code == -9
    assert "terminated by signal 9" in usage.notes
    # The program's own traceback sits above the report and must be ignored.
    assert usage.user_s == 0.51


def test_not_gnu_time_output() -> None:
    with pytest.raises(ValueError, match="not a GNU time"):
        parse_gnu_time("real 0.01\nuser 0.00\nsys 0.00\n")


def test_signal_wins_over_exit_status_line(data: Path) -> None:
    # Both "Command terminated by signal 9" and an "Exit status" line: the
    # signal is what ended the command, so it must not be lost.
    usage = parse_gnu_time((data / "tools" / "gnu_time_killed_exit_status.txt").read_text())
    assert usage.exit_code == -9
    assert "terminated by signal 9" in usage.notes


_NESTED_REPORT = """\
Command terminated by signal 9
\tCommand being timed: "inner_tool --big"
\tUser time (seconds): 9.00
\tElapsed (wall clock) time (h:mm:ss or m:ss): 0:20.00
\tExit status: 0
"""

_OUTER_REPORT = """\
\tCommand being timed: "run_step.sh"
\tUser time (seconds): 1.50
\tSystem time (seconds): 0.25
\tPercent of CPU this job got: 87%
\tElapsed (wall clock) time (h:mm:ss or m:ss): 0:02.00
\tMaximum resident set size (kbytes): 1024
\tExit status: 0
"""


def test_nested_report_in_program_stderr_is_ignored() -> None:
    # The program ran its own time -v and a killed child left a signal line in
    # stderr; the outer report says the step itself exited 0.
    usage = parse_gnu_time("log line\n" + _NESTED_REPORT + "more log\n" + _OUTER_REPORT)
    assert usage.exit_code == 0
    assert not any("signal" in note for note in usage.notes)
    assert usage.user_s == 1.5
    assert usage.wall_s == pytest.approx(2.0)
    assert usage.max_rss_bytes == 1024 * 1024


def test_signal_line_just_above_the_last_report_counts() -> None:
    text = _NESTED_REPORT + "Command terminated by signal 15\n" + _OUTER_REPORT
    usage = parse_gnu_time(text)
    assert usage.exit_code == -15
    assert "terminated by signal 15" in usage.notes
    assert "exit status line also present: 0" in usage.notes
    assert usage.user_s == 1.5
