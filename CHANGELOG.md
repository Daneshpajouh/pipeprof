# Changelog

Versions follow [Semantic Versioning](https://semver.org/).

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
- `tool_version` tolerates non UTF-8 bytes in `--version` output (samtools
  1.21 on Debian).
