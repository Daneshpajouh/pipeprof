# Input formats and where they are specified

| Source | Format read | Reference checked |
|---|---|---|
| `sacct` | `--parsable2` (pipe separated, no trailing pipe) with a header line; columns from `hjp report --show-sacct` or any subset that includes `JobID` | [sacct manual, Slurm 26.05](https://slurm.schedmd.com/sacct.html), read 2026-10-19 |
| `sacct` ReqMem, Slurm 20.11 and older | `4000Mc` (per CPU), `16000Mn` (per node), bare numbers in MB, also shown on steps | [sacct.1 at tag slurm-20-11-9-1](https://raw.githubusercontent.com/SchedMD/slurm/slurm-20-11-9-1/doc/man/man1/sacct.1): "A 'c' at the end of number represents Memory Per CPU" |
| `sacct` ReqMem, Slurm 21.08 and newer | whole-job memory with a unit letter, allocation line only | [21.08 release notes](https://raw.githubusercontent.com/SchedMD/slurm/slurm-21-08-8-2/RELEASE_NOTES) |
| `sstat` | `--parsable2 --noheader --allsteps --format=JobID,NTasks,AveCPU,AveRSS,MaxRSS,MaxDiskRead,MaxDiskWrite` | [sstat manual, Slurm 26.05](https://slurm.schedmd.com/sstat.html) |
| Job states | BOOT_FAIL, CANCELLED, COMPLETED, DEADLINE, FAILED, NODE_FAIL, OUT_OF_MEMORY, PENDING, PREEMPTED, RUNNING, REQUEUED, RESIZING, REVOKED, SUSPENDED, TIMEOUT | sacct manual, JOB STATE CODES |
| Nextflow trace | tab separated with header; default fields `task_id hash native_id name status exit submit duration realtime %cpu peak_rss peak_vmem rchar wchar` in human readable units, or raw numbers with `trace.raw = true` | [Nextflow reports](https://docs.seqera.io/nextflow/reports) |
| Snakemake benchmark | `s h:m:s max_rss max_vms max_uss max_pss io_in io_out mean_load cpu_time`, plus `jobid rule_name wildcards params threads cpu_usage resources input_size_mb` with `--benchmark-extended`; memory in MiB, `NA` when not sampled | `snakemake/benchmark.py` in Snakemake 9.27.0 (`get_header`, `to_tsv`) |
| Snakemake run log | `rule NAME:` or `localrule NAME:` blocks with four-space indented `jobid`, `threads`, `resources`; `Finished jobid: N`; `Error in rule NAME:` | real log from `examples/snakemake_clinvar`, Snakemake 9.27.0 |
| Snakemake Slurm resources | `mem_mb`, `runtime` (minutes), `cpus_per_task`, `slurm_partition`, `slurm_account` | `snakemake_executor_plugin_slurm` 2.8.0 source (`submit_string.py`) |

The sacct and Nextflow test files in `tests/data` are hand-written in these
formats; they are not recordings from a cluster (see `tests/data/README.md`).
