# Roadmap

Next milestones, in order. Each is something a run in this repository showed
is needed.

1. Calibrated intervals. The 95 percent interval covered 5 of 12 held-out
   values ([results](docs/results.md)). Widen it with the forward
   cross-validation errors (a split conformal step on the log ratios) and
   report coverage again on the same four runs. Done when coverage on those
   runs is at least 10 of 12 without making the requests larger than 2x the
   actual use.
2. Segmented model for buffer caps. Run A over-predicted memory 3.7x
   because `bcftools sort -m 512M` stops growing at the cap. Add a "linear
   then flat" model chosen by the same CV, and let `--mem-cap` declare a known
   cap. Done when run A's memory error is below 1.5x with default fractions.
3. Real Slurm end-to-end test. Run `watch --slurm-job` and
   `report --from-slurm` against a single node Slurm container in CI and
   replace the hand-written sacct fixtures with recorded ones for two Slurm
   versions.
4. Linux run of the Snakemake example. Confirm benchmark memory columns
   fill on Linux and add that report to the case study.
5. Index BAM subsamples when the input has a `.bai`, for tools that need
   region queries (same failure class as the ClinVar postmortem).
6. Nextflow example. A small real pipeline run with `-with-trace` (default
   fields) feeding `hjp report`, and a check that the emitted
   `nextflow_directives.nf` closures parse in Nextflow.
7. conda-forge. Submit `recipe/meta.yaml` to staged-recipes after the
   first PyPI release.
