"""Complete CI-selection, active-refinement, and lookup-oracle ablations."""

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

import numpy as np
import pandas as pd
import qiskit
import scipy
import yaml
from joblib import Parallel, delayed
from scipy.optimize import brentq
from scipy.stats import norm

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "experiments"))

from qae_abc.optimization.abc import ConfidenceAwareABC
from importlib import import_module
TrussProbabilityEvaluator = import_module("E5-truss").TrussProbabilityEvaluator


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run_variant(variant: str, run: int, config: dict, model_path: Path) -> tuple[dict, list[dict]]:
    seed = int(np.random.SeedSequence([config["master_seed"], run]).generate_state(1)[0])
    evaluator_method = "na_qae_abc" if variant == "without_ci_selection" else "lookup_abc"
    evaluator = TrussProbabilityEvaluator(model_path, evaluator_method, config, measurement_seed=seed)
    optimizer = ConfidenceAwareABC(
        objective=evaluator.truss.mass,
        evaluator=evaluator,
        lower_bounds=np.asarray(config["area_lower_bounds_m2"]),
        upper_bounds=np.asarray(config["area_upper_bounds_m2"]),
        target_probability=config["target_probability"],
        food_sources=config["food_sources"],
        iterations=config["iterations"],
        global_guidance=config["global_guidance"],
        scout_limit=config["scout_limit"],
        temperature=config["temperature"],
        use_confidence_intervals=variant != "without_ci_selection",
    )
    started = time.perf_counter()
    best, history = optimizer.optimize(seed)
    exact = evaluator.truss.exact_failure_probability(best.design, evaluator.uncertainty)["pf_system"]
    result = {
        "variant": variant,
        "run": run,
        "seed": seed,
        **{f"area_group_{index + 1}_m2": float(value) for index, value in enumerate(best.design)},
        "mass_kg": best.mass,
        "estimated_probability": best.reliability.estimate,
        "estimated_lower": best.reliability.lower,
        "estimated_upper": best.reliability.upper,
        "exact_continuous_probability": exact,
        "exact_beta": float(-norm.ppf(exact)) if exact > 0 else np.inf,
        "estimated_safe": bool(
            (best.reliability.estimate if variant == "without_ci_selection" else best.reliability.upper)
            <= config["target_probability"]
        ),
        "exact_feasible": bool(exact <= config["target_probability"]),
        "cumulative_oracle_queries": int(history[-1]["cumulative_oracle_queries"]),
        "cumulative_estimator_calls": int(history[-1]["cumulative_estimator_calls"]),
        "high_fidelity_refinement_calls": 0,
        "elapsed_seconds": time.perf_counter() - started,
    }
    for row in history:
        row.update({"variant": variant, "run": run, "seed": seed})
    return result, history


def active_refinement_rows(config: dict) -> pd.DataFrame:
    source = pd.read_parquet(
        PROJECT_ROOT / "results" / "raw" / "truss_optimization" / "optimization_runs_full.parquet"
    )
    source = source[source["method"] == "na_qae_abc"].copy()
    model_path = PROJECT_ROOT / "results" / "raw" / "truss_surrogate" / "sparse_pce.joblib"
    evaluator = TrussProbabilityEvaluator(model_path, "lookup_abc", config)
    upper = np.asarray(config["area_upper_bounds_m2"], dtype=float)
    rows = []
    for row in source.itertuples():
        design = np.array([getattr(row, f"area_group_{index}_m2") for index in range(1, 5)])
        calls = 0

        def residual(step: float) -> float:
            nonlocal calls
            calls += 1
            candidate = design + step * (upper - design)
            probability = evaluator.truss.exact_failure_probability(candidate, evaluator.uncertainty)["pf_system"]
            return probability - config["target_probability"]

        original_probability = evaluator.truss.exact_failure_probability(design, evaluator.uncertainty)["pf_system"]
        calls += 1
        if original_probability <= config["target_probability"]:
            step = 0.0
        elif residual(1.0) <= 0.0:
            step = float(brentq(residual, 0.0, 1.0, xtol=1e-10, rtol=1e-10))
        else:
            step = 1.0
        refined = evaluator.canonicalize_design(design + step * (upper - design))
        exact = evaluator.truss.exact_failure_probability(refined, evaluator.uncertainty)["pf_system"]
        calls += 1
        # Manufacturing-grid snapping can move the root slightly unsafe.  Move
        # one grid unit toward the upper design until independent validation passes.
        tolerance = float(config["cache_tolerance_m2"])
        while exact > config["target_probability"] and np.any(refined < upper):
            refined = np.minimum(upper, refined + tolerance)
            exact = evaluator.truss.exact_failure_probability(refined, evaluator.uncertainty)["pf_system"]
            calls += 1
        rows.append(
            {
                "variant": "full_qae_abc_active_refinement",
                "run": int(row.run),
                "seed": int(row.seed),
                **{f"area_group_{index + 1}_m2": float(value) for index, value in enumerate(refined)},
                "mass_kg": evaluator.truss.mass(refined),
                "estimated_probability": float(row.estimated_probability),
                "estimated_lower": float(row.estimated_lower),
                "estimated_upper": float(row.estimated_upper),
                "exact_continuous_probability": exact,
                "exact_beta": float(-norm.ppf(exact)),
                "estimated_safe": True,
                "exact_feasible": bool(exact <= config["target_probability"]),
                "cumulative_oracle_queries": int(row.cumulative_oracle_queries),
                "cumulative_estimator_calls": int(row.cumulative_estimator_calls),
                "high_fidelity_refinement_calls": calls,
                "elapsed_seconds": float(row.elapsed_seconds),
                "refinement_fraction_to_upper_bounds": step,
                "mass_change_from_pre_refinement_kg": evaluator.truss.mass(refined) - float(row.mass_kg),
            }
        )
    return pd.DataFrame(rows)


def existing_ablation_rows() -> pd.DataFrame:
    source = pd.read_parquet(
        PROJECT_ROOT / "results" / "raw" / "truss_optimization" / "optimization_runs_full.parquet"
    )
    mapping = {
        "noisy_mlae_abc": "without_noise_model",
        "na_qae_fixed_abc": "without_adaptive_precision",
        "na_qae_abc": "without_active_refinement",
    }
    selected = source[source["method"].isin(mapping)].copy()
    selected["variant"] = selected["method"].map(mapping)
    selected["high_fidelity_refinement_calls"] = 0
    return selected


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "configs" / "truss.yaml")
    parser.add_argument("--model", type=Path, default=PROJECT_ROOT / "results" / "raw" / "truss_surrogate" / "sparse_pce.joblib")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "results" / "raw" / "truss_ablations")
    parser.add_argument("--jobs", type=int, default=10)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    config["target_probability"] = float(norm.cdf(-float(config["target_beta"])))
    if args.smoke:
        config["food_sources"] = 8
        config["iterations"] = 3
        config["independent_runs"] = 1
        config["dynamic_query_budgets"] = [256, 1024]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    tasks = [(variant, run) for variant in ("without_ci_selection", "lookup_oracle_only") for run in range(int(config["independent_runs"]))]
    outputs = Parallel(n_jobs=args.jobs, prefer="processes", verbose=5)(
        delayed(run_variant)(variant, run, config, args.model) for variant, run in tasks
    )
    new_rows = pd.DataFrame([result for result, _ in outputs])
    history = pd.DataFrame([row for _, history_rows in outputs for row in history_rows])
    if not args.smoke:
        combined = pd.concat(
            [new_rows, active_refinement_rows(config), existing_ablation_rows()],
            ignore_index=True,
            sort=False,
        )
    else:
        combined = new_rows
    suffix = "smoke" if args.smoke else "full"
    run_path = args.output_dir / f"ablation_runs_{suffix}.parquet"
    history_path = args.output_dir / f"ablation_new_history_{suffix}.parquet"
    summary_path = args.output_dir / f"ablation_summary_{suffix}.csv"
    combined.to_parquet(run_path, index=False)
    history.to_parquet(history_path, index=False)
    summary = combined.groupby("variant").agg(runs=("run", "size"), mean_mass_kg=("mass_kg", "mean"), sd_mass_kg=("mass_kg", "std"), median_reference_probability=("exact_continuous_probability", "median"), reference_feasibility_rate=("exact_feasible", "mean"), median_oracle_queries=("cumulative_oracle_queries", "median"), median_high_fidelity_refinement_calls=("high_fidelity_refinement_calls", "median"), median_elapsed_seconds=("elapsed_seconds", "median")).reset_index()
    summary.to_csv(summary_path, index=False)
    report = {
        "scope": "Matched 30-run truss ablation. Full method adds disclosed independent high-fidelity active refinement to the QAE-ABC candidate; other rows omit one named component.",
        "config": config,
        "rows": len(combined),
        "variants": summary["variant"].tolist(),
        "versions": {"python": sys.version, "platform": platform.platform(), "numpy": np.__version__, "scipy": scipy.__version__, "qiskit": qiskit.__version__},
        "files": {str(path.relative_to(PROJECT_ROOT)): sha256(path) for path in [run_path, history_path, summary_path]},
    }
    (args.output_dir / f"ablation_report_{suffix}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
