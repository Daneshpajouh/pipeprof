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
    assert usage.cpu_percent == pytest.approx(83.33 * 4)  # per one core
    assert usage.max_rss_bytes == round(512.0 * 1024 * 1024)
    assert "state: COMPLETED" in usage.notes
    assert "cores: 4" in usage.notes
    assert any("memory efficiency: 50.0% of" in note for note in usage.notes)


def test_running_job(data: Path) -> None:
    usage = parse_seff((data / "tools" / "seff_running.txt").read_text())
    assert usage.exit_code is None
    assert usage.cpu_percent == pytest.approx(45.83 * 8)  # per one core
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


def test_cpu_percent_is_per_one_core(data: Path) -> None:
    # seff's CPU Efficiency is a share of all allocated cores (83.33% of 4);
    # StepUsage.cpu_percent is percent of one core, as GNU time and the
    # runners report it, so it is efficiency x cores: 3.33 cores busy.
    usage = parse_seff((data / "tools" / "seff_completed.txt").read_text())
    assert usage.cpu_percent == pytest.approx(83.33 * 4)
    assert "CPU efficiency: 83.33% of 4 allocated cores" in usage.notes
    # Without a core count the per-core figure is unknown, not guessed.
    no_cores = (
        (data / "tools" / "seff_completed.txt").read_text().replace("Cores per node: 4\n", "")
    )
    usage = parse_seff(no_cores)
    assert usage.cpu_percent is None
    assert any(n.startswith("CPU efficiency: 83.33%") for n in usage.notes)


@pytest.mark.parametrize("unit", ["node", "core"])
def test_memory_efficiency_with_per_unit_suffix(data: Path, unit: str) -> None:
    # Some seff versions add the request per node or per core after the total.
    text = (
        (data / "tools" / "seff_completed.txt")
        .read_text()
        .replace(
            "Memory Efficiency: 50.00% of 1.00 GB",
            f"Memory Efficiency: 50.00% of 1.00 GB (256.00 MB/{unit})",
        )
    )
    usage = parse_seff(text)
    assert any("memory efficiency: 50.0% of" in note for note in usage.notes)


def test_unread_memory_efficiency_line_is_noted(data: Path) -> None:
    text = (
        (data / "tools" / "seff_completed.txt")
        .read_text()
        .replace("Memory Efficiency: 50.00% of 1.00 GB", "Memory Efficiency: n/a")
    )
    usage = parse_seff(text)
    assert "memory efficiency line not read: Memory Efficiency: n/a" in usage.notes
