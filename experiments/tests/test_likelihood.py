import numpy as np

from qae_abc.quantum.likelihood import (
    allocate_schedule,
    fit_mlae,
    fit_noise_aware_qae,
    fit_visibility_calibrated_qae,
    simulate_counts,
    simulate_iterative_qae,
)


def test_schedule_respects_budget():
    for budget in (64, 256, 1024):
        depths, shots = allocate_schedule(budget, [0, 1, 2, 4, 8, 16])
        assert int(np.sum(shots * (2 * depths + 1))) <= budget
        assert depths[0] == 0


def test_mlae_recovers_deterministic_large_shot_amplitude():
    depths = np.array([0, 1, 2, 4, 8])
    shots = np.full_like(depths, 20000)
    schedule = simulate_counts(0.03, depths, shots, np.random.default_rng(7))
    estimate = fit_mlae(schedule)
    assert abs(estimate.amplitude - 0.03) < 0.003
    assert estimate.lower <= 0.03 <= estimate.upper


def test_noise_aware_fit_recovers_amplitude_and_decay():
    depths = np.array([0, 1, 2, 4, 8])
    shots = np.full_like(depths, 30000)
    schedule = simulate_counts(0.02, depths, shots, np.random.default_rng(11), noise_lambda=0.012)
    estimate = fit_noise_aware_qae(schedule)
    assert abs(estimate.amplitude - 0.02) < 0.004
    assert abs(estimate.noise_lambda - 0.012) < 0.005


def test_calibrated_visibility_fit_recovers_amplitude():
    depths = np.array([0, 1, 2, 4, 8])
    shots = np.full_like(depths, 12000)
    schedule = simulate_counts(0.02, depths, shots, np.random.default_rng(19), noise_lambda=0.01)
    estimate = fit_visibility_calibrated_qae(schedule, noise_lambda=0.01)
    assert abs(estimate.amplitude - 0.02) < 0.003
    assert estimate.lower <= 0.02 <= estimate.upper


def test_iqae_respects_query_budget_and_is_reproducible():
    first = simulate_iterative_qae(0.03, 8192, np.random.default_rng(31))
    second = simulate_iterative_qae(0.03, 8192, np.random.default_rng(31))
    assert first == second
    assert 0 < first.oracle_queries <= 8192
    assert sum(s * (2 * k + 1) for k, s in zip(first.grover_depths, first.shots)) == first.oracle_queries
    assert first.lower <= first.amplitude <= first.upper
    assert abs(first.amplitude - 0.03) < 0.02


def test_iqae_large_budget_interval_contains_true_amplitude():
    true_amplitude = 0.1
    estimate = simulate_iterative_qae(
        true_amplitude,
        65536,
        np.random.default_rng(99),
        shots_per_round=256,
    )
    assert estimate.lower <= true_amplitude <= estimate.upper
    assert estimate.upper - estimate.lower < 0.02
