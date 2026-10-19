from __future__ import annotations

from pathlib import Path

import pytest

from pipeprof.tools.psrecord import parse_psrecord, psrecord_samples


def test_samples(data: Path) -> None:
    text = (data / "tools" / "psrecord_ok.txt").read_text()
    samples = psrecord_samples(text)
    assert samples[0] == (0.0, 0.0, 10.2, 100.3)
    assert len(samples) == 5


def test_ok(data: Path) -> None:
    text = (data / "tools" / "psrecord_ok.txt").read_text()
    usage = parse_psrecord(text, step="scan")
    assert usage.source == "psrecord"
    assert usage.step == "scan"
    assert usage.wall_s == pytest.approx(2.00)
    assert usage.cpu_percent == pytest.approx((0.0 + 55.1 + 89.4 + 12.0 + 0.0) / 5)
    assert usage.max_rss_bytes == round(95.30 * 1024 * 1024)
    assert any("peak virtual memory" in note for note in usage.notes)


def test_no_samples(data: Path) -> None:
    usage = parse_psrecord((data / "tools" / "psrecord_empty.txt").read_text())
    assert usage.wall_s is None
    assert usage.max_rss_bytes is None
    assert usage.notes == ["no samples in log"]


def test_not_psrecord_output() -> None:
    with pytest.raises(ValueError, match="not a psrecord"):
        psrecord_samples("time,cpu,rss\n0,0,0\n")


def test_malformed_sample_line(data: Path) -> None:
    text = (data / "tools" / "psrecord_ok.txt").read_text() + "3.00 1.0 2.0\n"
    with pytest.raises(ValueError, match="bad psrecord sample line"):
        psrecord_samples(text)
