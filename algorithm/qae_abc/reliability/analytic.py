"""Analytic reliability benchmarks and classical estimators.

The Gaussian difference case is deliberately simple: it gives a closed-form
failure probability against which estimator bias can be measured without a
surrogate or numerical-integration confounder.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import beta, norm, qmc


@dataclass(frozen=True)
class GaussianDifferenceBenchmark:
    """Limit state ``g = R - S`` for independent normal variables."""

    mu_r: float
    sigma_r: float
    mu_s: float
    sigma_s: float

    @property
    def beta_index(self) -> float:
        return (self.mu_r - self.mu_s) / np.hypot(self.sigma_r, self.sigma_s)

    @property
    def failure_probability(self) -> float:
        return float(norm.cdf(-self.beta_index))

    @classmethod
    def from_failure_probability(
        cls,
        failure_probability: float,
        sigma_r: float = 1.0,
        sigma_s: float = 1.0,
        mu_s: float = 0.0,
    ) -> "GaussianDifferenceBenchmark":
        if not 0.0 < failure_probability < 0.5:
            raise ValueError("failure_probability must lie in (0, 0.5)")
        beta_index = -float(norm.ppf(failure_probability))
        mu_r = mu_s + beta_index * np.hypot(sigma_r, sigma_s)
        return cls(mu_r=mu_r, sigma_r=sigma_r, mu_s=mu_s, sigma_s=sigma_s)

    def evaluate_standard_normals(self, z: np.ndarray) -> np.ndarray:
        """Return ``g`` for an ``(n, 2)`` array of independent N(0,1) values."""

        z = np.asarray(z, dtype=float)
        if z.ndim != 2 or z.shape[1] != 2:
            raise ValueError("z must have shape (n, 2)")
        resistance = self.mu_r + self.sigma_r * z[:, 0]
        load = self.mu_s + self.sigma_s * z[:, 1]
        return resistance - load


@dataclass(frozen=True)
class ProbabilityEstimate:
    estimate: float
    lower: float
    upper: float
    samples: int
    oracle_queries: int


def clopper_pearson_interval(
    successes: int,
    trials: int,
    confidence_level: float = 0.95,
) -> tuple[float, float]:
    """Two-sided exact binomial interval, including boundary observations."""

    if trials <= 0 or not 0 <= successes <= trials:
        raise ValueError("Require 0 <= successes <= trials and trials > 0")
    alpha = 1.0 - confidence_level
    lower = 0.0 if successes == 0 else float(beta.ppf(alpha / 2, successes, trials - successes + 1))
    upper = 1.0 if successes == trials else float(beta.ppf(1 - alpha / 2, successes + 1, trials - successes))
    return lower, upper


def estimate_mc(
    benchmark: GaussianDifferenceBenchmark,
    budget: int,
    rng: np.random.Generator,
    confidence_level: float = 0.95,
) -> ProbabilityEstimate:
    z = rng.standard_normal((budget, 2))
    failures = int(np.count_nonzero(benchmark.evaluate_standard_normals(z) <= 0.0))
    lower, upper = clopper_pearson_interval(failures, budget, confidence_level)
    return ProbabilityEstimate(
        estimate=failures / budget,
        lower=lower,
        upper=upper,
        samples=budget,
        oracle_queries=budget,
    )


def estimate_scrambled_qmc(
    benchmark: GaussianDifferenceBenchmark,
    budget: int,
    seed: int,
    confidence_level: float = 0.95,
) -> ProbabilityEstimate:
    """Scrambled Sobol estimate using the largest power of two <= ``budget``.

    The returned interval is a binomial-style descriptive interval and is not
    treated as an exact randomized-QMC coverage statement in the manuscript.
    Replicate-to-replicate error is the primary QMC uncertainty measure.
    """

    exponent = int(np.floor(np.log2(budget)))
    n_samples = 2**exponent
    engine = qmc.Sobol(d=2, scramble=True, seed=seed)
    uniforms = np.clip(engine.random_base2(exponent), np.finfo(float).eps, 1 - np.finfo(float).eps)
    z = norm.ppf(uniforms)
    failures = int(np.count_nonzero(benchmark.evaluate_standard_normals(z) <= 0.0))
    lower, upper = clopper_pearson_interval(failures, n_samples, confidence_level)
    return ProbabilityEstimate(
        estimate=failures / n_samples,
        lower=lower,
        upper=upper,
        samples=n_samples,
        oracle_queries=n_samples,
    )

