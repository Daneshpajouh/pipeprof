from __future__ import annotations

import math
from typing import Any

import pytest

from hpc_job_profiler.emit import (
    make_plan,
    nextflow_config,
    nextflow_directives,
    rescale,
    safety_factor,
    sbatch_header,
    snakemake_profile,
    snakemake_rule,
)
from hpc_job_profiler.fit import fit_linear, fit_power
from hpc_job_profiler.units import GIB, MIB


def plan(**kw: Any) -> Any:
    args: dict[str, Any] = {
        "cpus": 2,
        "peak_rss": 1.0 * GIB,
        "peak_rss_upper": 1.1 * GIB,
        "wall_s": 600,
        "wall_upper_s": 700,
    }
    args.update(kw)
    return make_plan(**args)


def test_plan_rounds_up_with_headroom() -> None:
    p = plan()
    assert p.mem_bytes == 2 * GIB  # 1.1 GiB x 1.2 rounds up to 2 GiB
    assert p.time_s == 1200  # 700 s x 1.5 = 17.5 min, rounded to 20
    assert p.slurm_mem == "2G" and p.mem_mb == math.ceil(2 * GIB / 1e6) and p.runtime_min == 20
    assert plan(peak_rss=300 * MIB, peak_rss_upper=310 * MIB).slurm_mem == "512M"
    assert p.to_dict()["slurm_mem"] == "2G"
    with pytest.raises(ValueError, match="headroom"):
        plan(mem_headroom=0.9)


def test_sbatch_header() -> None:
    text = sbatch_header(plan(), "sort", partition="cpu", account="def-lab", note="a\nb")
    assert "#SBATCH --mem=2G" in text and "#SBATCH --time=00:20:00" in text
    assert "#SBATCH --partition=cpu" in text and "#SBATCH --account=def-lab" in text
    assert "# b" in text and text.endswith("set -euo pipefail\n")


def test_snakemake_rule_functions_run() -> None:
    xs = [10 * MIB, 20 * MIB, 40 * MIB, 80 * MIB]
    mem_fit = fit_linear(xs, [50 * MIB + 3 * x for x in xs])
    time_fit = fit_power(xs, [0.5 * (x / MIB) ** 1.1 for x in xs])
    text = snakemake_rule(plan(), "sort_vcf", mem_fit, time_fit, 200 * MIB, note="calibrated")
    namespace: dict[str, Any] = {}
    exec(text.split("rule sort_vcf:")[0], namespace)

    class Input:
        size_mb = 200.0

    mem1 = namespace["sort_vcf_mem_mb"](None, Input(), 1)
    mem2 = namespace["sort_vcf_mem_mb"](None, Input(), 2)
    expected_bytes = 50 * MIB + 3 * 200 * MIB
    assert mem1 >= expected_bytes / 1e6 * 1.2
    assert mem2 == 2 * mem1
    assert namespace["sort_vcf_runtime"](None, Input(), 1) >= 5
    assert "threads: 2" in text and "mem_mb=sort_vcf_mem_mb" in text
    fixed = snakemake_rule(plan(), "x", None, None, 1.0)
    assert "lambda wildcards, attempt:" in fixed


def test_rescale_and_safety() -> None:
    xs = [1.0, 2.0, 4.0, 8.0]
    lin = fit_linear(xs, [1 + 2 * x for x in xs])
    f = rescale(lin, 2.0, 1.0, 1.0)
    assert f.evaluate(3.0) == pytest.approx(lin.predict(6.0))
    pw = fit_power(xs, [3 * x**0.5 for x in xs])
    g = rescale(pw, 4.0, 2.0, 1.0)
    assert g.evaluate(2.0) == pytest.approx(pw.predict(8.0) / 2.0)
    assert "Math.pow" in g.groovy("s") and "**" in g.python("s")
    assert safety_factor(lin, 16.0, 1.2) == pytest.approx(1.2)


def test_profile_and_nextflow() -> None:
    profile = snakemake_profile(plan(), "sort_vcf", partition="cpu", account="def-lab")
    assert "executor: slurm" in profile and "slurm_partition: cpu" in profile
    assert "    mem_mb: " in profile and "cpus_per_task: 2" in profile
    cfg = nextflow_config(plan(), "SORT_VCF")
    assert "withName: 'SORT_VCF'" in cfg and "2.GB * task.attempt" in cfg
    assert "20.min * task.attempt" in cfg
    odd = make_plan(
        cpus=1, peak_rss=300 * MIB, peak_rss_upper=300 * MIB, wall_s=2400, wall_upper_s=2400
    )
    assert "512.MB" in nextflow_config(odd, "P") and "1.h" in nextflow_config(odd, "P")
    xs = [10 * MIB, 20 * MIB, 40 * MIB]
    mem_fit = fit_linear(xs, [100 * MIB + x for x in xs])
    d = nextflow_directives(plan(), mem_fit, mem_fit, 100 * MIB, input_var="vcf")
    assert "vcf.size()" in d and "task.attempt" in d
    assert "memory { 2.GB" in nextflow_directives(plan(), None, None, 1.0)
