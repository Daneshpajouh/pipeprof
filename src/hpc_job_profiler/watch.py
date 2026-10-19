"""During the run: sample a local process tree or a Slurm job into JSON lines, with alerts.

Each sample line has ``type: "sample"``; each alert has ``type: "alert"``.
Alerts fire once when their condition starts and once more (``cleared``)
when it ends, so a long run does not flood the log.

Alert rules:

* ``memory_near_limit``: RSS at or above ``mem_warn`` (default 90%) of the limit.
* ``cpu_underuse``: average CPU over the last ``window_s`` below
  ``cpu_underuse`` (default 50%) of the allocated CPUs.
* ``stalled``: no CPU progress and no I/O change for ``stall_s`` seconds.
  On macOS psutil has no per process I/O counters, so only CPU is checked.
"""

from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from hpc_job_profiler.logkit import new_run_id, utc_now_iso
from hpc_job_profiler.measure import TreeSample, TreeSampler
from hpc_job_profiler.slurm import TERMINAL_STATES, query_limits, query_sstat


@dataclass
class AlertConfig:
    """Thresholds for the live alerts."""

    mem_limit_bytes: int | None = None
    mem_warn: float = 0.9
    cpus: int | None = None
    cpu_underuse: float = 0.5
    window_s: float = 60.0
    stall_s: float = 120.0


@dataclass
class Point:
    """A sample reduced to what the alert rules need."""

    t: float
    rss_bytes: int
    cpu_seconds: float
    io_bytes: int | None


@dataclass
class AlertEngine:
    """Evaluates alert rules on a stream of points."""

    config: AlertConfig
    history: deque[Point] = field(default_factory=lambda: deque(maxlen=100_000))
    active: set[str] = field(default_factory=set)

    def feed(self, point: Point) -> list[dict[str, Any]]:
        """Add a point; return alert events that started or cleared."""
        self.history.append(point)
        while (
            self.history
            and point.t - self.history[0].t > max(self.config.window_s, self.config.stall_s) + 1
        ):
            self.history.popleft()
        conditions = {
            "memory_near_limit": self._memory(point),
            "cpu_underuse": self._underuse(point),
            "stalled": self._stalled(point),
        }
        events: list[dict[str, Any]] = []
        for name, message in conditions.items():
            if message and name not in self.active:
                self.active.add(name)
                events.append({"alert": name, "status": "started", "message": message})
            elif not message and name in self.active:
                self.active.discard(name)
                events.append({"alert": name, "status": "cleared", "message": ""})
        return events

    def _since(self, seconds: float, now: float) -> Point | None:
        older = [p for p in self.history if now - p.t >= seconds]
        return older[-1] if older else None

    def _memory(self, point: Point) -> str:
        limit = self.config.mem_limit_bytes
        if not limit:
            return ""
        share = point.rss_bytes / limit
        if share >= self.config.mem_warn:
            return f"RSS at {share:.0%} of the {limit / 2**30:.2f} GiB limit"
        return ""

    def _underuse(self, point: Point) -> str:
        if not self.config.cpus:
            return ""
        base = self._since(self.config.window_s, point.t)
        if base is None or point.t <= base.t:
            return ""
        busy = (point.cpu_seconds - base.cpu_seconds) / (point.t - base.t)
        if busy < self.config.cpu_underuse * self.config.cpus:
            return (
                f"{busy:.2f} cores busy over the last {self.config.window_s:.0f} s "
                f"with {self.config.cpus} allocated"
            )
        return ""

    def _stalled(self, point: Point) -> str:
        base = self._since(self.config.stall_s, point.t)
        if base is None:
            return ""
        cpu_moved = point.cpu_seconds - base.cpu_seconds > 0.01 * (point.t - base.t)
        io_moved = (
            point.io_bytes is not None
            and base.io_bytes is not None
            and point.io_bytes != base.io_bytes
        )
        if not cpu_moved and not io_moved:
            return f"no CPU or I/O progress for {point.t - base.t:.0f} s"
        return ""


class JsonlSink:
    """Append records to a JSON lines stream, each tagged with the run ID."""

    def __init__(self, handle: IO[str], run_id: str | None = None) -> None:
        self.handle = handle
        self.run_id = run_id or new_run_id()

    def write(self, record: dict[str, Any]) -> None:
        # Flush every line so `hjp tail` sees it at once.
        self.handle.write(json.dumps({"run_id": self.run_id, **record}) + "\n")
        self.handle.flush()

    def start(self, config: AlertConfig, **fields: Any) -> None:
        """Write the ``start`` record: what is watched and against which limits."""
        self.write(
            {
                "type": "start",
                "ts": utc_now_iso(),
                "mem_limit_bytes": config.mem_limit_bytes,
                "cpus": config.cpus,
                "thresholds": {
                    "mem_warn": config.mem_warn,
                    "cpu_underuse": config.cpu_underuse,
                    "window_s": config.window_s,
                    "stall_s": config.stall_s,
                },
                **fields,
            }
        )


def _io_total(sample: TreeSample) -> int | None:
    if sample.read_bytes is None or sample.write_bytes is None:
        return None
    return sample.read_bytes + sample.write_bytes


def watch_pid(
    pid: int,
    sink: JsonlSink,
    config: AlertConfig,
    interval: float = 5.0,
    max_seconds: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Sample a local process tree until it exits; return the number of samples."""
    sampler = TreeSampler(pid)
    engine = AlertEngine(config)
    sink.start(config, source="local", pid=pid, command=" ".join(sampler.root.cmdline()))
    count = 0
    started = time.monotonic()
    prev: TreeSample | None = None
    while True:
        sample = sampler.sample()
        if sample is None:
            break
        cpu_pct = None
        if prev is not None and sample.elapsed_s > prev.elapsed_s:
            cpu_pct = (
                100 * (sample.cpu_seconds - prev.cpu_seconds) / (sample.elapsed_s - prev.elapsed_s)
            )
        prev = sample
        count += 1
        sink.write(
            {
                "type": "sample",
                "ts": utc_now_iso(),
                "source": "local",
                "pid": pid,
                "cpu_percent": cpu_pct,
                **sample.to_dict(),
            }
        )
        point = Point(sample.elapsed_s, sample.rss_bytes, sample.cpu_seconds, _io_total(sample))
        for event in engine.feed(point):
            sink.write({"type": "alert", "ts": utc_now_iso(), "pid": pid, **event})
        if max_seconds is not None and time.monotonic() - started >= max_seconds:
            break
        sleep(interval)
    return count


def watch_slurm(
    job_id: str,
    sink: JsonlSink,
    config: AlertConfig,
    interval: float = 30.0,
    max_seconds: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Sample a Slurm job with sstat until it leaves the running state."""
    limits = query_limits(job_id)
    if config.mem_limit_bytes is None:
        config.mem_limit_bytes = limits.req_mem_bytes
    if config.cpus is None:
        config.cpus = limits.alloc_cpus or None
    engine = AlertEngine(config)
    sink.start(config, source="slurm", job_id=job_id, time_limit_s=limits.time_limit_s)
    started = time.monotonic()
    count = 0
    while True:
        steps = query_sstat(job_id)
        elapsed = time.monotonic() - started
        if steps:
            rss = max(s.max_rss_bytes for s in steps)
            cpu = sum(s.cpu_s for s in steps)
            reads = [s.max_disk_read_bytes for s in steps]
            writes = [s.max_disk_write_bytes for s in steps]
            io_total = None
            if all(v is not None for v in reads + writes):
                io_total = sum(v for v in reads + writes if v is not None)
            count += 1
            sink.write(
                {
                    "type": "sample",
                    "ts": utc_now_iso(),
                    "source": "slurm",
                    "job_id": job_id,
                    "elapsed_s": elapsed,
                    "rss_bytes": rss,
                    "cpu_seconds": cpu,
                    "io_bytes": io_total,
                    "steps": [s.step for s in steps],
                }
            )
            for event in engine.feed(Point(elapsed, rss, cpu, io_total)):
                sink.write({"type": "alert", "ts": utc_now_iso(), "job_id": job_id, **event})
        else:
            state = query_limits(job_id).state
            if state in TERMINAL_STATES:
                sink.write({"type": "end", "ts": utc_now_iso(), "job_id": job_id, "state": state})
                break
        if max_seconds is not None and elapsed >= max_seconds:
            break
        sleep(interval)
    return count


def read_watch_log(path: str | Path) -> list[dict[str, Any]]:
    """Read a watch log, ignoring a partly written last line."""
    records: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
    return records
