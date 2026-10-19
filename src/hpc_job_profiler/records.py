"""Read finished-job accounting into one record type.

Sources:

* ``sacct --parsable2`` output with a header line (Slurm 20.11 and 21.08+
  ReqMem formats, job steps, array tasks).
* Nextflow trace files, in the default human readable form and with
  ``trace.raw = true``. Process names such as ``mutect2Wf:mutect2 (3)`` keep
  their workflow prefix; the tag in brackets is dropped for grouping.
* Snakemake benchmark TSV files (plain and ``--benchmark-extended``) and the
  Snakemake run log, which supplies requested resources and failures when the
  benchmark files lack them.

A value that the source did not record stays ``None``. The analysis never
fills a gap with a guess, and a recommendation is only made from records
that carry the value it needs.
"""

from __future__ import annotations

import ast
import csv
import datetime as dt
import io
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from hpc_job_profiler.slurm import normalise_state, parse_parsable
from hpc_job_profiler.units import (
    MIB,
    parse_hms,
    parse_memory,
    parse_nextflow_duration,
    parse_nextflow_memory,
    parse_percent,
    parse_reqmem,
    parse_slurm_duration,
)


@dataclass
class TaskRecord:
    """One finished task, job or array element, whatever the source."""

    source: str
    job_id: str
    name: str
    state: str
    exit_code: int | None = None
    signal: int | None = None
    elapsed_s: float | None = None
    cpu_time_s: float | None = None
    alloc_cpus: int | None = None
    req_mem_bytes: int | None = None
    max_rss_bytes: int | None = None
    time_limit_s: float | None = None
    queue_wait_s: float | None = None
    array_job_id: str | None = None
    array_task_id: str | None = None
    attempt: int | None = None
    node: str | None = None
    inferred_state: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """JSON ready form."""
        return asdict(self)


def _int_or_none(text: str | None) -> int | None:
    if text is None:
        return None
    value = text.strip()
    if not value or value in {"-", "NA"}:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _parse_time(text: str | None) -> dt.datetime | None:
    if not text:
        return None
    value = text.strip()
    if value in {"", "Unknown", "None", "-", "NA"}:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(value, fmt)
        except ValueError:
            continue
    if value.isdigit():
        # Nextflow raw trace: epoch milliseconds.
        return dt.datetime.fromtimestamp(int(value) / 1000.0, dt.UTC).replace(tzinfo=None)
    return None


def _seconds_between(start: dt.datetime | None, end: dt.datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return max(0.0, (end - start).total_seconds())


# sacct

_STATE_PRIORITY = ("OUT_OF_MEMORY", "TIMEOUT", "NODE_FAIL", "FAILED", "CANCELLED", "PREEMPTED")
_ARRAY_RE = re.compile(r"^(\d+)_(\d+|\[.*\])$")


def _split_step(job_id: str) -> tuple[str, str | None]:
    if "." in job_id:
        base, step = job_id.split(".", 1)
        return base, step
    return job_id, None


def parse_sacct(text: str) -> list[TaskRecord]:
    """Parse sacct ``--parsable2`` output (header line required) into one record per job.

    The allocation line gives state, exit code, times, request and CPUs. Steps
    (``.batch``, ``.extern``, ``.0``) supply MaxRSS, which Slurm only records
    per step, and an OUT_OF_MEMORY step state upgrades a job that sacct shows
    as FAILED or CANCELLED.
    """
    rows = parse_parsable(text)
    if not rows:
        return []
    if "JobID" not in rows[0]:
        raise ValueError("sacct output needs a header line with a JobID column")
    jobs: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        base, step = _split_step(row["JobID"])
        entry = jobs.setdefault(base, {"alloc": None, "steps": []})
        if base not in order:
            order.append(base)
        if step is None:
            entry["alloc"] = row
        else:
            entry["steps"].append((step, row))

    records: list[TaskRecord] = []
    for base in order:
        entry = jobs[base]
        alloc: dict[str, str] | None = entry["alloc"]
        steps: list[tuple[str, dict[str, str]]] = entry["steps"]
        if alloc is None:
            # Steps without their allocation line: keep the first as the job.
            alloc = steps[0][1]
        records.append(_sacct_record(base, alloc, steps))
    return records


def _sacct_record(
    base: str, alloc: dict[str, str], steps: list[tuple[str, dict[str, str]]]
) -> TaskRecord:
    state = normalise_state(alloc.get("State", ""))
    notes: list[str] = []
    step_states = {normalise_state(r.get("State", "")) for s, r in steps if s != "extern"}
    if state in {"FAILED", "CANCELLED"} and "OUT_OF_MEMORY" in step_states:
        notes.append(f"job state {state}, a step ran out of memory")
        state = "OUT_OF_MEMORY"

    exit_code = signal = None
    if ":" in alloc.get("ExitCode", ""):
        code, sig = alloc["ExitCode"].split(":", 1)
        exit_code, signal = _int_or_none(code), _int_or_none(sig)

    cpus = _int_or_none(alloc.get("AllocCPUS"))
    nodes = _int_or_none(alloc.get("NNodes")) or 1
    req_mem = parse_reqmem(alloc.get("ReqMem", ""), cpus or 1, nodes)

    rss_values = [parse_memory(r.get("MaxRSS", "")) for _, r in steps]
    rss_values.append(parse_memory(alloc.get("MaxRSS", "")))
    known_rss = [v for v in rss_values if v is not None]
    max_rss = max(known_rss) if known_rss else None
    if max_rss is None and steps:
        notes.append("no MaxRSS on any step (accounting gather may be off)")

    cpu_time = parse_slurm_duration(alloc.get("TotalCPU", "")) if alloc.get("TotalCPU") else None
    if not cpu_time:
        step_cpu = [parse_slurm_duration(r.get("TotalCPU", "")) for s, r in steps if s != "extern"]
        known_cpu = [v for v in step_cpu if v is not None]
        cpu_time = sum(known_cpu) if known_cpu else cpu_time

    submit = _parse_time(alloc.get("Submit"))
    start = _parse_time(alloc.get("Start"))
    array_job = array_task = None
    match = _ARRAY_RE.match(base)
    if match:
        array_job, array_task = match.group(1), match.group(2)

    return TaskRecord(
        source="sacct",
        job_id=base,
        name=alloc.get("JobName", "") or base,
        state=state,
        exit_code=exit_code,
        signal=signal,
        elapsed_s=parse_slurm_duration(alloc.get("Elapsed", "")) if "Elapsed" in alloc else None,
        cpu_time_s=cpu_time,
        alloc_cpus=cpus,
        req_mem_bytes=req_mem,
        max_rss_bytes=max_rss,
        time_limit_s=parse_slurm_duration(alloc.get("Timelimit", ""))
        if "Timelimit" in alloc
        else None,
        queue_wait_s=_seconds_between(submit, start),
        array_job_id=array_job,
        array_task_id=array_task,
        node=alloc.get("NodeList") or None,
        notes=notes,
    )


# Nextflow trace

_NF_TAG_RE = re.compile(r"^(.*?)\s*\((.*)\)\s*$")


def nextflow_process_name(name: str) -> str:
    """``mutect2Wf:mutect2 (3)`` becomes ``mutect2Wf:mutect2``."""
    match = _NF_TAG_RE.match(name)
    return match.group(1) if match else name.strip()


def parse_nextflow_trace(text: str) -> list[TaskRecord]:
    """Parse a Nextflow trace file (tab separated, header first)."""
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    records: list[TaskRecord] = []
    seen: dict[str, int] = {}
    for row in reader:
        name = row.get("process") or nextflow_process_name(row.get("name", ""))
        status = (row.get("status") or "").strip().upper()
        exit_code = _int_or_none(row.get("exit"))
        realtime = parse_nextflow_duration(row["realtime"]) if row.get("realtime") else None
        pcpu = parse_percent(row["%cpu"]) if row.get("%cpu") else None
        cpu_time = realtime * pcpu / 100.0 if realtime is not None and pcpu is not None else None
        submit = _parse_time(row.get("submit"))
        start = _parse_time(row.get("start"))
        task_key = row.get("name", name)
        seen[task_key] = seen.get(task_key, 0) + 1
        attempt = _int_or_none(row.get("attempt")) or seen[task_key]
        record = TaskRecord(
            source="nextflow",
            job_id=(row.get("native_id") or row.get("task_id") or "").strip(),
            name=name,
            state=_nextflow_state(status),
            exit_code=exit_code,
            elapsed_s=realtime,
            cpu_time_s=cpu_time,
            alloc_cpus=_int_or_none(row.get("cpus")),
            req_mem_bytes=parse_nextflow_memory(row["memory"]) if row.get("memory") else None,
            max_rss_bytes=parse_nextflow_memory(row["peak_rss"]) if row.get("peak_rss") else None,
            time_limit_s=parse_nextflow_duration(row["time"]) if row.get("time") else None,
            queue_wait_s=_seconds_between(submit, start),
            attempt=attempt,
        )
        _infer_nextflow_failure(record)
        records.append(record)
    return records


def _nextflow_state(status: str) -> str:
    return {"COMPLETED": "COMPLETED", "CACHED": "COMPLETED", "FAILED": "FAILED"}.get(
        status, status or "UNKNOWN"
    )


def _infer_nextflow_failure(record: TaskRecord) -> None:
    """Nextflow reports only FAILED plus an exit status; infer the resource cause carefully."""
    if record.state != "FAILED" or record.exit_code is None:
        return
    if record.exit_code > 128:
        record.signal = record.exit_code - 128
    near_mem = (
        record.req_mem_bytes is not None
        and record.max_rss_bytes is not None
        and record.max_rss_bytes >= 0.9 * record.req_mem_bytes
    )
    near_time = (
        record.time_limit_s is not None
        and record.elapsed_s is not None
        and record.elapsed_s >= 0.95 * record.time_limit_s
    )
    if record.exit_code == 137 and near_mem:
        record.state = "OUT_OF_MEMORY"
        record.inferred_state = True
        record.notes.append("exit 137 (SIGKILL) with peak RSS at 90% or more of the request")
    elif record.exit_code in {137, 140, 143} and near_time:
        record.state = "TIMEOUT"
        record.inferred_state = True
        record.notes.append(f"exit {record.exit_code} at 95% or more of the time limit")
    elif record.exit_code == 137:
        record.notes.append(
            "exit 137 (SIGKILL); often memory, but peak RSS was not near the limit"
        )


# Snakemake benchmarks and log


def parse_snakemake_benchmark(text: str, path: str | Path = "") -> list[TaskRecord]:
    """Parse one Snakemake benchmark TSV (one row per repeat).

    Snakemake writes ``NA`` when it could not sample the process (it does
    this on macOS, where its psutil calls fail); those fields stay ``None``.
    ``max_rss`` is in MiB (psutil bytes divided by 1024 * 1024).
    """
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    p = Path(path)
    rule_from_path = p.parent.name if p.parent.name not in {"", ".", "benchmarks"} else p.stem
    records: list[TaskRecord] = []
    for row in reader:
        rss_mb = _float_or_none(row.get("max_rss"))
        resources = _literal_dict(row.get("resources"))
        threads = _int_or_none(row.get("threads"))
        req_mem = None
        if "mem_mb" in resources:
            req_mem = int(float(resources["mem_mb"]) * 10**6)
        time_limit = _snakemake_time_limit(resources)
        cpu_time = _float_or_none(row.get("cpu_time"))
        notes = []
        if rss_mb is None:
            notes.append("benchmark has no memory sample (NA)")
        records.append(
            TaskRecord(
                source="snakemake",
                job_id=str(row.get("jobid") or p.stem),
                name=str(row.get("rule_name") or rule_from_path),
                state="COMPLETED",
                elapsed_s=_float_or_none(row.get("s")) or parse_hms(row.get("h:m:s") or ""),
                cpu_time_s=cpu_time,
                alloc_cpus=threads,
                req_mem_bytes=req_mem,
                max_rss_bytes=int(rss_mb * MIB) if rss_mb is not None else None,
                time_limit_s=time_limit,
                notes=notes,
            )
        )
    return records


def _float_or_none(text: str | None) -> float | None:
    if text is None:
        return None
    value = text.strip()
    if value in {"", "NA", "-"}:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _literal_dict(text: str | None) -> dict[str, Any]:
    if not text or text.strip() in {"", "NA", "-"}:
        return {}
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return {}
    return value if isinstance(value, dict) else {}


def _snakemake_time_limit(resources: dict[str, Any]) -> float | None:
    """``runtime`` is minutes in Snakemake 8+; MAVIS (bcgsc/mavis) uses ``time_limit`` seconds."""
    if "runtime" in resources:
        try:
            return float(resources["runtime"]) * 60.0
        except (TypeError, ValueError):
            return None
    if "time_limit" in resources:
        try:
            return float(resources["time_limit"])
        except (TypeError, ValueError):
            return None
    return None


_LOG_RULE_RE = re.compile(r"^(?:local)?(?:rule|checkpoint) (\S+):\s*$")
_LOG_KV_RE = re.compile(r"^\s{4}(\w+): (.*)$")
_LOG_FINISHED_RE = re.compile(r"^Finished job(?:id)?:? (\d+)")
_LOG_ERROR_RE = re.compile(r"^Error in rule (\S+):\s*$")


@dataclass
class SnakemakeJob:
    """One job block from a Snakemake run log."""

    jobid: str
    rule: str
    wildcards: str = ""
    threads: int | None = None
    resources: dict[str, str] = field(default_factory=dict)
    benchmark: str | None = None
    status: str = "UNKNOWN"


def parse_snakemake_log(text: str) -> dict[str, SnakemakeJob]:
    """Read job blocks, ``Finished jobid`` lines and ``Error in rule`` blocks from a run log."""
    jobs: dict[str, SnakemakeJob] = {}
    current: dict[str, str] = {}
    current_rule: str | None = None
    in_error = False

    def flush() -> None:
        nonlocal current, current_rule
        if current_rule and "jobid" in current:
            jid = current["jobid"].strip()
            job = jobs.get(jid) or SnakemakeJob(jobid=jid, rule=current_rule)
            job.wildcards = current.get("wildcards", job.wildcards)
            if "threads" in current:
                job.threads = _int_or_none(current["threads"])
            if "resources" in current:
                job.resources = _parse_resource_line(current["resources"])
            job.benchmark = current.get("benchmark", job.benchmark)
            if in_error:
                job.status = "FAILED"
            jobs[jid] = job
        current = {}
        current_rule = None

    for line in text.splitlines():
        rule_match = _LOG_RULE_RE.match(line)
        error_match = _LOG_ERROR_RE.match(line)
        if rule_match or error_match:
            flush()
            in_error = error_match is not None
            current_rule = (rule_match or error_match).group(1)  # type: ignore[union-attr]
            continue
        kv = _LOG_KV_RE.match(line)
        if kv and current_rule:
            current[kv.group(1)] = kv.group(2)
            continue
        finished = _LOG_FINISHED_RE.match(line)
        if finished:
            flush()
            in_error = False
            jid = finished.group(1)
            if jid in jobs and jobs[jid].status != "FAILED":
                jobs[jid].status = "COMPLETED"
            continue
        if current_rule and line and not line.startswith(" "):
            flush()
            in_error = False
    flush()
    return jobs


def _parse_resource_line(text: str) -> dict[str, str]:
    resources: dict[str, str] = {}
    for part in text.split(","):
        if "=" in part:
            key, value = part.split("=", 1)
            resources[key.strip()] = value.strip()
    return resources


def apply_snakemake_log(records: list[TaskRecord], jobs: dict[str, SnakemakeJob]) -> None:
    """Fill requests from the log into benchmark records and add records for failed jobs.

    Benchmarks are matched by their ``benchmark:`` path when the log names
    one, else by job ID (extended benchmark format).
    """
    by_id = {r.job_id: r for r in records}
    for job in jobs.values():
        record = by_id.get(job.jobid)
        if record is None and job.benchmark:
            stem = Path(job.benchmark).stem
            record = next((r for r in records if r.job_id == stem and r.name == job.rule), None)
        if record is None:
            if job.status == "FAILED":
                records.append(
                    TaskRecord(
                        source="snakemake",
                        job_id=job.jobid,
                        name=job.rule,
                        state="FAILED",
                        notes=["failed in the run log; no benchmark row"],
                    )
                )
            continue
        if job.status == "FAILED":
            record.state = "FAILED"
        if record.alloc_cpus is None and job.threads is not None:
            record.alloc_cpus = job.threads
        if record.req_mem_bytes is None and "mem_mb" in job.resources:
            mem = _float_or_none(job.resources["mem_mb"])
            record.req_mem_bytes = int(mem * 10**6) if mem is not None else None
        if record.time_limit_s is None:
            record.time_limit_s = _snakemake_time_limit(dict(job.resources))


def read_sources(
    sacct: Iterable[Path] = (),
    nextflow: Iterable[Path] = (),
    snakemake_benchmarks: Iterable[Path] = (),
    snakemake_logs: Iterable[Path] = (),
) -> list[TaskRecord]:
    """Read every given file. Benchmark folders are searched for ``*.tsv`` and ``*.txt``."""
    records: list[TaskRecord] = []
    for path in sacct:
        records.extend(parse_sacct(path.read_text(encoding="utf-8")))
    for path in nextflow:
        records.extend(parse_nextflow_trace(path.read_text(encoding="utf-8")))
    bench_records: list[TaskRecord] = []
    for path in snakemake_benchmarks:
        files = (
            sorted(p for p in path.rglob("*") if p.suffix in {".tsv", ".txt"} and p.is_file())
            if path.is_dir()
            else [path]
        )
        for file in files:
            bench_records.extend(parse_snakemake_benchmark(file.read_text(encoding="utf-8"), file))
    for log in snakemake_logs:
        apply_snakemake_log(bench_records, parse_snakemake_log(log.read_text(encoding="utf-8")))
    records.extend(bench_records)
    return records
