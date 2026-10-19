# Scaling fit and its validation

Code: `src/pipeprof/fit.py`, `calibrate.py`, `emit.py`.

## Measurements

For each subsample size `x` (bytes of the input file by default, or records
with `--x records`) `calibrate` runs the command once (or `--repeats` times)
and records:

- wall time, from a monotonic clock around the process;
- CPU time, user plus system, from `os.wait4`, which includes every
  descendant the command waited for (where `os.wait4` is missing, from the
  change in `resource.getrusage(RUSAGE_CHILDREN)` across the wait);
- peak memory, the larger of `ru_maxrss` (largest single process, from the
  kernel) and the largest sum of RSS over the process tree seen by a psutil
  sampling thread. A pipe such as `samtools view | samtools sort` holds memory
  in two processes at once; `ru_maxrss` sees only one of them. The test
  `test_tree_peak_counts_concurrent_children` checks this case.

## Models

Two candidates are fitted by ordinary least squares:

- linear: `y = a + b x`. The intercept absorbs start-up cost and fixed
  buffers.
- power law: `y = c x^k`, fitted as a line in `log y` against `log x`. `k`
  near 1 is linear, near 0 is constant, above 1 is super-linear.

## Choosing between them: forward extrapolation cross-validation

Leave-one-out validation would let a model predict a middle point from points
on both sides. That is interpolation, an easier task than the real one. The
real task is extrapolation from the largest subsample (20 percent) to the full
input (100 percent). So each point `i` is predicted from the points with
strictly smaller `x` only, once at least two distinct smaller sizes exist.
The score is the mean of `|ln(predicted / observed)|`, which charges a factor
of two too high and a factor of two too low the same. A prediction of zero or
less costs `ln(1000)`. Ties go to the linear model.

With the default five fractions, three points are scored per model.

A fit with a negative slope or exponent is rejected before scoring: time,
CPU and memory do not fall as the input grows, and a falling fit comes from
start-up cost in the smallest runs. If both fits fall, the largest measured
value is used and the reason in `calibration.json` says so. That value is a
rule's pick, not a fit, so it has no interval either: pipeprof reports "no
interval (no fitted curve grows with input size)" and sizes the request as
below for fewer than three runs. Separately, the
prediction for the full input is never below the largest measured value. One
unmeasured warm-up run on the smallest subsample comes first (`--no-warmup`
turns it off).

The CPU request is taken from the measured runs, not from the two
predictions: CPU time and wall time of one run are measured together, while
their extrapolations can come from different models.

## Prediction interval

For a new observation at `x0` the standard OLS interval is

    y_hat(x0) +/- t(1 - alpha/2, n - 2) * s * sqrt(1 + 1/n + (x0 - x_mean)^2 / Sxx)

with `s` the residual standard deviation and `Sxx` the sum of squares of `x`
about its mean. For the power law the same formula runs in log space, so the
interval is multiplicative after `exp`. The Student t quantile is computed by
bisection on the t CDF, which uses the regularized incomplete beta function
(continued fraction, modified Lentz method). `tests/test_fit.py` checks the
quantiles against printed table values (for example `t(0.975, 3) = 3.1824`)
to four decimals.

With fewer than three runs (`n - 2 < 1`) a line passes through every point,
there is no residual variance and so no interval. pipeprof then reports
"no interval (fewer than three runs)" in the table, the JSON (`lower` and
`upper` are null) and every emitted request, instead of a zero width interval
that would read as certainty. The request is sized from the larger of the
point prediction and the largest run scaled in proportion to input, times the
headroom. The largest measured value alone is not used: it comes from a
subsample and would under-request the full input. A step whose memory grows
faster than its input can still exceed the proportional bound; use three or
more fractions.

## Flat memory: no growth seen is not no growth

On a cluster run of `bcftools sort`, peak RSS stayed flat up to the 10
percent subsample. The straight fit predicted 69.9 MB (interval 39.6 to
100.2 MB); the full input used 176.6 MB, 2.5 times more. Flat small runs
only show that the tool sits on a fixed buffer up to that size, not that it
stays there.

The rule, in `fit.predict_with_plateau` for peak RSS:

- Flat means a relative spread, `(max - min) / max`, of at most
  `FLAT_SPREAD` = 10 percent over the leading sizes. Peak RSS of one input
  repeats within a few percent, so 10 percent is clear of that noise; a
  step whose memory really scales moves by more than that across the default
  1 to 10 percent fractions.
- Every size flat: nothing can be extrapolated. The point is the largest
  measured value and the upper bound is that run scaled in proportion to
  input, `y_largest * x_full / x_largest`: what the full input would need if
  the whole of the largest run were per-unit cost. A step whose memory grows
  faster than its input can exceed it. `calibrate` warns and marks the
  prediction `flat` in the table and in `calibration.json`; larger
  fractions (`--fractions 0.05,0.1,0.2,0.3,0.5`) show the growth.
- Flat, then rising: a hinge. The model is fitted from the last flat size
  on, since the flat part says nothing about the slope after the hinge. With
  fewer than three sizes after it there is no residual variance, so the
  interval runs from the largest measured value to the proportional bound.
  The prediction is marked `flat then rising`. The emitted Snakemake and
  Nextflow memory formulas never ask for less than the flat level times the
  headroom at any input size: the curve fitted after the hinge falls under
  that level for inputs smaller than the hinge.

The bound is wide by design: with flat runs at 10 percent of the input it is
ten times the largest run. That is the price of not having measured growth.

## Wall time on a waiting filesystem

On Lustre the same `bcftools sort` took 15.15 s wall on 3.56 s CPU on one
node and 4.27 s on 3.25 s on another. A fit of wall time over such runs gave
an interval of 0.06 to 1,146 s. The wait depends on the filesystem's load at
that moment, not on input size, so there is nothing to fit.

A run is mostly waiting when it kept under `CPU_WAIT_THRESHOLD` (0.3, the
constant `pipeprof analyze` uses for the same label) of one core busy, that is
when wall time is above 3.3 times CPU time: more than 70 percent of the run
went on something other than computing. The share is of one core, not of
`--threads`, so a single-threaded tool given four threads is not called
waiting. The first run above is 0.235 cores busy, the second 0.76.

If any measured run is mostly waiting, `calibrate` does not fit wall time.
It says so, reports the observed wall range, and derives wall time from the
CPU model, which does scale with input: point and upper bound are the CPU
prediction and its upper bound divided by the lowest cores-busy ratio seen
(the full run waits as much as the worst measured run), the lower bound the
CPU lower bound divided by the highest. The prediction is marked
`mostly waiting`. One waiting run is enough, because it shows how far the
filesystem can stretch a run on that node.

A run that kept under `MIN_CPU_SHARE` (0.01) of one core busy is a pure
wait, such as a stalled mount. It need not have zero CPU: loading the binary
and its libraries costs a few hundredths of a CPU second whatever the input,
so a 500 s stall can show 0.02 s of CPU. Under 1 percent of one core that CPU
is start-up, not work that scales with input, and dividing by its ratio
(0.00004 here) would inflate the estimate to millions of seconds. Pure waits
are left out of the ratios and out of the CPU fit, where their start-up CPU
would pull the CPU model down, and the estimate is at least their longest
wall, since the full run can stall as long. If every run is a pure wait,
there is no CPU model to scale: the point and upper bound are the longest
observed wall and the lower bound the shortest. The note says so.

The derived upper bound is also capped: it never exceeds the largest wall of
any run scaled in proportion to input (wall times full size over that run's
size). A bound above that would mean the waiting grows faster than the input,
which no measured run showed. When the cap applies, the cores-busy ratio is
raised to CPU upper bound over the cap, so point, upper bound and the runtime
formula all come from the same scaled model. On the two Narval runs above the
cap is the slow run's 15.15 s times 10.

## From prediction to request

    memory request = round_up(upper_95(peak RSS) * 1.2)      steps: 256 MiB, 1 GiB, 4 GiB
    time request   = round_up(upper_95(wall) * 1.5)          steps: 5 min, 15 min, 1 h; at least 5 min
    CPUs           = ceil(max over measured runs of CPU/wall - 0.15), between 1 and --threads

The Snakemake and Nextflow snippets keep the fitted curve as a function of
input size (`input.size_mb`, `file.size()`), multiply by a safety factor
equal to the headroom times `upper / point` at the calibrated size, and double
the request on each retry (`attempt`, `task.attempt`). `upper` is the plan's
upper bound, not the raw fit's: for a flat memory series it is the
proportional bound, after a hinge with thin evidence the widened bound. So at
the full input size the formula asks for at least what the fixed request
asks for. When wall time is mostly waiting, the curve is the CPU model
divided by the lowest cores-busy ratio, so the runtime formula gives wall
minutes, not CPU minutes.

## Validation on real runs

[results.md](results.md) has the full table. In short, over four held-out full
runs (12 predictions):

- the point prediction was within 4 percent for time and CPU in three of the
  four bcftools runs, 13 percent low for a streaming filter, and 34 percent
  low for a BAM sort that spilled to disk only at full size;
- memory was over-predicted 3.7 times when the tool's buffer cap was beyond the
  largest subsample, and 2.0 times when two subsamples crossed it;
- the 95 percent interval contained the actual value 5 times out of 12, so it
  is too narrow when the curve shape changes past the largest subsample;
- the emitted request covered actual use in all four runs.

## Whole-run bottleneck analysis

Code: `src/pipeprof/timeline.py`, `bottleneck.py`, `bound.py`,
`advise.py`.

### Building the timeline

A Nextflow trace carries `submit`, and either `start` directly or `duration`
(submit to complete, including queue wait) plus `realtime` (start to
complete). Whichever is present, the other is filled in:
`start = complete - realtime`, `complete = submit + duration`. On macOS
Nextflow writes `-` for `%cpu`, `peak_rss`, `rchar` and `wchar`; the existing
unit parsers already return `None` for that.

A Snakemake benchmark file gives resource use but not wall-clock times; the
run log gives those. A timestamp line (`[Mon Oct 19 11:30:43 2026]`) always
stands alone right before the event it belongs to: the rule header that
starts a job, the `Finished jobid` line that ends it, or the `Error in rule`
block that ends it with a failure. The job ID itself is a few lines further
into the block, so `timeline._snakemake_log_times` tracks which event a
pending timestamp belongs to until the ID line turns up. Snakemake
schedules its own jobs rather than queueing through a separate scheduler, so
there is no `submit` time for it. A benchmark file with several rows
(`benchmark: repeat(...)`) is one job that ran the command that many times
in a row: it is one task on the timeline, with elapsed and CPU time added up
over the rows and the largest peak RSS.

### Critical path

Nextflow traces and Snakemake logs record what ran and when, not a
dependency graph. Without declared dependencies, the predecessor of a task
is inferred as the last task that finished before it started: a
conservative stand-in for "this is probably what it was scheduled after",
not a claim about data flow. With declared dependencies (a task name to the
names it depends on, from a source that has them) the predecessor at each
step is the named dependency that finished latest, since that is the one
that gated the task's start.

Either way, the critical path is built by tracing backward from the task
that finished last, repeatedly following the predecessor link, until a task
with no predecessor is reached. This gives a single chain whose total busy
time plus the gaps between its tasks equals the run's wall time exactly
(`wall_s == busy_s + waiting_s`, checked in `test_bottleneck.py`), because by
construction each task on the chain starts only after its predecessor
finished. The result records which method was used.

### Parallel efficiency

Tasks running (each counted once, or by its CPU count where known) against
`--cpus`, sampled at every start and end: between two consecutive events the
level is constant, so the whole run is a step function. An end at the same
instant as a start is processed first, so a task that finishes exactly when
another starts is never double counted. The average is time-weighted over
the whole span; "low" intervals are below the stated threshold (50% by
default).

### Boundness thresholds

| Constant | Value | Reason |
|---|---|---|
| `CPU_BOUND_THRESHOLD` | 80% | Nextflow and Snakemake report CPU as a percent of one core; divided by 100 that is cores kept busy. A step is CPU-bound when it keeps 80% of its requested cores busy, or at least 0.8 of one core whatever it requested: its time goes on computing, on as many cores as the tool uses. 80% leaves headroom for sampling noise. |
| `CPU_WAIT_THRESHOLD` | 30% | Under 0.3 of ONE core busy (not of the request), computing does not explain the time; something else (another task, a lock, a slow filesystem, the scheduler) does. `pipeprof calibrate` uses the same constant and the same one-core reading, so both commands call the same runs mostly waiting. A single-threaded tool given 4 CPUs at 100% of one core is CPU-bound on one core, not waiting. |
| `IO_BOUND_BYTES_PER_S` | 20 MiB/s | A shared HPC filesystem (NFS, a parallel filesystem under load from many concurrent jobs) struggles to sustain much more than this per stream. A step under the CPU-bound threshold moving data at or above this rate is I/O-bound: its time goes on moving data. A step at low CPU moving less is not I/O-bound; it is waiting on something else (often a slow or shared filesystem's latency, a lock or the queue) and is labelled by its CPU use. |
| `MEM_BOUND_FRACTION` | 80% | Peak RSS within this share of the request means the step is sized by what it was given; a slightly larger input next time risks an out-of-memory kill. |

Labels are checked in order: memory-bound, then CPU-bound (share of the
request, then one core), then I/O-bound, then mostly waiting, then unknown. A step with none of CPU, I/O or memory
measurements is "unknown" and says so, rather than guessing from whichever
numbers happen to be present.

CPU use is read once per step by `bound.cpu_use`, and both the label and the
thread advice take their numbers from it. Cores busy is the mean over the
tasks with CPU measured; the request is the mean over the same tasks, and is
left unknown unless every one of them has a request. The share of the request
is cores busy over that mean request (tasks asking for 1, 1 and 16 CPUs at
one core each: 1.00 cores busy of 6, a 17% share). The cores a tool uses are
`ceil(cores busy - 0.15)`, at least 1, the rounding `pipeprof calibrate` uses
for its CPU request: one task at 140% of one core uses about 2.

### Recommendations

Which critical-path step to speed up first is whichever step holds the most
time on the critical path; the expected saving is that time's share
recovered by the stated speed-up factor (2x by default). This assumes the
freed time is not absorbed by waiting elsewhere on a re-run, so it is an
approximation of recomputing the whole schedule, not a simulation of one;
`advise.pick_first_step`'s basis string says so. Thread scaling advice
compares `wall_s` at 2 and 4 threads (from three `pipeprof calibrate` runs, or any
other source of those two points): a speed-up under `THREAD_SCALING_THRESHOLD`
(1.2x) is flagged as not worth doubling the cores, since the extra cores
could go to another job on a shared cluster. An I/O-bound or mostly-waiting
step gets the same advice without needing thread calibration, since more
compute capacity cannot shorten time spent moving data or waiting. So does a
CPU-bound step that keeps under 80% of its requested cores busy (a
single-threaded tool given four CPUs): it already has cores it does not use. Memory
requests are flagged too large when a completed task's peak RSS is under
`REQUEST_TOO_LARGE_FRACTION` (50%, the same threshold `report.py` already
uses) of the request, and too small when a failed task's peak RSS was within
`REQUEST_TOO_SMALL_FRACTION` (95%) of the limit; `Task` carries no exit code,
so this stands in for the exit-137-near-the-limit check `records.py` makes
for individual jobs.

## Known limits and how to work around them

- Buffer caps and spill points. If the tool has a memory cap (`bcftools
  sort -m`, `samtools sort -m`, `sort -S`), choose fractions so that at least
  two subsamples exceed it: `--fractions 0.05,0.1,0.2,0.3,0.5`.
- Short runs. Below about 1 second per subsample, start-up time and
  scheduler noise dominate. Use larger fractions or `--repeats 3`.
- Different hardware. Calibrate on the node type the job will run on
  (`srun --pty` on a compute node), since CPU speed and file system latency
  change the slope.
- Interval coverage. The interval does not include model-shape error. The
  roadmap item "calibrated intervals" adds the cross-validation errors to it.
