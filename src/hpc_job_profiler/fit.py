"""Scaling models for resource use against input size, with prediction intervals.

Two models are fitted by ordinary least squares:

* linear: ``y = a + b * x``
* power law: ``y = c * x ** k``, fitted as a line in log-log space

The model is chosen by forward extrapolation cross-validation: each point is
predicted from the points with strictly smaller ``x`` only, which is the job
the model has to do when it predicts the full input from small subsamples.
The score is the mean absolute log ratio between prediction and observation,
so a factor of two too high and a factor of two too low cost the same.

Prediction intervals use the standard OLS formula with a Student t quantile
(computed here, so the package needs no SciPy). For the power law the
interval is built in log space and is multiplicative after back-transforming.
docs/algorithm.md derives both and reports how well they covered real runs.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ModelKind(StrEnum):
    """Available scaling models."""

    LINEAR = "linear"
    POWER = "power"


class FitError(ValueError):
    """Raised when there are too few or unusable points to fit."""


# Student t quantile without SciPy.


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (modified Lentz method)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    d = tiny if abs(d) < tiny else d
    d = 1.0 / d
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = tiny if abs(d) < tiny else d
        c = 1.0 + aa / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = tiny if abs(d) < tiny else d
        c = 1.0 + aa / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    return h


def regularized_beta(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_front = (
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    )
    front = math.exp(log_front)
    # Use the symmetry relation where the continued fraction converges fastest.
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def student_t_cdf(t: float, df: float) -> float:
    """Cumulative distribution function of Student's t."""
    if df <= 0:
        raise ValueError("degrees of freedom must be positive")
    x = df / (df + t * t)
    tail = 0.5 * regularized_beta(df / 2.0, 0.5, x)
    return 1.0 - tail if t >= 0 else tail


def student_t_ppf(p: float, df: float) -> float:
    """Quantile of Student's t, by bisection on the CDF (absolute error below 1e-9)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    if p == 0.5:
        return 0.0
    if p < 0.5:
        return -student_t_ppf(1.0 - p, df)
    lo, hi = 0.0, 1.0
    while student_t_cdf(hi, df) < p:
        hi *= 2.0
        if hi > 1e12:
            break
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if student_t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-10:
            break
    return 0.5 * (lo + hi)


# Least squares fits.


@dataclass(frozen=True)
class Fit:
    """A fitted scaling model.

    ``intercept`` and ``slope`` are in model space: for the power law they are
    ``log(c)`` and ``k`` of ``log(y) = log(c) + k * log(x)``.
    """

    kind: ModelKind
    intercept: float
    slope: float
    n: int
    residual_sd: float
    x_mean: float
    sxx: float

    @property
    def dof(self) -> int:
        return self.n - 2

    def _tx(self, x: float) -> float:
        return math.log(x) if self.kind is ModelKind.POWER else x

    def predict(self, x: float) -> float:
        value = self.intercept + self.slope * self._tx(x)
        return math.exp(value) if self.kind is ModelKind.POWER else value

    def interval(self, x: float, level: float = 0.95) -> tuple[float, float]:
        """Two sided prediction interval for a new observation at ``x``.

        With fewer than three points there is no residual variance estimate,
        so the interval collapses to the point prediction.
        """
        centre = self.intercept + self.slope * self._tx(x)
        if self.dof < 1 or self.sxx <= 0:
            half = 0.0
        else:
            t = student_t_ppf(0.5 + level / 2.0, self.dof)
            var_factor = 1.0 + 1.0 / self.n + (self._tx(x) - self.x_mean) ** 2 / self.sxx
            half = t * self.residual_sd * math.sqrt(var_factor)
        if self.kind is ModelKind.POWER:
            return math.exp(centre - half), math.exp(centre + half)
        return max(0.0, centre - half), max(0.0, centre + half)

    def formula(self, var: str = "x") -> str:
        """Human readable formula in the original units."""
        if self.kind is ModelKind.POWER:
            return f"{math.exp(self.intercept):.6g} * {var}^{self.slope:.4f}"
        sign = "+" if self.slope >= 0 else "-"
        return f"{self.intercept:.6g} {sign} {abs(self.slope):.6g} * {var}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": str(self.kind),
            "intercept": self.intercept,
            "slope": self.slope,
            "n": self.n,
            "residual_sd": self.residual_sd,
            "x_mean": self.x_mean,
            "sxx": self.sxx,
            "formula": self.formula(),
        }


def _ols(xs: Sequence[float], ys: Sequence[float], kind: ModelKind) -> Fit:
    n = len(xs)
    if n != len(ys):
        raise FitError("x and y must have the same length")
    if n < 2:
        raise FitError("at least two points are needed")
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    sxx = sum((x - x_mean) ** 2 for x in xs)
    if sxx <= 0:
        raise FitError("all x values are equal; use more than one subsample size")
    sxy = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys, strict=True))
    slope = sxy / sxx
    intercept = y_mean - slope * x_mean
    sse = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys, strict=True))
    residual_sd = math.sqrt(sse / (n - 2)) if n > 2 else 0.0
    return Fit(kind, intercept, slope, n, residual_sd, x_mean, sxx)


def fit_linear(xs: Sequence[float], ys: Sequence[float]) -> Fit:
    return _ols(xs, ys, ModelKind.LINEAR)


def fit_power(xs: Sequence[float], ys: Sequence[float]) -> Fit:
    """Fit ``y = c * x ** k`` in log-log space; all values must be positive."""
    if any(x <= 0 for x in xs) or any(y <= 0 for y in ys):
        raise FitError("the power law needs positive x and y")
    return _ols([math.log(x) for x in xs], [math.log(y) for y in ys], ModelKind.POWER)


FITTERS: dict[ModelKind, Callable[[Sequence[float], Sequence[float]], Fit]] = {
    ModelKind.LINEAR: fit_linear,
    ModelKind.POWER: fit_power,
}

#: Error charged when a model predicts zero or less, which can never be right.
NONPOSITIVE_PENALTY = math.log(1e3)


def extrapolation_errors(xs: Sequence[float], ys: Sequence[float], kind: ModelKind) -> list[float]:
    """Absolute log errors of predicting each point from all points with smaller ``x``.

    Points are only scored once at least two distinct smaller ``x`` values
    exist, since a line needs two.
    """
    pairs = sorted(zip(xs, ys, strict=True))
    errors: list[float] = []
    for index, (x_new, y_new) in enumerate(pairs):
        train = [(x, y) for x, y in pairs[:index] if x < x_new]
        if len({x for x, _ in train}) < 2 or y_new <= 0:
            continue
        try:
            fit = FITTERS[kind]([x for x, _ in train], [y for _, y in train])
        except FitError:
            errors.append(NONPOSITIVE_PENALTY)
            continue
        predicted = fit.predict(x_new)
        if predicted <= 0:
            errors.append(NONPOSITIVE_PENALTY)
        else:
            errors.append(abs(math.log(predicted / y_new)))
    return errors


@dataclass
class Selection:
    """The chosen model and the evidence for the choice."""

    chosen: Fit
    fits: dict[ModelKind, Fit]
    cv_scores: dict[ModelKind, float | None]
    cv_errors: dict[ModelKind, list[float]] = field(default_factory=dict)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "chosen": str(self.chosen.kind),
            "reason": self.reason,
            "fits": {str(k): v.to_dict() for k, v in self.fits.items()},
            "cv_scores": {str(k): v for k, v in self.cv_scores.items()},
            "cv_errors": {str(k): v for k, v in self.cv_errors.items()},
        }


def select_model(
    xs: Sequence[float],
    ys: Sequence[float],
    candidates: Sequence[ModelKind] = (ModelKind.LINEAR, ModelKind.POWER),
) -> Selection:
    """Fit each candidate and choose the one with the lowest extrapolation CV score.

    Ties (within 1e-9) go to the linear model, the simpler of the two. When no
    model can be scored (fewer than three distinct sizes) the linear model is
    used and the reason says so.
    """
    fits: dict[ModelKind, Fit] = {}
    scores: dict[ModelKind, float | None] = {}
    errors: dict[ModelKind, list[float]] = {}
    for kind in candidates:
        try:
            fits[kind] = FITTERS[kind](xs, ys)
        except FitError:
            continue
        errs = extrapolation_errors(xs, ys, kind)
        errors[kind] = errs
        scores[kind] = sum(errs) / len(errs) if errs else None
    if not fits:
        raise FitError("no model could be fitted to these points")
    scored = {k: s for k, s in scores.items() if s is not None}
    if not scored:
        chosen_kind = ModelKind.LINEAR if ModelKind.LINEAR in fits else next(iter(fits))
        reason = "fewer than three distinct sizes, so no cross-validation; linear by default"
    else:
        best = min(scored.values())
        tied = [k for k, s in scored.items() if s - best <= 1e-9]
        chosen_kind = ModelKind.LINEAR if ModelKind.LINEAR in tied else tied[0]
        detail = ", ".join(f"{k}={s:.4f}" for k, s in scored.items())
        reason = f"lowest mean absolute log extrapolation error ({detail})"
    return Selection(fits[chosen_kind], fits, scores, errors, reason)
