# Runbook: failed or wasteful HPC jobs

For each case: how it shows up, how `pipeprof` reports it, what to do.

## Out of memory

- Slurm: state `OUT_OF_MEMORY`, often exit code `0:125`. On Slurm releases
  that mark only the step, the job shows FAILED and the `.batch` step
  OUT_OF_MEMORY; `pipeprof report` reports OUT_OF_MEMORY with a note.
- Nextflow: status FAILED, exit 137. `pipeprof` labels it OUT_OF_MEMORY (inferred)
  only when peak RSS reached 90 percent of the request.
- During the run: `pipeprof watch` alert `memory_near_limit`.
- Action: raise memory to the report's recommendation (at least 1.5 x the
  failed request), or let the workflow retry with memory times attempt (the
  emitted Snakemake and Nextflow snippets do this). If the tool has a memory
  cap option (`-m`, `-S`), set it below the request so it spills to disk.

## Time limit

- Slurm: state `TIMEOUT`; the batch step is usually CANCELLED with signal 15.
- Nextflow: exit 140 or 143 near the `time` directive, labelled TIMEOUT
  (inferred).
- Action: check first whether the job was progressing (`pipeprof watch` alert
  `stalled`, or CPU time well below elapsed in the report). If it was, raise
  the limit (the report suggests 2 x) or split the input. If it was not, the
  cause is a hang, not the limit.

## Node failure

- Slurm: state `NODE_FAIL`, usually no step lines and no MaxRSS.
- Action: not caused by the job. Resubmit; use `--requeue` or workflow
  retries. Report repeated failures on the same node (the `NodeList` column)
  to the cluster administrators.

## File system quota

- Shows up as FAILED with a non-zero exit and "Disk quota exceeded" in stderr;
  `pipeprof report` classifies it as FAILED without a resource signal.
- Action: `quota` or `diskusage_report` (Alliance clusters) on the login node;
  move temporary files to `$SLURM_TMPDIR` or scratch. `pipeprof calibrate` reports
  output sizes only indirectly (write bytes on Linux), so check the tool's
  temporary directory size by hand.

## Stuck or hanging jobs

- Running for a long time with little CPU.
- `pipeprof watch --slurm-job ID` (or `--pid` locally) raises `stalled` after
  `--stall` seconds without CPU or I/O progress, and `cpu_underuse` when CPU
  stays below half of the allocation.
- Action: look at the job's stderr and open files (`lsof -p PID` on the node
  via `srun --jobid ID --pty bash`), then cancel. The Snakemake hang in the
  2026-10-19 postmortem is an example: no CPU use for 10 minutes.

## Waste without failure

- `pipeprof report` flags memory efficiency below 50 percent, CPU efficiency below
  50 percent (jobs over a minute), and time use below 25 percent.
- Action: apply the recommendation table; for job arrays, read the
  min/median/max line and size for the maximum, not the median.

## Escalation

Collect: job IDs, `sacct --parsable2` output from `pipeprof report --show-sacct`,
the `report.json`, and the watch log if one exists. File them with the ticket.
