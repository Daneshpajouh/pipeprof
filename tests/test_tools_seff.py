from __future__ import annotations

from pathlib import Path

import pytest

from pipeprof.tools.seff import parse_seff


def test_completed(data: Path) -> None:
    usage = parse_seff((data / "tools" / "seff_completed.txt").read_text(), step="1234567")
    assert usage.source == "seff"
    assert usage.step == "1234567"
    assert usage.exit_code == 0
    assert usage.user_s == pytest.approx(2400.0)
    assert usage.wall_s == pytest.approx(720.0)
    assert usage.cpu_percent == pytest.approx(83.33)
    assert usage.max_rss_bytes == round(512.0 * 1024 * 1024)
    assert "state: COMPLETED" in usage.notes
    assert "cores: 4" in usage.notes
    assert any("memory efficiency: 50.0% of" in note for note in usage.notes)


def test_running_job(data: Path) -> None:
    usage = parse_seff((data / "tools" / "seff_running.txt").read_text())
    assert usage.exit_code is None
    assert usage.cpu_percent == pytest.approx(45.83)
    assert usage.wall_s == pytest.approx(1 * 86400 + 3 * 3600)
    assert usage.user_s == pytest.approx(1 * 86400 + 2 * 3600)
    assert usage.max_rss_bytes == 0
    assert "cores: 8" in usage.notes
    assert "memory utilized is seff's estimated maximum" in usage.notes
    assert "efficiency statistics may be misleading for a RUNNING job" in usage.notes


def test_array_job(data: Path) -> None:
    usage = parse_seff((data / "tools" / "seff_array.txt").read_text())
    assert usage.exit_code == 1
    assert "array job id: 3000000_5" in usage.notes
    assert "state: FAILED" in usage.notes
    # Nodes x Cores per node, since this fixture has no single "Cores:" line.
    assert "cores: 32" in usage.notes
    assert usage.wall_s == pytest.approx(5 * 3600)
    assert usage.user_s == pytest.approx(4 * 86400 + 8 * 3600 + 30 * 60 + 15)
    assert usage.max_rss_bytes == round(12.50 * 1024**3)


def test_not_seff_output() -> None:
    with pytest.raises(ValueError, match="not a seff report"):
        parse_seff("JobID|State|Elapsed\n123|COMPLETED|00:01:00\n")
