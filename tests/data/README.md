# Test data

Small files the tests read. None of them comes from a production cluster.
"Hand-written" files follow the formats in the cited manual pages; "real"
files were produced by a local run while building this repository.

| File | Kind | What it exercises |
|---|---|---|
| `sacct_slurm_26_05.txt` | hand-written, Slurm 26.05 `sacct --parsable2` columns ([sacct manual](https://slurm.schedmd.com/sacct.html)) | completed job with `.batch` and `.extern` steps, OUT_OF_MEMORY, TIMEOUT (batch step CANCELLED), NODE_FAIL with no step lines, array tasks `1005_1` to `1005_3` plus a pending range `1005_[4-6]`, `CANCELLED by 51234` before start, a job whose steps have no MaxRSS, a RUNNING job, a two-node MPI step `1009.0` |
| `sacct_slurm_20_11.txt` | hand-written, Slurm 20.11 ReqMem format (`4000Mc` per CPU, `16000Mn` per node, shown on steps too; [20.11 sacct.1](https://raw.githubusercontent.com/SchedMD/slurm/slurm-20-11-9-1/doc/man/man1/sacct.1)), MaxRSS in K | job FAILED while its batch step is OUT_OF_MEMORY, a 2 hour queue wait, a `1-00:00:00` time limit |
| `sacct_slurm_20_02_container.txt` | real: `sacct --parsable2` from the `xenonmiddleware/slurm:20` container (Slurm 20.02.0, `accounting_storage/filetxt`), 2026-10-19 12:13 Pacific, after submitting a completed job, a job exiting 3, a 3-task array, a job over its 1 minute limit, and one rejected submission | accounting gaps on a real Slurm: AllocCPUS 0, TotalCPU 0, ReqMem `0n`, empty Timelimit, no step lines, array tasks under raw IDs (5, 7, 8) |
| `sacct_memory_units.txt` | hand-written | the same 1.5 GiB written as `1.5G`, `1536M` and `1572864K`; `512Mc` and `3Gn`; a bare `2000` on a two-node job, which Slurm 21.08+ reports for the whole job (sacct manual: ReqMem is "only from the job allocation"; 21.08 release notes: "the requested memory of the whole job"); a reduced column set |
| `sacct_truncated.txt` | first 300 bytes of `sacct_slurm_26_05.txt` | a line cut off mid record must raise a clear error, not parse silently |
| `empty.txt` | empty | every reader returns no records |
| `nextflow_trace_default.txt` | hand-written, default trace fields and human readable units as in the [Nextflow trace docs](https://docs.seqera.io/nextflow/reports) | process names with a subworkflow prefix (`mutect2Wf:mutect2 (chr1)`), a task killed with exit 137 and retried, `%cpu` above 100 |
| `nextflow_trace_raw.txt` | hand-written, `trace.raw = true` (ms, bytes, epoch ms) with `process`, `attempt`, `cpus`, `memory`, `time` | exit 137 near the memory request (inferred OOM), exit 143 at the time limit (inferred TIMEOUT), a retry with doubled memory |
| `snakemake_macos_run/` | real: benchmarks and run log from `examples/snakemake_clinvar` on macOS, 2026-10-19 | Snakemake wrote `NA` for every memory and CPU column, and the `stats` job for chrX failed inside Snakemake's own benchmark code |
| `snakemake_linux/sort_vcf_chr1.tsv` | hand-written, plain benchmark columns with values as Snakemake writes them on Linux | MiB to bytes conversion, `s` against `h:m:s` |

In `snakemake_macos_run/` the repository path is replaced with `REPO`, the data folder with `DATA_DIR` and the macOS temporary folder with `TMPDIR`; nothing else was edited.
