"""Command line interface: ``hjp calibrate``, ``watch``, ``tail``, ``report``, ``provenance``."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from hpc_job_profiler import __version__
from hpc_job_profiler.calibrate import (
    DEFAULT_FRACTIONS,
    CalibrationError,
    calibrate,
    write_outputs,
)
from hpc_job_profiler.logkit import RunLogger
from hpc_job_profiler.provenance import capture
from hpc_job_profiler.records import read_sources
from hpc_job_profiler.report import Thresholds, build_report, to_html, to_json, to_markdown
from hpc_job_profiler.slurm import SlurmError, run_text, sacct_command
from hpc_job_profiler.subsample import InputKind, SubsampleError
from hpc_job_profiler.tail import tail as tail_view
from hpc_job_profiler.units import format_memory, format_slurm_duration, parse_memory
from hpc_job_profiler.watch import AlertConfig, JsonlSink, watch_pid, watch_slurm

app = typer.Typer(
    help="Profile bioinformatics jobs before, during and after Slurm submission.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console(stderr=True)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"hjp {__version__}")
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
        "hjp_calibration"
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
) -> None:
    """Run COMMAND on graduated subsamples of INPUT_PATH and predict the full run."""
    outdir.mkdir(parents=True, exist_ok=True)
    log = RunLogger("hjp.calibrate", jsonl_path=outdir / "hjp.log.jsonl", console=sys.stderr)
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
        sink = JsonlSink(handle)
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
            sink.write({"type": "end", "returncode": code})
    console.print(f"{n} samples written to {out}")


@app.command("tail")
def tail_cmd(
    log: Annotated[Path, typer.Argument(help="JSON lines file written by hjp watch.")],
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
    from_slurm: Annotated[
        list[str], typer.Option(help="Job ID to read directly with sacct.")
    ] = [],  # noqa: B006
    outdir: Annotated[Path, typer.Option("--outdir", "-o")] = Path("hjp_report"),
    cpu_threshold: Annotated[float, typer.Option(help="Flag CPU efficiency below.")] = 0.5,
    mem_threshold: Annotated[float, typer.Option(help="Flag memory efficiency below.")] = 0.5,
    show_sacct: Annotated[bool, typer.Option(help="Print the sacct command to use.")] = False,
) -> None:
    """Read sacct, Nextflow trace and Snakemake benchmark files; write a report."""
    if show_sacct:
        typer.echo(" ".join(sacct_command(start="YYYY-MM-DD")))
        raise typer.Exit()
    sources = [str(p) for p in [*sacct, *nextflow_trace, *snakemake_benchmarks, *snakemake_log]]
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
    records = read_sources(sacct_files, nextflow_trace, snakemake_benchmarks, snakemake_log)
    if not records:
        console.print("[red]no records found in the given files[/red]")
        raise typer.Exit(2)
    thresholds = Thresholds(cpu_efficiency=cpu_threshold, mem_efficiency=mem_threshold)
    report = build_report(records, thresholds, sources)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "report.md").write_text(to_markdown(report), encoding="utf-8")
    (outdir / "report.html").write_text(to_html(report), encoding="utf-8")
    (outdir / "report.json").write_text(to_json(report), encoding="utf-8")
    typer.echo(to_markdown(report).split("## Tasks")[0].rstrip())
    console.print(f"Wrote report.md, report.html and report.json to {outdir}")


@app.command("provenance")
def provenance_cmd(
    inputs: Annotated[list[Path], typer.Argument(help="Files to hash.")] = [],  # noqa: B006
) -> None:
    """Print provenance JSON: host, tools, git commit and input SHA-256."""
    typer.echo(json.dumps(capture(inputs=inputs).to_dict(), indent=2))


def main() -> None:
    """Entry point for the ``hjp`` console script."""
    app()
