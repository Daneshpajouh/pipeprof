# Postmortem: example Snakemake workflow hung, then a job failed inside benchmarking

- Date: 2026-10-19, 11:18 to 11:31 Pacific
- Impact: the example workflow for this repository produced no benchmark
  memory data and one failed job; about 13 minutes lost
- Detected by: the run did not finish; `ps` showed Snakemake at 0.2 percent CPU
- Environment: Snakemake 9.27.0, snakemake-executor-plugin-slurm 2.8.0
  installed, psutil 7.2.2, Python 3.13.9, macOS 27.0.1 (Apple M3 Ultra)

## What happened

- 11:18:00 `snakemake --cores 4 --benchmark-extended` started on six
  chromosomes. Two `extract_chrom` jobs wrote their outputs at 11:18.
- 11:18 to 11:28 nothing else ran. Snakemake sat at 0.2 percent CPU with no
  child processes; the last log line was "Select jobs to execute...".
- 11:28 the run was killed. A one-rule workflow with and without a
  `benchmark:` directive both finished in about 1 second, so benchmarking
  alone was not the cause.
- 11:30 the same chain for one chromosome finished in 1 second with
  `--scheduler greedy`.
- 11:30:43 the full run with `--scheduler greedy` started; 17 of its 18 jobs completed. The
  `stats` job for chrX failed with
  `RuntimeError: mach_vm_region(VM_REGION_TOP_INFO) syscall failed`, raised
  from psutil's `memory_full_info` inside Snakemake's benchmark thread. The
  other 17 benchmark files have `NA` in every memory and CPU column, after the
  warning "unable to collect cpu and memory benchmark statistics".

## Cause

Shown: the hang happened with the default scheduler and not with
`--scheduler greedy`, on the same inputs. The single-job workflow, which
needs no scheduling decision, never hung. Suspected, not confirmed: the ILP
scheduler's solver call stalled on this machine.

Shown: on macOS, Snakemake's benchmark code calls psutil `memory_full_info`,
which failed for these processes; one failure was raised as a job error,
the rest were swallowed and written as `NA`.

## Fix

- The example README runs Snakemake with `--scheduler greedy`.
- `pipeprof report` treats `NA` as missing, says "no peak RSS recorded" and makes
  no memory recommendation from those rows. This is tested with the real
  files from this run (`tests/data/snakemake_macos_run/`).
- `pipeprof`'s own sampler uses `memory_info` (RSS), which worked for the same
  processes; `pipeprof calibrate` and `pipeprof watch` gave memory numbers on this Mac.

## Detection

`pipeprof watch --pid <snakemake pid> --stall 120` would have raised `stalled`
after 2 minutes instead of 10. The missing memory is now visible in every
report instead of silently producing a time-only recommendation.

## Follow-up

- Run the same workflow on Linux to confirm the benchmark columns fill there
  (roadmap: Linux example run).
- Check whether the hang reproduces with a fresh environment before reporting
  it upstream.
