# Results from real runs

All numbers on this page come from runs on one machine: Apple M3 Ultra,
28 cores, 96 GiB, macOS 27.0.1, local disk (no cluster), on 2026-10-19
between 11:39 and 11:45 Pacific. Tools: bcftools 1.23.1, samtools 1.23.1,
htslib 1.23.1, Python 3.13.9. Inputs are listed with SHA-256 in
[data_sources.md](data_sources.md). These are local runs; a cluster node with
a shared file system will give different absolute times.

## Held-out validation of `hjp calibrate`

Each calibration was followed by a run on the full input that the fit never
saw (`--validate-full`). That full run is the held-out test.

Commands (`DATA_DIR` as filled by `scripts/fetch_data.sh`):

```bash
V=$DATA_DIR/clinvar/clinvar.vcf.gz
B=$DATA_DIR/hpc-job-profiler/bam/NA12878.chr20.lowcov.bam
# A: default fractions 1, 2, 5, 10, 20 percent
nice -n 10 hjp calibrate $V --outdir clinvar_sort --job-name sort_vcf --validate-full -- \
  bcftools sort -m 512M -T '{workdir}/sorttmp' -Oz -o '{output}' '{input}'
# B: wider fractions 5, 10, 20, 30, 50 percent
nice -n 10 hjp calibrate $V --outdir clinvar_sort_wide --job-name sort_vcf \
  --fractions 0.05,0.1,0.2,0.3,0.5 --validate-full -- \
  bcftools sort -m 512M -T '{workdir}/sorttmp' -Oz -o '{output}' '{input}'
# C: streaming filter
nice -n 10 hjp calibrate $V --outdir clinvar_filter --job-name filter_pathogenic --validate-full -- \
  bcftools view -i 'INFO/CLNSIG~"Pathogenic"' -Oz -o '{output}' '{input}'
# D: BAM sort, 2 threads
nice -n 10 hjp calibrate $B --outdir bam_sort --job-name sort_bam --threads 2 --validate-full -- \
  samtools sort -@ '{threads}' -m 512M -T '{workdir}/samsort' -o '{output}' '{input}'
```

Inputs: ClinVar GRCh38 VCF, 4,594,764 records, 200,437,060 bytes; NA12878
chromosome 20 low coverage BAM (1000 Genomes phase 3), 3,245,545 alignments,
310,927,269 bytes.

| Run | Metric | Model chosen | Predicted | 95% interval | Actual | Predicted / actual | Inside |
|---|---|---|---|---|---|---|---|
| A sort, 1 to 20% | wall | linear | 22.75 s | 20.59 to 24.92 s | 23.60 s | 0.964 | yes |
| A | CPU | linear | 21.26 s | 21.07 to 21.45 s | 22.09 s | 0.962 | no |
| A | peak RSS | linear | 1,782 MB | 1,746 to 1,818 MB | 487 MB | 3.657 | no |
| B sort, 5 to 50% | wall | linear | 24.57 s | 22.56 to 26.58 s | 24.07 s | 1.021 | yes |
| B | CPU | linear | 22.47 s | 21.72 to 23.21 s | 22.22 s | 1.011 | yes |
| B | peak RSS | linear | 966 MB | 268 to 1,664 MB | 488 MB | 1.981 | yes |
| C filter | wall | linear | 17.17 s | 14.80 to 19.53 s | 19.74 s | 0.870 | no |
| C | CPU | linear | 16.47 s | 15.60 to 17.35 s | 18.86 s | 0.874 | no |
| C | peak RSS | power | 18.4 MB | 16.2 to 20.8 MB | 19.2 MB | 0.955 | yes |
| D BAM sort | wall | linear | 5.42 s | 5.19 to 5.65 s | 8.25 s | 0.656 | no |
| D | CPU | linear | 10.09 s | 9.89 to 10.28 s | 14.87 s | 0.678 | no |
| D | peak RSS | linear | 1,338 MB | 1,311 to 1,364 MB | 1,237 MB | 1.081 | no |

(MB here is 10^6 bytes, as printed in `calibration.json`.)

What this shows:

- Coverage of the 95% interval was 5 of 12. The interval only accounts
  for scatter around the chosen curve. It does not account for the curve being
  the wrong shape beyond the largest subsample, which is what happened in the
  misses. Treat the interval as a lower bound on uncertainty.
- Memory was over-predicted when the tool caps its buffer (run A).
  `bcftools sort -m 512M` grows linearly until 512 MB and then spills to
  temporary files. The 20 percent subsample peaked at 374 MiB, below the cap,
  so the line kept rising to 1.8 GB. Wider fractions (run B) put two points
  past the cap (466 and 464 MiB); the fit then had a wide interval that
  contained the truth. The request was safe in both cases, but A asked for
  3 GiB to use 465 MiB.
- Time was under-predicted when the full run did work the subsamples did
  not (run D, 34 percent low). With `-m 512M` per thread and 2 threads,
  the full BAM sort needed temporary files and a merge; the subsamples
  (at most 62 MB of BAM) sorted in memory. Run C was 13 percent low with no
  such obvious cause; the five subsample times (0.23 to 3.75 s) are short
  enough that start-up noise matters.
- No plan under-allocated. The emitted request covered the full run's
  memory and time in all four cases. For time this is mostly the 5-minute
  floor: the runs lasted 8 to 24 seconds. Without the floor, the 1.5 times
  headroom on the upper bound would still have covered D (5.64 s x 1.5 =
  8.47 s against 8.25 s actual), by 3 percent.
- Cost of calibrating: run A spent 8.1 s subsampling and 10.3 s running
  the five subsamples, against 23.6 s for the full run. On a cluster that
  ratio matters little for a single job and pays off for job arrays that run
  the same step on hundreds of inputs.

Minimum size for a meaningful fit. In runs C and D the 1 and 2 percent
subsamples took 0.13 to 0.40 s, and start-up and page cache effects were a
visible share of that. Choose fractions so the smallest measured run takes at
least about 1 s, or add `--repeats 3`. Since v1.0.1 `calibrate` makes one
unmeasured warm-up run first, rejects fits that decrease with input size, and
never predicts below the largest measured value.

A reviewer found that v1.0.0 could request too many CPUs: on a synthetic
200,000-record VCF (6.1 MB) sorted by single-threaded `bcftools sort`, every
run used 0.23 to 0.78 cores, but the request was `--cpus-per-task=4` because
the CPU and wall time predictions came from different models (linear and
power) and were divided. Since v1.0.1 the CPU request is the busiest measured
run's CPU time over wall time, rounded up and capped at `--threads`. The same
input now gives `--cpus-per-task=1`.

What the tool does with this: [algorithm.md](algorithm.md) recommends
fractions that cross any buffer size the tool is given (`-m`, `--mem`,
`-S`), and the roadmap in the repository has a milestone to widen the
interval with the cross-validation errors so that a wrong curve shape shows
up as uncertainty.

## Case study: requested against used (local runs)

First, the four calibrated requests against the held-out full runs.

| Run | Request from hjp | Peak RSS used | Memory use | Wall used | Time use | CPU efficiency |
|---|---|---|---|---|---|---|
| A sort (1 to 20%) | 1 CPU, 3G, 5 min | 464.7 MiB | 15% | 23.6 s | 8% | 94% |
| B sort (5 to 50%) | 1 CPU, 2G, 5 min | 465.2 MiB | 23% | 24.1 s | 8% | 92% |
| C filter | 1 CPU, 256M, 5 min | 18.3 MiB | 7% | 19.7 s | 7% | 96% |
| D BAM sort | 2 CPUs, 2G, 5 min | 1,180.1 MiB | 58% | 8.3 s | 3% | 90% |

Wider fractions (B against A) cut the memory request from 3 GiB to 2 GiB for
the same job. CPU requests matched use: 1 CPU for the single threaded
bcftools steps, 2 for `samtools sort -@ 2`, and CPU efficiency stayed at 90
to 96 percent. The time use is low because every request hit the 5-minute
floor that `hjp` applies to keep requests readable; at cluster scale (hours)
the floor stops mattering.

Second, the example Snakemake workflow before and after `hjp`.
`examples/snakemake_clinvar` first requested 1000 MB and 10 minutes for
`sort_vcf` (`bcftools sort -m 256M` on one chromosome). The calibrated
functions from run A were then dropped into `hjp_resources.smk` and the
workflow was run again on six chromosomes
(`snakemake --cores 4 --scheduler greedy --benchmark-extended`, 7.5 s).
`hjp report` on the two runs' benchmarks and logs:

```
before  | sort_vcf | 6 | 1 | n/a | 953.7M | n/a | 00:10:00 | 00:05:00 |
after   | sort_vcf | 6 | 1 | n/a | 244.1M | n/a | 00:05:00 | 00:05:00 |
```

(953.7M and 244.1M are 1000 MB and 256 MB shown in MiB.) The longest
`sort_vcf` task took 3 seconds. Snakemake 9.27.0 wrote `NA` for memory and
CPU on macOS (see the
[postmortem](operations/postmortem_2026-10-19_snakemake_macos.md)), so the
report makes no memory recommendation from these rows and says so. To check
the new 256 MB request, the same command was measured on the largest input,
chromosome 1 (17,838,151 bytes), with `hjp calibrate --validate-full`:
peak RSS 178.4 MB, wall 2.14 s. The request went from 5.6 times the measured
peak to 1.43 times, and from 280 times the run time to 140 times (the 5-minute
floor).

In the first of the two runs one `stats` job failed inside Snakemake's
benchmark code; in the second all 18 jobs completed (19 steps with the `all`
target).

## `hjp watch` on a real run

```bash
hjp watch --out watch_sort.jsonl --interval 1 --mem-limit 512M --cpus 2 --window 10 -- \
  bcftools sort -m 512M -T ./wsort -Oz -o watch_sorted.vcf.gz clinvar.vcf.gz
```

21 samples over 23 s. Alerts written (UTC):

```
18:54:50.905Z memory_near_limit started: RSS at 91% of the 0.50 GiB limit
18:54:59.018Z cpu_underuse started: 0.91 cores busy over the last 10 s with 2 allocated
18:55:07.110Z memory_near_limit cleared
```

Both alerts are correct for that run: `bcftools sort` holds about 460 MiB
against a 512 MiB limit and is single threaded, so a second CPU is wasted.
The local sampler needed about 0.2 s per sample on this Mac (2 samples in a 0.5 s test run) (psutil walks
the whole process table to find children), so spikes shorter than that can
be missed; `calibrate` also takes `ru_maxrss` from the kernel for that reason.
