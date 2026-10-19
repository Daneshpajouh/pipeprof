"""Command line: ``pipeprof calibrate``, ``watch``, ``tail``, ``report``, ``analyze``,
``provenance``.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from pipeprof import __version__
from pipeprof.advise import Advice, advise
from pipeprof.bottleneck import BottleneckReport, analyse
from pipeprof.bound import BoundLabel, label_steps
from pipeprof.calibrate import (
    DEFAULT_FRACTIONS,
    CalibrationError,
    calibrate,
    write_outputs,
)
from pipeprof.logkit import RunLogger, new_run_id, utc_now_iso
from pipeprof.provenance import capture
from pipeprof.records import read_sources
from pipeprof.report import Thresholds, build_report, to_html, to_json, to_markdown
from pipeprof.slurm import SlurmError, run_text, sacct_command
from pipeprof.subsample import InputKind, SubsampleError
from pipeprof.tail import tail as tail_view
from pipeprof.timeline import (
    Task,
    apply_usage,
    load_usage,
    tasks_from_nextflow_trace,
    tasks_from_snakemake,
    tasks_from_tsv,
)
from pipeprof.units import format_memory, format_slurm_duration, parse_memory
from pipeprof.watch import AlertConfig, JsonlSink, watch_pid, watch_slurm

app = typer.Typer(
    help="Profile bioinformatics jobs before, during and after Slurm submission.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console(stderr=True)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"pipeprof {__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Print version.")
    ] = False,
) -> None:
    """Profile bioinformatics jobs before, during and after Slurm submission."""


def _fractions(text: str) -> list[float]:
    try:
        return [float(x) for x in text.split(",") if x.strip()]
    except ValueError as exc:
        raise typer.BadParameter(f"not a comma separated list of numbers: {text}") from exc


@app.command("calibrate")
def calibrate_cmd(
    input_path: Annotated[Path, typer.Argument(help="Full input file (VCF, BAM, FASTQ, text).")],
    command: Annotated[list[str], typer.Argument(help="Command after --, with {input}.")],
    outdir: Annotated[Path, typer.Option("--outdir", "-o", help="Where results go.")] = Path(
        "pipeprof_calibration"
    ),
    workdir: Annotated[
        Path | None, typer.Option(help="Scratch folder for subsamples (default OUTDIR/work).")
    ] = None,
    fractions: Annotated[
        str, typer.Option(help="Comma separated subsample fractions.")
    ] = ",".join(str(f) for f in DEFAULT_FRACTIONS),
    repeats: Annotated[int, typer.Option(help="Runs per subsample.")] = 1,
    threads: Annotated[int, typer.Option(help="Value for {threads}.")] = 1,
    shell: Annotated[bool, typer.Option(help="Run the command through /bin/sh.")] = False,
    kind: Annotated[InputKind | None, typer.Option(help="Input format override.")] = None,
    x: Annotated[str, typer.Option(help="Size measure: bytes or records.")] = "bytes",
    interval: Annotated[float, typer.Option(help="Memory sampling interval, seconds.")] = 0.05,
    mem_headroom: Annotated[float, typer.Option(help="Memory factor on the upper bound.")] = 1.2,
    time_headroom: Annotated[float, typer.Option(help="Time factor on the upper bound.")] = 1.5,
    job_name: Annotated[str, typer.Option(help="Job, rule or process name.")] = "job",
    partition: Annotated[str | None, typer.Option(help="Slurm partition.")] = None,
    account: Annotated[str | None, typer.Option(help="Slurm account.")] = None,
    output_suffix: Annotated[
        str | None, typer.Option(help="Suffix for {output}; default copies the input's.")
    ] = None,
    validate_full: Annotated[
        bool, typer.Option(help="Also run on the full input and compare with the prediction.")
    ] = False,
    seed: Annotated[int, typer.Option(help="Subsampling seed.")] = 13,
    warmup: Annotated[
        bool, typer.Option(help="Run the smallest subsample once, unmeasured, first.")
    ] = True,
) -> None:
    """Run COMMAND on graduated subsamples of INPUT_PATH and predict the full run."""
    outdir.mkdir(parents=True, exist_ok=True)
    log = RunLogger(
        "pipeprof.calibrate", jsonl_path=outdir / "pipeprof.log.jsonl", console=sys.stderr
    )
    try:
        result = calibrate(
            input_path,
            command,
            workdir=workdir or outdir / "work",
            fractions=_fractions(fractions),
            repeats=repeats,
            threads=threads,
            shell=shell,
            kind=kind,
            x_variable=x,
            interval=interval,
            seed=seed,
            mem_headroom=mem_headroom,
            time_headroom=time_headroom,
            job_name=job_name,
            partition=partition,
            account=account,
            output_suffix=output_suffix,
            validate_full=validate_full,
            warmup=warmup,
            logger=log,
        )
    except (CalibrationError, SubsampleError, OSError) as exc:
        log.error(str(exc))
        log.close()
        console.print(f"[red]calibrate failed:[/red] {exc}")
        raise typer.Exit(2) from exc
    paths = write_outputs(result, outdir)
    log.event("written", "outputs written", files={k: str(v) for k, v in paths.items()})
    log.close()

    table = Table(title=f"Prediction for {Path(result.input_path).name}")
    for column in ("metric", "model", "predicted", "95% interval"):
        table.add_column(column)
    for metric, pred in result.predictions.items():
        fmt = format_memory if metric == "peak_rss_bytes" else (lambda v: f"{v:.2f} s")
        table.add_row(
            metric,
            str(pred.selection.chosen.kind),
            fmt(pred.point),
            f"{fmt(pred.lower)} to {fmt(pred.upper)}",
        )
    out = Console()
    out.print(table)
    plan = result.plan
    out.print(
        f"Request: --cpus-per-task={plan.cpus} --mem={plan.slurm_mem} "
        f"--time={format_slurm_duration(plan.time_s)}"
    )
    if result.validation:
        for metric, v in result.validation["metrics"].items():
            out.print(
                f"full run {metric}: predicted {v['predicted']:.4g}, actual {v['actual']:.4g}, "
                f"inside interval: {v['inside_interval']}"
            )
    out.print(f"Wrote {paths['json']} and snippets in {outdir}")


@app.command("watch")
def watch_cmd(
    out: Annotated[Path, typer.Option("--out", "-o", help="JSON lines output file.")],
    pid: Annotated[int | None, typer.Option(help="Local process ID to watch.")] = None,
    slurm_job: Annotated[
        str | None, typer.Option(help="Slurm job ID to watch with sstat.")
    ] = None,
    command: Annotated[
        list[str] | None, typer.Argument(help="Command to start and watch (after --).")
    ] = None,
    interval: Annotated[float, typer.Option(help="Seconds between samples.")] = 5.0,
    mem_limit: Annotated[str | None, typer.Option(help="Memory limit, e.g. 8G.")] = None,
    mem_warn: Annotated[float, typer.Option(help="Alert at this share of the limit.")] = 0.9,
    cpus: Annotated[
        int | None, typer.Option(help="Allocated CPUs for the underuse alert.")
    ] = None,
    cpu_underuse: Annotated[float, typer.Option(help="Alert below this share of CPUs.")] = 0.5,
    window: Annotated[float, typer.Option(help="CPU underuse window, seconds.")] = 60.0,
    stall: Annotated[float, typer.Option(help="Stall alert after this many seconds.")] = 120.0,
    max_seconds: Annotated[float | None, typer.Option(help="Stop watching after this.")] = None,
) -> None:
    """Sample a running process tree or Slurm job into JSON lines, with alerts."""
    chosen = sum(x is not None and x != [] for x in (pid, slurm_job, command))
    if chosen != 1:
        console.print("[red]give exactly one of --pid, --slurm-job or a command after --[/red]")
        raise typer.Exit(2)
    config = AlertConfig(
        mem_limit_bytes=parse_memory(mem_limit, "M") if mem_limit else None,
        mem_warn=mem_warn,
        cpus=cpus,
        cpu_underuse=cpu_underuse,
        window_s=window,
        stall_s=stall,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as handle:
        sink = JsonlSink(handle, new_run_id())
        if slurm_job is not None:
            try:
                n = watch_slurm(slurm_job, sink, config, interval, max_seconds)
            except SlurmError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(2) from exc
        elif pid is not None:
            n = watch_pid(pid, sink, config, interval, max_seconds)
        else:
            assert command is not None
            proc = subprocess.Popen(command)
            n = watch_pid(proc.pid, sink, config, interval, max_seconds)
            code = proc.wait()
            sink.write({"type": "end", "ts": utc_now_iso(), "returncode": code})
    console.print(f"{n} samples written to {out}")


@app.command("tail")
def tail_cmd(
    log: Annotated[Path, typer.Argument(help="JSON lines file written by pipeprof watch.")],
    once: Annotated[bool, typer.Option(help="Print once and exit.")] = False,
) -> None:
    """Show a live view of a watch log."""
    tail_view(log, follow=not once, console=Console())


@app.command("report")
def report_cmd(
    sacct: Annotated[list[Path], typer.Option(help="sacct --parsable2 output file.")] = [],  # noqa: B006
    nextflow_trace: Annotated[list[Path], typer.Option(help="Nextflow trace file.")] = [],  # noqa: B006
    snakemake_benchmarks: Annotated[
        list[Path], typer.Option(help="Snakemake benchmark file or folder.")
    ] = [],  # noqa: B006
    snakemake_log: Annotated[list[Path], typer.Option(help="Snakemake run log.")] = [],  # noqa: B006
    watch_log: Annotated[
        list[Path], typer.Option(help="JSON lines log from pipeprof watch.")
    ] = [],  # noqa: B006
    from_slurm: Annotated[
        list[str], typer.Option(help="Job ID to read directly with sacct.")
    ] = [],  # noqa: B006
    outdir: Annotated[Path, typer.Option("--outdir", "-o")] = Path("pipeprof_report"),
    cpu_threshold: Annotated[float, typer.Option(help="Flag CPU efficiency below.")] = 0.5,
    mem_threshold: Annotated[float, typer.Option(help="Flag memory efficiency below.")] = 0.5,
    show_sacct: Annotated[bool, typer.Option(help="Print the sacct command to use.")] = False,
) -> None:
    """Read sacct, Nextflow trace and Snakemake benchmark files; write a report."""
    if show_sacct:
        typer.echo(" ".join(sacct_command(start="YYYY-MM-DD")))
        raise typer.Exit()
    all_files = [*sacct, *nextflow_trace, *snakemake_benchmarks, *snakemake_log, *watch_log]
    sources = [str(p) for p in all_files]
    sacct_files = list(sacct)
    if from_slurm:
        try:
            text = run_text(sacct_command(jobs=from_slurm))
        except SlurmError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(2) from exc
        outdir.mkdir(parents=True, exist_ok=True)
        saved = outdir / "sacct.txt"
        saved.write_text(text, encoding="utf-8")
        sacct_files.append(saved)
        sources.append(f"sacct jobs {','.join(from_slurm)}")
    records = read_sources(
        sacct_files, nextflow_trace, snakemake_benchmarks, snakemake_log, watch_log
    )
    if not records:
        console.print("[red]no records found in the given files[/red]")
        raise typer.Exit(2)
    thresholds = Thresholds(cpu_efficiency=cpu_threshold, mem_efficiency=mem_threshold)
    report = build_report(records, thresholds, sources, run_id=new_run_id())
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "report.md").write_text(to_markdown(report), encoding="utf-8")
    (outdir / "report.html").write_text(to_html(report), encoding="utf-8")
    (outdir / "report.json").write_text(to_json(report), encoding="utf-8")
    typer.echo(to_markdown(report).split("## Tasks")[0].rstrip())
    console.print(f"Wrote report.md, report.html and report.json to {outdir}")


def _analysis_markdown(
    source: str, report: BottleneckReport, labels: list[BoundLabel], advice: Advice
) -> str:
    cp = report.critical_path
    out = [
        "# Workflow bottleneck analysis",
        "",
        f"Source: {source}",
        "",
        "## Critical path",
        "",
        f"Method: {cp.method}.",
        f"Wall time {cp.wall_s:.1f}s: busy {cp.busy_s:.1f}s, waiting {cp.waiting_s:.1f}s.",
        "",
    ]
    out += [f"- {s.task.name}: gap before {s.gap_before_s:.1f}s" for s in cp.steps]
    out += ["", "## Step share of wall time", ""]
    out += [
        f"- {s.step}: {s.critical_path_s:.1f}s on the critical path, {s.busy_s:.1f}s busy "
        f"overall, {s.task_count} task(s)"
        for s in report.step_shares
    ]
    if report.parallel_efficiency:
        pe = report.parallel_efficiency
        out += [
            "",
            "## Parallel efficiency",
            "",
            f"Average {pe.average:.0%} of {pe.cpus_available} CPUs; {pe.low_total_s:.1f}s "
            f"below {pe.threshold:.0%}.",
        ]
    out += [
        "",
        "## Idle gaps",
        "",
        f"Total idle time {report.idle_total_s:.1f}s over {len(report.idle_gaps)} gap(s).",
        "",
        "## Queue wait",
        "",
        f"Total {report.queue_wait_total_s:.1f}s over {len(report.queue_wait_s)} task(s).",
        "",
        "## Retries",
        "",
    ]
    out += [f"- {r.name}: {r.attempts} attempts, {r.lost_s:.1f}s lost" for r in report.retries]
    if not report.retries:
        out.append("- none")
    out += ["", "## Step boundness", ""]
    out += [f"- {b.step}: {b.label} ({'; '.join(b.evidence)})" for b in labels]
    out += ["", "## Recommendations", ""]
    if advice.first_step:
        f = advice.first_step
        out.append(f"- work on {f.step} first: {f.basis}")
    out += [f"- {a.basis}" for a in advice.thread_advice]
    out += [f"- {a.step} request {a.kind}: {a.basis}" for a in advice.request_advice]
    if not (advice.first_step or advice.thread_advice or advice.request_advice):
        out.append("- no recommendation: not enough measurements")
    return "\n".join(out)


def _analysis_json(
    source: str,
    task_count: int,
    report: BottleneckReport,
    labels: list[BoundLabel],
    advice: Advice,
) -> str:
    data = {
        "source": source,
        "tasks": task_count,
        "critical_path": {
            "method": report.critical_path.method,
            "wall_s": report.critical_path.wall_s,
            "busy_s": report.critical_path.busy_s,
            "waiting_s": report.critical_path.waiting_s,
            "steps": [
                {
                    "name": s.task.name,
                    "step": s.task.step,
                    "start": s.task.start,
                    "end": s.task.end,
                    "gap_before_s": s.gap_before_s,
                }
                for s in report.critical_path.steps
            ],
        },
        "step_shares": [dataclasses.asdict(s) for s in report.step_shares],
        "parallel_efficiency": dataclasses.asdict(report.parallel_efficiency)
        if report.parallel_efficiency
        else None,
        "idle_gaps": [dataclasses.asdict(g) for g in report.idle_gaps],
        "idle_total_s": report.idle_total_s,
        "queue_wait_s": report.queue_wait_s,
        "queue_wait_total_s": report.queue_wait_total_s,
        "retries": [dataclasses.asdict(r) for r in report.retries],
        "retry_lost_s": report.retry_lost_s,
        "bound_labels": [dataclasses.asdict(b) for b in labels],
        "advice": {
            "first_step": dataclasses.asdict(advice.first_step) if advice.first_step else None,
            "thread_advice": [dataclasses.asdict(a) for a in advice.thread_advice],
            "request_advice": [dataclasses.asdict(a) for a in advice.request_advice],
        },
    }
    return json.dumps(data, indent=2)


@app.command("analyze")
def analyze_cmd(
    nextflow_trace: Annotated[
        Path | None, typer.Option("--nextflow-trace", help="Nextflow trace file.")
    ] = None,
    snakemake_benchmarks: Annotated[
        Path | None, typer.Option(help="Snakemake benchmark folder.")
    ] = None,
    snakemake_log: Annotated[
        Path | None, typer.Option(help="Snakemake run log (with --snakemake-benchmarks).")
    ] = None,
    tsv: Annotated[
        Path | None, typer.Option(help="Plain name/start/end TSV, any runner.")
    ] = None,
    usage: Annotated[
        list[Path], typer.Option("--usage", help="StepUsage JSON lines file(s).")
    ] = [],  # noqa: B006
    cpus: Annotated[
        int | None, typer.Option(help="CPUs available, for parallel efficiency.")
    ] = None,
    outdir: Annotated[Path, typer.Option("--outdir", "-o")] = Path("pipeprof_analysis"),
) -> None:
    """Whole-run bottleneck analysis: critical path, step shares, efficiency, advice."""
    chosen = sum(x is not None for x in (nextflow_trace, snakemake_benchmarks, tsv))
    if chosen != 1:
        console.print(
            "[red]give exactly one of --nextflow-trace, --snakemake-benchmarks or --tsv[/red]"
        )
        raise typer.Exit(2)

    usage_map = load_usage(usage) if usage else {}
    tasks: list[Task]
    if nextflow_trace is not None:
        tasks = tasks_from_nextflow_trace(nextflow_trace.read_text(encoding="utf-8"))
        source = str(nextflow_trace)
    elif snakemake_benchmarks is not None:
        tasks = tasks_from_snakemake(snakemake_benchmarks, snakemake_log)
        source = str(snakemake_benchmarks)
    else:
        assert tsv is not None
        tasks = tasks_from_tsv(tsv.read_text(encoding="utf-8"), usage_map)
        source = str(tsv)
    if not tasks:
        console.print("[red]no tasks found in the given file[/red]")
        raise typer.Exit(2)
    if usage_map and tsv is None:
        matched = apply_usage(tasks, usage_map)
        console.print(f"Tool readings matched {matched} of {len(tasks)} tasks")

    report = analyse(tasks, cpus_available=cpus)
    by_step: dict[str, list[Task]] = {}
    for t in tasks:
        by_step.setdefault(t.step, []).append(t)
    labels = label_steps(by_step)
    recommendation = advise(tasks, report.critical_path, labels)

    outdir.mkdir(parents=True, exist_ok=True)
    md = _analysis_markdown(source, report, labels, recommendation)
    (outdir / "analysis.md").write_text(md, encoding="utf-8")
    (outdir / "analysis.json").write_text(
        _analysis_json(source, len(tasks), report, labels, recommendation), encoding="utf-8"
    )
    typer.echo(md)
    console.print(f"Wrote analysis.md and analysis.json to {outdir}")


_USAGE_READERS = {
    "gnu-time": "pipeprof.tools.gnu_time:parse_gnu_time",
    "bsd-time": "pipeprof.tools.bsd_time:parse_bsd_time",
    "psrecord": "pipeprof.tools.psrecord:parse_psrecord",
    "seff": "pipeprof.tools.seff:parse_seff",
}


@app.command("usage")
def usage_cmd(
    tool: Annotated[str, typer.Argument(help="One of: " + ", ".join(_USAGE_READERS))],
    path: Annotated[Path, typer.Argument(help="The tool's output file.")],
    step: Annotated[
        str, typer.Option(help="Task name the reading belongs to, as the runner names it.")
    ],
) -> None:
    """Print one StepUsage JSON line from a tool's output, for pipeprof analyze --usage."""
    import importlib

    if tool not in _USAGE_READERS:
        console.print(f"[red]unknown tool {tool!r}; use one of {', '.join(_USAGE_READERS)}[/red]")
        raise typer.Exit(2)
    module_name, func_name = _USAGE_READERS[tool].split(":")
    reader = getattr(importlib.import_module(module_name), func_name)
    try:
        reading = reader(path.read_text(encoding="utf-8"), step=step)
    except ValueError as exc:
        console.print(f"[red]{path}: {exc}[/red]")
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(dataclasses.asdict(reading), sort_keys=True))


@app.command("provenance")
def provenance_cmd(
    inputs: Annotated[list[Path], typer.Argument(help="Files to hash.")] = [],  # noqa: B006
) -> None:
    """Print provenance JSON: host, tools, git commit and input SHA-256."""
    typer.echo(json.dumps(capture(inputs=inputs).to_dict(), indent=2))


def main() -> None:
    app()
