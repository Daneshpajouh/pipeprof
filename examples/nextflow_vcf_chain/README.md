# Nextflow and bcftools chain on a synthetic VCF

The workflow behind [the case study](../../docs/case_study_nextflow_vcf_chain.md).
Needs Nextflow, bcftools and bgzip; `make_vcf.py` needs only Python.

```bash
python3 make_vcf.py --records 4000000 --seed 7 | bgzip -@4 > raw.vcf.gz
nextflow run main.nf --outdir results
./collect_usage.sh results > results/usage.jsonl      # macOS: reads /usr/bin/time -l files
pipeprof analyze --nextflow-trace results/trace.txt --usage results/usage.jsonl --cpus 4 -o results/pipeprof
```

`main.nf` is the version after the case study's change (`SPLIT` writes
uncompressed BCF). For the before version, change `SPLIT` back to
`-Oz -o ${contig}.vcf.gz` and its output to `${contig}.vcf.gz`.

`/usr/bin/time -l` is the macOS form. On Linux use `/usr/bin/time -v` and
`pipeprof usage gnu-time` in `collect_usage.sh`.
