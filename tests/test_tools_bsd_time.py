from __future__ import annotations

from pathlib import Path

import pytest

from pipeprof.tools.bsd_time import parse_bsd_time
from pipeprof.tools.gnu_time import parse_gnu_time


def test_ok(data: Path) -> None:
    usage = parse_bsd_time((data / "tools" / "bsd_time_ok.txt").read_text(), step="alloc")
    assert usage.source == "bsd_time"
    assert usage.step == "alloc"
    assert usage.wall_s == pytest.approx(0.05)
    assert usage.user_s == pytest.approx(0.02)
    assert usage.sys_s == pytest.approx(0.01)
    assert usage.max_rss_bytes == 68714496
    assert usage.exit_code is None
    assert usage.notes == ["block input operations: 0", "block output operations: 0"]


def test_failing_command_still_reports_usage(data: Path) -> None:
    # macOS time -l never prints the command's exit status, pass or fail.
    usage = parse_bsd_time((data / "tools" / "bsd_time_failing.txt").read_text())
    assert usage.wall_s == pytest.approx(0.04)
    assert usage.max_rss_bytes == 18792448
    assert usage.exit_code is None


def test_block_io_counts_in_notes(data: Path) -> None:
    usage = parse_bsd_time((data / "tools" / "bsd_time_io.txt").read_text())
    assert "block input operations: 512" in usage.notes
    assert "block output operations: 48" in usage.notes


def test_not_bsd_time_output() -> None:
    with pytest.raises(ValueError, match="not a macOS time -l"):
        parse_bsd_time('Command being timed: "x"\nUser time (seconds): 0.0\n')


def test_rss_units_differ_gnu_kbytes_vs_bsd_bytes(data: Path) -> None:
    # GNU time -v reports kbytes (so the reader multiplies by 1024); macOS
    # time -l already reports bytes. Same fixture value, different meaning.
    gnu = parse_gnu_time((data / "tools" / "gnu_time_ok.txt").read_text())
    bsd = parse_bsd_time((data / "tools" / "bsd_time_ok.txt").read_text())
    assert gnu.max_rss_bytes == 20480 * 1024
    assert bsd.max_rss_bytes == 68714496
    assert bsd.max_rss_bytes != 68714496 * 1024
