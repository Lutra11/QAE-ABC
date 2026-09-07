import numpy as np

from qae_abc.structural.truss import TenBarTruss, TrussUncertainty
from qae_abc.surrogate.truss_surrogates import (
    generate_truss_doe,
    grouped_train_validation_test_split,
    surrogate_metrics,
)


def test_grouped_split_has_no_design_leakage():
    frame = generate_truss_doe(TenBarTruss(), TrussUncertainty(), 30, 3, 17)
    split = grouped_train_validation_test_split(frame, 17)
    groups = frame["design_id"].to_numpy()
    train = set(groups[split.train])
    validation = set(groups[split.validation])
    test = set(groups[split.test])
    assert train.isdisjoint(validation)
    assert train.isdisjoint(test)
    assert validation.isdisjoint(test)


def test_surrogate_metrics_detect_false_safe_predictions():
    truth = np.array([-1.0, -0.1, 0.1, 1.0])
    prediction = np.array([0.5, -0.2, 0.2, 1.1])
    metrics = surrogate_metrics(truth, prediction, boundary_width=0.2)
    assert metrics["false_safe_rate"] == 0.5
    assert metrics["boundary_samples"] == 2

