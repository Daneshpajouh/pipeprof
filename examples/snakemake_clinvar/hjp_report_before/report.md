# Job efficiency report

Sources: benchmarks, .snakemake/log/2026-10-19T113043.412429.snakemake.log

Tasks: 18, not completed: 1.
Core hours allocated 0.000, used 0.000.
Memory GiB hours requested 0.000, used 0.000.
Queue wait 0.000 hours in total.

## Recommendations

| Name | Tasks | CPUs now | CPUs rec. | Memory now | Memory rec. | Time now | Time rec. |
|---|---|---|---|---|---|---|---|
| extract_chrom | 6 | 1 | n/a | 476.8M | n/a | 00:10:00 | 00:05:00 |
| sort_vcf | 6 | 1 | n/a | 953.7M | n/a | 00:10:00 | 00:05:00 |
| stats | 6 | 2 | n/a | 953.7M | n/a | 00:10:00 | 00:05:00 |

- extract_chrom: memory: no peak RSS recorded for completed tasks; no recommendation; time: max elapsed 00:00:03 over 6 completed tasks x 1.5
- sort_vcf: memory: no peak RSS recorded for completed tasks; no recommendation; time: max elapsed 00:00:03 over 6 completed tasks x 1.5
- stats: memory: no peak RSS recorded for completed tasks; no recommendation; time: max elapsed 00:00:01 over 5 completed tasks x 1.5

## Failures

- FAILED: 1 task(s). Non-zero exit without a resource signal: read the job's stderr; more resources will not help.

## Tasks

| Job | Name | State | Elapsed | CPU eff. | Peak RSS | Mem eff. | Time use | Flags |
|---|---|---|---|---|---|---|---|---|
| 3 | extract_chrom | COMPLETED | 00:00:03 | n/a | n/a | n/a | 0% | time limit far above use (0% used) |
| 9 | extract_chrom | COMPLETED | 00:00:02 | n/a | n/a | n/a | 0% | time limit far above use (0% used) |
| 6 | extract_chrom | COMPLETED | 00:00:03 | n/a | n/a | n/a | 0% | time limit far above use (0% used) |
| 12 | extract_chrom | COMPLETED | 00:00:01 | n/a | n/a | n/a | 0% | time limit far above use (0% used) |
| 15 | extract_chrom | COMPLETED | 00:00:01 | n/a | n/a | n/a | 0% | time limit far above use (0% used) |
| 18 | extract_chrom | COMPLETED | 00:00:02 | n/a | n/a | n/a | 0% | time limit far above use (0% used) |
| 2 | sort_vcf | COMPLETED | 00:00:03 | n/a | n/a | n/a | 0% | time limit far above use (0% used) |
| 8 | sort_vcf | COMPLETED | 00:00:02 | n/a | n/a | n/a | 0% | time limit far above use (0% used) |
| 5 | sort_vcf | COMPLETED | 00:00:03 | n/a | n/a | n/a | 0% | time limit far above use (0% used) |
| 11 | sort_vcf | COMPLETED | 00:00:01 | n/a | n/a | n/a | 0% | time limit far above use (0% used) |
| 14 | sort_vcf | COMPLETED | 00:00:01 | n/a | n/a | n/a | 0% | time limit far above use (0% used) |
| 17 | sort_vcf | COMPLETED | 00:00:02 | n/a | n/a | n/a | 0% | time limit far above use (0% used) |
| 1 | stats | COMPLETED | 00:00:01 | n/a | n/a | n/a | 0% | time limit far above use (0% used) |
| 7 | stats | COMPLETED | 00:00:01 | n/a | n/a | n/a | 0% | time limit far above use (0% used) |
| 4 | stats | COMPLETED | 00:00:01 | n/a | n/a | n/a | 0% | time limit far above use (0% used) |
| 10 | stats | COMPLETED | 00:00:01 | n/a | n/a | n/a | 0% | time limit far above use (0% used) |
| 13 | stats | COMPLETED | 00:00:01 | n/a | n/a | n/a | 0% | time limit far above use (0% used) |
| 16 | stats | FAILED | n/a | n/a | n/a | n/a | n/a | FAILED |
