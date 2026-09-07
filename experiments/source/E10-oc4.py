"""Run paired OC4 reliability optimizations on the audited meta-surrogate.

Every optimization call is made against the fit-period reliability meta-model.
After all runs are frozen, every returned design is re-evaluated with the
underlying multi-fidelity response model on the untouched 2020--2024 observed
environment.  QMC here is explicitly an oracle-amplitude control, not an
environmental QMC claim.
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
import scipy
from joblib import Parallel, delayed
from scipy.optimize import differential_evolution
from scipy.stats import beta, norm, qmc

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "experiments"))

from qae_abc.optimization.abc import (
    ConfidenceAwareABC,
    FoodSource,
    ReliabilityEvaluation,
    constraint_rank,
)
from qae_abc.quantum.likelihood import (
    allocate_schedule,
    fit_visibility_calibrated_qae,
    simulate_counts,
)
from qae_abc.reliability.oc4 import (
    DESIGN_COLUMNS,
    SCREENING_TARGETS,
    predict_response_cartesian,
    system_failure_probability,
)
from run_openfast_oc4_doe import BASELINE, BOUNDS, geometry_mass_and_frequency_proxy


DATA_PATH = PROJECT_ROOT / "data" / "processed" / "metocean" / "era5_open_meteo_54N_6.5E_2000_2024.parquet"
ABC_METHODS = ["meta_reference_abc", "mc_abc", "qmc_amplitude_abc", "na_qae_abc"]
ALL_METHODS = ABC_METHODS + ["na_qae_de"]


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


class OC4MetaEvaluator:
    def __init__(self, bundle: dict, method: str, config: dict, seed: int) -> None:
        self.bundle = bundle
        self.method = method
        self.config = config
        self.rng_seed = int(seed)
        self.cache: dict[tuple[int, ...], ReliabilityEvaluation] = {}
        self.mass_cache: dict[tuple[int, ...], float] = {}

    def _key(self, design: np.ndarray) -> tuple[int, ...]:
        tolerance = float(self.config["cache_tolerance_m"])
        return tuple(np.rint(np.asarray(design, dtype=float) / tolerance).astype(int))

    def canonicalize_design(self, design: np.ndarray) -> np.ndarray:
        tolerance = float(self.config["cache_tolerance_m"])
        return np.rint(np.asarray(design, dtype=float) / tolerance) * tolerance

    def mass(self, design: np.ndarray) -> float:
        key = self._key(design)
        if key not in self.mass_cache:
            row = dict(zip(DESIGN_COLUMNS, np.asarray(design, dtype=float), strict=True))
            self.mass_cache[key] = float(geometry_mass_and_frequency_proxy(row)[0])
        return self.mass_cache[key]

    def meta_probability(self, design: np.ndarray) -> float:
        model = self.bundle["models"]["logit_probability"]["model"]
        predicted_logit = float(model.predict(np.asarray(design, dtype=float).reshape(1, -1))[0])
        # Do not bind scipy.special.expit as a ``__main__`` global.  On
        # Windows, loky's spawn-based workers otherwise try to unpickle the
        # ufunc as ``__main__.expit`` when ``run_abc`` is dispatched, which
        # breaks multi-process execution even though the fitted model itself
        # is valid.
        return float(np.clip(scipy.special.expit(predicted_logit), 1e-9, 1 - 1e-9))

    def __call__(self, design: np.ndarray, rng: np.random.Generator) -> ReliabilityEvaluation:
        key = self._key(design)
        cached = self.cache.get(key)
        if cached is not None:
            return ReliabilityEvaluation(
                cached.estimate,
                cached.lower,
                cached.upper,
                cached.true_probability,
                0,
                0,
            )
        probability = self.meta_probability(design)
        confidence = float(self.config["confidence_level"])
        if self.method == "meta_reference_abc":
            result = ReliabilityEvaluation(probability, probability, probability, probability, 0, 1)
            self.cache[key] = result
            return result

        estimates: tuple[float, float, float] | None = None
        total_queries = 0
        calls = 0
        budgets = self.config["dynamic_query_budgets"]
        for budget in budgets:
            budget = int(budget)
            calls += 1
            if self.method == "mc_abc":
                successes = int(rng.binomial(budget, probability))
                interval = clopper_pearson(successes, budget, confidence)
                estimates = (successes / budget, *interval)
                total_queries += budget
            elif self.method == "qmc_amplitude_abc":
                exponent = int(np.floor(np.log2(budget)))
                uniform = qmc.Sobol(1, scramble=True, seed=int(rng.integers(0, 2**31 - 1))).random_base2(exponent)[:, 0]
                successes = int(np.count_nonzero(uniform < probability))
                trials = len(uniform)
                interval = clopper_pearson(successes, trials, confidence)
                estimates = (successes / trials, *interval)
                total_queries += trials
            else:
                depths, shots = allocate_schedule(budget, self.config["grover_depths"])
                schedule = simulate_counts(
                    probability,
                    depths,
                    shots,
                    rng,
                    noise_lambda=float(self.config["noise_lambda"]),
                )
                fitted = fit_visibility_calibrated_qae(
                    schedule,
                    noise_lambda=float(self.config["noise_lambda"]),
                    confidence_level=confidence,
                )
                estimates = (fitted.amplitude, fitted.lower, fitted.upper)
                total_queries += schedule.oracle_queries
            if estimates[2] <= self.config["target_probability"] or estimates[1] > self.config["target_probability"]:
                break
        assert estimates is not None
        result = ReliabilityEvaluation(
            float(estimates[0]),
            float(estimates[1]),
            float(estimates[2]),
            probability,
            total_queries,
            calls,
        )
        self.cache[key] = result
        return result


def result_record(
    method: str,
    run: int,
    seed: int,
    best: FoodSource,
    evaluator: OC4MetaEvaluator,
    queries: int,
    calls: int,
    elapsed: float,
) -> dict:
    return {
        "method": method,
        "run": run,
        "seed": seed,
        **{name: float(value) for name, value in zip(DESIGN_COLUMNS, best.design, strict=True)},
        "mass_kg": float(best.mass),
        "estimated_probability": float(best.reliability.estimate),
        "estimated_lower": float(best.reliability.lower),
        "estimated_upper": float(best.reliability.upper),
        "meta_reference_probability": evaluator.meta_probability(best.design),
        "estimated_safe": bool(best.reliability.upper <= evaluator.config["target_probability"]),
        "cumulative_oracle_queries": int(queries),
        "cumulative_estimator_calls": int(calls),
        "unique_cached_designs": len(evaluator.cache),
        "elapsed_seconds": elapsed,
    }


def run_abc(method: str, run: int, bundle: dict, config: dict) -> tuple[dict, list[dict]]:
    started = time.perf_counter()
    seed = int(np.random.SeedSequence([config["master_seed"], run]).generate_state(1)[0])
    evaluator = OC4MetaEvaluator(bundle, method, config, seed)
    optimizer = ConfidenceAwareABC(
        objective=evaluator.mass,
        evaluator=evaluator,
        lower_bounds=np.asarray([BOUNDS[name][0] for name in DESIGN_COLUMNS]),
        upper_bounds=np.asarray([BOUNDS[name][1] for name in DESIGN_COLUMNS]),
        target_probability=config["target_probability"],
        food_sources=config["food_sources"],
        iterations=config["iterations"],
        global_guidance=config["global_guidance"],
        scout_limit=config["scout_limit"],
        temperature=config["temperature"],
    )
    best, history = optimizer.optimize(seed)
    result = result_record(
        method,
        run,
        seed,
        best,
        evaluator,
        history[-1]["cumulative_oracle_queries"],
        history[-1]["cumulative_estimator_calls"],
        time.perf_counter() - started,
    )
    for row in history:
        row.update({"method": method, "run": run, "seed": seed})
    return result, history


def run_de(run: int, bundle: dict, config: dict) -> tuple[dict, list[dict]]:
    started = time.perf_counter()
    method = "na_qae_de"
    seed = int(np.random.SeedSequence([config["master_seed"], run]).generate_state(1)[0])
    rng = np.random.default_rng(seed)
    evaluator = OC4MetaEvaluator(bundle, method, config, seed)
    best: FoodSource | None = None
    queries = calls = 0
    history: list[dict] = []
    baseline_mass = evaluator.mass(np.asarray([BASELINE[name] for name in DESIGN_COLUMNS]))

    def objective(design: np.ndarray) -> float:
        nonlocal best, queries, calls
        canonical = evaluator.canonicalize_design(design)
        reliability = evaluator(canonical, rng)
        source = FoodSource(canonical, evaluator.mass(canonical), reliability)
        queries += reliability.oracle_queries
        calls += reliability.estimator_calls
        if best is None or constraint_rank(source, config["target_probability"]) < constraint_rank(best, config["target_probability"]):
            best = FoodSource(source.design.copy(), source.mass, source.reliability)
        violation = max(0.0, reliability.upper - config["target_probability"]) / config["target_probability"]
        return source.mass / baseline_mass + 100.0 * violation

    def callback(_x: np.ndarray, convergence: float) -> bool:
        assert best is not None
        history.append(
            {
                "iteration": len(history),
                "best_mass": best.mass,
                "best_probability_estimate": best.reliability.estimate,
                "best_probability_lower": best.reliability.lower,
                "best_probability_upper": best.reliability.upper,
                "best_true_discrete_probability": best.reliability.true_probability,
                "cumulative_oracle_queries": queries,
                "cumulative_estimator_calls": calls,
                "de_convergence": float(convergence),
            }
        )
        return False

    differential_evolution(
        objective,
        [(BOUNDS[name][0], BOUNDS[name][1]) for name in DESIGN_COLUMNS],
        strategy="best1bin",
        maxiter=config["de_iterations"],
        popsize=config["de_popsize"],
        tol=0.0,
        polish=False,
        seed=seed,
        workers=1,
        updating="immediate",
        callback=callback,
    )
    assert best is not None
    if not history:
        callback(best.design, 0.0)
    result = result_record(
        method, run, seed, best, evaluator, queries, calls, time.perf_counter() - started
    )
    for row in history:
        row.update({"method": method, "run": run, "seed": seed})
    return result, history


def temporal_validation(results: pd.DataFrame, response_bundle: dict, capacities: dict) -> pd.DataFrame:
    metocean = pd.read_parquet(DATA_PATH)
    environment = metocean[(metocean["complete_core"]) & (metocean["split"] == "temporal_test")].copy()
    probabilities: list[float] = []
    quantiles: list[float] = []
    for start in range(0, len(results), 4):
        stop = min(start + 4, len(results))
        designs = results.iloc[start:stop][DESIGN_COLUMNS]
        response = predict_response_cartesian(
            response_bundle, designs, environment, SCREENING_TARGETS, design_chunk_size=1
        )
        probability, ratio = system_failure_probability(response, capacities)
        probabilities.extend(probability.tolist())
        quantiles.extend(np.quantile(ratio, 1 - float(norm.cdf(-3.0)), axis=1).tolist())
    validated = results.copy()
    validated["temporal_test_probability"] = probabilities
    clipped = np.clip(validated["temporal_test_probability"].to_numpy(float), 1e-15, 1 - 1e-15)
    validated["temporal_test_beta"] = -norm.ppf(clipped)
    validated["temporal_test_system_ratio_quantile"] = quantiles
    validated["temporal_test_feasible"] = validated["temporal_test_probability"] <= float(norm.cdf(-3.0))
    return validated


def summarize(results: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for method, group in results.groupby("method", sort=False):
        feasible = group["temporal_test_feasible"]
        rows.append(
            {
                "method": method,
                "runs": len(group),
                "temporal_test_feasibility_rate": float(feasible.mean()),
                "mean_mass_kg": float(group["mass_kg"].mean()),
                "sd_mass_kg": float(group["mass_kg"].std(ddof=1)),
                "median_mass_kg": float(group["mass_kg"].median()),
                "iqr_mass_kg": float(group["mass_kg"].quantile(0.75) - group["mass_kg"].quantile(0.25)),
                "median_temporal_test_probability": float(group["temporal_test_probability"].median()),
                "median_temporal_test_beta": float(group["temporal_test_beta"].median()),
                "median_oracle_queries": float(group["cumulative_oracle_queries"].median()),
                "median_elapsed_seconds": float(group["elapsed_seconds"].median()),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--meta-bundle",
        type=Path,
        default=PROJECT_ROOT / "results" / "raw" / "oc4_optimization" / "oc4_reliability_meta_models_final_400.joblib",
    )
    parser.add_argument(
        "--response-bundle",
        type=Path,
        default=PROJECT_ROOT / "results" / "raw" / "oc4_surrogates" / "oc4_multifidelity_models_final_400.joblib",
    )
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "results" / "raw" / "oc4_optimization")
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--food-sources", type=int, default=12)
    parser.add_argument("--iterations", type=int, default=40)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--label", type=str, default="final_400")
    args = parser.parse_args()
    args.meta_bundle = args.meta_bundle.resolve()
    args.response_bundle = args.response_bundle.resolve()
    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.smoke:
        args.runs = 1
        args.workers = 1
        args.food_sources = 4
        args.iterations = 2
    meta_bundle = joblib.load(args.meta_bundle)
    response_bundle = joblib.load(args.response_bundle)
    config = {
        "master_seed": 20260951,
        "target_probability": float(meta_bundle["target_probability"]),
        "confidence_level": 0.95,
        "dynamic_query_budgets": [2048, 8192],
        "grover_depths": [0, 1, 2, 4, 8, 16],
        "noise_lambda": 0.005,
        "cache_tolerance_m": 0.002,
        "food_sources": args.food_sources,
        "iterations": args.iterations,
        "global_guidance": 0.5,
        "scout_limit": max(12, args.food_sources * len(DESIGN_COLUMNS) // 2),
        "temperature": 5.0,
        "de_iterations": args.iterations,
        "de_popsize": max(3, args.food_sources // len(DESIGN_COLUMNS)),
    }
    jobs = [(method, run) for method in ABC_METHODS for run in range(args.runs)]
    outputs = Parallel(n_jobs=args.workers, verbose=10)(
        delayed(run_abc)(method, run, meta_bundle, config) for method, run in jobs
    )
    de_outputs = Parallel(n_jobs=args.workers, verbose=10)(
        delayed(run_de)(run, meta_bundle, config) for run in range(args.runs)
    )
    outputs.extend(de_outputs)
    raw = pd.DataFrame([item[0] for item in outputs])
    histories = pd.DataFrame([row for item in outputs for row in item[1]])
    baseline_design = np.asarray([BASELINE[name] for name in DESIGN_COLUMNS])
    baseline_evaluator = OC4MetaEvaluator(meta_bundle, "meta_reference_abc", config, 0)
    baseline_probability = baseline_evaluator.meta_probability(baseline_design)
    baseline = pd.DataFrame(
        [
            {
                "method": "original_oc4",
                "run": -1,
                "seed": 0,
                **BASELINE,
                "mass_kg": baseline_evaluator.mass(baseline_design),
                "estimated_probability": baseline_probability,
                "estimated_lower": baseline_probability,
                "estimated_upper": baseline_probability,
                "meta_reference_probability": baseline_probability,
                "estimated_safe": baseline_probability <= config["target_probability"],
                "cumulative_oracle_queries": 0,
                "cumulative_estimator_calls": 1,
                "unique_cached_designs": 1,
                "elapsed_seconds": 0.0,
            }
        ]
    )
    validated = temporal_validation(pd.concat([baseline, raw], ignore_index=True), response_bundle, meta_bundle["capacities"])
    summary = summarize(validated)
    candidate_rows = []
    for method, group in validated.groupby("method", sort=False):
        feasible = group[group["temporal_test_feasible"]]
        selected = (feasible if len(feasible) else group.sort_values("temporal_test_probability")).sort_values("mass_kg").iloc[0]
        candidate_rows.append(selected.to_dict())
    candidates = pd.DataFrame(candidate_rows)

    suffix = ("smoke_" if args.smoke else "") + args.label
    raw_path = args.output_dir / f"oc4_optimization_runs_{suffix}.parquet"
    history_path = args.output_dir / f"oc4_optimization_history_{suffix}.parquet"
    summary_path = args.output_dir / f"oc4_optimization_summary_{suffix}.csv"
    candidate_path = args.output_dir / f"oc4_selected_candidates_{suffix}.csv"
    validated.to_parquet(raw_path, index=False)
    histories.to_parquet(history_path, index=False)
    summary.to_csv(summary_path, index=False)
    candidates.to_csv(candidate_path, index=False)
    files = [raw_path, history_path, summary_path, candidate_path]
    report = {
        "scope": "Paired OC4 optimization on fit-period reliability meta-model, followed by untouched 2020-2024 response-surrogate validation.",
        "methods": ALL_METHODS,
        "qmc_scope": "randomized QMC control on the scalar Bernoulli-amplitude oracle; not environmental QMC",
        "runs_per_method": args.runs,
        "configuration": config,
        "meta_bundle_sha256": sha256(args.meta_bundle),
        "response_bundle_sha256": sha256(args.response_bundle),
        "temporal_test_used_only_after_optimization": True,
        "non_certification_statement": "Reliability limits are operational method-validation quantities, not IEC/DNV certification limits.",
        "platform": platform.platform(),
        "scipy_version": scipy.__version__,
        "files": {str(path.relative_to(PROJECT_ROOT)): sha256(path) for path in files},
    }
    report_path = args.output_dir / f"oc4_optimization_report_{suffix}.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(summary.to_string(index=False))
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
