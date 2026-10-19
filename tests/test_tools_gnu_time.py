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
