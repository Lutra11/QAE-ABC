import numpy as np

from qae_abc.reliability.nonlinear import (
    ParabolicLimitState,
    QuadraticLimitState,
    estimate_form,
    estimate_qmc_limit_state,
    estimate_subset_simulation,
    estimate_subset_simulation_pcn,
)


def test_nonlinear_reference_round_trips():
    for target in (1e-2, 1e-3, 1e-4):
        parabolic = ParabolicLimitState.from_failure_probability(target)
        quadratic = QuadraticLimitState.from_failure_probability(target, dimension=5)
        assert np.isclose(parabolic.failure_probability, target, rtol=2e-9)
        assert np.isclose(quadratic.failure_probability, target, rtol=2e-12)


def test_qmc_and_form_return_finite_results():
    benchmark = ParabolicLimitState.from_failure_probability(1e-2)
    qmc_estimate = estimate_qmc_limit_state(benchmark, 2**16, seed=7)
    form_estimate = estimate_form(benchmark)
    assert abs(qmc_estimate.estimate - 1e-2) < 2e-3
    assert form_estimate.converged
    assert 0.0 < form_estimate.estimate < 0.5


def test_subset_simulation_reaches_rare_event():
    benchmark = QuadraticLimitState.from_failure_probability(1e-3, dimension=5)
    estimate = estimate_subset_simulation(
        benchmark,
        samples_per_level=1000,
        rng=np.random.default_rng(123),
        max_levels=6,
    )
    assert estimate.converged
    assert estimate.model_evaluations >= 1000
    assert 1e-5 < estimate.estimate < 2e-2
    assert estimate.lower <= estimate.estimate <= estimate.upper


def test_vectorized_pcn_subset_simulation_reaches_rare_event():
    benchmark = QuadraticLimitState.from_failure_probability(1e-3, dimension=5)
    estimate = estimate_subset_simulation_pcn(
        benchmark,
        samples_per_level=2000,
        rng=np.random.default_rng(456),
        max_levels=6,
    )
    assert estimate.converged
    assert 1e-5 < estimate.estimate < 2e-2
    assert estimate.model_evaluations >= 2000
