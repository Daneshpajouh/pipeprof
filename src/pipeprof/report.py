"""After the run: efficiency, waste, failures and recommendations, as Markdown, HTML and JSON.

Definitions (also in docs/user_guide.md):

* CPU efficiency = CPU time / (elapsed time x allocated CPUs).
* Memory efficiency = peak RSS / requested memory. For sacct this uses the
  largest step MaxRSS, which is per task; it is exact for the single task
  steps typical of bioinformatics tools and an underestimate for multi-task
  MPI steps.
* Time use = elapsed / time limit.

A recommendation for a resource is only made from records that carry that
resource's measurement. Groups where every record lacks peak memory get no
memory recommendation and say so.
"""

from __future__ import annotations

import html
import json
import math
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from pipeprof.records import TaskRecord
from pipeprof.units import (
    GIB,
    format_memory,
    format_slurm_duration,
    round_up_memory,
    round_up_minutes,
)

FAILURE_ADVICE = {
    "OUT_OF_MEMORY": "Out of memory: raise memory to at least 1.5x the request, or let the "
    "workflow retry with memory scaled by attempt.",
    "TIMEOUT": "Hit the time limit: raise the limit (2x the request) or split the input; "
    "check whether the job stalled first.",
    "NODE_FAIL": "Node failure: not caused by the job. Resubmit; use --requeue or "
    "workflow retries.",
    "FAILED": "Non-zero exit without a resource signal: read the job's stderr; more resources "
    "will not help.",
    "CANCELLED": "Cancelled by a user or administrator: confirm it was intended.",
    "PREEMPTED": "Pre-empted by a higher priority job: resubmit or use a non-preemptible QOS.",
    "BOOT_FAIL": "Node failed to boot: resubmit; report to the cluster administrators if "
    "it repeats.",
}


@dataclass
class Thresholds:
    """Limits below which a job is flagged as wasteful."""

    cpu_efficiency: float = 0.5
    mem_efficiency: float = 0.5
    time_use: float = 0.25
    mem_headroom: float = 1.2
    time_headroom: float = 1.5


@dataclass
class TaskMetrics:
    """Derived metrics and flags for one record."""

    record: TaskRecord
    cpu_efficiency: float | None
    mem_efficiency: float | None
    time_use: float | None
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "record": self.record.to_dict(),
            "cpu_efficiency": self.cpu_efficiency,
            "mem_efficiency": self.mem_efficiency,
            "time_use": self.time_use,
            "flags": self.flags,
        }


@dataclass
class Recommendation:
    """Suggested request for one job name, process or rule."""

    name: str
    tasks: int
    cpus: int | None
    mem_bytes: int | None
    time_s: int | None
    basis: list[str]
    current_cpus: int | None = None
    current_mem_bytes: int | None = None
    current_time_s: float | None = None


@dataclass
class ArraySummary:
    """seff style summary of one job array."""

    array_job_id: str
    name: str
    tasks: int
    states: dict[str, int]
    cpu_efficiency: tuple[float, float, float] | None
    mem_efficiency: tuple[float, float, float] | None
    elapsed_s: tuple[float, float, float] | None
    max_rss_bytes: int | None


@dataclass
class Totals:
    """Allocated against used resources over all records that carry both."""

    core_hours_allocated: float = 0.0
    core_hours_used: float = 0.0
    gib_hours_requested: float = 0.0
    gib_hours_used: float = 0.0
    queue_wait_hours: float = 0.0
    tasks: int = 0
    failed: int = 0


@dataclass
class Report:
    """Everything ``pipeprof report`` writes."""

    tasks: list[TaskMetrics]
    recommendations: list[Recommendation]
    arrays: list[ArraySummary]
    totals: Totals
    thresholds: Thresholds
    sources: list[str]
    run_id: str = ""
    #: Nextflow tasks reused on -resume, left out of every finding and only counted.
    cached_tasks: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "sources": self.sources,
            "cached_tasks": self.cached_tasks,
            "thresholds": asdict(self.thresholds),
            "totals": asdict(self.totals),
            "recommendations": [asdict(r) for r in self.recommendations],
            "arrays": [asdict(a) for a in self.arrays],
            "tasks": [t.to_dict() for t in self.tasks],
        }


def _ratio(num: float | None, den: float | None) -> float | None:
    if num is None or den is None or den <= 0:
        return None
    return num / den


def task_metrics(record: TaskRecord, thresholds: Thresholds) -> TaskMetrics:
    cpu_eff = None
    if record.cpu_time_s is not None and record.elapsed_s and record.alloc_cpus:
        cpu_eff = record.cpu_time_s / (record.elapsed_s * record.alloc_cpus)
    mem_eff = _ratio(
        float(record.max_rss_bytes) if record.max_rss_bytes is not None else None,
        float(record.req_mem_bytes) if record.req_mem_bytes is not None else None,
    )
    time_use = _ratio(record.elapsed_s, record.time_limit_s)
    flags: list[str] = []
    if record.state != "COMPLETED":
        flags.append(record.state + (" (inferred)" if record.inferred_state else ""))
    finished_ok = record.state == "COMPLETED"
    # Short jobs give noisy CPU numbers, so CPU waste is only flagged past a minute.
    if (
        finished_ok
        and cpu_eff is not None
        and cpu_eff < thresholds.cpu_efficiency
        and (record.elapsed_s or 0) >= 60
    ):
        flags.append(f"CPU underuse ({cpu_eff:.0%} of {record.alloc_cpus} CPUs)")
    if finished_ok and mem_eff is not None and mem_eff < thresholds.mem_efficiency:
        flags.append(f"memory over-request ({mem_eff:.0%} used)")
    if finished_ok and time_use is not None and time_use < thresholds.time_use:
        flags.append(f"time limit far above use ({time_use:.0%} used)")
    return TaskMetrics(record, cpu_eff, mem_eff, time_use, flags)


def _spread(values: Sequence[float]) -> tuple[float, float, float] | None:
    if not values:
        return None
    return (min(values), statistics.median(values), max(values))


def _tasks(n: int) -> str:
    return "1 completed task (a single run; confirm on more)" if n == 1 else f"{n} completed tasks"


def recommend(name: str, group: Sequence[TaskMetrics], thresholds: Thresholds) -> Recommendation:
    """Recommend a request for one group from its completed and failed records."""
    records = [m.record for m in group]
    basis: list[str] = []
    done = [r for r in records if r.state == "COMPLETED"]
    oom = [r for r in records if r.state == "OUT_OF_MEMORY"]
    timeouts = [r for r in records if r.state == "TIMEOUT"]

    rss = [r.max_rss_bytes for r in done if r.max_rss_bytes is not None]
    mem: int | None = None
    if rss:
        mem = round_up_memory(max(rss) * thresholds.mem_headroom)
        basis.append(
            f"memory: max peak RSS {format_memory(max(rss))} over {_tasks(len(rss))} "
            f"x {thresholds.mem_headroom}"
        )
    elif done:
        basis.append("memory: no peak RSS recorded for completed tasks; no recommendation")
    if oom:
        reqs = [r.req_mem_bytes for r in oom if r.req_mem_bytes is not None]
        if reqs:
            floor = round_up_memory(max(reqs) * 1.5)
            mem = max(mem or 0, floor)
            basis.append(f"memory: {len(oom)} out of memory at {format_memory(max(reqs))}; x1.5")

    elapsed = [r.elapsed_s for r in done if r.elapsed_s is not None]
    time_s: int | None = None
    if elapsed:
        time_s = round_up_minutes(max(elapsed) * thresholds.time_headroom) * 60
        basis.append(
            f"time: max elapsed {format_slurm_duration(max(elapsed))} over "
            f"{_tasks(len(elapsed))} x {thresholds.time_headroom}"
        )
    if timeouts:
        limits = [r.time_limit_s for r in timeouts if r.time_limit_s is not None]
        if not limits:
            basis.append(f"time: {len(timeouts)} timed out, limit not recorded; no recommendation")
        if limits:
            floor_s = round_up_minutes(max(limits) * 2) * 60
            time_s = max(time_s or 0, floor_s)
            basis.append(
                f"time: {len(timeouts)} timed out at {format_slurm_duration(max(limits))}; x2"
            )

    cpu_used = [
        r.cpu_time_s / r.elapsed_s
        for r in done
        if r.cpu_time_s is not None and r.elapsed_s and r.elapsed_s > 0
    ]
    cpus: int | None = None
    if cpu_used:
        busiest = max(cpu_used)
        cpus = max(1, math.ceil(busiest - 0.15))
        basis.append(f"cpus: busiest task kept {busiest:.2f} cores busy on average")

    if not basis:
        basis.append("no completed tasks with measurements; no recommendation")

    def first(values: Iterable[float | int | None]) -> Any:
        known = [v for v in values if v is not None]
        return max(known) if known else None

    return Recommendation(
        name=name,
        tasks=len(records),
        cpus=cpus,
        mem_bytes=mem,
        time_s=time_s,
        basis=basis,
        current_cpus=first(r.alloc_cpus for r in records),
        current_mem_bytes=first(r.req_mem_bytes for r in records),
        current_time_s=first(r.time_limit_s for r in records),
    )


def summarise_arrays(metrics: Sequence[TaskMetrics]) -> list[ArraySummary]:
    groups: dict[str, list[TaskMetrics]] = {}
    for m in metrics:
        if m.record.array_job_id:
            groups.setdefault(m.record.array_job_id, []).append(m)
    summaries: list[ArraySummary] = []
    for array_id, group in sorted(groups.items()):
        states: dict[str, int] = {}
        for m in group:
            states[m.record.state] = states.get(m.record.state, 0) + 1
        rss = [m.record.max_rss_bytes for m in group if m.record.max_rss_bytes is not None]
        summaries.append(
            ArraySummary(
                array_job_id=array_id,
                name=group[0].record.name,
                tasks=len(group),
                states=states,
                cpu_efficiency=_spread(
                    [m.cpu_efficiency for m in group if m.cpu_efficiency is not None]
                ),
                mem_efficiency=_spread(
                    [m.mem_efficiency for m in group if m.mem_efficiency is not None]
                ),
                elapsed_s=_spread(
                    [m.record.elapsed_s for m in group if m.record.elapsed_s is not None]
                ),
                max_rss_bytes=max(rss) if rss else None,
            )
        )
    return summaries


def build_report(
    records: Sequence[TaskRecord],
    thresholds: Thresholds | None = None,
    sources: Sequence[str] = (),
    run_id: str = "",
) -> Report:
    limits = thresholds or Thresholds()
    # Cached tasks carry the times and usage of the earlier run that computed
    # them; efficiency and requests describe this run, as analyze does.
    cached = sum(1 for r in records if r.state == "CACHED")
    metrics = [
        task_metrics(r, limits)
        for r in records
        if r.state not in {"RUNNING", "PENDING", "CACHED"}
    ]
    groups: dict[str, list[TaskMetrics]] = {}
    for m in metrics:
        groups.setdefault(m.record.name, []).append(m)
    recommendations = [recommend(name, group, limits) for name, group in sorted(groups.items())]

    totals = Totals()
    for m in metrics:
        r = m.record
        totals.tasks += 1
        if r.state != "COMPLETED":
            totals.failed += 1
        if r.elapsed_s is not None and r.alloc_cpus and r.cpu_time_s is not None:
            totals.core_hours_allocated += r.elapsed_s * r.alloc_cpus / 3600
            totals.core_hours_used += r.cpu_time_s / 3600
        if r.elapsed_s is not None and r.req_mem_bytes and r.max_rss_bytes is not None:
            totals.gib_hours_requested += r.req_mem_bytes / GIB * r.elapsed_s / 3600
            totals.gib_hours_used += r.max_rss_bytes / GIB * r.elapsed_s / 3600
        if r.queue_wait_s is not None:
            totals.queue_wait_hours += r.queue_wait_s / 3600
    return Report(
        metrics,
        recommendations,
        summarise_arrays(metrics),
        totals,
        limits,
        list(sources),
        run_id,
        cached,
    )


# Rendering


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _mem(value: float | None) -> str:
    return "n/a" if value is None else format_memory(value)


def _dur(value: float | None) -> str:
    return "n/a" if value is None else format_slurm_duration(value)


def _rows(report: Report) -> tuple[list[str], list[list[str]], list[str], list[list[str]]]:
    rec_head = [
        "Name",
        "Tasks",
        "CPUs now",
        "CPUs rec.",
        "Memory now",
        "Memory rec.",
        "Time now",
        "Time rec.",
    ]
    rec_rows = [
        [
            r.name,
            str(r.tasks),
            "n/a" if r.current_cpus is None else str(r.current_cpus),
            "n/a" if r.cpus is None else str(r.cpus),
            _mem(r.current_mem_bytes),
            _mem(r.mem_bytes),
            _dur(r.current_time_s),
            _dur(r.time_s),
        ]
        for r in report.recommendations
    ]
    task_head = [
        "Job",
        "Name",
        "State",
        "Elapsed",
        "CPU eff.",
        "Peak RSS",
        "Mem eff.",
        "Time use",
        "Flags",
    ]
    task_rows = [
        [
            m.record.job_id,
            m.record.name,
            m.record.state,
            _dur(m.record.elapsed_s),
            _pct(m.cpu_efficiency),
            _mem(m.record.max_rss_bytes),
            _pct(m.mem_efficiency),
            _pct(m.time_use),
            "; ".join(m.flags) or "",
        ]
        for m in report.tasks
    ]
    return rec_head, rec_rows, task_head, task_rows


def _md_table(head: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(cell.replace("|", "/") for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def _failure_lines(report: Report) -> list[str]:
    counts: dict[str, int] = {}
    for m in report.tasks:
        if m.record.state != "COMPLETED":
            counts[m.record.state] = counts.get(m.record.state, 0) + 1
    return [
        f"{state}: {count} task(s). {FAILURE_ADVICE.get(state, 'Check the job logs.')}"
        for state, count in sorted(counts.items())
    ]


def _cached_lines(report: Report) -> list[str]:
    if not report.cached_tasks:
        return []
    return [
        f"{report.cached_tasks} cached task(s) left out: their times are from the "
        "earlier run that computed them."
    ]


def to_markdown(report: Report) -> str:
    t = report.totals
    rec_head, rec_rows, task_head, task_rows = _rows(report)
    out = [
        "# Job efficiency report",
        "",
        f"Sources: {', '.join(report.sources) or 'n/a'}",
        f"Run ID: {report.run_id or 'n/a'}",
        "",
        f"Tasks: {t.tasks}, not completed: {t.failed}.",
        *_cached_lines(report),
        f"Core hours allocated {t.core_hours_allocated:.3f}, used {t.core_hours_used:.3f}.",
        f"Memory GiB hours requested {t.gib_hours_requested:.3f}, used {t.gib_hours_used:.3f}.",
        f"Queue wait {t.queue_wait_hours:.3f} hours in total.",
        "",
        "## Recommendations",
        "",
        _md_table(rec_head, rec_rows),
        "",
    ]
    for r in report.recommendations:
        out.append(f"- {r.name}: " + "; ".join(r.basis))
    failures = _failure_lines(report)
    if failures:
        out += ["", "## Failures", ""] + [f"- {line}" for line in failures]
    if report.arrays:
        out += ["", "## Job arrays", ""]
        for a in report.arrays:
            states = ", ".join(f"{k} {v}" for k, v in sorted(a.states.items()))

            def spread(s: tuple[float, float, float] | None, fmt: str) -> str:
                return "n/a" if s is None else " / ".join(format(v, fmt) for v in s)

            out.append(
                f"- {a.array_job_id} ({a.name}): {a.tasks} tasks ({states}); CPU efficiency "
                f"min/median/max {spread(a.cpu_efficiency, '.0%')}; memory efficiency "
                f"{spread(a.mem_efficiency, '.0%')}; max RSS {_mem(a.max_rss_bytes)}"
            )
    out += ["", "## Tasks", "", _md_table(task_head, task_rows), ""]
    return "\n".join(out)


def to_html(report: Report) -> str:
    """Render the report as a self-contained HTML page."""
    rec_head, rec_rows, task_head, task_rows = _rows(report)

    def table(head: list[str], rows: list[list[str]]) -> str:
        th = "".join(f"<th>{html.escape(h)}</th>" for h in head)
        body = "".join(
            "<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in row) + "</tr>" for row in rows
        )
        return f"<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>"

    t = report.totals
    failures = "".join(f"<li>{html.escape(line)}</li>" for line in _failure_lines(report))
    basis = "".join(
        f"<li><b>{html.escape(r.name)}</b>: {html.escape('; '.join(r.basis))}</li>"
        for r in report.recommendations
    )
    style = (
        "body{font-family:system-ui,sans-serif;margin:24px;color:#1f2328;background:#fff}"
        "table{border-collapse:collapse;margin:12px 0;font-size:14px}"
        "th,td{border:1px solid #d0d7de;padding:4px 8px;text-align:left}"
        "th{background:#f6f8fa}"
        "@media (prefers-color-scheme: dark){body{background:#0d1117;color:#e6edf3}"
        "th{background:#161b22}th,td{border-color:#30363d}}"
    )
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>Job efficiency report</title><style>{style}</style></head><body>"
        "<h1>Job efficiency report</h1>"
        f"<p>Sources: {html.escape(', '.join(report.sources) or 'n/a')}</p>"
        f"<p>Tasks {t.tasks}, not completed {t.failed}. "
        + "".join(html.escape(line) + " " for line in _cached_lines(report))
        + "Core hours allocated "
        f"{t.core_hours_allocated:.3f}, used {t.core_hours_used:.3f}. Memory GiB hours "
        f"requested {t.gib_hours_requested:.3f}, used {t.gib_hours_used:.3f}.</p>"
        f"<h2>Recommendations</h2>{table(rec_head, rec_rows)}<ul>{basis}</ul>"
        + (f"<h2>Failures</h2><ul>{failures}</ul>" if failures else "")
        + f"<h2>Tasks</h2>{table(task_head, task_rows)}</body></html>\n"
    )


def to_json(report: Report) -> str:
    return json.dumps(report.to_dict(), indent=2)
