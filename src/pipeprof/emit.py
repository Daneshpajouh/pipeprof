"""Turn predicted resource use into scheduler requests and workflow snippets.

Outputs:

* an ``#SBATCH`` header for a plain Slurm batch script;
* a Nextflow ``process`` config block and a process directive block whose
  memory and time are closures over the input file size and ``task.attempt``;
* Snakemake resource functions over ``input.size_mb`` and ``attempt``, plus a
  profile for the snakemake-executor-plugin-slurm.

Units. Slurm reads ``--mem`` in MiB. Snakemake ``mem_mb`` is in MB (10^6
bytes) and the Slurm executor plugin passes the number straight to
``--mem``. Writing ``ceil(bytes / 10**6)`` therefore asks for slightly more
than needed under either reading, never less.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from pipeprof.fit import Fit, ModelKind
from pipeprof.units import (
    MIB,
    format_slurm_duration,
    round_up_memory,
    round_up_minutes,
)

MB = 10**6


@dataclass
class ResourcePlan:
    """A resource request with the numbers it came from."""

    cpus: int
    mem_bytes: int
    time_s: int
    predicted_peak_rss_bytes: float
    predicted_peak_rss_upper_bytes: float
    predicted_wall_s: float
    predicted_wall_upper_s: float
    mem_headroom: float
    time_headroom: float

    @property
    def mem_mb(self) -> int:
        """Memory in MB (10^6 bytes), rounded up, as Snakemake expects."""
        return math.ceil(self.mem_bytes / MB)

    @property
    def runtime_min(self) -> int:
        """Time in whole minutes, as Snakemake's ``runtime`` expects."""
        return math.ceil(self.time_s / 60)

    @property
    def slurm_mem(self) -> str:
        """Memory for ``--mem``: whole GiB when exact, else MiB."""
        if self.mem_bytes % (1024 * MIB) == 0:
            return f"{self.mem_bytes // (1024 * MIB)}G"
        return f"{math.ceil(self.mem_bytes / MIB)}M"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.update(mem_mb=self.mem_mb, runtime_min=self.runtime_min, slurm_mem=self.slurm_mem)
        return data


def make_plan(
    *,
    cpus: int,
    peak_rss: float,
    peak_rss_upper: float,
    wall_s: float,
    wall_upper_s: float,
    mem_headroom: float = 1.2,
    time_headroom: float = 1.5,
) -> ResourcePlan:
    """Apply headroom to the upper prediction bounds and round to scheduler friendly steps."""
    if mem_headroom < 1 or time_headroom < 1:
        raise ValueError("headroom factors must be at least 1.0")
    mem = round_up_memory(max(peak_rss, peak_rss_upper) * mem_headroom)
    minutes = round_up_minutes(max(wall_s, wall_upper_s) * time_headroom)
    return ResourcePlan(
        cpus=max(1, cpus),
        mem_bytes=mem,
        time_s=minutes * 60,
        predicted_peak_rss_bytes=peak_rss,
        predicted_peak_rss_upper_bytes=peak_rss_upper,
        predicted_wall_s=wall_s,
        predicted_wall_upper_s=wall_upper_s,
        mem_headroom=mem_headroom,
        time_headroom=time_headroom,
    )


def sbatch_header(
    plan: ResourcePlan,
    job_name: str,
    partition: str | None = None,
    account: str | None = None,
    note: str = "",
) -> str:
    lines = [
        "#!/usr/bin/env bash",
        f"#SBATCH --job-name={job_name}",
        "#SBATCH --nodes=1",
        "#SBATCH --ntasks=1",
        f"#SBATCH --cpus-per-task={plan.cpus}",
        f"#SBATCH --mem={plan.slurm_mem}",
        f"#SBATCH --time={format_slurm_duration(plan.time_s)}",
        "#SBATCH --output=logs/%x-%j.out",
    ]
    if partition:
        lines.append(f"#SBATCH --partition={partition}")
    if account:
        lines.append(f"#SBATCH --account={account}")
    if note:
        lines.extend(f"# {line}" for line in note.splitlines())
    lines.append("set -euo pipefail")
    return "\n".join(lines) + "\n"


@dataclass
class ScaledFormula:
    """A fitted model rewritten for workflow code: y_out = f(size_in) * safety.

    ``coef`` and ``exponent`` (power law) or ``intercept`` and ``coef``
    (linear) are already converted to the output and input units.
    """

    kind: ModelKind
    intercept: float
    coef: float
    exponent: float
    safety: float

    def python(self, var: str) -> str:
        if self.kind is ModelKind.POWER:
            return f"{self.coef:.6g} * {var} ** {self.exponent:.4f}"
        return f"{self.intercept:.6g} + {self.coef:.6g} * {var}"

    def groovy(self, var: str) -> str:
        if self.kind is ModelKind.POWER:
            return f"{self.coef:.6g} * Math.pow({var}, {self.exponent:.4f})"
        return f"{self.intercept:.6g} + {self.coef:.6g} * {var}"

    def evaluate(self, size: float) -> float:
        """Value at ``size`` (in the input unit), including the safety factor."""
        if self.kind is ModelKind.POWER:
            base = self.coef * math.pow(size, self.exponent)
        else:
            base = self.intercept + self.coef * size
        return base * self.safety


def rescale(fit: Fit, x_unit: float, y_unit: float, safety: float) -> ScaledFormula:
    """Rewrite a fit of y bytes or seconds against x bytes in new units.

    ``x_unit`` is the size of one new input unit in old units (MiB for
    Snakemake's ``input.size_mb``); ``y_unit`` likewise for the output.
    """
    if fit.kind is ModelKind.POWER:
        coef = math.exp(fit.intercept) * x_unit**fit.slope / y_unit
        return ScaledFormula(ModelKind.POWER, 0.0, coef, fit.slope, safety)
    return ScaledFormula(
        ModelKind.LINEAR, fit.intercept / y_unit, fit.slope * x_unit / y_unit, 1.0, safety
    )


def safety_factor(fit: Fit, x_full: float, headroom: float, level: float = 0.95) -> float:
    """Headroom times the ratio of the upper prediction bound to the point prediction at x_full."""
    point = fit.predict(x_full)
    upper = fit.interval(x_full, level)[1]
    ratio = upper / point if point > 0 else 1.0
    return headroom * max(1.0, ratio)


def snakemake_rule(
    plan: ResourcePlan,
    rule_name: str,
    mem_fit: Fit | None,
    time_fit: Fit | None,
    x_full: float,
    note: str = "",
) -> str:
    """Return Snakemake resource functions and a rule skeleton.

    Memory and runtime grow with ``input.size_mb`` following the chosen fit
    and double on each retry (``attempt``), so ``--retries 2`` recovers from
    an underestimate. Without a fit the plan's fixed values are used.
    """
    lines = [f"# {line}" for line in note.splitlines()] if note else []
    lines.append("import math")
    lines.append("")
    if mem_fit is not None:
        mem = rescale(mem_fit, MIB, MB, safety_factor(mem_fit, x_full, plan.mem_headroom))
        lines += [
            f"def {rule_name}_mem_mb(wildcards, input, attempt):",
            f"    # {mem_fit.kind} fit; safety factor {mem.safety:.3f} covers the 95% upper bound",
            "    # and headroom; each retry doubles the request.",
            "    size_mb = max(input.size_mb, 1e-6)",
            f"    return max(256, math.ceil(({mem.python('size_mb')}) * {mem.safety:.4f})"
            " * 2 ** (attempt - 1))",
            "",
        ]
        mem_ref = f"{rule_name}_mem_mb"
    else:
        mem_ref = f"lambda wildcards, attempt: {plan.mem_mb} * 2 ** (attempt - 1)"
    if time_fit is not None:
        tm = rescale(time_fit, MIB, 60.0, safety_factor(time_fit, x_full, plan.time_headroom))
        lines += [
            f"def {rule_name}_runtime(wildcards, input, attempt):",
            f"    # {time_fit.kind} fit in minutes; safety factor {tm.safety:.3f}.",
            "    size_mb = max(input.size_mb, 1e-6)",
            f"    return max(5, math.ceil(({tm.python('size_mb')}) * {tm.safety:.4f})"
            " * 2 ** (attempt - 1))",
            "",
        ]
        time_ref = f"{rule_name}_runtime"
    else:
        time_ref = f"lambda wildcards, attempt: {plan.runtime_min} * 2 ** (attempt - 1)"
    lines += [
        f"rule {rule_name}:",
        "    # input, output and shell as in your workflow",
        f"    threads: {plan.cpus}",
        "    resources:",
        f"        mem_mb={mem_ref},",
        f"        runtime={time_ref},",
        "",
    ]
    return "\n".join(lines)


def snakemake_profile(
    plan: ResourcePlan,
    rule_name: str,
    partition: str | None = None,
    account: str | None = None,
    retries: int = 2,
) -> str:
    """Return a ``config.yaml`` profile for snakemake-executor-plugin-slurm.

    Fixed values for the calibrated rule go under ``set-resources``; rules
    that define resource functions keep them unless this block overrides.
    """
    lines = [
        "executor: slurm",
        "jobs: 100",
        f"retries: {retries}",
        "latency-wait: 60",
        "default-resources:",
    ]
    if partition:
        lines.append(f"  slurm_partition: {partition}")
    if account:
        lines.append(f"  slurm_account: {account}")
    lines += [
        "  mem_mb: 2000",
        "  runtime: 30",
        "set-threads:",
        f"  {rule_name}: {plan.cpus}",
        "set-resources:",
        f"  {rule_name}:",
        f"    mem_mb: {plan.mem_mb}",
        f"    runtime: {plan.runtime_min}",
        f"    cpus_per_task: {plan.cpus}",
    ]
    return "\n".join(lines) + "\n"


def _nf_memory(num_bytes: int) -> str:
    if num_bytes % (1024 * MIB) == 0:
        return f"{num_bytes // (1024 * MIB)}.GB"
    return f"{math.ceil(num_bytes / MIB)}.MB"


def _nf_time(seconds: int) -> str:
    minutes = math.ceil(seconds / 60)
    if minutes % 60 == 0:
        return f"{minutes // 60}.h"
    return f"{minutes}.min"


def nextflow_config(plan: ResourcePlan, process_name: str, max_retries: int = 2) -> str:
    """Return a ``process`` block for ``nextflow.config`` with retry on kill signals.

    Exit statuses 137 (SIGKILL, the usual out of memory kill) and 140 and
    143 (signals sent at a time limit, depending on the site) trigger a retry
    with the request multiplied by ``task.attempt``.
    """
    return (
        "process {\n"
        f"    withName: '{process_name}' {{\n"
        f"        cpus          = {plan.cpus}\n"
        f"        memory        = {{ {_nf_memory(plan.mem_bytes)} * task.attempt }}\n"
        f"        time          = {{ {_nf_time(plan.time_s)} * task.attempt }}\n"
        "        errorStrategy = { task.exitStatus in [137, 140, 143] ? 'retry' : 'terminate' }\n"
        f"        maxRetries    = {max_retries}\n"
        "    }\n"
        "}\n"
    )


def nextflow_directives(
    plan: ResourcePlan,
    mem_fit: Fit | None,
    time_fit: Fit | None,
    x_full: float,
    input_var: str = "input_file",
) -> str:
    """Return process directives whose memory and time scale with ``input_var.size()``.

    Paste these inside the process definition, where ``input_var`` is the
    name of the path input. ``size()`` is in bytes; the formulas take MiB.
    """
    lines = [f"cpus {plan.cpus}"]
    size = f"({input_var}.size() / 1048576.0)"
    if mem_fit is not None:
        mem = rescale(mem_fit, MIB, MIB, safety_factor(mem_fit, x_full, plan.mem_headroom))
        lines.append(
            f"memory {{ 1.MB * Math.max(256L, Math.ceil(({mem.groovy(size)}) * {mem.safety:.4f})"
            " as long) * task.attempt }"
        )
    else:
        lines.append(f"memory {{ {_nf_memory(plan.mem_bytes)} * task.attempt }}")
    if time_fit is not None:
        tm = rescale(time_fit, MIB, 60.0, safety_factor(time_fit, x_full, plan.time_headroom))
        lines.append(
            f"time {{ 1.min * Math.max(5L, Math.ceil(({tm.groovy(size)}) * {tm.safety:.4f})"
            " as long) * task.attempt }"
        )
    else:
        lines.append(f"time {{ {_nf_time(plan.time_s)} * task.attempt }}")
    lines.append("errorStrategy { task.exitStatus in [137, 140, 143] ? 'retry' : 'terminate' }")
    lines.append("maxRetries 2")
    return "\n".join(lines) + "\n"
