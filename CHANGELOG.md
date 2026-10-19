# Changelog

Versions follow [Semantic Versioning](https://semver.org/).

## 1.1.0 (2026-10-19)

- First release under the name pipeprof (was hpc-job-profiler): package,
  import and command are `pipeprof`.
- `pipeprof analyze`: whole-run bottleneck analysis over a Nextflow trace,
  Snakemake benchmarks plus run log (a benchmark's repeats are one job, so
  one task), or a plain TSV. Critical path (declared
  or inferred dependencies), each step's share of wall time, parallel
  efficiency over time, idle gaps, queue wait and retries.
- Per-step boundness labels (cpu-bound, io-bound, memory-bound, mostly
  waiting) from CPU use, I/O rate and peak RSS against their requests, each
  with its evidence and named thresholds.
- Recommendations with numbers: which critical-path step to speed up first
  and the expected wall-time saving; where more threads will not help;
  memory requests that are too large or too small.
- `pipeprof.timeline`: a run timeline (`Task`) built from a Nextflow
  trace, Snakemake benchmarks and run log, or a plain TSV, with an optional
  `StepUsage` per task for CPU, memory and I/O metrics.
- Readers for standard tools' output, each returning a `StepUsage`: GNU
  `/usr/bin/time -v`, macOS `/usr/bin/time -l`, psrecord logs and Slurm
  `seff` (with or without the per-node or per-core request after the memory
  total; a memory line it cannot read is noted, not dropped); py-spy
  speedscope JSON with top functions by self and total time.
- `pipeprof usage TOOL FILE --step NAME`: one `StepUsage` JSON line for
  `pipeprof analyze --usage`, which now applies to every source and fills only
  the metrics the runner left empty (Nextflow on macOS records none).
- Case study: a Nextflow and bcftools run on a synthetic VCF, analysed,
  changed once and rerun (docs/case_study_nextflow_vcf_chain.md).
- `pipeprof report` and `pipeprof analyze` check every input path first and exit 2
  with one line naming a missing file, instead of a `FileNotFoundError`
  traceback. Found on a cluster with a mistyped `--watch-log`.
- `pipeprof calibrate`: peak RSS that stays flat across the small runs is no
  longer read as no growth. All sizes flat: the upper bound is the largest
  run scaled in proportion to input, with a warning. Flat then rising: the
  model is fitted from the hinge on. Found on a cluster, where a flat series
  predicted 69.9 MB (interval 39.6 to 100.2 MB) for a run that used 176.6 MB.
  The comment in the emitted sbatch, Snakemake and Nextflow snippets states
  which of the two applied and why, and after a hinge the emitted memory
  formulas never ask for less than the flat level at any input size. Rule and
  threshold in docs/algorithm.md.
- `pipeprof calibrate`: when any run kept under 30 percent of one core busy
  (wall above 3.3 times CPU; the "mostly waiting" threshold of
  `pipeprof analyze`), wall time is not fitted. The output says so, gives the
  observed wall range, and takes time from the CPU model divided by the
  lowest cores-busy ratio. Found on Lustre, where the fitted interval was
  0.06 to 1,146 s and one sort took 15.15 s on one node and 4.27 s on another.
- Measurement falls back to `resource.getrusage(RUSAGE_CHILDREN)`
  differences where `os.wait4` is missing; neither path needs
  `/usr/bin/time`, which compute nodes on Narval lack.
- docs/clusters.md: installing on Alliance clusters from the wheelhouse plus
  the `pipeprof` wheel, with psutil pinned below 7.2 (7.2.2+computecanada aborts
  at exit after `children(recursive=True)` then `cpu_count()`; 7.0.0 is
  clean). Runbook entry for that abort. The case study gains "On a cluster":
  watch overhead 0.29 s on a 4.3 s job, peak RSS within 0.3 percent of
  rusage, on Narval.
- `pipeprof calibrate`: a memory plateau now ends on a whole size. With
  repeats, a flat run that broke between two runs of one size left a tail
  of one size and crashed the fit.
- `pipeprof calibrate`: when wall time is mostly waiting, the Snakemake and
  Nextflow runtime formulas now scale the derived wall time (CPU model over
  the lowest cores-busy ratio), not CPU seconds, so they agree with the
  fixed sbatch time.
- `pipeprof calibrate`: the Snakemake and Nextflow memory and time formulas
  take their safety factor from the plan's upper bound, so a flat or hinged
  memory series asks for at least the fixed request at full size.
- `pipeprof calibrate`: with fewer than three runs, or when every fit falls
  and the largest measured value is used, there is no interval. The table,
  the JSON and every emitted request now say "no interval" and why, instead
  of a zero width interval that read as certainty, and the request is sized
  from the larger of the point prediction and the largest run scaled in
  proportion to input.
- `pipeprof calibrate`: runs under 1 percent of one core busy (pure waits,
  with at most start-up CPU) are left out of the cores-busy ratio and of the
  CPU fit, so they no longer inflate the wall estimate a million times or
  pull the CPU model; the estimate is at least their longest wall, or the
  observed wall range when every run is a pure wait. The derived wall bound
  never exceeds the largest wall of any run scaled in proportion to input.
  The note says so.
- `pipeprof calibrate`: the fit step in the run log records the model kind
  again under "model" (linear, power, largest_measured) and the basis under
  "basis".
- `pipeprof usage gnu-time`: a report with both "Command terminated by
  signal N" and an "Exit status" line now reads as killed by the signal
  (exit code -N), instead of the exit status hiding it.
- `pipeprof usage seff`: `cpu_percent` is now percent of one core
  (CPU Efficiency times allocated cores), as for every other reader; the
  raw efficiency stays in the notes. With no core count it is left unset.
- `pipeprof analyze`: the I/O-bound rule is stated the same way in code,
  comment and docs/algorithm.md: low CPU at 20 MiB/s or more is I/O-bound;
  low CPU moving less is mostly waiting, and its evidence now says the time
  is not spent moving data. The rule itself is unchanged.
- `pipeprof analyze`: "mostly waiting" now means under 0.3 of one core busy,
  as in `pipeprof calibrate`, not 0.3 of the requested cores. A
  single-threaded tool given 4 CPUs at full use is CPU-bound on one core,
  and the thread advice says more threads will not help because it uses one.
  Evidence lines give cores busy and their share of the request. Label and
  advice read cores busy, the request and the cores used from one helper
  over the same tasks, so they state the same numbers.
- `pipeprof analyze` and `pipeprof report` on standard Snakemake benchmarks
  (no `--benchmark-extended`): each file is matched to its log job by the
  `benchmark:` path, so log times and requests attach to the right job. Before,
  the file stem was taken as the job ID: times never attached, or attached to
  another rule's job when stems were numbers. A file moved away from the
  logged path is matched by its stem within the rule only when one file and
  one job share that stem; otherwise it stays unmatched, with a note.
- `pipeprof analyze` on a Nextflow `-resume` trace: CACHED tasks are no
  longer read as completed now. They are left out of the critical path, idle
  time, efficiency, labels and advice, and counted on their own ("N cached
  task(s) left out"). A day-old cached task had stretched the critical path
  to 86,430 s for a 30 s run.
- `pipeprof report` on a Nextflow `-resume` trace: CACHED tasks were counted
  as completed, so an earlier run's usage set this run's flags and requests.
  They are now left out of the totals, flags and recommendations and counted
  on their own, as in `pipeprof analyze`; `report.json` carries
  `cached_tasks`.
- `parse_speedscope` reads every profile of a multi-thread py-spy file, so
  `top_functions` ranks over all threads; `profile_index` reads one. It used
  to read only the profile the viewer opens first.
- `pipeprof analyze --usage`: a CPU share derived from a tool's user plus
  system time is now over the task's wall time, as documented, and over the
  tool's own wall time only when the task has no start and end. A plain TSV
  goes through the same step, so the derived share applies to it too.
- `pipeprof usage` with a missing file exits 2 with one line naming it, as
  `report` and `analyze` do, instead of a `FileNotFoundError` traceback.
- docs/case_study_nextflow_vcf_chain.md: the STATS row now reads cpu-bound,
  which is what `pipeprof analyze` gives on the recorded run (0.86 cores
  busy); the table said unknown. The other numbers still hold.

## 1.0.1 (2026-10-19)

- Tests no longer need git on the host: the git checks use a fake or broken
  `git` on `PATH` and skip the real-repository case when git is absent.
  Found by a run in a `python:3.12-slim` container without git, where two
  tests failed. The library already returned `commit: None` there.
- `provenance.git_state` logs at debug level when git is missing or fails.
- CPU request from the busiest measured run (CPU/wall), capped at
  `--threads`, instead of dividing two separately extrapolated predictions,
  which asked for 4 CPUs for a single-threaded `bcftools sort`.
- Fits that decrease with input size are rejected; the full-input prediction
  is never below the largest measured value; one warm-up run before measuring.
- Report wording: "1 completed task" with a note that it is a single run.
- Classifier "Development Status :: 4 - Beta".
- README quick start runs in under a minute without a download.

## 1.0.0 (2026-10-19)

First release.

- `pipeprof calibrate`: nested subsampling of VCF, BAM/CRAM, FASTQ and text;
  process tree measurement; linear and power law fits chosen by forward
  extrapolation cross-validation; prediction intervals; `#SBATCH`, Snakemake
  (resource functions and Slurm executor profile) and Nextflow snippets;
  `--validate-full` against a full run.
- `pipeprof watch` and `pipeprof tail`: local process tree or Slurm job (`sstat`)
  sampling to JSON lines with memory, CPU underuse and stall alerts.
- `pipeprof report`: sacct (Slurm 20.11 and 21.08+ ReqMem formats, steps, arrays),
  Nextflow trace (default and raw), Snakemake benchmarks and run log, and
  `pipeprof watch` logs; Markdown, HTML and JSON output.
- `logkit` and `provenance`: JSON lines logging with a shared run ID
  (`PIPEPROF_RUN_ID`), step timing, allow listed environment capture, SHA-256 of
  inputs.
- Subsamples of indexed VCFs are indexed with tabix (ClinVar has no
  `##contig` lines).
- sacct rows with 0 allocated CPUs (filetxt accounting) count as missing CPU
  data, not zero use.
- `tool_version` tolerates non UTF-8 bytes in `--version` output (samtools
  1.21 on Debian).
