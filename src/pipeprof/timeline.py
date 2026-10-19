"""A run timeline: tasks with start and end times, built from different sources.

A :class:`Task` is the unit the bottleneck and bound analyses work from,
whatever tool produced it. Times are epoch seconds. A naive timestamp (no
time zone, as sacct, Nextflow and Snakemake all write) is read as UTC, so two
tasks parsed from the same file compare correctly regardless of the host's
own time zone; a time the source did not record stays ``None``.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pipeprof.records import (
    TaskRecord,
    _int_or_none,
    _parse_time,
    apply_snakemake_log,
    nextflow_process_name,
    parse_snakemake_benchmark,
    parse_snakemake_log,
)
from pipeprof.tools.usage import StepUsage
from pipeprof.units import parse_nextflow_duration, parse_nextflow_memory, parse_percent


@dataclass
class Task:
    """One task on the timeline, whatever source it came from.

    ``name`` is the specific task (a Nextflow tag or a Snakemake job ID);
    ``step`` is the process or rule it belongs to, for grouping.
    """

    name: str
    step: str
    submit: float | None
    start: float | None
    end: float | None
    attempt: int
    cpus: int | None
    status: str
    cpu_percent: float | None = None
    max_rss_bytes: int | None = None
    mem_request_bytes: int | None = None
    read_bytes: int | None = None
    write_bytes: int | None = None

    @property
    def wall_s(self) -> float | None:
        """End minus start, when both are known."""
        if self.start is None or self.end is None:
            return None
        return self.end - self.start

    @property
    def queue_wait_s(self) -> float | None:
        """Start minus submit, when both are known."""
        if self.submit is None or self.start is None:
            return None
        return max(0.0, self.start - self.submit)


def _epoch(value: dt.datetime | None) -> float | None:
    """Epoch seconds for a timestamp, treating a naive one as UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.UTC)
    return value.timestamp()


# Nextflow trace


def tasks_from_nextflow_trace(text: str) -> list[Task]:
    """Build a timeline from a Nextflow trace file, default units or ``trace.raw``.

    The default trace carries ``submit``, ``duration`` (submit to complete,
    including queue wait) and ``realtime`` (start to complete, the actual
    run); a custom trace can carry ``start`` or ``complete`` directly.
    Whichever fields are present, start and complete are filled in from the
    others: ``start = complete - realtime`` and ``complete = submit +
    duration`` when the direct column is missing.

    On macOS Nextflow writes ``-`` for ``%cpu``, ``peak_rss``, ``rchar`` and
    ``wchar``; :func:`~pipeprof.units.parse_percent` and
    :func:`~pipeprof.units.parse_nextflow_memory` already return
    ``None`` for that, so no extra case is needed here.
    """
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    seen: dict[str, int] = {}
    tasks: list[Task] = []
    for row in reader:
        name = row.get("process") or nextflow_process_name(row.get("name", ""))
        task_key = row.get("name", name)
        seen[task_key] = seen.get(task_key, 0) + 1
        attempt = _int_or_none(row.get("attempt")) or seen[task_key]

        submit = _parse_time(row.get("submit"))
        realtime = parse_nextflow_duration(row["realtime"]) if row.get("realtime") else None
        duration = parse_nextflow_duration(row["duration"]) if row.get("duration") else None
        start = _parse_time(row.get("start")) if row.get("start") else None
        complete = _parse_time(row.get("complete")) if row.get("complete") else None

        if start is None and complete is not None and realtime is not None:
            start = complete - dt.timedelta(seconds=realtime)
        if start is None and submit is not None and duration is not None and realtime is not None:
            start = submit + dt.timedelta(seconds=duration - realtime)
        if complete is None and start is not None and realtime is not None:
            complete = start + dt.timedelta(seconds=realtime)
        if complete is None and submit is not None and duration is not None:
            complete = submit + dt.timedelta(seconds=duration)

        status = (row.get("status") or "").strip().upper()
        tasks.append(
            Task(
                name=task_key,
                step=name,
                submit=_epoch(submit),
                start=_epoch(start),
                end=_epoch(complete),
                attempt=attempt,
                cpus=_int_or_none(row.get("cpus")),
                status={"COMPLETED": "COMPLETED", "CACHED": "COMPLETED"}.get(
                    status, status or "UNKNOWN"
                ),
                cpu_percent=parse_percent(row["%cpu"]) if row.get("%cpu") else None,
                max_rss_bytes=parse_nextflow_memory(row["peak_rss"])
                if row.get("peak_rss")
                else None,
                mem_request_bytes=parse_nextflow_memory(row["memory"])
                if row.get("memory")
                else None,
                read_bytes=parse_nextflow_memory(row["rchar"]) if row.get("rchar") else None,
                write_bytes=parse_nextflow_memory(row["wchar"]) if row.get("wchar") else None,
            )
        )
    return tasks


# Snakemake benchmarks and log

_TS_RE = re.compile(r"^\[(\w{3})\s+(\w{3})\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})\s+(\d{4})\]$")
_RULE_HEADER_RE = re.compile(r"^(?:local)?(?:rule|checkpoint) \S+:\s*$")
_ERROR_HEADER_RE = re.compile(r"^Error in rule \S+:\s*$")
_FINISHED_RE = re.compile(r"^Finished job(?:id)?:? (\d+)")
_JOBID_LINE_RE = re.compile(r"^\s{4}jobid: (\d+)")


def _parse_log_timestamp(match: re.Match[str]) -> float | None:
    _, month, day, hour, minute, second, year = match.groups()
    naive = dt.datetime.strptime(
        f"{month} {int(day)} {year} {hour}:{minute}:{second}", "%b %d %Y %H:%M:%S"
    )
    return _epoch(naive)


def _snakemake_log_times(text: str) -> dict[str, tuple[float | None, float | None]]:
    """Start and end epoch seconds per job ID, from the run log's timestamp lines.

    A timestamp line stands alone right before the event it belongs to: the
    rule header that starts a job, the ``Finished jobid`` line that ends it,
    or the ``Error in rule`` block that ends it with failure. The job ID
    itself is a few lines further down in the header and error blocks, so a
    small state machine remembers which event the pending timestamp belongs
    to until the ID line turns up.
    """
    starts: dict[str, float] = {}
    ends: dict[str, float] = {}
    pending_ts: float | None = None
    mode: str | None = None  # "start" or "end": which dict the next jobid line fills
    for line in text.splitlines():
        ts_match = _TS_RE.match(line.strip())
        if ts_match:
            pending_ts = _parse_log_timestamp(ts_match)
            mode = None
            continue
        if _RULE_HEADER_RE.match(line):
            mode = "start"
            continue
        if _ERROR_HEADER_RE.match(line):
            mode = "end"
            continue
        finished = _FINISHED_RE.match(line)
        if finished and pending_ts is not None:
            ends.setdefault(finished.group(1), pending_ts)
            mode = None
            continue
        jobid_line = _JOBID_LINE_RE.match(line)
        if jobid_line and pending_ts is not None and mode == "start":
            starts.setdefault(jobid_line.group(1), pending_ts)
            mode = None
        elif jobid_line and pending_ts is not None and mode == "end":
            ends[jobid_line.group(1)] = pending_ts
            mode = None
    return {jid: (start, ends.get(jid)) for jid, start in starts.items()}


def tasks_from_snakemake(benchmark_dir: Path, log_path: Path | None = None) -> list[Task]:
    """Build a timeline from Snakemake benchmark files plus the run log.

    Benchmark files (read the same way as ``records.read_sources``) give
    resource use; the log gives start and end times and catches jobs that
    failed before they could write a benchmark file (``apply_snakemake_log``
    already adds those as records with no times). Snakemake schedules its own
    jobs rather than queueing through a scheduler, so ``submit`` stays
    ``None``.
    """
    files = sorted(
        p for p in Path(benchmark_dir).rglob("*") if p.suffix in {".tsv", ".txt"} and p.is_file()
    )
    records: list[TaskRecord] = []
    for file in files:
        records.extend(parse_snakemake_benchmark(file.read_text(encoding="utf-8"), file))

    log_text = log_path.read_text(encoding="utf-8") if log_path is not None else ""
    # The log's job ID for each record, from the same matching apply_snakemake_log
    # uses: by benchmark path first. A standard benchmark's own ID is its file
    # stem, which is not a job ID and can equal another rule's job ID.
    log_ids: dict[int, str] = {}
    if log_path is not None:
        pairs = apply_snakemake_log(records, parse_snakemake_log(log_text))
        log_ids = {id(record): job.jobid for record, job in pairs}
    times = _snakemake_log_times(log_text)

    tasks: list[Task] = []
    for record in records:
        job_id = log_ids.get(id(record))
        if job_id is None and not record.job_id_from_file:
            job_id = record.job_id
        log_start, log_end = times.get(job_id, (None, None)) if job_id else (None, None)
        start = log_start
        if start is not None and record.elapsed_s is not None:
            end = start + record.elapsed_s
        else:
            end = log_end
        # A record never matched to a log job has only its file stem to show.
        label = f"{record.name} (job {job_id})" if job_id else f"{record.name} ({record.job_id})"
        cpu_percent = None
        if record.cpu_time_s is not None and record.elapsed_s:
            cpu_percent = 100.0 * record.cpu_time_s / record.elapsed_s
        tasks.append(
            Task(
                name=label,
                step=record.name,
                submit=None,
                start=start,
                end=end,
                attempt=1,
                cpus=record.alloc_cpus,
                status=record.state,
                cpu_percent=cpu_percent,
                max_rss_bytes=record.max_rss_bytes,
                mem_request_bytes=record.req_mem_bytes,
                read_bytes=None,
                write_bytes=None,
            )
        )
    return tasks


# Plain TSV, any runner


def tasks_from_tsv(text: str, usage: Mapping[str, StepUsage] | None = None) -> list[Task]:
    """Build a timeline from a plain ``name\tstart\tend`` TSV (epoch seconds), any runner.

    Each row is its own step (there is no separate process/rule column in
    this format). An optional ``usage`` mapping (step name to
    :class:`~pipeprof.tools.usage.StepUsage`, as a tool reader would
    fill) adds CPU, memory and I/O metrics for the bound and advice analyses.
    """
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    usage = usage or {}
    tasks: list[Task] = []
    for row in reader:
        name = row["name"]
        start_text, end_text = row.get("start"), row.get("end")
        start = float(start_text) if start_text not in (None, "", "-") else None
        end = float(end_text) if end_text not in (None, "", "-") else None
        u = usage.get(name)
        tasks.append(
            Task(
                name=name,
                step=name,
                submit=None,
                start=start,
                end=end,
                attempt=1,
                cpus=None,
                status="COMPLETED",
                cpu_percent=u.cpu_percent if u else None,
                max_rss_bytes=u.max_rss_bytes if u else None,
                mem_request_bytes=None,
                read_bytes=u.read_bytes if u else None,
                write_bytes=u.write_bytes if u else None,
            )
        )
    return tasks


def apply_usage(tasks: Sequence[Task], usage: Mapping[str, StepUsage]) -> int:
    """Fill a task's missing metrics from the tool reading for the same task name.

    A runner's own value wins over a tool's: the tool reading only fills
    fields the runner left empty (Nextflow on macOS records no CPU, memory or
    I/O columns, so there every metric comes from the tool). A tool that ran
    with no wall time of its own gives no CPU share, so ``cpu_percent`` is
    derived from user plus system time over the task's wall time only when
    the tool did not report it. Returns how many tasks matched a reading.
    """
    matched = 0
    for task in tasks:
        u = usage.get(task.name)
        if u is None:
            continue
        matched += 1
        if task.cpu_percent is None:
            if u.cpu_percent is not None:
                task.cpu_percent = u.cpu_percent
            elif u.cpu_s is not None and u.wall_s:
                task.cpu_percent = 100.0 * u.cpu_s / u.wall_s
        if task.max_rss_bytes is None:
            task.max_rss_bytes = u.max_rss_bytes
        if task.read_bytes is None:
            task.read_bytes = u.read_bytes
        if task.write_bytes is None:
            task.write_bytes = u.write_bytes
    return matched


def load_usage(paths: Sequence[Path]) -> dict[str, StepUsage]:
    """Read ``StepUsage`` JSON lines files into a mapping by step name.

    The tool readers (GNU time, psrecord, py-spy, seff) that write these
    lines are built on another branch against the already committed
    :class:`StepUsage`; this only loads what they write.
    """
    import json

    fields = StepUsage.__dataclass_fields__
    usage: dict[str, StepUsage] = {}
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            step_usage = StepUsage(**{k: v for k, v in data.items() if k in fields})
            if step_usage.step:
                usage[step_usage.step] = step_usage
    return usage
