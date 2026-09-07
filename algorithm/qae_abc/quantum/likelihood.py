"""Likelihood-based low-depth amplitude estimation.

This module operates on depth-tagged counts. It is used both for controlled
statistical experiments and for counts emitted by Qiskit circuits. A controlled
visibility-decay experiment is not labeled as a hardware experiment.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Iterable

import numpy as np
from scipy.optimize import minimize, minimize_scalar
from scipy.stats import beta, chi2


PROB_EPS = 1e-12
THETA_EPS = 1e-10


@dataclass(frozen=True)
class CountSchedule:
    grover_depths: np.ndarray
    shots: np.ndarray
    effective_depths: np.ndarray
    successes: np.ndarray

    def __post_init__(self) -> None:
        arrays = [
            np.asarray(self.grover_depths),
            np.asarray(self.shots),
            np.asarray(self.effective_depths),
            np.asarray(self.successes),
        ]
        size = arrays[0].size
        if any(a.ndim != 1 or a.size != size for a in arrays):
            raise ValueError("All CountSchedule fields must be equal-length vectors")
        if np.any(arrays[1] <= 0) or np.any(arrays[3] < 0) or np.any(arrays[3] > arrays[1]):
            raise ValueError("Invalid shots or success counts")

    @property
    def oracle_queries(self) -> int:
        return int(np.sum(self.shots * (2 * self.grover_depths + 1)))

    @property
    def total_shots(self) -> int:
        return int(np.sum(self.shots))


@dataclass(frozen=True)
class AmplitudeEstimate:
    amplitude: float
    theta: float
    noise_lambda: float
    lower: float
    upper: float
    log_likelihood: float
    converged: bool
    interval_method: str


@dataclass(frozen=True)
class IterativeAmplitudeEstimate:
    """Shot-level result of the Grinko et al. iterative QAE protocol."""

    amplitude: float
    lower: float
    upper: float
    oracle_queries: int
    grover_depths: tuple[int, ...]
    shots: tuple[int, ...]
    successes: tuple[int, ...]
    converged: bool


def _clopper_pearson_interval(
    successes: int,
    trials: int,
    confidence_level: float,
) -> tuple[float, float]:
    """Exact two-sided binomial interval without a reliability-module dependency."""

    if trials <= 0 or not 0 <= successes <= trials:
        raise ValueError("Require 0 <= successes <= trials and trials > 0")
    if not 0.0 < confidence_level < 1.0:
        raise ValueError("confidence_level must lie in (0, 1)")
    alpha = 1.0 - confidence_level
    lower = 0.0 if successes == 0 else float(beta.ppf(alpha / 2, successes, trials - successes + 1))
    upper = 1.0 if successes == trials else float(beta.ppf(1 - alpha / 2, successes + 1, trials - successes))
    return lower, upper


def ideal_success_probability(theta: float | np.ndarray, grover_depths: np.ndarray) -> np.ndarray:
    return np.sin((2 * grover_depths + 1) * theta) ** 2


def observed_success_probability(
    theta: float | np.ndarray,
    noise_lambda: float | np.ndarray,
    grover_depths: np.ndarray,
    effective_depths: np.ndarray,
) -> np.ndarray:
    p_ideal = ideal_success_probability(theta, grover_depths)
    visibility = np.exp(-np.asarray(noise_lambda) * effective_depths)
    return np.clip(0.5 + visibility * (p_ideal - 0.5), PROB_EPS, 1.0 - PROB_EPS)


def allocate_schedule(
    budget: int,
    candidate_depths: Iterable[int],
    minimum_shots_per_depth: int = 12,
) -> tuple[np.ndarray, np.ndarray]:
    """Allocate equal shots while respecting an oracle-query budget.

    The deepest candidates are removed until each retained circuit can receive
    the declared minimum number of shots. Remaining budget is distributed one
    shot at a time from shallow to deep without exceeding the budget.
    """

    depths = np.array(sorted(set(int(m) for m in candidate_depths)), dtype=int)
    if depths.size == 0 or depths[0] != 0 or np.any(depths < 0):
        raise ValueError("candidate_depths must be nonnegative and include 0")
    costs = 2 * depths + 1
    while depths.size > 1 and minimum_shots_per_depth * int(costs.sum()) > budget:
        depths = depths[:-1]
        costs = costs[:-1]
    if int(costs.sum()) > budget:
        raise ValueError("Budget is too small for even one shot at the retained depths")
    base_shots = max(1, budget // int(costs.sum()))
    shots = np.full(depths.size, base_shots, dtype=int)
    remainder = budget - int(np.sum(shots * costs))
    for index, cost in enumerate(costs):
        add = remainder // int(cost)
        if add:
            shots[index] += add
            remainder -= int(add * cost)
    return depths, shots


def simulate_counts(
    amplitude: float,
    grover_depths: np.ndarray,
    shots: np.ndarray,
    rng: np.random.Generator,
    noise_lambda: float = 0.0,
    effective_depths: np.ndarray | None = None,
) -> CountSchedule:
    if not 0.0 <= amplitude <= 1.0:
        raise ValueError("amplitude must lie in [0, 1]")
    depths = np.asarray(grover_depths, dtype=int)
    shots = np.asarray(shots, dtype=int)
    if effective_depths is None:
        effective_depths = (2 * depths + 1).astype(float)
    else:
        effective_depths = np.asarray(effective_depths, dtype=float)
    theta = float(np.arcsin(np.sqrt(amplitude)))
    probabilities = observed_success_probability(theta, noise_lambda, depths, effective_depths)
    successes = rng.binomial(shots, probabilities)
    return CountSchedule(depths, shots, effective_depths, successes)


def _iqae_find_next_k(
    current_k: int,
    upper_half_circle: bool,
    theta_interval: tuple[float, float],
    min_ratio: float,
) -> tuple[int, bool]:
    """Find the largest non-aliasing Grover power used by iterative QAE.

    Angles use the normalized convention ``a = sin²(2π theta)`` with
    ``theta in [0, 1/4]``.  This is the reference interval construction from
    the IQAE algorithm, separated from circuit execution for statistical
    simulation and unit testing.
    """

    theta_lower, theta_upper = theta_interval
    old_scaling = 4 * current_k + 2
    max_scaling = int(1 / (2 * (theta_upper - theta_lower)))
    scaling = max_scaling - (max_scaling - 2) % 4
    while scaling >= min_ratio * old_scaling:
        theta_min = scaling * theta_lower - int(scaling * theta_lower)
        theta_max = scaling * theta_upper - int(scaling * theta_upper)
        if theta_min <= theta_max <= 0.5:
            return int((scaling - 2) / 4), True
        if theta_max >= theta_min >= 0.5:
            return int((scaling - 2) / 4), False
        scaling -= 4
    return int(current_k), upper_half_circle


def estimate_iterative_qae_from_sampler(
    query_budget: int,
    sample_successes: Callable[[int, int, int], int],
    confidence_level: float = 0.95,
    shots_per_round: int = 100,
    minimum_shots: int = 20,
    min_ratio: float = 2.0,
    max_rounds: int = 30,
) -> IterativeAmplitudeEstimate:
    """Run budget-capped IQAE from an actual or simulated measurement sampler.

    ``sample_successes(k, shots, round_index)`` must return the number of
    objective-one outcomes from an ``A Q^k`` circuit. Query cost is counted as
    ``shots * (2k + 1)`` to match the rest of this repository.
    """

    if query_budget < minimum_shots:
        raise ValueError("query_budget is too small")
    if not 0.0 < confidence_level < 1.0:
        raise ValueError("confidence_level must lie in (0, 1)")
    if shots_per_round < minimum_shots or minimum_shots <= 0:
        raise ValueError("Require shots_per_round >= minimum_shots > 0")
    alpha = 1.0 - confidence_level
    theta_intervals: list[tuple[float, float]] = [(0.0, 0.25)]
    amplitude_intervals: list[tuple[float, float]] = [(0.0, 1.0)]
    powers = [0]
    upper_half_circle = True
    counts_by_k: dict[int, tuple[int, int]] = {}
    depths: list[int] = []
    round_shots: list[int] = []
    successes: list[int] = []
    queries = 0

    for round_index in range(max_rounds):
        k, upper_half_circle = _iqae_find_next_k(
            powers[-1], upper_half_circle, theta_intervals[-1], min_ratio
        )
        cost_per_shot = 2 * k + 1
        affordable = (query_budget - queries) // cost_per_shot
        if affordable < minimum_shots:
            break
        shots = int(min(shots_per_round, affordable))
        one_counts = int(sample_successes(k, shots, round_index))
        if not 0 <= one_counts <= shots:
            raise ValueError("sample_successes returned an invalid count")
        prior_successes, prior_shots = counts_by_k.get(k, (0, 0))
        total_successes = prior_successes + one_counts
        total_shots = prior_shots + shots
        counts_by_k[k] = (total_successes, total_shots)
        lower_probability, upper_probability = _clopper_pearson_interval(
            total_successes, total_shots, 1.0 - alpha / max_rounds
        )
        if upper_half_circle:
            theta_min_i = np.arccos(1 - 2 * lower_probability) / (2 * np.pi)
            theta_max_i = np.arccos(1 - 2 * upper_probability) / (2 * np.pi)
        else:
            theta_min_i = 1 - np.arccos(1 - 2 * upper_probability) / (2 * np.pi)
            theta_max_i = 1 - np.arccos(1 - 2 * lower_probability) / (2 * np.pi)
        scaling = 4 * k + 2
        theta_lower = (int(scaling * theta_intervals[-1][0]) + theta_min_i) / scaling
        theta_upper = (int(scaling * theta_intervals[-1][1]) + theta_max_i) / scaling
        theta_intervals.append((float(theta_lower), float(theta_upper)))
        amplitude_lower = float(np.sin(2 * np.pi * theta_lower) ** 2)
        amplitude_upper = float(np.sin(2 * np.pi * theta_upper) ** 2)
        amplitude_intervals.append(tuple(sorted((amplitude_lower, amplitude_upper))))
        powers.append(k)
        depths.append(k)
        round_shots.append(shots)
        successes.append(one_counts)
        queries += shots * cost_per_shot

    lower, upper = amplitude_intervals[-1]
    return IterativeAmplitudeEstimate(
        amplitude=(lower + upper) / 2.0,
        lower=lower,
        upper=upper,
        oracle_queries=queries,
        grover_depths=tuple(depths),
        shots=tuple(round_shots),
        successes=tuple(successes),
        converged=queries > 0 and (upper - lower) < 1.0,
    )


def simulate_iterative_qae(
    amplitude: float,
    query_budget: int,
    rng: np.random.Generator,
    confidence_level: float = 0.95,
    shots_per_round: int = 100,
    minimum_shots: int = 20,
    min_ratio: float = 2.0,
    noise_lambda: float = 0.0,
    max_rounds: int = 30,
) -> IterativeAmplitudeEstimate:
    """Run IQAE with binomial ideal or controlled visibility-decay counts.

    Under nonzero decay the ideal IQAE interval update is intentionally
    retained, representing the conventional noise-naive IQAE baseline.
    """

    if not 0.0 <= amplitude <= 1.0:
        raise ValueError("amplitude must lie in [0, 1]")
    theta_true = float(np.arcsin(np.sqrt(amplitude)))

    def sampler(k: int, shots: int, _round_index: int) -> int:
        ideal_probability = float(np.sin((2 * k + 1) * theta_true) ** 2)
        visibility = math.exp(-noise_lambda * (2 * k + 1))
        observed_probability = float(
            np.clip(0.5 + visibility * (ideal_probability - 0.5), 0.0, 1.0)
        )
        return int(rng.binomial(shots, observed_probability))

    return estimate_iterative_qae_from_sampler(
        query_budget=query_budget,
        sample_successes=sampler,
        confidence_level=confidence_level,
        shots_per_round=shots_per_round,
        minimum_shots=minimum_shots,
        min_ratio=min_ratio,
        max_rounds=max_rounds,
    )


def _log_likelihood(theta: float, noise_lambda: float, schedule: CountSchedule) -> float:
    probabilities = observed_success_probability(
        theta,
        noise_lambda,
        schedule.grover_depths,
        schedule.effective_depths,
    )
    return float(
        np.sum(
            schedule.successes * np.log(probabilities)
            + (schedule.shots - schedule.successes) * np.log1p(-probabilities)
        )
    )


def _theta_grid_candidates(
    schedule: CountSchedule,
    noise_lambdas: Iterable[float],
    grid_size: int = 4097,
    keep: int = 12,
) -> list[tuple[float, float]]:
    theta_grid = np.linspace(THETA_EPS, np.pi / 2 - THETA_EPS, grid_size)
    candidates: list[tuple[float, float, float]] = []
    k = 2 * schedule.grover_depths + 1
    for lam in noise_lambdas:
        probabilities = np.sin(np.outer(theta_grid, k)) ** 2
        visibility = np.exp(-float(lam) * schedule.effective_depths)
        probabilities = np.clip(0.5 + visibility * (probabilities - 0.5), PROB_EPS, 1 - PROB_EPS)
        log_likelihood = (
            schedule.successes * np.log(probabilities)
            + (schedule.shots - schedule.successes) * np.log1p(-probabilities)
        ).sum(axis=1)
        local_maxima = np.flatnonzero(
            (log_likelihood[1:-1] >= log_likelihood[:-2])
            & (log_likelihood[1:-1] >= log_likelihood[2:])
        ) + 1
        local_maxima = np.unique(np.concatenate(([0], local_maxima, [grid_size - 1])))
        take = min(keep, len(local_maxima))
        best = local_maxima[np.argpartition(log_likelihood[local_maxima], -take)[-take:]]
        candidates.extend((float(log_likelihood[i]), float(theta_grid[i]), float(lam)) for i in best)
    candidates.sort(reverse=True)
    selected: list[tuple[float, float]] = []
    min_separation = np.pi / (4 * max(1, int(np.max(k))))
    for _, theta, lam in candidates:
        if all(abs(theta - prior_theta) > min_separation for prior_theta, _ in selected):
            selected.append((theta, lam))
        if len(selected) >= keep:
            break
    return selected


def _connected_profile_interval(
    theta_hat: float,
    max_log_likelihood: float,
    profile_log_likelihood,
    confidence_level: float,
) -> tuple[float, float]:
    threshold = max_log_likelihood - 0.5 * float(chi2.ppf(confidence_level, df=1))

    def accepted_margin(theta: float) -> float:
        return float(profile_log_likelihood(theta) - threshold)

    def seek(direction: int) -> float:
        boundary = THETA_EPS if direction < 0 else np.pi / 2 - THETA_EPS
        current = theta_hat
        step = max(theta_hat * 0.04, 2e-5) if direction < 0 else max((np.pi / 2 - theta_hat) * 0.02, 2e-5)
        current_margin = accepted_margin(current)
        for _ in range(50):
            proposal = max(boundary, current - step) if direction < 0 else min(boundary, current + step)
            proposal_margin = accepted_margin(proposal)
            if proposal_margin <= 0.0 <= current_margin:
                lo, hi = (proposal, current) if direction < 0 else (current, proposal)
                for _ in range(55):
                    mid = (lo + hi) / 2
                    margin = accepted_margin(mid)
                    if direction < 0:
                        if margin > 0:
                            hi = mid
                        else:
                            lo = mid
                    else:
                        if margin > 0:
                            lo = mid
                        else:
                            hi = mid
                return (lo + hi) / 2
            if proposal == boundary:
                return boundary
            current, current_margin = proposal, proposal_margin
            step *= 1.55
        return boundary

    return seek(-1), seek(1)


def fit_mlae(schedule: CountSchedule, confidence_level: float = 0.95) -> AmplitudeEstimate:
    candidates = _theta_grid_candidates(schedule, noise_lambdas=[0.0], keep=16)
    best_result = None
    max_k = int(np.max(2 * schedule.grover_depths + 1))
    # A sin^2(k theta) fringe has adjacent extrema pi/(2k) apart.  Staying
    # inside half that distance keeps the bounded scalar refinement unimodal.
    radius = np.pi / (4 * max(1, max_k))
    for theta_start, _ in candidates:
        lower = max(THETA_EPS, theta_start - radius)
        upper = min(np.pi / 2 - THETA_EPS, theta_start + radius)
        result = minimize_scalar(
            lambda theta: -_log_likelihood(float(theta), 0.0, schedule),
            bounds=(lower, upper),
            method="bounded",
            options={"xatol": 1e-12},
        )
        if best_result is None or result.fun < best_result.fun:
            best_result = result
    assert best_result is not None
    theta_hat = float(best_result.x)
    max_ll = -float(best_result.fun)
    theta_lower, theta_upper = _connected_profile_interval(
        theta_hat,
        max_ll,
        lambda theta: _log_likelihood(theta, 0.0, schedule),
        confidence_level,
    )
    return AmplitudeEstimate(
        amplitude=float(np.sin(theta_hat) ** 2),
        theta=theta_hat,
        noise_lambda=0.0,
        lower=float(np.sin(theta_lower) ** 2),
        upper=float(np.sin(theta_upper) ** 2),
        log_likelihood=max_ll,
        converged=bool(best_result.success),
        interval_method="connected likelihood-ratio profile (1 parameter)",
    )


def fit_visibility_calibrated_qae(
    schedule: CountSchedule,
    noise_lambda: float,
    confidence_level: float = 0.95,
) -> AmplitudeEstimate:
    """Fit amplitude with visibility decay fixed by a calibration experiment."""

    if noise_lambda < 0:
        raise ValueError("noise_lambda must be nonnegative")
    candidates = _theta_grid_candidates(schedule, noise_lambdas=[noise_lambda], keep=16)
    best_result = None
    max_k = int(np.max(2 * schedule.grover_depths + 1))
    radius = np.pi / (4 * max(1, max_k))
    for theta_start, _ in candidates:
        lower = max(THETA_EPS, theta_start - radius)
        upper = min(np.pi / 2 - THETA_EPS, theta_start + radius)
        result = minimize_scalar(
            lambda theta: -_log_likelihood(float(theta), noise_lambda, schedule),
            bounds=(lower, upper),
            method="bounded",
            options={"xatol": 1e-12},
        )
        if best_result is None or result.fun < best_result.fun:
            best_result = result
    assert best_result is not None
    theta_hat = float(best_result.x)
    max_ll = -float(best_result.fun)
    theta_lower, theta_upper = _connected_profile_interval(
        theta_hat,
        max_ll,
        lambda theta: _log_likelihood(theta, noise_lambda, schedule),
        confidence_level,
    )
    return AmplitudeEstimate(
        amplitude=float(np.sin(theta_hat) ** 2),
        theta=theta_hat,
        noise_lambda=float(noise_lambda),
        lower=float(np.sin(theta_lower) ** 2),
        upper=float(np.sin(theta_upper) ** 2),
        log_likelihood=max_ll,
        converged=bool(best_result.success),
        interval_method="connected likelihood-ratio profile (calibrated visibility)",
    )


def fit_noise_aware_qae(
    schedule: CountSchedule,
    confidence_level: float = 0.95,
    lambda_upper: float = 0.2,
) -> AmplitudeEstimate:
    lambda_starts = np.linspace(0.0, min(lambda_upper, 0.04), 5)
    starts = _theta_grid_candidates(schedule, noise_lambdas=lambda_starts, keep=16)
    best_result = None
    for theta_start, lambda_start in starts:
        result = minimize(
            lambda params: -_log_likelihood(float(params[0]), float(params[1]), schedule),
            x0=np.array([theta_start, lambda_start]),
            method="Nelder-Mead",
            bounds=((THETA_EPS, np.pi / 2 - THETA_EPS), (0.0, lambda_upper)),
            options={"xatol": 1e-10, "fatol": 1e-8, "maxiter": 1500},
        )
        if best_result is None or result.fun < best_result.fun:
            best_result = result
    assert best_result is not None
    theta_hat, lambda_hat = map(float, best_result.x)
    max_ll = -float(best_result.fun)

    def profile(theta: float) -> float:
        result = minimize_scalar(
            lambda lam: -_log_likelihood(theta, float(lam), schedule),
            bounds=(0.0, lambda_upper),
            method="bounded",
            options={"xatol": 1e-10},
        )
        boundary_ll = max(_log_likelihood(theta, 0.0, schedule), _log_likelihood(theta, lambda_upper, schedule))
        return max(-float(result.fun), boundary_ll)

    theta_lower, theta_upper = _connected_profile_interval(
        theta_hat,
        max_ll,
        profile,
        confidence_level,
    )
    return AmplitudeEstimate(
        amplitude=float(np.sin(theta_hat) ** 2),
        theta=theta_hat,
        noise_lambda=lambda_hat,
        lower=float(np.sin(theta_lower) ** 2),
        upper=float(np.sin(theta_upper) ** 2),
        log_likelihood=max_ll,
        converged=bool(best_result.success),
        interval_method="connected profile-likelihood ratio (noise profiled)",
    )
