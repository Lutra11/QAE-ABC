"""Confidence-aware Artificial Bee Colony optimizer for the truss case."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np


@dataclass(frozen=True)
class ReliabilityEvaluation:
    estimate: float
    lower: float
    upper: float
    true_probability: float
    oracle_queries: int
    estimator_calls: int


@dataclass
class FoodSource:
    design: np.ndarray
    mass: float
    reliability: ReliabilityEvaluation
    trials: int = 0


def constraint_rank(source: FoodSource, target_probability: float) -> tuple[float, ...]:
    """Deb-style lexicographic rank with an explicit uncertain class."""

    estimate = source.reliability
    if estimate.upper <= target_probability:
        return (0.0, source.mass, estimate.upper)
    if estimate.lower <= target_probability < estimate.upper:
        ambiguity = max(0.0, estimate.upper - target_probability) / target_probability
        return (1.0, ambiguity, source.mass)
    violation = (estimate.lower - target_probability) / target_probability
    return (2.0, violation, source.mass)


def point_estimate_constraint_rank(
    source: FoodSource, target_probability: float
) -> tuple[float, ...]:
    """Ablation rank that deliberately ignores interval uncertainty."""

    estimate = source.reliability.estimate
    if estimate <= target_probability:
        return (0.0, source.mass, estimate)
    return (2.0, (estimate - target_probability) / target_probability, source.mass)


class ConfidenceAwareABC:
    def __init__(
        self,
        objective: Callable[[np.ndarray], float],
        evaluator: Callable[[np.ndarray, np.random.Generator], ReliabilityEvaluation],
        lower_bounds: np.ndarray,
        upper_bounds: np.ndarray,
        target_probability: float,
        food_sources: int = 30,
        iterations: int = 100,
        global_guidance: float = 0.5,
        scout_limit: int | None = None,
        temperature: float = 5.0,
        use_confidence_intervals: bool = True,
    ) -> None:
        self.objective = objective
        self.evaluator = evaluator
        self.lower = np.asarray(lower_bounds, dtype=float)
        self.upper = np.asarray(upper_bounds, dtype=float)
        self.target = float(target_probability)
        self.food_source_count = int(food_sources)
        self.iterations = int(iterations)
        self.global_guidance = float(global_guidance)
        self.scout_limit = int(scout_limit or (food_sources * len(self.lower) / 2))
        self.temperature = float(temperature)
        self.use_confidence_intervals = bool(use_confidence_intervals)
        if self.lower.shape != self.upper.shape or np.any(self.lower >= self.upper):
            raise ValueError("Invalid design bounds")

    def _rank(self, source: FoodSource) -> tuple[float, ...]:
        if self.use_confidence_intervals:
            return constraint_rank(source, self.target)
        return point_estimate_constraint_rank(source, self.target)

    def _evaluate(self, design: np.ndarray, rng: np.random.Generator) -> FoodSource:
        clipped = np.clip(np.asarray(design, dtype=float), self.lower, self.upper)
        canonicalizer = getattr(self.evaluator, "canonicalize_design", None)
        if callable(canonicalizer):
            clipped = np.clip(np.asarray(canonicalizer(clipped), dtype=float), self.lower, self.upper)
        return FoodSource(clipped, float(self.objective(clipped)), self.evaluator(clipped, rng))

    def _neighbor(
        self,
        population: list[FoodSource],
        index: int,
        best: FoodSource,
        rng: np.random.Generator,
    ) -> np.ndarray:
        candidates = np.delete(np.arange(len(population)), index)
        other = population[int(rng.choice(candidates))]
        phi = rng.uniform(-1.0, 1.0, size=len(self.lower))
        guided = (
            population[index].design
            + phi * (population[index].design - other.design)
            + self.global_guidance * rng.random(len(self.lower)) * (best.design - population[index].design)
        )
        return np.clip(guided, self.lower, self.upper)

    def _greedy_replace(self, incumbent: FoodSource, candidate: FoodSource) -> FoodSource:
        if self._rank(candidate) < self._rank(incumbent):
            candidate.trials = 0
            return candidate
        incumbent.trials += 1
        return incumbent

    @staticmethod
    def _archive_copy(source: FoodSource) -> FoodSource:
        """Return an immutable-in-practice copy for the global best archive."""

        return FoodSource(
            source.design.copy(),
            source.mass,
            source.reliability,
            trials=0,
        )

    def optimize(self, seed: int) -> tuple[FoodSource, list[dict[str, float | int]]]:
        rng = np.random.default_rng(seed)
        evaluation_seed = int(np.random.SeedSequence([seed, 923]).generate_state(1)[0])
        evaluation_rng = np.random.default_rng(evaluation_seed)
        designs = rng.uniform(self.lower, self.upper, size=(self.food_source_count, len(self.lower)))
        population = [self._evaluate(design, evaluation_rng) for design in designs]
        global_best = self._archive_copy(
            min(population, key=self._rank)
        )
        history: list[dict[str, float | int]] = []
        cumulative_queries = sum(source.reliability.oracle_queries for source in population)
        cumulative_calls = sum(source.reliability.estimator_calls for source in population)
        for iteration in range(self.iterations):
            for index in range(len(population)):
                candidate = self._evaluate(
                    self._neighbor(population, index, global_best, rng), evaluation_rng
                )
                cumulative_queries += candidate.reliability.oracle_queries
                cumulative_calls += candidate.reliability.estimator_calls
                population[index] = self._greedy_replace(population[index], candidate)
                if self._rank(population[index]) < self._rank(global_best):
                    global_best = self._archive_copy(population[index])

            ranks = np.array(
                [
                    rank[0] * 10.0 + (rank[1] if len(rank) > 1 else 0.0)
                    for rank in (self._rank(source) for source in population)
                ]
            )
            quality = np.exp(-self.temperature * (ranks - ranks.min()) / max(np.ptp(ranks), 1e-12))
            probabilities = quality / quality.sum()
            for _ in range(self.food_source_count):
                index = int(rng.choice(len(population), p=probabilities))
                candidate = self._evaluate(
                    self._neighbor(population, index, global_best, rng), evaluation_rng
                )
                cumulative_queries += candidate.reliability.oracle_queries
                cumulative_calls += candidate.reliability.estimator_calls
                population[index] = self._greedy_replace(population[index], candidate)
                if self._rank(population[index]) < self._rank(global_best):
                    global_best = self._archive_copy(population[index])

            for index, source in enumerate(population):
                if source.trials >= self.scout_limit:
                    replacement = self._evaluate(
                        rng.uniform(self.lower, self.upper), evaluation_rng
                    )
                    cumulative_queries += replacement.reliability.oracle_queries
                    cumulative_calls += replacement.reliability.estimator_calls
                    population[index] = replacement
                    if self._rank(replacement) < self._rank(global_best):
                        global_best = self._archive_copy(replacement)

            history.append(
                {
                    "iteration": iteration + 1,
                    "best_mass_kg": global_best.mass,
                    "best_probability_estimate": global_best.reliability.estimate,
                    "best_probability_lower": global_best.reliability.lower,
                    "best_probability_upper": global_best.reliability.upper,
                    "best_true_discrete_probability": global_best.reliability.true_probability,
                    "cumulative_oracle_queries": cumulative_queries,
                    "cumulative_estimator_calls": cumulative_calls,
                }
            )
        return global_best, history
