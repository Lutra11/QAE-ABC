"""Nonlinear standard-normal reliability benchmarks and classical baselines.

The two limit states mirror the S0-2 cases in the frozen research plan.  Their
thresholds are calibrated against deterministic numerical references, while a
separate experiment records the requested >=10^7-point scrambled-Sobol audit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np
from scipy.integrate import quad
from scipy.optimize import brentq, minimize
from scipy.stats import chi2, norm, qmc

from qae_abc.reliability.analytic import ProbabilityEstimate, clopper_pearson_interval


class StandardNormalLimitState(Protocol):
    dimension: int

    def evaluate_standard_normals(self, z: np.ndarray) -> np.ndarray: ...


@dataclass(frozen=True)
class ParabolicLimitState:
    """``g=c-z2-kappa*(z1-a)^2`` for two independent standard normals."""

    c: float
    kappa: float = 0.25
    a: float = 0.5
    dimension: int = 2

    def evaluate_standard_normals(self, z: np.ndarray) -> np.ndarray:
        z = np.asarray(z, dtype=float)
        if z.ndim != 2 or z.shape[1] != self.dimension:
            raise ValueError("z must have shape (n, 2)")
        return self.c - z[:, 1] - self.kappa * (z[:, 0] - self.a) ** 2

    @property
    def failure_probability(self) -> float:
        integrand = lambda x: norm.sf(self.c - self.kappa * (x - self.a) ** 2) * norm.pdf(x)
        probability, _ = quad(integrand, -np.inf, np.inf, epsabs=2e-13, epsrel=2e-13, limit=300)
        return float(probability)

    @classmethod
    def from_failure_probability(
        cls,
        failure_probability: float,
        kappa: float = 0.25,
        a: float = 0.5,
    ) -> "ParabolicLimitState":
        if not 0.0 < failure_probability < 0.5:
            raise ValueError("failure_probability must lie in (0, 0.5)")

        def residual(c: float) -> float:
            return cls(c=c, kappa=kappa, a=a).failure_probability - failure_probability

        threshold = brentq(residual, -10.0, 100.0, xtol=1e-13, rtol=1e-13)
        return cls(c=float(threshold), kappa=kappa, a=a)


@dataclass(frozen=True)
class QuadraticLimitState:
    """``g=c-sum(z_j^2)`` with a chi-square reference probability."""

    c: float
    dimension: int = 5

    def __post_init__(self) -> None:
        if self.dimension < 2:
            raise ValueError("dimension must be at least two")
        if self.c <= 0:
            raise ValueError("c must be positive")

    def evaluate_standard_normals(self, z: np.ndarray) -> np.ndarray:
        z = np.asarray(z, dtype=float)
        if z.ndim != 2 or z.shape[1] != self.dimension:
            raise ValueError(f"z must have shape (n, {self.dimension})")
        return self.c - np.sum(z**2, axis=1)

    @property
    def failure_probability(self) -> float:
        return float(chi2.sf(self.c, self.dimension))

    @classmethod
    def from_failure_probability(
        cls,
        failure_probability: float,
        dimension: int = 5,
    ) -> "QuadraticLimitState":
        if not 0.0 < failure_probability < 0.5:
            raise ValueError("failure_probability must lie in (0, 0.5)")
        return cls(c=float(chi2.isf(failure_probability, dimension)), dimension=dimension)


@dataclass(frozen=True)
class FormEstimate:
    estimate: float
    beta_index: float
    design_point: tuple[float, ...]
    model_evaluations: int
    converged: bool


@dataclass(frozen=True)
class SubsetSimulationEstimate:
    estimate: float
    lower: float
    upper: float
    model_evaluations: int
    samples_per_level: int
    levels: int
    thresholds: tuple[float, ...]
    acceptance_rates: tuple[float, ...]
    converged: bool


def estimate_mc_limit_state(
    benchmark: StandardNormalLimitState,
    budget: int,
    rng: np.random.Generator,
    confidence_level: float = 0.95,
) -> ProbabilityEstimate:
    if budget <= 0:
        raise ValueError("budget must be positive")
    z = rng.standard_normal((budget, benchmark.dimension))
    failures = int(np.count_nonzero(benchmark.evaluate_standard_normals(z) <= 0.0))
    lower, upper = clopper_pearson_interval(failures, budget, confidence_level)
    return ProbabilityEstimate(failures / budget, lower, upper, budget, budget)


def estimate_qmc_limit_state(
    benchmark: StandardNormalLimitState,
    budget: int,
    seed: int,
    confidence_level: float = 0.95,
) -> ProbabilityEstimate:
    if budget <= 0:
        raise ValueError("budget must be positive")
    exponent = int(np.floor(np.log2(budget)))
    n_samples = 2**exponent
    uniforms = qmc.Sobol(benchmark.dimension, scramble=True, seed=seed).random_base2(exponent)
    uniforms = np.clip(uniforms, np.finfo(float).eps, 1 - np.finfo(float).eps)
    failures = int(np.count_nonzero(benchmark.evaluate_standard_normals(norm.ppf(uniforms)) <= 0.0))
    lower, upper = clopper_pearson_interval(failures, n_samples, confidence_level)
    return ProbabilityEstimate(failures / n_samples, lower, upper, n_samples, n_samples)


def estimate_form(
    benchmark: StandardNormalLimitState,
    starts: int = 24,
) -> FormEstimate:
    """First-order reliability estimate from a multi-start design-point search."""

    if starts < 1:
        raise ValueError("starts must be positive")
    dimension = benchmark.dimension
    initial_points = [np.zeros(dimension)]
    radius = 3.0
    for axis in range(dimension):
        for sign in (-1.0, 1.0):
            point = np.zeros(dimension)
            point[axis] = sign * radius
            initial_points.append(point)
    rng = np.random.default_rng(910247 + dimension)
    while len(initial_points) < starts:
        direction = rng.standard_normal(dimension)
        direction /= np.linalg.norm(direction)
        initial_points.append(radius * direction)

    candidates = []
    model_evaluations = 0
    for initial in initial_points[:starts]:
        result = minimize(
            lambda x: 0.5 * float(np.dot(x, x)),
            initial,
            method="SLSQP",
            constraints={"type": "eq", "fun": lambda x: float(benchmark.evaluate_standard_normals(x[None, :])[0])},
            options={"ftol": 1e-12, "maxiter": 1000},
        )
        model_evaluations += int(result.nfev)
        residual = abs(float(benchmark.evaluate_standard_normals(np.asarray(result.x)[None, :])[0]))
        model_evaluations += 1
        if result.success and residual <= 1e-7 and np.all(np.isfinite(result.x)):
            candidates.append(result)
    if not candidates:
        return FormEstimate(np.nan, np.nan, tuple(np.full(dimension, np.nan)), model_evaluations, False)
    best = min(candidates, key=lambda item: float(np.dot(item.x, item.x)))
    beta_index = float(np.linalg.norm(best.x))
    return FormEstimate(
        estimate=float(norm.cdf(-beta_index)),
        beta_index=beta_index,
        design_point=tuple(float(value) for value in best.x),
        model_evaluations=model_evaluations,
        converged=True,
    )


def estimate_subset_simulation(
    benchmark: StandardNormalLimitState,
    samples_per_level: int,
    rng: np.random.Generator,
    conditional_probability: float = 0.1,
    proposal_scale: float = 1.0,
    max_levels: int = 8,
    confidence_level: float = 0.95,
) -> SubsetSimulationEstimate:
    """Modified-Metropolis subset simulation in standard-normal space.

    Intervals are delta-method lognormal approximations and are explicitly
    descriptive; replicate dispersion is used for inferential comparisons.
    """

    if samples_per_level < 100:
        raise ValueError("samples_per_level must be at least 100")
    if not 0.0 < conditional_probability < 1.0:
        raise ValueError("conditional_probability must lie in (0, 1)")
    if proposal_scale <= 0.0 or max_levels < 1:
        raise ValueError("proposal_scale and max_levels must be positive")
    n_seeds = int(round(samples_per_level * conditional_probability))
    if n_seeds < 1 or samples_per_level % n_seeds:
        raise ValueError("samples_per_level must be divisible by its rounded conditional seed count")
    chain_length = samples_per_level // n_seeds
    z = rng.standard_normal((samples_per_level, benchmark.dimension))
    g = benchmark.evaluate_standard_normals(z)
    model_evaluations = samples_per_level
    thresholds: list[float] = []
    acceptance_rates: list[float] = []
    prior_levels = 0

    while True:
        order = np.argsort(g, kind="mergesort")
        threshold = float(g[order[n_seeds - 1]])
        thresholds.append(max(0.0, threshold))
        if threshold <= 0.0:
            failure_fraction = float(np.mean(g <= 0.0))
            probability = conditional_probability**prior_levels * failure_fraction
            break
        if prior_levels >= max_levels - 1:
            probability = conditional_probability ** (prior_levels + 1)
            failure_fraction = conditional_probability
            break

        seeds = z[order[:n_seeds]].copy()
        next_z = np.empty_like(z)
        next_g = np.empty_like(g)
        accepted_components = 0
        proposed_components = 0
        cursor = 0
        for seed in seeds:
            current = seed.copy()
            current_g = float(benchmark.evaluate_standard_normals(current[None, :])[0])
            model_evaluations += 1
            for chain_index in range(chain_length):
                if chain_index:
                    candidate = current.copy()
                    for coordinate in range(benchmark.dimension):
                        proposed = candidate[coordinate] + proposal_scale * rng.standard_normal()
                        log_ratio = -0.5 * (proposed**2 - candidate[coordinate] ** 2)
                        proposed_components += 1
                        if np.log(rng.random()) <= min(0.0, log_ratio):
                            candidate[coordinate] = proposed
                            accepted_components += 1
                    candidate_g = float(benchmark.evaluate_standard_normals(candidate[None, :])[0])
                    model_evaluations += 1
                    if candidate_g <= threshold:
                        current, current_g = candidate, candidate_g
                next_z[cursor] = current
                next_g[cursor] = current_g
                cursor += 1
        z, g = next_z, next_g
        acceptance_rates.append(accepted_components / max(1, proposed_components))
        prior_levels += 1

    probability = float(np.clip(probability, np.finfo(float).tiny, 1.0))
    # Independent-chain approximation; autocorrelation is represented by the
    # across-replicate dispersion in the final statistical analysis.
    cov_squared = prior_levels * (1 - conditional_probability) / (
        samples_per_level * conditional_probability
    ) + (1 - failure_fraction) / (samples_per_level * max(failure_fraction, 1 / samples_per_level))
    z_value = float(norm.ppf(0.5 + confidence_level / 2))
    sigma_log = float(np.sqrt(np.log1p(max(0.0, cov_squared))))
    lower = float(max(0.0, probability * np.exp(-z_value * sigma_log)))
    upper = float(min(1.0, probability * np.exp(z_value * sigma_log)))
    return SubsetSimulationEstimate(
        estimate=probability,
        lower=lower,
        upper=upper,
        model_evaluations=model_evaluations,
        samples_per_level=samples_per_level,
        levels=prior_levels + 1,
        thresholds=tuple(thresholds),
        acceptance_rates=tuple(acceptance_rates),
        converged=thresholds[-1] == 0.0,
    )


def estimate_subset_simulation_pcn(
    benchmark: StandardNormalLimitState,
    samples_per_level: int,
    rng: np.random.Generator,
    conditional_probability: float = 0.1,
    pcn_step: float = 0.8,
    max_levels: int = 8,
    confidence_level: float = 0.95,
) -> SubsetSimulationEstimate:
    """Vectorized subset simulation using prior-preserving pCN proposals.

    The pCN kernel leaves the standard-normal prior invariant, so conditional
    transitions require only the intermediate-event acceptance test.  Chains
    are advanced in parallel for efficient use inside optimization loops.
    """

    if samples_per_level < 100:
        raise ValueError("samples_per_level must be at least 100")
    if not 0.0 < conditional_probability < 1.0:
        raise ValueError("conditional_probability must lie in (0, 1)")
    if not 0.0 < pcn_step <= 1.0:
        raise ValueError("pcn_step must lie in (0, 1]")
    n_seeds = int(round(samples_per_level * conditional_probability))
    if n_seeds < 1 or samples_per_level % n_seeds:
        raise ValueError("samples_per_level must tile the conditional chains")
    chain_length = samples_per_level // n_seeds
    z = rng.standard_normal((samples_per_level, benchmark.dimension))
    g = benchmark.evaluate_standard_normals(z)
    model_evaluations = samples_per_level
    thresholds: list[float] = []
    acceptance_rates: list[float] = []
    prior_levels = 0
    rho = float(pcn_step)
    persistence = float(np.sqrt(1.0 - rho**2))

    while True:
        order = np.argsort(g, kind="mergesort")
        threshold = float(g[order[n_seeds - 1]])
        thresholds.append(max(0.0, threshold))
        if threshold <= 0.0:
            failure_fraction = float(np.mean(g <= 0.0))
            probability = conditional_probability**prior_levels * failure_fraction
            break
        if prior_levels >= max_levels - 1:
            probability = conditional_probability ** (prior_levels + 1)
            failure_fraction = conditional_probability
            break
        current = z[order[:n_seeds]].copy()
        current_g = g[order[:n_seeds]].copy()
        chains_z = np.empty((chain_length, n_seeds, benchmark.dimension), dtype=float)
        chains_g = np.empty((chain_length, n_seeds), dtype=float)
        chains_z[0], chains_g[0] = current, current_g
        accepted = 0
        proposed = 0
        for step in range(1, chain_length):
            candidate = persistence * current + rho * rng.standard_normal(current.shape)
            candidate_g = benchmark.evaluate_standard_normals(candidate)
            model_evaluations += n_seeds
            accept = candidate_g <= threshold
            current[accept] = candidate[accept]
            current_g[accept] = candidate_g[accept]
            accepted += int(np.count_nonzero(accept))
            proposed += n_seeds
            chains_z[step], chains_g[step] = current, current_g
        z = chains_z.reshape(samples_per_level, benchmark.dimension)
        g = chains_g.reshape(samples_per_level)
        acceptance_rates.append(accepted / max(1, proposed))
        prior_levels += 1

    probability = float(np.clip(probability, np.finfo(float).tiny, 1.0))
    cov_squared = prior_levels * (1 - conditional_probability) / (
        samples_per_level * conditional_probability
    ) + (1 - failure_fraction) / (
        samples_per_level * max(failure_fraction, 1 / samples_per_level)
    )
    z_value = float(norm.ppf(0.5 + confidence_level / 2))
    sigma_log = float(np.sqrt(np.log1p(max(0.0, cov_squared))))
    return SubsetSimulationEstimate(
        estimate=probability,
        lower=float(max(0.0, probability * np.exp(-z_value * sigma_log))),
        upper=float(min(1.0, probability * np.exp(z_value * sigma_log))),
        model_evaluations=model_evaluations,
        samples_per_level=samples_per_level,
        levels=prior_levels + 1,
        thresholds=tuple(thresholds),
        acceptance_rates=tuple(acceptance_rates),
        converged=thresholds[-1] == 0.0,
    )
