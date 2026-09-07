import numpy as np

from qae_abc.optimization.abc import ConfidenceAwareABC, FoodSource, ReliabilityEvaluation


def test_confidence_aware_abc_improves_toy_design():
    def objective(x):
        return float(x[0])

    def evaluator(x, rng):
        probability = float(max(0.0, 1.0 - x[0]))
        return ReliabilityEvaluation(probability, probability, probability, probability, 0, 1)

    optimizer = ConfidenceAwareABC(
        objective,
        evaluator,
        lower_bounds=np.array([0.0]),
        upper_bounds=np.array([1.0]),
        target_probability=0.2,
        food_sources=8,
        iterations=20,
    )
    best, history = optimizer.optimize(7)
    assert best.design[0] >= 0.8
    assert best.design[0] < 0.9
    assert len(history) == 20


def test_global_archive_survives_aggressive_scout_replacement():
    def objective(x):
        return float(x[0])

    def evaluator(x, rng):
        del rng
        return ReliabilityEvaluation(0.0, 0.0, 0.0, 0.0, 0, 1)

    optimizer = ConfidenceAwareABC(
        objective,
        evaluator,
        lower_bounds=np.array([0.0]),
        upper_bounds=np.array([1.0]),
        target_probability=0.2,
        food_sources=6,
        iterations=15,
        scout_limit=1,
    )
    best, history = optimizer.optimize(19)
    archived_masses = np.asarray([row["best_mass_kg"] for row in history])
    assert np.all(np.diff(archived_masses) <= 1e-12)
    assert best.mass == archived_masses[-1]


def test_objective_and_reliability_use_same_canonical_design():
    objective_inputs = []
    reliability_inputs = []

    class GridEvaluator:
        @staticmethod
        def canonicalize_design(x):
            return np.round(np.asarray(x) / 0.1) * 0.1

        def __call__(self, x, rng):
            del rng
            reliability_inputs.append(float(x[0]))
            return ReliabilityEvaluation(0.0, 0.0, 0.0, 0.0, 0, 1)

    def objective(x):
        objective_inputs.append(float(x[0]))
        return float(x[0])

    optimizer = ConfidenceAwareABC(
        objective,
        GridEvaluator(),
        lower_bounds=np.array([0.0]),
        upper_bounds=np.array([1.0]),
        target_probability=0.2,
        food_sources=4,
        iterations=3,
    )
    best, _ = optimizer.optimize(23)
    assert objective_inputs == reliability_inputs
    assert np.isclose(best.design[0] / 0.1, np.round(best.design[0] / 0.1))


def test_point_estimate_ablation_changes_uncertain_selection():
    common = dict(
        objective=lambda x: float(x[0]),
        evaluator=lambda x, rng: ReliabilityEvaluation(0, 0, 0, 0, 0, 0),
        lower_bounds=np.array([0.0]),
        upper_bounds=np.array([1.0]),
        target_probability=0.2,
        food_sources=2,
        iterations=1,
    )
    incumbent = FoodSource(
        np.array([0.5]), 2.0, ReliabilityEvaluation(0.15, 0.12, 0.18, 0.15, 1, 1)
    )
    uncertain = FoodSource(
        np.array([0.4]), 1.0, ReliabilityEvaluation(0.10, 0.01, 0.30, 0.10, 1, 1)
    )
    ci_optimizer = ConfidenceAwareABC(**common, use_confidence_intervals=True)
    point_optimizer = ConfidenceAwareABC(**common, use_confidence_intervals=False)
    assert ci_optimizer._greedy_replace(incumbent, uncertain) is incumbent
    assert point_optimizer._greedy_replace(incumbent, uncertain) is uncertain
