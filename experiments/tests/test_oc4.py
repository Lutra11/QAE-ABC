from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "experiments"))

from qae_abc.reliability.oc4 import response_feature_frame, stratified_observed_environment
from run_oc4_low_fidelity_doe import low_fidelity_response
from run_openfast_oc4_doe import BASELINE
from importlib import import_module
frozen_train_test_indices = import_module("E8-surrogate").frozen_train_test_indices


def test_vectorized_low_fidelity_features_match_scalar_reference() -> None:
    environment = pd.DataFrame(
        {"U100": [11.3], "Hs": [2.7], "Tp": [8.4], "wind_wave_angle": [37.0]}
    )
    design = pd.DataFrame([BASELINE])
    vector = response_feature_frame(design, environment).iloc[0]
    scalar_input = {
        **BASELINE,
        "wind_speed_used": 11.3,
        "wave_height_used": 2.7,
        "wave_period_used": 8.4,
        "wave_direction_used": 37.0,
    }
    scalar = low_fidelity_response(scalar_input)
    for name, expected in scalar.items():
        assert np.isclose(vector[name], expected, rtol=1e-12, atol=1e-12)


def test_stratified_environment_retains_requested_observed_rows() -> None:
    environment = pd.DataFrame(
        {
            "U100": np.arange(100, dtype=float),
            "Hs": np.arange(100, dtype=float) / 10,
            "Tp": np.arange(100, dtype=float) / 20 + 2,
            "wind_wave_angle": np.arange(100, dtype=float),
        }
    )
    selected = stratified_observed_environment(environment, 20, seed=7)
    assert len(selected) == 20
    assert set(selected["U100"]).issubset(set(environment["U100"]))
    assert selected["U100"].min() < 10
    assert selected["U100"].max() > 90


def test_pre_active_holdout_ids_are_reused_without_leakage() -> None:
    frame = pd.DataFrame({"case_id": [f"HF0300_{index:04d}" for index in range(60)]})
    frozen = frame.iloc[::6]["case_id"].tolist()
    train, test, recorded, policy = frozen_train_test_indices(frame, frozen)
    assert policy == "pre_active_learning_frozen_case_ids"
    assert recorded == frozen
    assert set(frame.iloc[test]["case_id"]) == set(frozen)
    assert set(frame.iloc[train]["case_id"]).isdisjoint(frozen)
