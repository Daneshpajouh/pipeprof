# SOP: profiling a new pipeline step

Purpose: set the Slurm, Nextflow or Snakemake resource request for a new or
changed pipeline step from measurements, and record how it was set.

Applies to: any step that runs on one node (single or multi-threaded tools).
For MPI steps use `hjp report` on test jobs instead of `calibrate`.

## 1. Prepare

1. Pick one representative input, ideally the largest common one. Record its
   path and SHA-256 (`hjp provenance INPUT`).
2. Open an interactive session on the node type the step will use, for
   example `salloc --cpus-per-task=4 --mem=16G --time=1:00:00`.
3. Note any memory cap option the tool has (`-m`, `-S`, `--max-memory`).

## 2. Calibrate

```bash
hjp calibrate INPUT --outdir calib/STEP --job-name STEP --threads N \
  --fractions 0.01,0.02,0.05,0.1,0.2 -- TOOL ARGS '{input}' -o '{output}'
```

- If the tool has a memory cap, use fractions that put at least two
  subsamples above it, for example `0.05,0.1,0.2,0.3,0.5`.
- If each subsample takes under a second, add `--repeats 3`.
- If the step is cheap enough, add `--validate-full` once to check the
  prediction against a full run.

## 3. Review

Open `calib/STEP/calibration.json` and check:

- every run has `returncode` 0;
- `cv_scores`: below 0.1 means the chosen model extrapolated within about
  10 percent on the held back sizes; above 0.3 means the curve is not stable,
  so widen the fractions or add repeats;
- with `--validate-full`, `ratio_predicted_to_actual` for each metric and
  `plan_covers_actual`.

## 4. Apply

Copy the matching snippet into the pipeline:

- Slurm script: `sbatch_header.sh`;
- Snakemake: the functions in `snakemake_resources.smk`, and for the Slurm
  executor plugin the `set-resources` block of `snakemake_slurm_profile.yaml`;
- Nextflow: `nextflow_resources.config` (fixed, retry on 137/140/143) or
  `nextflow_directives.nf` (scales with input size).

Commit the snippet together with `calibration.json` so the request can be
traced back to the run that produced it.

## 5. After the first production batch

```bash
hjp report --from-slurm JOBIDS --outdir reports/STEP      # or --nextflow-trace / --snakemake-benchmarks
```

If memory efficiency is below 50 percent or time use below 25 percent across
the batch, lower the request to the report's recommendation. If any job ran
out of memory or time, apply the report's floor (1.5 x memory, 2 x time) and
open a ticket with the job IDs.

## 6. Record

Write in the step's README or the change ticket: input used, date, command,
chosen model and CV score, the request applied, and the report that confirmed
it.
