"""Before submission: run a command on graduated subsamples and predict the full run.

The command is a template. ``{input}`` is replaced by each subsample path,
``{output}`` by a scratch output path, ``{threads}`` by the thread count and
``{workdir}`` by the scratch folder. For example::

    hjp calibrate clinvar.vcf.gz -- bcftools sort -Oz -o {output} {input}
"""

from __future__ import annotations

import json
import math
import shlex
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hpc_job_profiler.emit import (
    ResourcePlan,
    make_plan,
    nextflow_config,
    nextflow_directives,
    sbatch_header,
    snakemake_profile,
    snakemake_rule,
)
from hpc_job_profiler.fit import Selection, select_model
from hpc_job_profiler.logkit import RunLogger
from hpc_job_profiler.measure import Measurement, run_measured
from hpc_job_profiler.provenance import capture
from hpc_job_profiler.subsample import (
    FullInput,
    InputKind,
    make_subsamples,
    remove_subsamples,
)
from hpc_job_profiler.units import format_memory

DEFAULT_FRACTIONS = (0.01, 0.02, 0.05, 0.1, 0.2)
METRICS = ("wall_s", "cpu_s", "peak_rss_bytes")


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
        """The named metric of this run."""
        if metric == "cpu_s":
            return self.measurement.cpu_s
        return float(getattr(self.measurement, metric))

    def to_dict(self) -> dict[str, Any]:
        """JSON ready form."""
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
    lower: float
    upper: float
    level: float
    selection: Selection

    def to_dict(self) -> dict[str, Any]:
        """JSON ready form."""
        return {
            "metric": self.metric,
            "point": self.point,
            "lower": self.lower,
            "upper": self.upper,
            "level": self.level,
            "selection": self.selection.to_dict(),
        }


@dataclass
class CalibrationResult:
    """Everything ``hjp calibrate`` produces."""

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
        """JSON ready form."""
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
    keep_subsamples: bool = False,
    logger: RunLogger | None = None,
) -> CalibrationResult:
    """Calibrate ``template`` on subsamples of ``input_path`` and predict the full run."""
    if x_variable not in {"bytes", "records"}:
        raise CalibrationError("x_variable must be 'bytes' or 'records'")
    if repeats < 1:
        raise CalibrationError("repeats must be at least 1")
    log = logger or RunLogger("hjp.calibrate", console=None)
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
    xs = [_x_of(p, x_variable) for p in points]
    predictions: dict[str, Prediction] = {}
    with log.step("fit", x_variable=x_variable, points=len(points)) as info:
        for metric in METRICS:
            ys = [max(p.value(metric), 1e-9) for p in points]
            selection = select_model(xs, ys)
            lower, upper = selection.chosen.interval(x_full, level)
            predictions[metric] = Prediction(
                metric, selection.chosen.predict(x_full), lower, upper, level, selection
            )
            info[metric] = {
                "model": str(selection.chosen.kind),
                "point": predictions[metric].point,
                "upper": upper,
            }

    cpu_ratio = predictions["cpu_s"].point / max(predictions["wall_s"].point, 1e-9)
    cpus = max(threads, math.ceil(cpu_ratio - 0.15)) if cpu_ratio > 1.15 else threads
    plan = make_plan(
        cpus=cpus,
        peak_rss=predictions["peak_rss_bytes"].point,
        peak_rss_upper=predictions["peak_rss_bytes"].upper,
        wall_s=predictions["wall_s"].point,
        wall_upper_s=predictions["wall_s"].upper,
        mem_headroom=mem_headroom,
        time_headroom=time_headroom,
    )
    mem_sel = predictions["peak_rss_bytes"].selection.chosen
    wall_sel = predictions["wall_s"].selection.chosen
    stamp = time.strftime("%Y-%m-%d %H:%M %Z")
    note = (
        f"hjp calibrate {stamp}: {len(points)} runs on {len(subs)} subsamples of {source.name}\n"
        f"predicted peak RSS {format_memory(plan.predicted_peak_rss_bytes)} "
        f"(95% upper {format_memory(plan.predicted_peak_rss_upper_bytes)}), "
        f"wall {plan.predicted_wall_s:.1f} s (95% upper {plan.predicted_wall_upper_s:.1f} s)"
    )
    x_bytes_full = float(full.size_bytes)
    size_mem_fit = mem_sel if x_variable == "bytes" else None
    size_wall_fit = wall_sel if x_variable == "bytes" else None
    snippets = {
        "sbatch": sbatch_header(plan, job_name, partition, account, note),
        "snakemake_rule": snakemake_rule(
            plan, job_name, size_mem_fit, size_wall_fit, x_bytes_full, note
        ),
        "snakemake_profile": snakemake_profile(plan, job_name, partition, account),
        "nextflow_config": nextflow_config(plan, job_name.upper()),
        "nextflow_directives": nextflow_directives(
            plan, size_mem_fit, size_wall_fit, x_bytes_full, nf_input_var
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
                    "inside_interval": pred.lower <= actual <= pred.upper,
                }
            validation["plan_covers_actual"] = {
                "memory": plan.mem_bytes >= m.peak_rss_bytes,
                "time": plan.time_s >= m.wall_s,
            }
            info.update(validation["plan_covers_actual"])

    prov = capture(inputs=[], tools=_tools_in(template)).to_dict()
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
