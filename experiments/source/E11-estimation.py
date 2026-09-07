"""Evaluate probability estimators at frozen OC4 candidate designs."""

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
from joblib import Parallel, delayed
from scipy.stats import beta, qmc

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "experiments"))

from qae_abc.evaluation.metrics import summarize_estimates
from qae_abc.quantum.likelihood import (
    allocate_schedule,
    fit_mlae,
    fit_noise_aware_qae,
    fit_visibility_calibrated_qae,
    simulate_counts,
    simulate_iterative_qae,
)
from qae_abc.reliability.oc4 import (
    DESIGN_COLUMNS,
    SCREENING_TARGETS,
    predict_response_cartesian,
    system_failure_probability,
)


DATA_PATH = PROJECT_ROOT / "data" / "processed" / "metocean" / "era5_open_meteo_54N_6.5E_2000_2024.parquet"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def clopper_pearson(successes: int, trials: int, confidence: float) -> tuple[float, float]:
    alpha = 1.0 - confidence
    lower = 0.0 if successes == 0 else float(beta.ppf(alpha / 2, successes, trials - successes + 1))
    upper = 1.0 if successes == trials else float(beta.ppf(1 - alpha / 2, successes + 1, trials - successes))
    return lower, upper


def run_replicate(candidate: dict, budget: int, replicate: int, noise: float) -> list[dict]:
    seed = int(
        np.random.SeedSequence([20260971, int(candidate["candidate_index"]), budget, replicate]).generate_state(1)[0]
    )
    probability = float(candidate["reference_probability"])
    confidence = 0.95
    rng = np.random.default_rng(seed)
    rows = []
    common = {
        "candidate_index": int(candidate["candidate_index"]),
        "candidate_method": candidate["method"],
        "candidate_run": int(candidate["run"]),
        "model": f"OC4-{candidate['method']}",
        "query_budget": budget,
        "replicate": replicate,
        "seed": seed,
        "true_probability": probability,
        "experiment": "OC4 temporal-test response-surrogate amplitude with controlled visibility decay",
    }
    successes = int(rng.binomial(budget, probability))
    lower, upper = clopper_pearson(successes, budget, confidence)
    rows.append(common | {"method": "MC", "estimate": successes / budget, "lower": lower, "upper": upper, "oracle_queries": budget, "shots": budget})

    exponent = int(np.floor(np.log2(budget)))
    uniform = qmc.Sobol(1, scramble=True, seed=seed + 1).random_base2(exponent)[:, 0]
    qmc_successes = int(np.count_nonzero(uniform < probability))
    qmc_trials = len(uniform)
    lower, upper = clopper_pearson(qmc_successes, qmc_trials, confidence)
    rows.append(common | {"method": "QMC-amplitude", "estimate": qmc_successes / qmc_trials, "lower": lower, "upper": upper, "oracle_queries": qmc_trials, "shots": qmc_trials})

    depths, shots = allocate_schedule(budget, [0, 1, 2, 4, 8, 16, 32])
    schedule = simulate_counts(probability, depths, shots, np.random.default_rng(seed + 2), noise_lambda=noise)
    fitted = {
        "MLAE": fit_mlae(schedule, confidence),
        "NA-QAE": fit_noise_aware_qae(schedule, confidence),
        "CAL-QAE": fit_visibility_calibrated_qae(schedule, noise_lambda=noise, confidence_level=confidence),
    }
    for method, estimate in fitted.items():
        rows.append(
            common
            | {
                "method": method,
                "estimate": estimate.amplitude,
                "lower": estimate.lower,
                "upper": estimate.upper,
                "oracle_queries": schedule.oracle_queries,
                "shots": schedule.total_shots,
                "noise_lambda_estimate": estimate.noise_lambda,
                "grover_depths": ",".join(map(str, schedule.grover_depths)),
                "successes": ",".join(map(str, schedule.successes)),
            }
        )
    iqae = simulate_iterative_qae(
        probability,
        budget,
        np.random.default_rng(seed + 3),
        confidence_level=confidence,
        noise_lambda=noise,
    )
    rows.append(
        common
        | {
            "method": "IQAE",
            "estimate": iqae.amplitude,
            "lower": iqae.lower,
            "upper": iqae.upper,
            "oracle_queries": iqae.oracle_queries,
            "shots": sum(iqae.shots),
            "grover_depths": ",".join(map(str, iqae.grover_depths)),
            "successes": ",".join(map(str, iqae.successes)),
        }
    )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--candidates",
        type=Path,
        default=PROJECT_ROOT / "results" / "raw" / "oc4_optimization" / "oc4_selected_candidates_final_400.csv",
    )
    parser.add_argument(
        "--response-bundle",
        type=Path,
        default=PROJECT_ROOT / "results" / "raw" / "oc4_surrogates" / "oc4_multifidelity_models_final_400.joblib",
    )
    parser.add_argument(
        "--meta-bundle",
        type=Path,
        default=PROJECT_ROOT / "results" / "raw" / "oc4_optimization" / "oc4_reliability_meta_models_final_400.joblib",
    )
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "results" / "raw" / "oc4_reliability")
    parser.add_argument("--replicates", type=int, default=100)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--noise-lambda", type=float, default=0.005)
    args = parser.parse_args()
    args.candidates = args.candidates.resolve()
    args.response_bundle = args.response_bundle.resolve()
    args.meta_bundle = args.meta_bundle.resolve()
    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    candidates = pd.read_csv(args.candidates)
    candidates.insert(0, "candidate_index", np.arange(len(candidates)))
    response_bundle = joblib.load(args.response_bundle)
    meta_bundle = joblib.load(args.meta_bundle)
    metocean = pd.read_parquet(DATA_PATH)
    temporal = metocean[(metocean["complete_core"]) & (metocean["split"] == "temporal_test")].copy()
    response = predict_response_cartesian(
        response_bundle, candidates[DESIGN_COLUMNS], temporal, SCREENING_TARGETS, design_chunk_size=1
    )
    reference, ratios = system_failure_probability(response, meta_bundle["capacities"])
    candidates["reference_probability"] = reference
    candidates["reference_system_ratio_quantile_beta3"] = np.quantile(
        ratios, 1 - float(meta_bundle["target_probability"]), axis=1
    )
    tasks = [
        (candidate, budget, replicate)
        for candidate in candidates.to_dict(orient="records")
        for budget in (2048, 8192, 32768)
        for replicate in range(args.replicates)
    ]
    nested = Parallel(n_jobs=args.workers, prefer="processes", verbose=5)(
        delayed(run_replicate)(candidate, budget, replicate, args.noise_lambda)
        for candidate, budget, replicate in tasks
    )
    raw = pd.DataFrame([row for group in nested for row in group])
    summary = summarize_estimates(raw)
    raw_path = args.output_dir / "oc4_reliability_raw_full.parquet"
    summary_path = args.output_dir / "oc4_reliability_summary_full.csv"
    reference_path = args.output_dir / "oc4_candidate_reference_probabilities.csv"
    raw.to_parquet(raw_path, index=False)
    summary.to_csv(summary_path, index=False)
    candidates.to_csv(reference_path, index=False)
    files = [raw_path, summary_path, reference_path]
    report = {
        "scope": "Probability-estimator experiment at frozen OC4 designs using the untouched temporal-test response-surrogate amplitude.",
        "candidate_designs": len(candidates),
        "temporal_environment_rows": len(temporal),
        "replicates_per_candidate_budget": args.replicates,
        "query_budgets": [2048, 8192, 32768],
        "controlled_noise_lambda": args.noise_lambda,
        "qmc_scope": "scalar Bernoulli-amplitude QMC control",
        "non_hardware_statement": "All quantum counts in this experiment are simulated; fake-backend and functional-circuit experiments are reported separately.",
        "elapsed_seconds": time.perf_counter() - started,
        "platform": platform.platform(),
        "files": {str(path.relative_to(PROJECT_ROOT)): sha256(path) for path in files},
    }
    report_path = args.output_dir / "oc4_reliability_report_full.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
