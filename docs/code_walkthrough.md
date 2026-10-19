# Code walk-through

About 2,200 statements in `src/pipeprof`. Suggested reading order,
with the decisions a reviewer is likely to question.

1. `units.py`. Every time and memory string from Slurm, Nextflow and
   Snakemake goes through here. Memory suffixes are binary (1K = 1024), as in
   Slurm and Nextflow. `parse_reqmem` handles both sacct formats: before 21.08
   `4000Mc` meant per CPU and `16000Mn` per node; from 21.08 the value is for
   the whole job.

2. `measure.py`. `run_measured` starts the command with `Popen` and reaps it
   with `os.wait4` instead of `Popen.wait`, because `wait4` returns the
   kernel's resource usage for that child and its waited-for descendants.
   A sampling thread sums RSS over the psutil process tree. The two are
   combined because each misses something: `ru_maxrss` is the largest single
   process, sampling misses spikes shorter than the interval.

3. `subsample.py`. One pass over text input, one uniform draw per record,
   nested outputs. BAM goes through `samtools view --subsample` with a fixed
   seed. Subsamples of an indexed VCF are indexed with tabix; this was added
   after `bcftools sort` failed on ClinVar subsamples, which have no
   `##contig` header lines (postmortem in `operations/`).

4. `fit.py`. Two OLS models, forward extrapolation CV, t-based prediction
   intervals. No NumPy or SciPy: the fits are two parameter closed forms and
   the t quantile is 60 lines checked against tables, so the package installs
   with three pure dependencies (psutil, typer, rich). The cost is that more
   complex models (segmented fits for buffer caps) would need NumPy.

5. `emit.py`. Requests and snippets. Snakemake `mem_mb` is written as
   `ceil(bytes / 10^6)`, which is at least the MiB value Slurm reads from
   `--mem`, so the request is never short under either reading.

6. `calibrate.py`. Orchestration only: subsample, measure, fit, plan,
   snippets, optional full validation, provenance. Each stage is a
   `log.step`, so the JSON log has timings for free.

7. `slurm.py`, `records.py`, `report.py`. `records.py` turns each source
   into `TaskRecord` and keeps unknown values as `None`. `report.py` never
   fills a gap: a group whose completed tasks lack peak memory gets no memory
   recommendation. sacct job steps are merged into their job: state and
   requests from the allocation line, MaxRSS from the steps (Slurm records
   it per step), and an OUT_OF_MEMORY step upgrades a FAILED job.

8. `watch.py`, `tail.py`. Alerts are edge triggered (start and clear) so a
   long job does not write the same alert every sample. Functions take a
   `sleep` argument so tests run without waiting.

9. `logkit.py`, `provenance.py`. Built on the standard `logging` module so a
   pipeline that already logs can add the JSON lines handler. The provenance
   environment capture is an allow list, so a token in the environment cannot
   reach a log file.

10. `cli.py`. Typer commands; validation of user input happens here and in
    the file readers, not in the internal functions.

Trade-offs not taken:

- No database for watch samples: JSON lines can be appended by a process
  that may be killed at any moment, and read with `jq` on a login node.
- No pandas: the tables are tens to thousands of rows.
- `pipeprof report` reads files rather than the Slurm REST API, because `sacct`
  output works on every cluster without extra credentials.
