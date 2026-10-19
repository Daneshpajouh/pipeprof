# Scaling fit and its validation

Code: `src/pipeprof/fit.py`, `calibrate.py`, `emit.py`.

## Measurements

For each subsample size `x` (bytes of the input file by default, or records
with `--x records`) `calibrate` runs the command once (or `--repeats` times)
and records:

- wall time, from a monotonic clock around the process;
- CPU time, user plus system, from `os.wait4`, which includes every
  descendant the command waited for;
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
value is used and the reason in `calibration.json` says so. Separately, the
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

## From prediction to request

    memory request = round_up(upper_95(peak RSS) * 1.2)      steps: 256 MiB, 1 GiB, 4 GiB
    time request   = round_up(upper_95(wall) * 1.5)          steps: 5 min, 15 min, 1 h; at least 5 min
    CPUs           = ceil(max over measured runs of CPU/wall - 0.15), between 1 and --threads

The Snakemake and Nextflow snippets keep the fitted curve as a function of
input size (`input.size_mb`, `file.size()`), multiply by a safety factor
equal to the headroom times `upper / point` at the calibrated size, and double
the request on each retry (`attempt`, `task.attempt`).

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
