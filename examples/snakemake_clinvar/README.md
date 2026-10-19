# Example: Snakemake workflow on ClinVar

Three rules per chromosome: `extract_chrom` (indexed region fetch),
`sort_vcf`, `stats`. Each writes a benchmark file. `sort_vcf` takes its
memory and runtime from `hjp_resources.smk`, which is the
`snakemake_resources.smk` that `hjp calibrate` wrote for run A in
`docs/results.md`.

```bash
cd examples/snakemake_clinvar
snakemake --cores 4 --scheduler greedy --benchmark-extended \
  --config clinvar_vcf=/path/to/clinvar.vcf.gz
hjp report --snakemake-benchmarks benchmarks \
  --snakemake-log .snakemake/log/$(ls .snakemake/log | tail -1) --outdir hjp_report
```

`--scheduler greedy` is there because the default scheduler hung on the
machine this was developed on (see `docs/operations/`). On macOS Snakemake
9.27.0 writes `NA` for memory in the benchmark files; the report then gives
time recommendations only.
