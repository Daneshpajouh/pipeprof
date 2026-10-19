# Postmortem: calibration failed on the first ClinVar subsample

- Date: 2026-10-19, 11:39:48 to 11:41:04 Pacific
- Impact: the first real `pipeprof calibrate` run stopped after 8 seconds
- Detected by: `pipeprof calibrate` exit status 2 and the error in its log

## What happened

`pipeprof calibrate clinvar.vcf.gz -- bcftools sort ...` made five subsamples and
ran the first one. `bcftools sort` exited with status 255:

```
[W::vcf_parse] Contig '1' is not defined in the header. (Quick workaround: index the file with tabix.)
Error encountered while parsing the input at 1:930281
```

## Cause

The ClinVar VCF has no `##contig` header lines. htslib then takes the contig
names from the `.tbi` index. The full file has an index; the subsamples did
not, so the same command failed on them and would have worked on the full
input.

## Fix

Commit `a7798da` (`subsample.py`, `_index_like_source`): when the input VCF
has a `.tbi` or `.csi` index and `tabix` is available, every subsample is
indexed the same way. The rerun started at 11:40 and completed at 11:41:04; its predictions are
run A in [results.md](../results.md). Test:
`test_bgzipped_indexed_vcf_gets_indexed_subsamples`.

## Detection

The calibration log named the failing subsample and the command, which was
enough to diagnose it in one read. The general lesson for calibration: a
subsample has to look like the input in every way the tool checks, including
side files such as indexes.

## Follow-up

None open. A subsample of a BAM is not indexed either; tools that need a BAM
index (`samtools idxstats`, region queries) will hit the same problem. The
roadmap has an item to index BAM subsamples when the input has a `.bai`.
