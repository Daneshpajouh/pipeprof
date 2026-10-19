"""Before submission: run a command on graduated subsamples and predict the full run.

The command is a template. ``{input}`` is replaced by each subsample path,
``{output}`` by a scratch output path, ``{threads}`` by the thread count and
``{workdir}`` by the scratch folder. For example::

    pipeprof calibrate clinvar.vcf.gz -- bcftools sort -Oz -o {output} {input}
"""

from __future__ import annotations

import json
import math
import shlex
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pipeprof.bound import CPU_WAIT_THRESHOLD
from pipeprof.emit import (
    ResourcePlan,
    make_plan,
    nextflow_config,
    nextflow_directives,
    sbatch_header,
    snakemake_profile,
    snakemake_rule,
)
from pipeprof.fit import (
    NO_INTERVAL,
    Selection,
    constant_fit,
    predict_with_plateau,
    proportional_bound,
    scale_fit,
    select_model,
)
from pipeprof.logkit import RunLogger
from pipeprof.measure import Measurement, run_measured
from pipeprof.provenance import capture
from pipeprof.subsample import (
    FullInput,
    InputKind,
    make_subsamples,
    remove_subsamples,
)
from pipeprof.units import format_memory

DEFAULT_FRACTIONS = (0.01, 0.02, 0.05, 0.1, 0.2)
METRICS = ("wall_s", "cpu_s", "peak_rss_bytes")
#: Share of one core under which a run counts as a pure wait. Loading a binary
#: and its libraries costs a few hundredths of a CPU second whatever the input,
#: so a stalled run still shows a little CPU; under 1 percent of one core that
#: CPU is start-up, not work that scales with input, and its ratio measures the
#: stall, not the tool.
MIN_CPU_SHARE = 0.01


class CalibrationError(RuntimeError):
    """Raised when calibration cannot produce a prediction."""


@dataclass
class RunPoint:
    """One measured run of the command on one input."""

    fraction: float
    repeat: int
    input_path: str
    input_bytes: int
    records: int
    measurement: Measurement

    def value(self, metric: str) -> float:
        if metric == "cpu_s":
            return self.measurement.cpu_s
        return float(getattr(self.measurement, metric))

    def to_dict(self) -> dict[str, Any]:
        return {
            "fraction": self.fraction,
            "repeat": self.repeat,
            "input_path": self.input_path,
            "input_bytes": self.input_bytes,
            "records": self.records,
            "measurement": self.measurement.to_dict(),
        }


@dataclass
class Prediction:
    """Prediction for one metric at the full input size."""

    metric: str
    point: float
    #: ``None`` when the fit has no interval (fewer than three runs).
    lower: float | None
    upper: float | None
    level: float
    selection: Selection
    #: "fitted"; for memory "flat" (no growth seen) or "flat then rising" (hinge fit);
    #: for wall time "mostly waiting" (derived from the CPU model, not fitted).
    basis: str = "fitted"
    note: str = ""
    observed_range: tuple[float, float] | None = None
    #: With no interval, the bound the request is sized from instead.
    fallback_upper: float | None = None

    @property
    def plan_upper(self) -> float:
        """The upper bound the request is sized from: the interval's, else the fallback."""
        if self.upper is not None:
            return self.upper
        return self.fallback_upper if self.fallback_upper is not None else self.point

    def interval_text(self, fmt: Callable[[float], str]) -> str:
        if self.lower is None or self.upper is None:
            return NO_INTERVAL
        return f"{fmt(self.lower)} to {fmt(self.upper)}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "point": self.point,
            "lower": self.lower,
            "upper": self.upper,
            "fallback_upper": self.fallback_upper,
            "level": self.level,
            "basis": self.basis,
            "note": self.note,
            "observed_range": list(self.observed_range) if self.observed_range else None,
            "selection": self.selection.to_dict(),
        }


@dataclass
class CalibrationResult:
    """Everything ``pipeprof calibrate`` produces."""

    command_template: list[str]
    shell: bool
    input_path: str
    kind: str
    x_variable: str
    full_input_bytes: int
    full_records: int
    threads: int
    points: list[RunPoint]
    predictions: dict[str, Prediction]
    plan: ResourcePlan
    snippets: dict[str, str]
    validation: dict[str, Any] | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    run_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "command_template": self.command_template,
            "shell": self.shell,
            "input_path": self.input_path,
            "kind": self.kind,
            "x_variable": self.x_variable,
            "full_input_bytes": self.full_input_bytes,
            "full_records": self.full_records,
            "threads": self.threads,
            "points": [p.to_dict() for p in self.points],
            "predictions": {k: v.to_dict() for k, v in self.predictions.items()},
            "plan": self.plan.to_dict(),
            "snippets": self.snippets,
            "validation": self.validation,
            "provenance": self.provenance,
        }


def render_command(
    template: Sequence[str],
    input_path: Path,
    output_path: Path,
    threads: int,
    workdir: Path,
    quote: bool = False,
) -> list[str]:
    """Fill the placeholders in each argument of the template.

    With ``quote`` (shell mode) the substituted paths are shell quoted, so a
    path with spaces cannot split into two words.
    """
    values = {
        "input": str(input_path),
        "output": str(output_path),
        "threads": str(threads),
        "workdir": str(workdir),
    }
    if quote:
        values = {k: shlex.quote(v) for k, v in values.items()}
    rendered: list[str] = []
    for arg in template:
        try:
            rendered.append(arg.format(**values))
        except (KeyError, IndexError) as exc:
            raise CalibrationError(f"unknown placeholder in {arg!r}: {exc}") from exc
    return rendered


_KNOWN_EXTENSIONS = (
    ".vcf.gz",
    ".vcf.bgz",
    ".fastq.gz",
    ".fq.gz",
    ".txt.gz",
    ".tsv.gz",
    ".vcf",
    ".bam",
    ".cram",
    ".sam",
    ".fastq",
    ".fq",
)


def file_extension(name: str) -> str:
    """Return the format extension of a file name, such as ``.vcf.gz`` or ``.bam``."""
    lower = name.lower()
    for ext in _KNOWN_EXTENSIONS:
        if lower.endswith(ext):
            return name[-len(ext) :]
    suffixes = Path(name).suffixes
    if len(suffixes) >= 2 and suffixes[-1].lower() == ".gz":
        return "".join(suffixes[-2:])
    return suffixes[-1] if suffixes else ""


def _x_of(point: RunPoint | FullInput, x_variable: str) -> float:
    if isinstance(point, RunPoint):
        return float(point.records if x_variable == "records" else point.input_bytes)
    return float(point.records if x_variable == "records" else point.size_bytes)


def measure_once(
    template: Sequence[str],
    input_path: Path,
    workdir: Path,
    tag: str,
    *,
    shell: bool,
    threads: int,
    interval: float,
    output_suffix: str | None,
) -> Measurement:
    """Run the command once on one input and remove its output afterwards."""
    suffix = output_suffix if output_suffix is not None else file_extension(input_path.name)
    output = workdir / f"out_{tag}{suffix}"
    argv = render_command(template, input_path, output, threads, workdir, quote=shell)
    command: list[str] | str = " ".join(argv) if shell else argv
    measurement = run_measured(command, shell=shell, interval=interval, cwd=workdir)
    for leftover in workdir.glob(output.name + "*"):
        if leftover.is_file():
            leftover.unlink()
    return measurement


def cpus_from_runs(points: Sequence[RunPoint], threads: int) -> tuple[int, float]:
    """CPUs to request, from the busiest measured run; also returns that run's cores busy.

    CPU time and wall time are measured on the same run, so their ratio is
    reliable; dividing two separately extrapolated predictions is not (each
    may pick a different model). The request is at least 1 and at most
    ``threads``.
    """
    ratios = [
        p.measurement.cpu_s / p.measurement.wall_s for p in points if p.measurement.wall_s > 0
    ]
    busiest = max(ratios, default=0.0)
    return max(1, min(threads, math.ceil(busiest - 0.15))), busiest


def cores_busy(point: RunPoint) -> float:
    """CPU seconds per wall second of one run."""
    m = point.measurement
    return m.cpu_s / m.wall_s if m.wall_s > 0 else 0.0


def waiting_runs(points: Sequence[RunPoint]) -> list[RunPoint]:
    """Runs that kept under ``CPU_WAIT_THRESHOLD`` of one core busy: mostly waiting.

    This is the threshold ``pipeprof analyze`` uses for its "mostly waiting" label,
    so both commands call the same runs waiting. It is a share of one core, not
    of ``--threads``: a single-threaded tool given four threads keeps one core
    busy and is not waiting. Under 0.3 the run spent more than 70 percent of
    its wall time (wall above 3.3 times CPU) on something other than computing,
    on a cluster usually a shared filesystem.
    """
    return [p for p in points if p.measurement.wall_s > 0 and cores_busy(p) < CPU_WAIT_THRESHOLD]


def pure_waits(points: Sequence[RunPoint]) -> list[RunPoint]:
    """Runs that kept under ``MIN_CPU_SHARE`` of one core busy, including no CPU at all."""
    return [p for p in points if p.measurement.wall_s > 0 and cores_busy(p) < MIN_CPU_SHARE]


def _wall_from_cpu(
    points: Sequence[RunPoint],
    cpu: Prediction,
    waiting: Sequence[RunPoint],
    xs: Sequence[float],
    x_full: float,
) -> Prediction:
    """Wall time from the CPU prediction and the measured CPU/wall ratios.

    How long a run waits depends on the filesystem's load at that moment, not
    on input size, so a fit of wall time on such runs is noise (0.06 to
    1,146 s on a cluster). CPU time does scale with input. The point and
    upper bound assume the full run waits as much as the worst measured run;
    the lower bound assumes it waits as little as the best one.

    A run under ``MIN_CPU_SHARE`` of one core (a pure wait, such as a stalled
    mount, with at most start-up CPU) has a cores-busy ratio near zero, and
    dividing by it inflates the estimate without limit. Such runs are left
    out of the ratios (and of the CPU fit); the estimate is at least their
    longest wall, since the full run can stall as long. If every run is a
    pure wait there is no CPU model to scale and the estimate is the observed
    wall range.

    The derived bound never exceeds the largest wall of any run scaled in
    proportion to input: a wall above that would mean waiting grows faster
    than the input, which no run showed.
    """
    timed = [p for p in points if p.measurement.wall_s > 0]
    walls = [p.measurement.wall_s for p in points]
    pure = pure_waits(points)
    pure_ids = {id(p) for p in pure}
    busy = [p for p in timed if id(p) not in pure_ids]
    note = (
        f"mostly waiting: {len(waiting)} of {len(points)} runs kept under "
        f"{CPU_WAIT_THRESHOLD:.0%} of one core busy, so wall time is not fitted; "
        f"observed wall {min(walls):.2f} to {max(walls):.2f} s"
    )
    if pure:
        pure_walls = [p.measurement.wall_s for p in pure]
        note += (
            f"; {len(pure)} run(s) kept under {MIN_CPU_SHARE:.0%} of one core busy "
            f"(pure waits, no CPU to speak of, wall {min(pure_walls):.2f} to "
            f"{max(pure_walls):.2f} s), left out of the CPU fit and the cores-busy "
            f"ratio"
        )
    if not busy:
        longest = max(walls)
        fit = constant_fit(walls)
        selection = Selection(
            fit, {fit.kind: fit}, {}, {}, "mostly waiting: no run used CPU, observed wall range"
        )
        note += "; no CPU model to scale, so the estimate is the observed wall range"
        return Prediction(
            "wall_s",
            longest,
            min(walls),
            longest,
            cpu.level,
            selection,
            basis="mostly waiting",
            note=note,
            observed_range=(min(walls), max(walls)),
        )
    ratios = [cores_busy(p) for p in busy]
    worst, best = min(ratios), max(ratios)
    slowest = min(busy, key=cores_busy).measurement
    note += (
        f"; time is the CPU model divided by the lowest cores-busy ratio ({worst:.3f}: "
        f"wall {slowest.wall_s:.2f} s on {slowest.cpu_s:.2f} s CPU)"
    )
    # The largest wall any run implies at full size if all of it scaled with input.
    scaled = [
        p.measurement.wall_s * max(1.0, x_full / x)
        for p, x in zip(points, xs, strict=True)
        if x > 0
    ]
    ratio = worst
    cpu_upper = cpu.plan_upper
    if scaled and cpu_upper / worst > max(scaled):
        cap = max(scaled)
        ratio = cpu_upper / cap
        note += (
            f"; capped at {cap:.2f} s, the longest wall of any run scaled in proportion "
            f"to input (cores-busy ratio {ratio:.3f} used)"
        )
    point, upper = cpu.point / ratio, cpu_upper / ratio
    if pure:
        stall = max(p.measurement.wall_s for p in pure)
        if stall > point:
            note += f"; raised to the longest pure wait {stall:.2f} s"
        point, upper = max(point, stall), max(upper, stall)
    # The wall model is the CPU model scaled by 1 / ratio, so the Snakemake and
    # Nextflow runtime formulas built from it give wall minutes, as the fixed
    # sbatch time does; reusing the CPU selection would emit CPU minutes.
    cpu_fit = cpu.selection.chosen
    wall_fit = scale_fit(cpu_fit, 1.0 / ratio)
    reason = (
        f"mostly waiting: CPU model ({cpu_fit.kind}) divided by the lowest cores-busy "
        f"ratio {worst:.3f}"
    )
    if ratio > worst:
        reason += f", raised to {ratio:.3f} by the proportional cap"
    selection = Selection(
        wall_fit,
        {wall_fit.kind: wall_fit},
        dict(cpu.selection.cv_scores),
        dict(cpu.selection.cv_errors),
        reason,
    )
    if cpu.lower is None or cpu.upper is None:
        # No CPU interval, so none for the wall derived from it either.
        return Prediction(
            "wall_s",
            point,
            None,
            None,
            cpu.level,
            selection,
            basis="mostly waiting",
            note=f"{note}; {NO_INTERVAL}",
            observed_range=(min(walls), max(walls)),
            fallback_upper=upper,
        )
    return Prediction(
        "wall_s",
        point,
        min(cpu.lower / best, point),
        upper,
        cpu.level,
        selection,
        basis="mostly waiting",
        note=note,
        observed_range=(min(walls), max(walls)),
    )


def predict_metrics(
    points: Sequence[RunPoint], x_full: float, x_variable: str, level: float = 0.95
) -> dict[str, Prediction]:
    """Predict each metric at ``x_full`` from the measured runs."""
    all_xs = [_x_of(p, x_variable) for p in points]
    pure = pure_waits(points)
    predictions: dict[str, Prediction] = {}
    for metric in METRICS:
        used = list(points)
        note = ""
        if metric == "cpu_s" and pure:
            # A pure wait's CPU is start-up, not work: it would pull the CPU model down.
            pure_ids = {id(p) for p in pure}
            rest = [p for p in points if id(p) not in pure_ids]
            if len({_x_of(p, x_variable) for p in rest}) >= 2:
                used = rest
                note = (
                    f"{len(pure)} run(s) under {MIN_CPU_SHARE:.0%} of one core busy "
                    f"left out of the CPU fit"
                )
        xs = [_x_of(p, x_variable) for p in used]
        ys = [max(p.value(metric), 1e-9) for p in used]
        basis = "fitted"
        if metric == "peak_rss_bytes":
            # Memory often sits on a fixed buffer for the small runs and only then grows.
            flat = predict_with_plateau(xs, ys, x_full, level)
            selection, point, lower, upper = flat.selection, flat.point, flat.lower, flat.upper
            if flat.plateau is not None:
                basis = "flat" if flat.plateau.whole else "flat then rising"
                note = flat.note
        else:
            selection = select_model(xs, ys)
            point = selection.chosen.predict(x_full)
            lower, upper = selection.chosen.interval(x_full, level) or (None, None)
        largest = max(ys)
        if x_full >= max(xs) and point < largest:
            # The full input cannot need less than its largest subsample did.
            selection.reason += (
                f"; prediction {point:.4g} raised to the largest measured value {largest:.4g}"
            )
            point = largest
            if upper is not None:
                upper = max(upper, largest)
        fallback = None
        if lower is None or upper is None:
            # Two runs fit a line exactly and say nothing about the scatter. The
            # largest measured value alone would size the full input from a
            # subsample (the 2.5x memory miss); the largest run scaled in proportion
            # to input is the bound used when no growth can be read.
            lower = upper = None
            fallback = max(point, proportional_bound(xs, ys, x_full))
            extra = (
                f"{NO_INTERVAL}; request sized from {fallback:.4g}, the larger of the point "
                "prediction and the largest run scaled in proportion to input"
            )
            note = f"{note}; {extra}" if note else extra
        predictions[metric] = Prediction(
            metric,
            point,
            lower,
            upper,
            level,
            selection,
            basis=basis,
            note=note,
            fallback_upper=fallback,
        )
    waiting = waiting_runs(points)
    if waiting:
        predictions["wall_s"] = _wall_from_cpu(
            points, predictions["cpu_s"], waiting, all_xs, x_full
        )
    return predictions


def calibrate(
    input_path: str | Path,
    template: Sequence[str],
    *,
    workdir: str | Path,
    fractions: Sequence[float] = DEFAULT_FRACTIONS,
    repeats: int = 1,
    threads: int = 1,
    shell: bool = False,
    kind: InputKind | None = None,
    x_variable: str = "bytes",
    interval: float = 0.05,
    seed: int = 13,
    mem_headroom: float = 1.2,
    time_headroom: float = 1.5,
    level: float = 0.95,
    job_name: str = "job",
    partition: str | None = None,
    account: str | None = None,
    nf_input_var: str = "input_file",
    output_suffix: str | None = None,
    validate_full: bool = False,
    warmup: bool = True,
    keep_subsamples: bool = False,
    logger: RunLogger | None = None,
) -> CalibrationResult:
    """Calibrate ``template`` on subsamples of ``input_path`` and predict the full run."""
    if x_variable not in {"bytes", "records"}:
        raise CalibrationError("x_variable must be 'bytes' or 'records'")
    if repeats < 1:
        raise CalibrationError("repeats must be at least 1")
    log = logger or RunLogger("pipeprof.calibrate", console=None)
    source = Path(input_path).resolve()
    work = Path(workdir).resolve()
    work.mkdir(parents=True, exist_ok=True)

    with log.step("subsample", input=str(source), fractions=list(fractions)) as info:
        subs, full = make_subsamples(
            source, fractions, work, kind=kind, seed=seed, threads=threads
        )
        info["records_full"] = full.records
        info["bytes_full"] = full.size_bytes
        info["subsamples"] = [s.to_dict() for s in subs]

    points: list[RunPoint] = []
    try:
        if warmup:
            # One discarded run loads the binary and libraries into the page cache, so
            # the smallest measured run does not carry that start-up cost alone.
            with log.step("warmup", input=str(subs[0].path)):
                measure_once(
                    template,
                    subs[0].path,
                    work,
                    "warmup",
                    shell=shell,
                    threads=threads,
                    interval=interval,
                    output_suffix=output_suffix,
                )
        for sub in subs:
            for repeat in range(1, repeats + 1):
                with log.step(
                    "measure", fraction=sub.fraction, repeat=repeat, records=sub.records
                ) as info:
                    m = measure_once(
                        template,
                        sub.path,
                        work,
                        f"{sub.fraction:.4f}_{repeat}",
                        shell=shell,
                        threads=threads,
                        interval=interval,
                        output_suffix=output_suffix,
                    )
                    info.update(
                        returncode=m.returncode,
                        wall_s=round(m.wall_s, 4),
                        cpu_s=round(m.cpu_s, 4),
                        peak_rss=m.peak_rss_bytes,
                    )
                if m.returncode != 0:
                    raise CalibrationError(
                        f"command failed with status {m.returncode} on {sub.path}: {m.command}"
                    )
                points.append(
                    RunPoint(sub.fraction, repeat, str(sub.path), sub.size_bytes, sub.records, m)
                )
    finally:
        if not keep_subsamples:
            remove_subsamples(subs)

    x_full = _x_of(full, x_variable)
    with log.step("fit", x_variable=x_variable, points=len(points)) as info:
        predictions = predict_metrics(points, x_full, x_variable, level)
        for metric, pred in predictions.items():
            # "model" is the curve kind the prediction comes from; "basis" says whether
            # it was fitted directly, flat, after a hinge or derived from CPU.
            info[metric] = {
                "model": str(pred.selection.chosen.kind),
                "basis": pred.basis,
                "point": pred.point,
                "upper": pred.upper,
                "fallback_upper": pred.fallback_upper,
            }
    for pred in predictions.values():
        if pred.basis != "fitted" or pred.upper is None:
            log.warning(f"{pred.metric}: {pred.note}", metric=pred.metric, basis=pred.basis)

    cpus, busiest = cpus_from_runs(points, threads)
    if busiest > threads + 0.15:
        log.warning(
            f"the command kept {busiest:.2f} cores busy with --threads {threads}; "
            "the request is capped at --threads, so pass the tool's thread option",
            busiest_cores=round(busiest, 3),
        )
    plan = make_plan(
        cpus=cpus,
        peak_rss=predictions["peak_rss_bytes"].point,
        peak_rss_upper=predictions["peak_rss_bytes"].plan_upper,
        wall_s=predictions["wall_s"].point,
        wall_upper_s=predictions["wall_s"].plan_upper,
        mem_headroom=mem_headroom,
        time_headroom=time_headroom,
    )
    mem_sel = predictions["peak_rss_bytes"].selection.chosen
    wall_sel = predictions["wall_s"].selection.chosen
    stamp = time.strftime("%Y-%m-%d %H:%M %Z")
    mem_pred, wall_pred = predictions["peak_rss_bytes"], predictions["wall_s"]
    note = (
        f"pipeprof calibrate {stamp}: {len(points)} runs on "
        f"{len(subs)} subsamples of {source.name}\n"
        f"predicted peak RSS {format_memory(plan.predicted_peak_rss_bytes)} "
        f"({_upper_text(mem_pred, format_memory)}), "
        f"wall {plan.predicted_wall_s:.1f} s "
        f"({_upper_text(wall_pred, lambda v: f'{v:.1f} s')})"
    )
    for pred in predictions.values():
        if pred.basis != "fitted":
            note += f"\n{pred.metric}: {pred.basis} ({pred.selection.reason.split(';')[0]})"
    x_bytes_full = float(full.size_bytes)
    size_mem_fit = mem_sel if x_variable == "bytes" else None
    size_wall_fit = wall_sel if x_variable == "bytes" else None
    snippets = {
        "sbatch": sbatch_header(plan, job_name, partition, account, note),
        "snakemake_rule": snakemake_rule(
            plan, job_name, size_mem_fit, size_wall_fit, x_bytes_full, note
        ),
        "snakemake_profile": snakemake_profile(plan, job_name, partition, account),
        "nextflow_config": nextflow_config(plan, job_name.upper(), note=note),
        "nextflow_directives": nextflow_directives(
            plan, size_mem_fit, size_wall_fit, x_bytes_full, nf_input_var, note=note
        ),
    }

    validation: dict[str, Any] | None = None
    if validate_full:
        with log.step("validate_full", input=str(source)) as info:
            m = measure_once(
                template,
                source,
                work,
                "full",
                shell=shell,
                threads=threads,
                interval=interval,
                output_suffix=output_suffix,
            )
            validation = {"measurement": m.to_dict(), "returncode": m.returncode, "metrics": {}}
            for metric, pred in predictions.items():
                actual = m.cpu_s if metric == "cpu_s" else float(getattr(m, metric))
                validation["metrics"][metric] = {
                    "predicted": pred.point,
                    "lower": pred.lower,
                    "upper": pred.upper,
                    "actual": actual,
                    "ratio_predicted_to_actual": pred.point / actual if actual > 0 else None,
                    "inside_interval": (
                        None
                        if pred.lower is None or pred.upper is None
                        else pred.lower <= actual <= pred.upper
                    ),
                }
            validation["plan_covers_actual"] = {
                "memory": plan.mem_bytes >= m.peak_rss_bytes,
                "time": plan.time_s >= m.wall_s,
            }
            info.update(validation["plan_covers_actual"])

    prov = capture(inputs=[source], tools=_tools_in(template)).to_dict()
    prov["input"] = {"path": str(source), "size_bytes": full.size_bytes, "records": full.records}
    return CalibrationResult(
        command_template=list(template),
        shell=shell,
        input_path=str(source),
        kind=str(full.kind),
        x_variable=x_variable,
        full_input_bytes=full.size_bytes,
        full_records=full.records,
        threads=threads,
        points=points,
        predictions=predictions,
        plan=plan,
        snippets=snippets,
        validation=validation,
        provenance=prov,
        run_id=log.run_id,
    )


def _upper_text(pred: Prediction, fmt: Callable[[float], str]) -> str:
    """The upper bound as the emitted requests state it."""
    if pred.upper is None:
        return f"{NO_INTERVAL}; sized from {fmt(pred.plan_upper)}"
    return f"95% upper {fmt(pred.upper)}"


def _tools_in(template: Sequence[str]) -> list[str]:
    first = template[0] if template else ""
    tool = shlex.split(first)[0] if first.strip() else ""
    return [tool] if tool and "{" not in tool else []


def write_outputs(result: CalibrationResult, outdir: str | Path) -> dict[str, Path]:
    """Write the JSON result and each snippet to ``outdir``; return the paths."""
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    names = {
        "sbatch": "sbatch_header.sh",
        "snakemake_rule": "snakemake_resources.smk",
        "snakemake_profile": "snakemake_slurm_profile.yaml",
        "nextflow_config": "nextflow_resources.config",
        "nextflow_directives": "nextflow_directives.nf",
    }
    paths: dict[str, Path] = {}
    for key, name in names.items():
        path = out / name
        path.write_text(result.snippets[key], encoding="utf-8")
        paths[key] = path
    json_path = out / "calibration.json"
    json_path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    paths["json"] = json_path
    return paths
