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
$ pytest --cov=hpc_job_profiler            # macOS, Python 3.13.9, 2026-10-19 12:14
112 passed in 31.18s; TOTAL 2177 statements, 97% coverage (branch coverage on)
$ docker run --rm hjp-test                # Debian, python:3.13-slim, 2026-10-19 12:11
111 passed in 10.58s; TOTAL 2169 statements, 97%
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
each `sstat` call until it completes.

One real Slurm was also used, by hand: the `xenonmiddleware/slurm:20` image
(`sha256:20d4481829c7859e7d2a3fb50b7296394948c2e6cad7516493c83c72dd27ff0a`,
Slurm 20.02.0, amd64 under emulation on the Mac). The exact `sstat` and
`sacct` calls that `hjp watch --slurm-job` makes ran without error there
(`--allsteps` is accepted; `MaxDiskRead` came back as a bare byte count,
`6825`). Its `sacct` output after five test jobs is in `tests/data` and showed
that this accounting setup (`filetxt`) records 0 CPUs and 0 CPU time. Before
that file was added, `hjp report` recommended 1 CPU from "0.00 cores busy";
it now treats 0 allocated CPUs as not recorded. `hjp` itself was not run
inside that container (its Python is too old), so `watch --slurm-job` has
not been run end to end against a real `sstat` stream.
