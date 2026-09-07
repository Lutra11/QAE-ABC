import numpy as np

from qae_abc.reliability.analytic import GaussianDifferenceBenchmark, clopper_pearson_interval


def test_closed_form_target_round_trip():
    for probability in (0.1, 0.01, 0.001, 0.0001):
        benchmark = GaussianDifferenceBenchmark.from_failure_probability(probability)
        assert np.isclose(benchmark.failure_probability, probability, rtol=1e-12, atol=1e-14)


def test_clopper_pearson_boundaries():
    lower, upper = clopper_pearson_interval(0, 100)
    assert lower == 0.0
    assert 0.0 < upper < 0.1
    lower, upper = clopper_pearson_interval(100, 100)
    assert 0.9 < lower < 1.0
    assert upper == 1.0

