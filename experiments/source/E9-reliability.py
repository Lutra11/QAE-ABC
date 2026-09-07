"""Calibrate operational OC4 limits and train a fast reliability meta-model.

The response layer is the independently audited multi-fidelity OpenFAST
surrogate.  Capacity values are deliberately labelled *operational screening
limits*: they anchor the original OC4 design to beta=3 under the 2000--2019
empirical environment and are not IEC/DNV certification allowables.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
from pathlib import Path

# Resolve imports within the curated package; data/output defaults stay unchanged.
import sys as _sys
_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
for _folder in (
    _PACKAGE_ROOT / "algorithm",
    _PACKAGE_ROOT / "experiments" / "common",
    _PACKAGE_ROOT / "experiments" / "source",
):
    _sys.path.insert(0, str(_folder))

import joblib
import numpy as np
import pandas as pd
import sklearn
from scipy.special import expit, logit
from scipy.stats import norm, qmc
from sklearn.base import clone
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import KFold, cross_val_predict, train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import PolynomialFeatures, StandardScaler
from sklearn.linear_model import Ridge

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "experiments"))

from qae_abc.reliability.oc4 import (
    DESIGN_COLUMNS,
    ENV_COLUMNS,
    SCREENING_TARGETS,
    predict_response_cartesian,
    stratified_observed_environment,
    system_failure_probability,
)
from run_openfast_oc4_doe import BASELINE, BOUNDS, geometry_mass_and_frequency_proxy


DATA_PATH = PROJECT_ROOT / "data" / "processed" / "metocean" / "era5_open_meteo_54N_6.5E_2000_2024.parquet"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def model_candidates(seed: int) -> dict[str, object]:
    return {
        "HGBR": HistGradientBoostingRegressor(
            max_iter=500,
            learning_rate=0.04,
            max_leaf_nodes=20,
            l2_regularization=0.2,
            random_state=seed,
        ),
        "ExtraTrees": ExtraTreesRegressor(
            n_estimators=600,
            min_samples_leaf=2,
            max_features=0.9,
            n_jobs=1,
            random_state=seed,
        ),
        "RandomForest": RandomForestRegressor(
            n_estimators=500,
            min_samples_leaf=2,
            max_features=0.9,
            n_jobs=1,
            random_state=seed,
        ),
        "QuadraticRidge": make_pipeline(
            StandardScaler(),
            PolynomialFeatures(degree=2, include_bias=False),
            StandardScaler(),
            Ridge(alpha=1.0),
        ),
    }


def design_lhs(n_designs: int, seed: int) -> pd.DataFrame:
    sampler = qmc.LatinHypercube(len(DESIGN_COLUMNS), seed=seed, optimization="random-cd")
    lower = np.asarray([BOUNDS[name][0] for name in DESIGN_COLUMNS])
    upper = np.asarray([BOUNDS[name][1] for name in DESIGN_COLUMNS])
    values = qmc.scale(sampler.random(n_designs), lower, upper)
    values[0] = np.asarray([BASELINE[name] for name in DESIGN_COLUMNS])
    return pd.DataFrame(values, columns=DESIGN_COLUMNS)


def calibration_capacities(
    response_bundle: dict,
    environment_fit: pd.DataFrame,
    target_probability: float,
) -> tuple[dict[str, float], dict]:
    baseline = pd.DataFrame([BASELINE])
    response = predict_response_cartesian(
        response_bundle, baseline, environment_fit, SCREENING_TARGETS, design_chunk_size=1
    )
    component_scales = {
        name: float(np.quantile(response[name][0], 0.99)) for name in SCREENING_TARGETS
    }
    ratios = np.stack(
        [response[name][0] / component_scales[name] for name in SCREENING_TARGETS], axis=1
    )
    system_score = np.max(ratios, axis=1)
    common_threshold = float(np.quantile(system_score, 1.0 - target_probability, method="higher"))
    capacities = {name: component_scales[name] * common_threshold for name in SCREENING_TARGETS}
    probability, system_ratio = system_failure_probability(response, capacities)
    component_probabilities = {
        name: float(np.mean(response[name][0] > capacities[name])) for name in SCREENING_TARGETS
    }
    report = {
        "capacity_definition": "component fit-period q99 scales times the empirical system-score quantile at 1-Pf_target",
        "capacity_role": "operational screening limits; not IEC/DNV certification allowables",
        "fit_environment_rows": len(environment_fit),
        "target_probability": target_probability,
        "target_beta": float(-norm.ppf(target_probability)),
        "component_q99_scales": component_scales,
        "common_system_threshold": common_threshold,
        "capacities": capacities,
        "baseline_fit_system_probability": float(probability[0]),
        "baseline_fit_system_beta": float(-norm.ppf(np.clip(probability[0], 1e-15, 1 - 1e-15))),
        "baseline_fit_component_probabilities": component_probabilities,
        "baseline_system_ratio_quantile": float(np.quantile(system_ratio[0], 1 - target_probability)),
    }
    return capacities, report


def audit_models(frame: pd.DataFrame, seed: int, target_probability: float) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    x = frame[DESIGN_COLUMNS].to_numpy(float)
    y_probability = frame["smoothed_probability"].to_numpy(float)
    targets = {
        "logit_probability": logit(np.clip(y_probability, 1e-9, 1 - 1e-9)),
        "system_ratio_quantile": frame["system_ratio_quantile"].to_numpy(float),
    }
    train, test = train_test_split(np.arange(len(frame)), test_size=0.20, random_state=seed)
    folds = KFold(5, shuffle=True, random_state=seed)
    rows: list[dict] = []
    predictions = frame[["design_id"]].copy()
    bundle: dict = {
        "design_columns": DESIGN_COLUMNS,
        "target_probability": target_probability,
        "models": {},
        "residual_calibration": {},
        "train_indices": train,
        "holdout_indices": test,
    }
    for target_name, y in targets.items():
        scores: dict[str, float] = {}
        safety_threshold = logit(target_probability) if target_name == "logit_probability" else 1.0
        for model_name, model in model_candidates(seed).items():
            cv_prediction = cross_val_predict(clone(model), x[train], y[train], cv=folds, n_jobs=1)
            cv_rmse = float(np.sqrt(mean_squared_error(y[train], cv_prediction)))
            true_unsafe_cv = y[train] > safety_threshold
            false_safe_cv = (cv_prediction <= safety_threshold) & true_unsafe_cv
            cv_false_safe_failures = float(
                np.count_nonzero(false_safe_cv) / max(1, np.count_nonzero(true_unsafe_cv))
            )
            normalized_cv_rmse = cv_rmse / max(float(np.std(y[train])), 1e-15)
            selection_score = normalized_cv_rmse + 2.0 * cv_false_safe_failures
            scores[model_name] = selection_score
            fitted_train = clone(model).fit(x[train], y[train])
            prediction = np.asarray(fitted_train.predict(x[test]), dtype=float)
            holdout_residual = prediction - y[test]
            true_unsafe_holdout = y[test] > safety_threshold
            false_safe_holdout = (prediction <= safety_threshold) & true_unsafe_holdout
            rows.extend(
                [
                    {
                        "target": target_name,
                        "model": model_name,
                        "split": "five_fold_cv_train",
                        "n": len(train),
                        "r2": float(r2_score(y[train], cv_prediction)),
                        "rmse": cv_rmse,
                        "normalized_rmse": normalized_cv_rmse,
                        "mae": float(mean_absolute_error(y[train], cv_prediction)),
                        "false_safe_rate": float(np.mean(false_safe_cv)),
                        "false_safe_rate_failures": cv_false_safe_failures,
                        "selection_score": selection_score,
                    },
                    {
                        "target": target_name,
                        "model": model_name,
                        "split": "independent_design_holdout",
                        "n": len(test),
                        "r2": float(r2_score(y[test], prediction)),
                        "rmse": float(np.sqrt(mean_squared_error(y[test], prediction))),
                        "normalized_rmse": float(
                            np.sqrt(mean_squared_error(y[test], prediction))
                            / max(float(np.std(y[test])), 1e-15)
                        ),
                        "mae": float(mean_absolute_error(y[test], prediction)),
                        "false_safe_rate": float(np.mean(false_safe_holdout)),
                        "false_safe_rate_failures": float(
                            np.count_nonzero(false_safe_holdout)
                            / max(1, np.count_nonzero(true_unsafe_holdout))
                        ),
                        "selection_score": selection_score,
                    },
                ]
            )
            full_prediction = np.full(len(frame), np.nan)
            full_prediction[test] = prediction
            predictions[f"{target_name}__{model_name}__holdout"] = full_prediction
        selected = min(scores, key=scores.get)
        selected_holdout = predictions[f"{target_name}__{selected}__holdout"].to_numpy()[test]
        residual = selected_holdout - y[test]
        final_model = clone(model_candidates(seed)[selected]).fit(x, y)
        selected_metrics = next(
            row
            for row in rows
            if row["target"] == target_name
            and row["model"] == selected
            and row["split"] == "five_fold_cv_train"
        )
        bundle["models"][target_name] = {
            "selected_name": selected,
            "model": final_model,
            "cv_rmse": selected_metrics["rmse"],
            "selection_score": scores[selected],
        }
        bundle["residual_calibration"][target_name] = {
            "holdout_rmse": float(np.sqrt(np.mean(residual**2))),
            "absolute_error_q95": float(np.quantile(np.abs(residual), 0.95)),
            "signed_error_q05": float(np.quantile(residual, 0.05)),
            "signed_error_q95": float(np.quantile(residual, 0.95)),
        }
        predictions[f"{target_name}__selected_holdout"] = predictions[
            f"{target_name}__{selected}__holdout"
        ]
    return bundle, pd.DataFrame(rows), predictions


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--response-bundle",
        type=Path,
        default=PROJECT_ROOT / "results" / "raw" / "oc4_surrogates" / "oc4_multifidelity_models_final_400.joblib",
    )
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "results" / "raw" / "oc4_optimization")
    parser.add_argument("--n-designs", type=int, default=1500)
    parser.add_argument("--n-environment", type=int, default=8192)
    parser.add_argument("--seed", type=int, default=20260941)
    parser.add_argument("--target-beta", type=float, default=3.0)
    parser.add_argument("--label", type=str, default="final_400")
    args = parser.parse_args()
    args.response_bundle = args.response_bundle.resolve()
    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    target_probability = float(norm.cdf(-args.target_beta))
    response_bundle = joblib.load(args.response_bundle)
    metocean = pd.read_parquet(DATA_PATH)
    complete = metocean[metocean["complete_core"]].copy()
    fit = complete[complete["split"] == "fit"].reset_index(drop=True)
    temporal_test = complete[complete["split"] == "temporal_test"].reset_index(drop=True)

    capacities, capacity_report = calibration_capacities(response_bundle, fit, target_probability)
    test_baseline_response = predict_response_cartesian(
        response_bundle, pd.DataFrame([BASELINE]), temporal_test, SCREENING_TARGETS, design_chunk_size=1
    )
    test_probability, test_ratio = system_failure_probability(test_baseline_response, capacities)
    capacity_report.update(
        {
            "baseline_temporal_test_rows": len(temporal_test),
            "baseline_temporal_test_probability": float(test_probability[0]),
            "baseline_temporal_test_beta": float(-norm.ppf(np.clip(test_probability[0], 1e-15, 1 - 1e-15))),
            "baseline_temporal_test_system_ratio_quantile": float(
                np.quantile(test_ratio[0], 1 - target_probability)
            ),
        }
    )

    sampled_environment = stratified_observed_environment(fit, args.n_environment, args.seed)
    designs = design_lhs(args.n_designs, args.seed)
    responses = predict_response_cartesian(response_bundle, designs, sampled_environment, SCREENING_TARGETS)
    probabilities, system_ratio = system_failure_probability(responses, capacities)
    failures = np.count_nonzero(system_ratio > 1.0, axis=1)
    dataset = designs.copy()
    dataset.insert(0, "design_id", [f"META{index:05d}" for index in range(len(dataset))])
    dataset["structural_mass_kg"] = [
        geometry_mass_and_frequency_proxy(row)[0] for row in designs.to_dict(orient="records")
    ]
    dataset["failure_count"] = failures
    dataset["environment_samples"] = args.n_environment
    dataset["empirical_probability"] = probabilities
    dataset["smoothed_probability"] = (failures + 0.5) / (args.n_environment + 1.0)
    dataset["system_ratio_quantile"] = np.quantile(system_ratio, 1 - target_probability, axis=1)
    dataset["empirical_feasible"] = dataset["empirical_probability"] <= target_probability

    model_bundle, metrics, predictions = audit_models(dataset, args.seed, target_probability)
    model_bundle.update(
        {
            "capacities": capacities,
            "response_bundle_path": str(args.response_bundle),
            "environment_samples": args.n_environment,
            "sampling": "one observed 2000-2019 row per rank-severity stratum",
        }
    )
    suffix = args.label
    capacity_path = args.output_dir / f"oc4_operational_capacities_{suffix}.json"
    dataset_path = args.output_dir / f"oc4_reliability_meta_dataset_{suffix}.parquet"
    model_path = args.output_dir / f"oc4_reliability_meta_models_{suffix}.joblib"
    metric_path = args.output_dir / f"oc4_reliability_meta_metrics_{suffix}.csv"
    prediction_path = args.output_dir / f"oc4_reliability_meta_holdout_{suffix}.parquet"
    environment_path = args.output_dir / f"oc4_meta_environment_{suffix}.parquet"
    capacity_path.write_text(json.dumps(capacity_report, indent=2, ensure_ascii=False), encoding="utf-8")
    dataset.to_parquet(dataset_path, index=False)
    joblib.dump(model_bundle, model_path)
    metrics.to_csv(metric_path, index=False)
    predictions.to_parquet(prediction_path, index=False)
    sampled_environment.to_parquet(environment_path, index=False)
    files = [capacity_path, dataset_path, model_path, metric_path, prediction_path, environment_path]
    report = {
        "scope": "OC4 operational screening reliability meta-model built from the multi-fidelity OpenFAST response surrogate.",
        "non_certification_statement": "Limits and probabilities are method-validation quantities, not an IEC/DNV design certification.",
        "response_bundle": str(args.response_bundle.relative_to(PROJECT_ROOT)),
        "response_bundle_sha256": sha256(args.response_bundle),
        "designs": args.n_designs,
        "fit_environment_samples_per_design": args.n_environment,
        "target_beta": args.target_beta,
        "target_probability": target_probability,
        "selected_models": {name: item["selected_name"] for name, item in model_bundle["models"].items()},
        "elapsed_seconds": time.perf_counter() - started,
        "platform": platform.platform(),
        "sklearn_version": sklearn.__version__,
        "files": {str(path.relative_to(PROJECT_ROOT)): sha256(path) for path in files},
    }
    report_path = args.output_dir / f"oc4_reliability_meta_report_{suffix}.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report | {"capacity_summary": capacity_report}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
