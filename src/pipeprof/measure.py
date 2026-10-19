"""Measure a command: wall time, CPU time, peak memory of the whole process tree and I/O.

Two sources are combined:

* ``os.wait4`` returns the kernel's resource usage for the child and every
  descendant it waited for. This gives exact CPU time and the largest single
  process peak (``ru_maxrss``).
* A sampling thread walks the process tree with :mod:`psutil` every
  ``interval`` seconds and sums resident memory across all live processes.
  This catches pipelines where several processes hold memory at once, which
  ``ru_maxrss`` cannot see.

The reported peak is the larger of the two. Sampling can miss a spike shorter
than the interval, so the sampled value is a lower bound; the docs explain how
to choose the interval.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import IO, Any

import psutil

#: psutil exposes per process I/O counters on Linux and Windows, not on macOS.
HAS_IO_COUNTERS = hasattr(psutil.Process, "io_counters")


@dataclass
class TreeSample:
    """One snapshot of a process tree."""

    elapsed_s: float
    rss_bytes: int
    num_procs: int
    cpu_seconds: float
    read_bytes: int | None
    write_bytes: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TreeSampler:
    """Sample a process and all of its descendants.

    CPU seconds and I/O bytes are cumulative: the last value seen for each
    process is kept after it exits, so the totals do not drop when a short
    child finishes between samples.
    """

    def __init__(self, pid: int) -> None:
        self.root = psutil.Process(pid)
        self.start = time.monotonic()
        self._cpu: dict[tuple[int, float], float] = {}
        self._read: dict[tuple[int, float], int] = {}
        self._write: dict[tuple[int, float], int] = {}

    def processes(self) -> list[psutil.Process]:
        try:
            return [self.root, *self.root.children(recursive=True)]
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            return []
        except psutil.AccessDenied:
            return [self.root]

    def sample(self) -> TreeSample | None:
        """Take one sample, or return ``None`` once the root process is gone."""
        procs = self.processes()
        if not procs:
            return None
        rss = 0
        count = 0
        for proc in procs:
            try:
                with proc.oneshot():
                    if proc.status() == psutil.STATUS_ZOMBIE:
                        continue
                    key = (proc.pid, proc.create_time())
                    mem = proc.memory_info()
                    cpu = proc.cpu_times()
                    rss += int(mem.rss)
                    count += 1
                    self._cpu[key] = float(cpu.user + cpu.system)
                    if HAS_IO_COUNTERS:
                        io = proc.io_counters()  # type: ignore[attr-defined,unused-ignore]
                        self._read[key] = int(io.read_bytes)
                        self._write[key] = int(io.write_bytes)
            except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
                continue
        if count == 0:
            return None
        return TreeSample(
            elapsed_s=time.monotonic() - self.start,
            rss_bytes=rss,
            num_procs=count,
            cpu_seconds=sum(self._cpu.values()),
            read_bytes=sum(self._read.values()) if HAS_IO_COUNTERS else None,
            write_bytes=sum(self._write.values()) if HAS_IO_COUNTERS else None,
        )


@dataclass
class Measurement:
    """Resource use of one finished command."""

    command: str
    returncode: int
    wall_s: float
    cpu_user_s: float
    cpu_system_s: float
    peak_rss_bytes: int
    sampled_peak_rss_bytes: int
    rusage_maxrss_bytes: int
    read_bytes: int | None
    write_bytes: int | None
    block_input_ops: int
    block_output_ops: int
    samples: int
    interval_s: float
    timeline: list[TreeSample] = field(default_factory=list)

    @property
    def cpu_s(self) -> float:
        """User plus system CPU seconds."""
        return self.cpu_user_s + self.cpu_system_s

    @property
    def cpu_efficiency_1core(self) -> float:
        """CPU seconds per wall second; 1.0 means one core kept busy."""
        return self.cpu_s / self.wall_s if self.wall_s > 0 else 0.0

    def to_dict(self, with_timeline: bool = False) -> dict[str, Any]:
        data = asdict(self)
        data["cpu_s"] = self.cpu_s
        if not with_timeline:
            data.pop("timeline")
        return data


def maxrss_to_bytes(value: int, platform: str = sys.platform) -> int:
    """Convert ``ru_maxrss`` to bytes: Linux reports kilobytes, macOS bytes."""
    return int(value) if platform == "darwin" else int(value) * 1024


class CommandFailedError(RuntimeError):
    """Raised when a measured command exits with a non-zero status and ``check`` is set."""

    def __init__(self, measurement: Measurement) -> None:
        super().__init__(
            f"command exited with status {measurement.returncode}: {measurement.command}"
        )
        self.measurement = measurement


def run_measured(
    command: Sequence[str] | str,
    *,
    shell: bool = False,
    interval: float = 0.05,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    stdout: IO[Any] | int | None = subprocess.DEVNULL,
    stderr: IO[Any] | int | None = None,
    keep_timeline: bool = False,
    on_sample: Callable[[TreeSample], None] | None = None,
    check: bool = False,
) -> Measurement:
    """Run ``command`` and measure it.

    ``command`` is a list of arguments, or a string when ``shell`` is true
    (then ``/bin/sh -c`` runs it, which allows pipes). ``on_sample`` is called
    from the sampling thread for each sample, which ``pipeprof watch`` uses.
    """
    if shell:
        text = command if isinstance(command, str) else " ".join(command)
        argv: list[str] = ["/bin/sh", "-c", text]
        printable = text
    else:
        argv = shlex.split(command) if isinstance(command, str) else list(command)
        printable = shlex.join(argv)

    start = time.monotonic()
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        stdout=stdout,
        stderr=stderr,
    )
    sampler = TreeSampler(proc.pid)
    timeline: list[TreeSample] = []
    stats = {"peak": 0, "count": 0, "read": None, "write": None}
    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            sample = sampler.sample()
            if sample is not None:
                stats["count"] = int(stats["count"] or 0) + 1
                stats["peak"] = max(int(stats["peak"] or 0), sample.rss_bytes)
                stats["read"] = sample.read_bytes
                stats["write"] = sample.write_bytes
                if keep_timeline:
                    timeline.append(sample)
                if on_sample is not None:
                    on_sample(sample)
            stop.wait(interval)

    thread = threading.Thread(target=loop, name="pipeprof-sampler", daemon=True)
    thread.start()
    try:
        _, status, usage = os.wait4(proc.pid, 0)
    except BaseException:
        proc.kill()
        proc.wait()
        stop.set()
        thread.join()
        raise
    wall = time.monotonic() - start
    stop.set()
    thread.join()
    returncode = os.waitstatus_to_exitcode(status)
    proc.returncode = returncode

    rusage_peak = maxrss_to_bytes(usage.ru_maxrss)
    sampled_peak = int(stats["peak"] or 0)
    read_bytes = stats["read"]
    write_bytes = stats["write"]
    measurement = Measurement(
        command=printable,
        returncode=returncode,
        wall_s=wall,
        cpu_user_s=float(usage.ru_utime),
        cpu_system_s=float(usage.ru_stime),
        peak_rss_bytes=max(rusage_peak, sampled_peak),
        sampled_peak_rss_bytes=sampled_peak,
        rusage_maxrss_bytes=rusage_peak,
        read_bytes=int(read_bytes) if read_bytes is not None else None,
        write_bytes=int(write_bytes) if write_bytes is not None else None,
        block_input_ops=int(usage.ru_inblock),
        block_output_ops=int(usage.ru_oublock),
        samples=int(stats["count"] or 0),
        interval_s=interval,
        timeline=timeline,
    )
    if check and returncode != 0:
        raise CommandFailedError(measurement)
    return measurement
