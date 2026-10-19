# Case study: one change to a Nextflow and bcftools run, measured

A small workflow was run, analysed with `pipeprof analyze`, changed once where the
analysis pointed, and run again. Every number here comes from those runs on
2026-10-19. The workflow, the data generator and the helper script are in
[examples/nextflow_vcf_chain](https://github.com/Daneshpajouh/pipeprof/tree/main/examples/nextflow_vcf_chain).

## Setup

- Machine: macOS 27.0.1, Apple M3 Ultra, 28 CPUs, 96 GiB. Other work was
  running: the 1-minute load average was 21.4 to 27.5 during the measured
  runs.
- Tools: Nextflow 26.10.2, bcftools 1.23.1, local executor limited to 4
  CPUs, 1 CPU per task.
- Data: `make_vcf.py --records 4000000 --seed 7`, a synthetic single-sample
  VCF with 8 contigs of unequal size (the largest holds a quarter of the
  records), positions unsorted, bgzip-compressed: 47,012,837 bytes.
- Workflow: for each contig, `SPLIT` (`bcftools view -t`), `SORT`
  (`bcftools sort`) and `STATS` (`bcftools stats`); then `CONCAT`
  (`bcftools concat`) joins the sorted parts. 25 tasks.

Nextflow on macOS records times but writes `-` for `%cpu`, `rss`, `rchar`
and `wchar`, so the trace alone cannot say what a step is bound by. Each
command therefore runs under `/usr/bin/time -l`, and `collect_usage.sh`
turns those files into `StepUsage` lines with `pipeprof usage bsd-time`.

```bash
nextflow run main.nf --outdir results
./collect_usage.sh results > results/usage.jsonl
pipeprof analyze --nextflow-trace results/trace.txt --usage results/usage.jsonl --cpus 4 -o results/pipeprof
```

## What the analysis said

On a before run (wall time 10.5 s from first submit to last completion):

| Step | On the critical path | Busy over all tasks | Label |
|---|---|---|---|
| SPLIT | 4.0 s | 16.6 s (8 tasks) | cpu-bound |
| CONCAT | 3.6 s | 3.6 s (1 task) | cpu-bound |
| SORT | 2.0 s | 7.7 s (8 tasks) | cpu-bound |
| STATS | 0.5 s | 1.4 s (8 tasks) | unknown |

Parallel efficiency averaged 69% of 4 CPUs, with 3.4 s below 50% (the
`CONCAT` tail, one task on four CPUs). The recommendation: work on `SPLIT`
first; a 2x speed-up saves about 2.0 s.

`SPLIT` is CPU-bound on one thread, and each of its 8 tasks reads the whole
input. Its output is compressed (`-Oz`) only for `SORT` to decompress it
again. The one change: `SPLIT` writes uncompressed BCF (`-Ou`).

```diff
-    tuple val(contig), path("${contig}.vcf.gz"), emit: part
+    tuple val(contig), path("${contig}.bcf"), emit: part
-  /usr/bin/time -l bcftools view -t ${contig} -Oz -o ${contig}.vcf.gz ${vcf} 2> split_${contig}.time
+  /usr/bin/time -l bcftools view -t ${contig} -Ou -o ${contig}.bcf ${vcf} 2> split_${contig}.time
```

## Measured difference

Six before and after pairs, run alternately so both versions met the same
machine load. Wall time is first submit to last completion in the trace; CPU
is user plus system time from `time -l`, summed over the step's tasks.

| | Before, median (range) | After, median (range) |
|---|---|---|
| Wall time | 11.48 s (10.52 to 13.24) | 9.90 s (9.32 to 10.65) |
| SPLIT CPU | 15.61 s (15.35 to 16.16) | 11.89 s (11.78 to 12.16) |
| SORT CPU | 7.44 s (7.22 to 7.65) | 5.94 s (5.80 to 6.00) |
| CONCAT CPU | 3.49 s (3.43 to 3.53) | 3.56 s (3.43 to 3.70) |

The after run was faster in 5 of the 6 pairs; the median paired difference
was 1.68 s (from 0.13 s slower to 3.89 s faster), about 15% of the before
median. The predicted saving for a 2x `SPLIT` was about 2 s; the change
cut `SPLIT` CPU by 24%, not by half, and also removed decompression work from
`SORT`. The joined output is the same: 4,000,000 records, and the SHA-256 of
`bcftools view -H joined.vcf.gz` matched between the last before and after
runs.

After the change, `pipeprof analyze` names `CONCAT` first: 3.7 s of a 9.7 s
critical path, one CPU-bound task with three CPUs idle. That is the next
thing to try (`bcftools concat --threads`), and it is not part of this
study.

## Limits

- Five earlier before runs at a higher load (load average 36 to 53) took
  11.7 to 18.0 s; the first, with a cold file cache, was the slowest. They
  are left out of the comparison, as are five unpaired after runs (9.18 to
  10.55 s at load 20 to 22): the two sets met different loads. The paired
  runs are the measurement.
- The critical path is inferred (each task's predecessor is the last task
  that finished before it started); Nextflow's trace has no dependency
  graph. On this run it mixes contigs, which is why step totals are given
  beside it.
- The data are synthetic; a real VCF with more INFO fields and samples
  spends a different share of time in compression.
