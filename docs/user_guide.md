# User guide

## Install

```bash
pip install pipeprof      # once published; until then: pip install -e .
pipeprof --version
```

`calibrate` on BAM or CRAM needs `samtools` on `PATH`; indexed VCF input
uses `tabix` to index the subsamples (see below).

## Before: `pipeprof calibrate`

```bash
pipeprof calibrate INPUT [options] -- COMMAND ...
```

Placeholders in COMMAND: `{input}` (subsample path), `{output}` (scratch
output with the input's extension, or `--output-suffix`), `{threads}`,
`{workdir}`. Quote them so the shell does not expand the braces.

Main options:

| Option | Default | Meaning |
|---|---|---|
| `--fractions` | `0.01,0.02,0.05,0.1,0.2` | subsample sizes; cross any buffer cap the tool has |
| `--repeats` | 1 | runs per size; use 3 when runs are under a second |
| `--threads` | 1 | value for `{threads}` and the minimum CPU request |
| `--shell` | off | run COMMAND through `/bin/sh -c`, for pipes |
| `--x` | `bytes` | size measure: file bytes or `records` |
| `--mem-headroom`, `--time-headroom` | 1.2, 1.5 | factors on the 95% upper bound |
| `--validate-full` | off | also run the full input and compare |
| `--job-name`, `--partition`, `--account` | | used in the snippets |

Subsamples are nested: every record of the 1 percent file is also in the
2 percent file. VCF headers go to every subsample. When the input VCF has a
`.tbi` or `.csi` index, each subsample is indexed too, because VCFs without
`##contig` lines (ClinVar is one) need the index for `bcftools sort`.

Outputs in `--outdir`: `calibration.json` (every run, both fits, the
cross-validation scores, the plan and provenance), `sbatch_header.sh`,
`snakemake_resources.smk`, `snakemake_slurm_profile.yaml`,
`nextflow_resources.config`, `nextflow_directives.nf` and `pipeprof.log.jsonl`.

## During: `pipeprof watch` and `pipeprof tail`

```bash
pipeprof watch --out w.jsonl --interval 5 --mem-limit 8G --cpus 4 -- my_tool args   # start and watch
pipeprof watch --out w.jsonl --pid 12345 --mem-limit 8G                              # attach to a process
pipeprof watch --out w.jsonl --slurm-job 4242 --interval 30                          # sstat on a login node
pipeprof tail w.jsonl            # live view; --once prints and exits
```

For a Slurm job the memory limit and CPU count come from `sacct` unless
given. Alerts: `memory_near_limit` (RSS at 90% of the limit), `cpu_underuse`
(below 50% of the allocated CPUs over `--window` seconds), `stalled` (no CPU
or I/O progress for `--stall` seconds; on macOS only CPU, since psutil has no
per process I/O counters there). Each alert is written when it starts and
when it clears.

## After: `pipeprof report`

```bash
pipeprof report --show-sacct                       # the sacct command to run
sacct --parsable2 --format=JobID,JobIDRaw,JobName,Partition,State,ExitCode,Submit,Start,End,Elapsed,Timelimit,AllocCPUS,NNodes,ReqMem,TotalCPU,MaxRSS,NodeList --starttime=2026-10-01 > sacct.txt
pipeprof report --sacct sacct.txt --outdir report
pipeprof report --from-slurm 4242,4243 --outdir report        # calls sacct itself
pipeprof report --nextflow-trace results/pipeline_info/trace.txt --outdir report
pipeprof report --snakemake-benchmarks benchmarks/ --snakemake-log .snakemake/log/LATEST.snakemake.log
```

Definitions:

- CPU efficiency = CPU time / (elapsed x allocated CPUs). Flagged below 50%
  for completed jobs longer than a minute.
- Memory efficiency = peak RSS / requested memory. Flagged below 50%.
  For sacct, peak RSS is the largest step MaxRSS, which Slurm records per
  task; for multi-task MPI steps this underestimates the job total.
- Time use = elapsed / time limit. Flagged below 25%, since an oversized limit
  keeps the job out of backfill gaps.
- Queue wait = Start - Submit.

Recommendations per job name, Nextflow process or Snakemake rule:

- memory: largest peak RSS of completed tasks x 1.2, rounded up; after an
  out of memory failure, at least 1.5 x the failed request;
- time: longest completed elapsed x 1.5, rounded up; after a timeout, at least
  2 x the failed limit;
- CPUs: the busiest task's CPU time / elapsed, rounded up.

A resource gets no recommendation when none of the completed records carries
its measurement. The report says so instead of guessing.

Failure classes: OUT_OF_MEMORY, TIMEOUT, NODE_FAIL, FAILED, CANCELLED,
PREEMPTED and BOOT_FAIL from sacct. Nextflow only reports FAILED with an exit
status; `pipeprof` marks a failure as out of memory only for exit 137 with peak RSS
at 90% or more of the request, and as a timeout for exit 137, 140 or 143 at 95%
or more of the time limit. Both are labelled "(inferred)".

## Logging library

```python
from pipeprof.logkit import RunLogger, read_jsonl, step_durations
from pipeprof.provenance import capture

log = RunLogger("align", jsonl_path="logs/align.jsonl")  # PIPEPROF_RUN_ID shares an ID
log.event("provenance", **capture(inputs=["reads.fq.gz"]).to_dict())
with log.step("bwa_mem", sample="S1") as fields:
    ...
    fields["reads"] = 1204311
log.close()
print(step_durations(read_jsonl("logs/align.jsonl")))
```

Provenance records only allow listed environment variables (`SLURM_*`,
`NXF_*`, `SNAKEMAKE_*`, conda and container names) and drops any whose name
contains TOKEN, SECRET, PASSWORD, KEY, CREDENTIAL or AUTH.
