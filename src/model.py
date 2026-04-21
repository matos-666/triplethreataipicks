"""Probability + EV model.

Per-player, per-market: fit a Normal (or Poisson for low-count integer stats) to
the last N games. Convert the sportsbook line into P(Over), P(Under), then
compute EV for both sides given the posted decimal odds.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import erf, sqrt

import numpy as np
from scipy.stats import poisson


@dataclass
class ModelOutput:
    mean: float
    std: float
    n: int
    prob_over: float
    prob_under: float


def _norm_sf(x: float, mu: float, sigma: float) -> float:
    """P(X > x) for Normal(mu, sigma). Half-point correction baked in by caller."""
    if sigma <= 1e-9:
        return 1.0 if mu > x else 0.0
    return 0.5 * (1 - erf((x - mu) / (sigma * sqrt(2))))


def fit_and_predict(
    values: np.ndarray,
    line: float,
    distribution: str = "normal",
) -> ModelOutput:
    n = len(values)
    if n < 1:
        return ModelOutput(0.0, 0.0, 0, 0.5, 0.5)
    mu = float(np.mean(values))
    sigma = float(np.std(values, ddof=1)) if n > 1 else max(1.0, mu * 0.2)

    if distribution == "poisson":
        # Continuity-correct for integer Poisson: P(X > line) = 1 - CDF(floor(line))
        if line == int(line):
            p_over = float(1 - poisson.cdf(line, max(mu, 0.01)))
        else:
            p_over = float(1 - poisson.cdf(int(line), max(mu, 0.01)))
    else:
        # Sportsbook lines are .5 for most prop markets, so no continuity correction.
        # For integer lines we treat X == line as a push (excluded from both sides proportionally).
        if line == int(line):
            p_eq = 0.0  # treat as 0 for continuous approximation
        p_over = _norm_sf(line, mu, sigma)

    p_over = max(0.001, min(0.999, p_over))
    return ModelOutput(mean=mu, std=sigma, n=n, prob_over=p_over, prob_under=1 - p_over)


def ev(prob: float, decimal_odds: float) -> float:
    """Expected value per unit staked. prob in [0,1], decimal_odds >= 1."""
    return prob * (decimal_odds - 1) - (1 - prob)


def kelly(prob: float, decimal_odds: float, fraction: float = 0.25) -> float:
    b = decimal_odds - 1
    q = 1 - prob
    if b <= 0:
        return 0.0
    raw = (b * prob - q) / b
    return max(0.0, raw * fraction)


def implied_prob(decimal_odds: float) -> float:
    return 1.0 / decimal_odds
