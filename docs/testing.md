# Testing

`tests/` is separate from `src/`. Measured on 2026-10-19 with Python 3.13.9
on macOS and in the `python:3.13-slim` Linux container.

| Layer | What it covers | Command |
|---|---|---|
| Unit | units, Student t quantiles against printed tables, OLS fits, model selection, emitters, logging, provenance | `pytest tests/test_units.py tests/test_fit.py tests/test_emit.py tests/test_logkit_provenance.py` |
| Parser edge cases | every file in `tests/data` (two Slurm ReqMem formats, OOM, TIMEOUT, NODE_FAIL, arrays, steps, missing MaxRSS, `1.5G`/`1536M`/`1572864K`, `c`/`n` suffixes, truncated and empty files, Nextflow retries, Snakemake `NA`) | `pytest tests/test_records_report.py tests/test_edge_cases.py` |
| Property based | duration and memory round trips, fits recover exact linear and power models, the interval contains the point prediction (hypothesis) | `pytest tests/test_edge_cases.py -k "round_trip or scale or recover or contains"` |
| Process measurement | real child processes: a 120 MiB allocation, two concurrent children (tree peak above `ru_maxrss`), exit codes | `pytest tests/test_measure.py` |
| Integration | subsampling with real bgzip, tabix and samtools; calibrate end to end; CLI; `watch` and `report --from-slurm` against a fake `sacct`/`sstat` (`tests/shim/fake_slurm.py`) | `pytest tests/test_subsample_calibrate.py tests/test_watch_cli.py` |
| Shell | `scripts/fetch_data.sh` offline: reuse of present files, MD5 mismatch stops the script | `bats tests/shell/fetch_data.bats` and `shellcheck scripts/*.sh` |
| Linux container | the whole suite on Debian with apt's samtools 1.21 | `docker build --target test -t hjp-test . && docker run --rm hjp-test` |
| Real data | the four held-out calibrations in [results.md](results.md) | commands in results.md |

Results of the last run of each:

```
$ pytest --cov=hpc_job_profiler            # macOS, Python 3.13.9, 2026-10-19 12:09
111 passed in 23.14s; TOTAL 2169 statements, 97% coverage (branch coverage on)
$ docker run --rm hjp-test                # Debian, python:3.13-slim, 2026-10-19 12:00
111 passed in 6.17s; TOTAL 97%
$ bats tests/shell/fetch_data.bats
ok 1 reuses present files and prints their SHA-256
ok 2 stops on an MD5 mismatch
```

The container run found one real bug that the Mac run could not: `samtools
--version` from Debian's samtools 1.21 contains a byte that is not UTF-8, and
`provenance.tool_version` raised `UnicodeDecodeError`. It now decodes with
`errors="replace"`.

Platform branches are tested on both systems through CI (`ubuntu-latest` and
`macos-latest`, Python 3.11 to 3.13): `ru_maxrss` is kilobytes on Linux and
bytes on macOS (`maxrss_to_bytes`), and psutil I/O counters exist only on
Linux (`HAS_IO_COUNTERS`; the stall alert falls back to CPU progress).

The Slurm path is tested without a cluster. The fake `sacct` and `sstat`
replay the hand-written fixtures and simulate one job whose memory grows on
each `sstat` call until it completes. An end-to-end test against a real
single node Slurm container was not done; it is on the roadmap.
