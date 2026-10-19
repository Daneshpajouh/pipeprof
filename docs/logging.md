# Logging: one run ID from sizing to review

Every `pipeprof` command writes JSON lines. `PIPEPROF_RUN_ID` sets the run ID for all of
them, so the calibration, the live samples and the review of one job can be
joined on that field. Without it, each command makes its own ID
(`20261019T190226Z-3f2a9c`: UTC time plus six random hex digits).

| Stage | File | Records |
|---|---|---|
| before | `OUTDIR/pipeprof.log.jsonl`, `OUTDIR/calibration.json` | `step_start` and `step_end` per subsample, measurement, fit; the manifest in `calibration.json` has the input path, size and SHA-256, tool versions, git commit, host, allow listed environment |
| during | the `--out` file of `pipeprof watch` | one `start` record (limits and alert thresholds), `sample` records, `alert` records (started, cleared), one `end` record |
| after | `report.json`, `report.md`, `report.html` | per task efficiency and flags, recommendations, failures, totals |

## A real run

ClinVar filter on the local machine, 2026-10-19 12:02 Pacific:

```bash
export PIPEPROF_RUN_ID=clinvar-filter-20261019
V=clinvar.vcf.gz
pipeprof calibrate $V --outdir before --job-name filter_pathogenic -- \
  bcftools view -i 'INFO/CLNSIG~"Pathogenic"' -Oz -o '{output}' '{input}'
# Request: --cpus-per-task=1 --mem=256M --time=00:05:00
pipeprof watch --out during.jsonl --interval 1 --mem-limit 256M --cpus 1 -- \
  bcftools view -i 'INFO/CLNSIG~"Pathogenic"' -Oz -o pathogenic.vcf.gz $V
# 20 samples written to during.jsonl
pipeprof report --watch-log during.jsonl --outdir after
```

`before/pipeprof.log.jsonl` (first lines, cut at the right):

```json
{"ts": "2026-10-19T19:02:26.209Z", "level": "INFO", "logger": "pipeprof.calibrate", "run_id": "clinvar-filter-20261019", "event": "step_start", "msg": "start subsample", "step": "subsample", "input": ".../clinvar.vcf.gz", "fractions": [0.01, 0.02, 0.05, 0.1, 0.2]}
{"ts": "2026-10-19T19:02:33.147Z", "level": "INFO", "logger": "pipeprof.calibrate", "run_id": "clinvar-filter-20261019", "event": "step_end", "msg": "done subsample", "records_full": 4594764, "bytes_full": 200437060, ...
```

`before/calibration.json`, provenance of the input:

```json
"run_id": "clinvar-filter-20261019",
"inputs": [{"path": ".../clinvar.vcf.gz", "size_bytes": 200437060,
            "sha256": "05d9481dd7217cc4df15803362d4a4c9eae5875b27b1750867d6bfd2643b3d10"}],
"tool_versions": {"bcftools": "bcftools 1.23.1"}
```

`during.jsonl` (first, second and last of 22 lines):

```json
{"run_id": "clinvar-filter-20261019", "type": "start", "ts": "2026-10-19T19:02:41.442Z", "mem_limit_bytes": 268435456, "cpus": 1, "thresholds": {"mem_warn": 0.9, "cpu_underuse": 0.5, "window_s": 60.0, "stall_s": 120.0}, "source": "local", "pid": 52493, "command": "bcftools view -i INFO/CLNSIG~\"Pathogenic\" -Oz -o pathogenic.vcf.gz .../clinvar.vcf.gz"}
{"run_id": "clinvar-filter-20261019", "type": "sample", "ts": "2026-10-19T19:02:41.604Z", "source": "local", "pid": 52493, "cpu_percent": null, "elapsed_s": 0.16, "rss_bytes": 15056896, "num_procs": 1, "cpu_seconds": 0.15, "read_bytes": null, "write_bytes": null}
{"run_id": "clinvar-filter-20261019", "type": "end", "ts": "2026-10-19T19:03:05.627Z", "returncode": 0}
```

`read_bytes` is null because psutil has no per process I/O counters on macOS.

`after/report.md`:

```
Run ID: clinvar-filter-20261019
Tasks: 1, not completed: 0.
Core hours allocated 0.006, used 0.005.
| bcftools | 1 | 1 | 1 | 256.0M | 256.0M | n/a | 00:05:00 |
- bcftools: memory: max peak RSS 16.7M over 1 completed tasks x 1.2; time: max elapsed 00:00:24 over 1 completed tasks x 1.5; cpus: busiest task kept 0.84 cores busy on average
```

The 256M request stays because 256 MiB is the smallest step `pipeprof` rounds to.

## Using the library in a pipeline

```python
import os
from pipeprof.logkit import RunLogger
from pipeprof.provenance import capture

log = RunLogger("annotate", jsonl_path=f"logs/annotate_{os.environ['SLURM_JOB_ID']}.jsonl")
log.event("provenance", **capture(inputs=["calls.vcf.gz"]).to_dict())
with log.step("vep", sample="S1") as fields:
    run_vep()
    fields["variants"] = 48_211
log.close()
```

On failure inside `step`, the `step_end` record has `status: "error"`, the
exception type and the traceback, and the exception propagates.
