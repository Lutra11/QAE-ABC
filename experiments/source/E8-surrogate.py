"""Train and audit OC4 multi-fidelity response surrogates."""

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
from sklearn.base import clone
from sklearn.compose import TransformedTargetRegressor
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel
from sklearn.linear_model import LassoCV
from sklearn.metrics import f1_score, mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import KFold, cross_val_predict, train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import PolynomialFeatures, StandardScaler

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "experiments"))

from run_oc4_low_fidelity_doe import low_fidelity_response


DESIGN_COLUMNS = ["D_L1", "t_L1", "D_L2", "t_L2", "D_B1", "t_B1", "D_B2", "t_B2"]
ENV_COLUMNS = ["U100", "Hs", "Tp"]
TARGETS = {
    "max_tower_base_moment_knm": "lf_tower_base_moment_knm",
    "tower_base_moment_del4_knm": "lf_tower_base_moment_del4_knm",
    "max_top_displacement_m": "lf_top_displacement_m",
    "max_hydrodynamic_force_kn": "lf_hydrodynamic_force_kn",
    "max_tracked_member_force_kn": "lf_member_force_kn",
    "mean_generator_power_kw": "lf_wind_force_kn",
    "max_platform_pitch_deg": "lf_platform_pitch_deg",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_hf(partial: bool, path: Path | None = None) -> pd.DataFrame:
    path = path or (PROJECT_ROOT / "results" / "raw" / "openfast_oc4_doe" / "oc4_hf_results_300.parquet")
    if path.exists():
        frame = pd.read_parquet(path)
    elif partial:
        records = []
        root = PROJECT_ROOT / "data" / "interim" / "openfast_oc4_doe_v1" / "cases"
        for result_path in root.glob("HF0300_*/result.json"):
            record = json.loads(result_path.read_text(encoding="utf-8"))
            if record.get("success"):
                records.append(record)
        frame = pd.DataFrame(records)
    else:
        raise FileNotFoundError("The complete 300-case HF result is not available")
    frame = frame[frame["success"].astype(bool)].sort_values("case_id").reset_index(drop=True)
    if len(frame) < (50 if partial else 300):
        raise RuntimeError(f"Insufficient successful HF cases: {len(frame)}")
    return frame


def add_features(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    lf = pd.DataFrame([low_fidelity_response(row) for row in work.to_dict(orient="records")])
    for column in lf:
        work[column] = lf[column].to_numpy()
    angle = np.deg2rad(work["wave_direction_used"].to_numpy(float))
    work["relative_direction_sin"] = np.sin(angle)
    work["relative_direction_cos"] = np.cos(angle)
    return work


def candidates(feature_count: int) -> dict[str, object]:
    return {
        "HGBR": TransformedTargetRegressor(
            regressor=HistGradientBoostingRegressor(
                max_iter=400,
                learning_rate=0.045,
                max_leaf_nodes=15,
                l2_regularization=0.1,
                random_state=20260903,
            ),
            transformer=StandardScaler(),
        ),
        "ExtraTrees": ExtraTreesRegressor(
            n_estimators=500,
            min_samples_leaf=2,
            max_features=0.85,
            random_state=20260903,
            n_jobs=1,
        ),
        "MaternGP": make_pipeline(
            StandardScaler(),
            GaussianProcessRegressor(
                kernel=ConstantKernel(1.0) * Matern(np.ones(feature_count), nu=1.5)
                + WhiteKernel(0.03),
                normalize_y=True,
                optimizer=None,
                random_state=20260903,
            ),
        ),
        "SparsePCE": TransformedTargetRegressor(
            regressor=make_pipeline(
                StandardScaler(),
                PolynomialFeatures(degree=2, include_bias=False),
                StandardScaler(),
                LassoCV(
                    cv=5,
                    alphas=100,
                    max_iter=20000,
                    random_state=20260903,
                ),
            ),
            transformer=StandardScaler(),
        ),
    }


def metrics(y_true: np.ndarray, y_pred: np.ndarray, threshold: float) -> dict[str, float]:
    true_failure = y_true > threshold
    predicted_failure = y_pred > threshold
    false_safe = (~predicted_failure) & true_failure
    return {
        "r2": float(r2_score(y_true, y_pred)),
        "rmse": float(np.sqrt(mean_squared_error(y_true, y_pred))),
        "nrmse_std": float(np.sqrt(mean_squared_error(y_true, y_pred)) / max(np.std(y_true), 1e-15)),
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "boundary_f1": float(f1_score(true_failure, predicted_failure, zero_division=0)),
        "false_safe_rate_all": float(np.mean(false_safe)),
        "false_safe_rate_failures": float(np.sum(false_safe) / max(1, np.sum(true_failure))),
    }


def frozen_train_test_indices(
    hf: pd.DataFrame, frozen_case_ids: list[str] | None = None
) -> tuple[np.ndarray, np.ndarray, list[str], str]:
    """Return a deterministic split, optionally reusing a pre-active holdout."""

    if frozen_case_ids is None:
        train_indices, test_indices = train_test_split(
            np.arange(len(hf)), test_size=0.2, random_state=20260903
        )
        frozen_ids = hf.iloc[np.sort(test_indices)]["case_id"].astype(str).tolist()
        policy = "deterministic_random_split_before_active_learning"
    else:
        frozen_ids = [str(value) for value in frozen_case_ids]
        if len(set(frozen_ids)) != len(frozen_ids):
            raise RuntimeError("Frozen holdout IDs must be unique")
        available_ids = set(hf["case_id"].astype(str))
        missing_ids = sorted(set(frozen_ids) - available_ids)
        if missing_ids:
            raise RuntimeError(f"Frozen holdout IDs absent from HF data: {missing_ids[:10]}")
        test_mask = hf["case_id"].astype(str).isin(frozen_ids).to_numpy()
        test_indices = np.flatnonzero(test_mask)
        train_indices = np.flatnonzero(~test_mask)
        policy = "pre_active_learning_frozen_case_ids"
    if len(train_indices) < 40 or len(test_indices) < 10:
        raise RuntimeError(
            f"Invalid train/holdout split: train={len(train_indices)}, holdout={len(test_indices)}"
        )
    return train_indices, test_indices, frozen_ids, policy


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "results" / "raw" / "oc4_surrogates")
    parser.add_argument("--partial", action="store_true")
    parser.add_argument("--hf-path", type=Path, default=None)
    parser.add_argument("--label", type=str, default=None)
    parser.add_argument(
        "--holdout-case-ids",
        type=Path,
        default=None,
        help="Optional JSON list of case IDs frozen before active learning.",
    )
    parser.add_argument(
        "--refit-all",
        action="store_true",
        help="After model selection/audit, refit the selected deployment model on every HF case.",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    hf = add_features(load_hf(args.partial, args.hf_path))
    suffix = args.label or (f"partial_{len(hf)}" if args.partial else f"full_{len(hf)}")
    feature_columns = DESIGN_COLUMNS + ENV_COLUMNS + [
        "relative_direction_sin",
        "relative_direction_cos",
    ] + sorted(set(TARGETS.values()))
    requested_holdout = None
    if args.holdout_case_ids is not None:
        requested_holdout = json.loads(args.holdout_case_ids.resolve().read_text(encoding="utf-8"))
        if isinstance(requested_holdout, dict):
            requested_holdout = requested_holdout["case_ids"]
    train_indices, test_indices, frozen_ids, split_policy = frozen_train_test_indices(
        hf, requested_holdout
    )
    holdout_id_path = args.output_dir / f"oc4_holdout_case_ids_{suffix}.json"
    holdout_id_path.write_text(
        json.dumps({"case_ids": frozen_ids, "split_policy": split_policy}, indent=2), encoding="utf-8"
    )
    x = hf[feature_columns].to_numpy(float)
    x_train, x_test = x[train_indices], x[test_indices]
    folds = KFold(n_splits=5, shuffle=True, random_state=20260903)
    metric_rows = []
    predictions = hf[["case_id"]].copy()
    model_bundle = {
        "feature_columns": feature_columns,
        "models": {},
        "thresholds": {},
        "split_policy": split_policy,
        "holdout_case_ids": frozen_ids,
        "deployment_refit_all": bool(args.refit_all),
    }

    for target, _lf_column in TARGETS.items():
        y = hf[target].to_numpy(float)
        y_train, y_test = y[train_indices], y[test_indices]
        threshold = float(np.quantile(y_train, 0.90))
        model_bundle["thresholds"][target] = threshold
        fitted_candidates = {}
        cv_scores = {}
        for name, model in candidates(len(feature_columns)).items():
            cv_prediction = cross_val_predict(
                clone(model), x_train, y_train, cv=folds, n_jobs=1, method="predict"
            )
            cv_metric = metrics(y_train, cv_prediction, threshold)
            score = cv_metric["nrmse_std"] + 2.0 * cv_metric["false_safe_rate_failures"]
            cv_scores[name] = score
            metric_rows.append(
                {"target": target, "model": name, "split": "five_fold_cv_train", "threshold_training_q90": threshold, "selection_score": score} | cv_metric
            )
            fitted = clone(model).fit(x_train, y_train)
            fitted_candidates[name] = fitted
            test_prediction = fitted.predict(x_test)
            test_metric = metrics(y_test, test_prediction, threshold)
            metric_rows.append(
                {"target": target, "model": name, "split": "independent_holdout", "threshold_training_q90": threshold, "selection_score": score} | test_metric
            )
            full_prediction = np.full(len(hf), np.nan)
            full_prediction[test_indices] = test_prediction
            predictions[f"{target}__{name}__holdout"] = full_prediction
        selected = min(cv_scores, key=cv_scores.get)
        selected_model = fitted_candidates[selected]
        if args.refit_all:
            selected_model = clone(selected_model).fit(x, y)
        model_bundle["models"][target] = {
            "selected_name": selected,
            "model": selected_model,
            "candidate_cv_scores": cv_scores,
            "candidate_models": fitted_candidates,
        }
        predictions[f"{target}__selected_holdout"] = predictions[
            f"{target}__{selected}__holdout"
        ]

    model_path = args.output_dir / f"oc4_multifidelity_models_{suffix}.joblib"
    metric_path = args.output_dir / f"oc4_surrogate_metrics_{suffix}.csv"
    prediction_path = args.output_dir / f"oc4_holdout_predictions_{suffix}.parquet"
    data_path = args.output_dir / f"oc4_hf_with_lf_features_{suffix}.parquet"
    joblib.dump(model_bundle, model_path)
    pd.DataFrame(metric_rows).to_csv(metric_path, index=False)
    predictions.to_parquet(prediction_path, index=False)
    hf.to_parquet(data_path, index=False)
    metric_frame = pd.DataFrame(metric_rows)
    selected_rows = []
    for target, item in model_bundle["models"].items():
        selected_rows.append(
            metric_frame[
                (metric_frame["target"] == target)
                & (metric_frame["model"] == item["selected_name"])
                & (metric_frame["split"] == "independent_holdout")
            ].iloc[0].to_dict()
        )
    selected_path = args.output_dir / f"oc4_selected_surrogate_metrics_{suffix}.csv"
    pd.DataFrame(selected_rows).to_csv(selected_path, index=False)
    report = {
        "scope": "Multi-fidelity surrogate: HF OpenFAST responses regressed on design, environment, and independently defined physics-informed LF features.",
        "partial": args.partial,
        "successful_hf_cases": len(hf),
        "train_cases": len(train_indices),
        "independent_holdout_cases": len(test_indices),
        "split_policy": split_policy,
        "holdout_case_ids_file": str(holdout_id_path.relative_to(PROJECT_ROOT)),
        "deployment_refit_all_after_audit": bool(args.refit_all),
        "feature_columns": feature_columns,
        "targets": list(TARGETS),
        "threshold_role": "Training-set 90th percentile used only for boundary F1/false-safe diagnostics; not a certification limit.",
        "selected_models": {target: item["selected_name"] for target, item in model_bundle["models"].items()},
        "elapsed_seconds": time.perf_counter() - started,
        "platform": platform.platform(),
        "sklearn_version": sklearn.__version__,
        "files": {str(path.relative_to(PROJECT_ROOT)): sha256(path) for path in [model_path, metric_path, prediction_path, data_path, selected_path, holdout_id_path]},
    }
    (args.output_dir / f"oc4_surrogate_report_{suffix}.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
