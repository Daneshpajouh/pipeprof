# Changelog

Versions follow [Semantic Versioning](https://semver.org/).

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

- `hjp calibrate`: nested subsampling of VCF, BAM/CRAM, FASTQ and text;
  process tree measurement; linear and power law fits chosen by forward
  extrapolation cross-validation; prediction intervals; `#SBATCH`, Snakemake
  (resource functions and Slurm executor profile) and Nextflow snippets;
  `--validate-full` against a full run.
- `hjp watch` and `hjp tail`: local process tree or Slurm job (`sstat`)
  sampling to JSON lines with memory, CPU underuse and stall alerts.
- `hjp report`: sacct (Slurm 20.11 and 21.08+ ReqMem formats, steps, arrays),
  Nextflow trace (default and raw), Snakemake benchmarks and run log, and
  `hjp watch` logs; Markdown, HTML and JSON output.
- `logkit` and `provenance`: JSON lines logging with a shared run ID
  (`HJP_RUN_ID`), step timing, allow listed environment capture, SHA-256 of
  inputs.
- Subsamples of indexed VCFs are indexed with tabix (ClinVar has no
  `##contig` lines).
- sacct rows with 0 allocated CPUs (filetxt accounting) count as missing CPU
  data, not zero use.
- `tool_version` tolerates non UTF-8 bytes in `--version` output (samtools
  1.21 on Debian).
