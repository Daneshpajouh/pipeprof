from __future__ import annotations

import sys

import pytest

from pipeprof.measure import CommandFailedError, TreeSampler, maxrss_to_bytes, run_measured

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
    # Children hold memory for 2 s so a slow sampler on a loaded machine still sees both.
    hold = ALLOC.replace("time.sleep(0.4)", "time.sleep(2)")
    script = f'"{PY}" -c "{hold}" & "{PY}" -c "{hold}" & wait'
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


def test_getrusage_fallback_without_wait4(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from pipeprof import measure

    # As on a host whose Python has no os.wait4: the getrusage path must do the work.
    monkeypatch.delattr(os, "wait4")
    monkeypatch.setattr(measure, "HAS_WAIT4", False)
    busy = "sum(i * i for i in range(3_000_000))"
    m = run_measured([PY, "-c", f"{busy}; {ALLOC}"], interval=0.02)
    assert m.returncode == 0
    assert m.cpu_s > 0.05
    assert m.wall_s >= 0.4
    assert m.peak_rss_bytes >= 100 * 1024 * 1024
    assert run_measured([PY, "-c", "import sys; sys.exit(3)"]).returncode == 3


def test_getrusage_maxrss_counts_only_when_it_rose(monkeypatch: pytest.MonkeyPatch) -> None:
    import resource

    from pipeprof import measure

    def rusage(utime: float, maxrss: int) -> resource.struct_rusage:
        return resource.struct_rusage((utime, 0.5, maxrss, *([0] * 13)))

    class Done:
        def wait(self) -> int:
            return 0

    before = rusage(1.0, 500)
    monkeypatch.setattr(resource, "getrusage", lambda who: rusage(3.5, 500))
    code, usage = measure._wait_getrusage(Done(), before)  # type: ignore[arg-type]
    assert code == 0 and usage.utime == 2.5 and usage.stime == 0.0
    assert usage.maxrss == 0  # an earlier child set that peak, not this command
    monkeypatch.setattr(resource, "getrusage", lambda who: rusage(3.5, 900))
    assert measure._wait_getrusage(Done(), before)[1].maxrss == 900  # type: ignore[arg-type]
