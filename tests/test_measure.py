from __future__ import annotations

import sys

import pytest

from hpc_job_profiler.measure import CommandFailedError, TreeSampler, maxrss_to_bytes, run_measured

PY = sys.executable
ALLOC = (
    "import time; b = bytearray(120 * 1024 * 1024); "
    "b[::4096] = b'x' * len(b[::4096]); time.sleep(0.4)"
)


def test_measures_memory_of_one_process() -> None:
    m = run_measured([PY, "-c", ALLOC], interval=0.02, keep_timeline=True)
    assert m.returncode == 0
    assert m.peak_rss_bytes >= 100 * 1024 * 1024
    assert m.samples >= 1 and m.timeline
    assert m.wall_s >= 0.4
    data = m.to_dict()
    assert "timeline" not in data and data["cpu_s"] == m.cpu_s
    assert m.to_dict(with_timeline=True)["timeline"]


def test_tree_peak_counts_concurrent_children() -> None:
    script = f'"{PY}" -c "{ALLOC}" & "{PY}" -c "{ALLOC}" & wait'
    seen: list[int] = []
    m = run_measured(
        script, shell=True, interval=0.02, on_sample=lambda s: seen.append(s.num_procs)
    )
    # Two children hold 120 MiB each at the same time; ru_maxrss sees only one.
    assert m.sampled_peak_rss_bytes >= 200 * 1024 * 1024
    assert m.rusage_maxrss_bytes < m.sampled_peak_rss_bytes
    assert max(seen) >= 3


def test_cpu_time_and_failures() -> None:
    m = run_measured([PY, "-c", "sum(i * i for i in range(3_000_000))"])
    assert m.cpu_s > 0.05
    assert 0 < m.cpu_efficiency_1core <= 1.5
    failed = run_measured(f"{PY} -c 'import sys; sys.exit(3)'")
    assert failed.returncode == 3
    with pytest.raises(CommandFailedError) as info:
        run_measured([PY, "-c", "import sys; sys.exit(4)"], check=True)
    assert info.value.measurement.returncode == 4


def test_sampler_returns_none_after_exit() -> None:
    import subprocess

    proc = subprocess.Popen([PY, "-c", "import time; time.sleep(5)"])
    sampler = TreeSampler(proc.pid)
    assert sampler.sample() is not None
    proc.kill()
    proc.wait()
    assert sampler.sample() is None


def test_maxrss_units() -> None:
    assert maxrss_to_bytes(10, "darwin") == 10
    assert maxrss_to_bytes(10, "linux") == 10240
