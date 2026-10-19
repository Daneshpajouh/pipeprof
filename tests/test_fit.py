from __future__ import annotations

import math

import pytest

from pipeprof.fit import (
    FitError,
    ModelKind,
    extrapolation_errors,
    fit_linear,
    fit_power,
    regularized_beta,
    select_model,
    student_t_cdf,
    student_t_ppf,
)


@pytest.mark.parametrize(
    ("p", "df", "expected"),
    [
        (0.975, 1, 12.7062),
        (0.975, 2, 4.3027),
        (0.975, 3, 3.1824),
        (0.975, 10, 2.2281),
        (0.95, 5, 2.0150),
        (0.995, 4, 4.6041),
        (0.025, 3, -3.1824),
    ],
)
def test_t_quantiles_match_tables(p: float, df: int, expected: float) -> None:
    assert student_t_ppf(p, df) == pytest.approx(expected, abs=1e-4)


def test_t_edges() -> None:
    assert student_t_ppf(0.5, 7) == 0.0
    assert student_t_cdf(0.0, 3) == pytest.approx(0.5)
    assert regularized_beta(1, 1, 0.0) == 0.0
    assert regularized_beta(1, 1, 1.0) == 1.0
    assert regularized_beta(2, 3, 0.9) == pytest.approx(0.9963, abs=1e-4)
    with pytest.raises(ValueError, match="p must"):
        student_t_ppf(1.0, 3)
    with pytest.raises(ValueError, match="degrees"):
        student_t_cdf(1.0, 0)


def test_linear_recovers_line() -> None:
    xs = [1, 2, 4, 8, 16]
    fit = fit_linear(xs, [3 + 2 * x for x in xs])
    assert fit.intercept == pytest.approx(3)
    assert fit.slope == pytest.approx(2)
    assert fit.predict(100) == pytest.approx(203)
    lo, hi = fit.interval(100)
    assert lo == pytest.approx(203) and hi == pytest.approx(203)
    assert "2 * x" in fit.formula()
    assert fit.to_dict()["kind"] == "linear"


def test_power_recovers_curve() -> None:
    xs = [1, 2, 4, 8, 16]
    fit = fit_power(xs, [2 * x**1.5 for x in xs])
    assert fit.slope == pytest.approx(1.5)
    assert fit.predict(100) == pytest.approx(2000)
    assert "^1.5000" in fit.formula()


def test_interval_widens_with_noise_and_distance() -> None:
    xs = [1, 2, 3, 4, 5, 6]
    ys = [2.1, 3.9, 6.2, 7.8, 10.1, 12.0]
    fit = fit_linear(xs, ys)
    near = fit.interval(3.5)
    far = fit.interval(60)
    assert near[0] < fit.predict(3.5) < near[1]
    assert (far[1] - far[0]) > (near[1] - near[0])
    power = fit_power(xs, ys)
    lo, hi = power.interval(60)
    assert lo < power.predict(60) < hi


def test_negative_slope_formula_and_clipping() -> None:
    fit = fit_linear([1, 2, 3], [10, 8, 6.5])
    assert " - " in fit.formula()
    assert fit.interval(100)[0] == 0.0


def test_fit_errors() -> None:
    with pytest.raises(FitError):
        fit_linear([1], [1])
    with pytest.raises(FitError):
        fit_linear([1, 1, 1], [1, 2, 3])
    with pytest.raises(FitError):
        fit_linear([1, 2], [1])
    with pytest.raises(FitError):
        fit_power([0, 1, 2], [1, 2, 3])


def test_selection_prefers_true_model() -> None:
    xs = [1, 2, 4, 8, 16]
    linear = select_model(xs, [5 + 3 * x for x in xs])
    assert linear.chosen.kind is ModelKind.LINEAR
    power = select_model(xs, [0.5 * x**2 for x in xs])
    assert power.chosen.kind is ModelKind.POWER
    assert "extrapolation" in power.reason
    assert power.to_dict()["chosen"] == "power"


def test_selection_with_two_sizes_defaults_to_linear() -> None:
    sel = select_model([1, 2], [3, 5])
    assert sel.chosen.kind is ModelKind.LINEAR
    assert "fewer than three" in sel.reason


def test_selection_skips_power_for_nonpositive_values() -> None:
    sel = select_model([0, 1, 2, 3], [1, 2, 3, 4])
    assert ModelKind.POWER not in sel.fits
    with pytest.raises(FitError):
        select_model([1, 1], [1, 2])


def test_extrapolation_penalises_negative_predictions() -> None:
    errors = extrapolation_errors([1, 2, 3, 4], [10, 5, 0.1, 9], ModelKind.LINEAR)
    assert errors and max(errors) == pytest.approx(math.log(1e3))


# Flat-then-rise memory. The series below are synthetic, shaped like the
# Narval bcftools sort: peak RSS flat near 70 MB up to the 10 percent
# subsample, 176.6 MB on the full input. A straight fit predicted 69.9 MB
# (interval 39.6 to 100.2 MB) there.

MB = 1e6
FULL_X = 1e9
ACTUAL_FULL = 176.6 * MB


def _synthetic_rss(fraction: float) -> float:
    """70 MB up to fraction 0.1, then linear to 176.6 MB at the full input."""
    if fraction <= 0.1:
        return 70 * MB
    return 70 * MB + (ACTUAL_FULL - 70 * MB) * (fraction - 0.1) / 0.9


def _series(fractions: list[float]) -> tuple[list[float], list[float]]:
    # Small synthetic jitter on the flat part, as repeated RSS readings show.
    jitter = [0.993, 1.004, 0.997, 1.002, 1.0, 1.0]
    xs = [f * FULL_X for f in fractions]
    ys = [_synthetic_rss(f) * j for f, j in zip(fractions, jitter, strict=False)]
    return xs, ys


def test_find_plateau() -> None:
    from pipeprof.fit import find_plateau

    assert find_plateau([1, 2, 5, 10], [10, 20, 50, 100]) is None
    xs, ys = _series([0.01, 0.02, 0.05, 0.1, 0.2])
    plateau = find_plateau(xs, ys)
    assert plateau is not None and not plateau.whole
    assert plateau.end_x == 0.1 * FULL_X and plateau.count == 4
    assert find_plateau([1, 1], [5, 5]) is None  # one size is not a series


def test_flat_series_interval_covers_the_rise() -> None:
    from pipeprof.fit import predict_with_plateau

    xs, ys = _series([0.01, 0.02, 0.05, 0.1])
    # The straight fit misses, as it did on the cluster.
    _, old_upper = select_model(xs, ys).chosen.interval(FULL_X)
    assert old_upper < ACTUAL_FULL
    pred = predict_with_plateau(xs, ys, FULL_X)
    assert pred.plateau is not None and pred.plateau.whole
    assert pred.lower <= ACTUAL_FULL <= pred.upper
    assert pred.upper == pytest.approx(ys[-1] * 10)  # largest run scaled by 1 / 0.1
    assert "flat" in pred.note and "larger fractions" in pred.note


def test_flat_then_rise_fits_after_the_hinge() -> None:
    from pipeprof.fit import predict_with_plateau

    xs, ys = _series([0.01, 0.02, 0.05, 0.1, 0.2])
    pred = predict_with_plateau(xs, ys, FULL_X)
    assert pred.plateau is not None and not pred.plateau.whole
    assert pred.lower <= ACTUAL_FULL <= pred.upper
    # Two sizes after the hinge: the line through them, widened to the proportional bound.
    assert pred.point == pytest.approx(ACTUAL_FULL, rel=0.01)
    assert pred.upper == pytest.approx(ys[-1] * 5)
    assert "flat then rising" in pred.selection.reason

    xs, ys = _series([0.01, 0.02, 0.05, 0.1, 0.2, 0.4])
    pred = predict_with_plateau(xs, ys, FULL_X)
    assert "under three sizes" not in pred.note  # enough sizes to trust the tail fit
    assert pred.point == pytest.approx(ACTUAL_FULL, rel=0.01)


def test_growing_series_is_unchanged() -> None:
    from pipeprof.fit import predict_with_plateau

    xs = [1.0, 2.0, 5.0, 10.0, 20.0]
    ys = [11.0, 21.0, 52.0, 99.0, 201.0]
    pred = predict_with_plateau(xs, ys, 100.0)
    sel = select_model(xs, ys)
    assert pred.plateau is None
    assert (pred.lower, pred.upper) == sel.chosen.interval(100.0)
