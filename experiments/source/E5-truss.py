"""Run paired confidence-aware ABC experiments on the 10-bar bridge case."""

from __future__ import annotations

import argparse
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
import qiskit
import scipy
import sklearn
import yaml
from joblib import Parallel, delayed
from scipy.stats import beta, norm, qmc

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qae_abc.optimization.abc import ConfidenceAwareABC, ReliabilityEvaluation
from qae_abc.quantum.functional_oracle import (
    bit_rows,
    decoded_latent_variables,
    state_preparation_probabilities,
)
from qae_abc.quantum.likelihood import (
    allocate_schedule,
    fit_mlae,
    fit_noise_aware_qae,
    fit_visibility_calibrated_qae,
    simulate_counts,
)
from qae_abc.structural.truss import TenBarTruss, TrussUncertainty
from qae_abc.surrogate.truss_surrogates import truss_feature_matrix


METHODS = [
    "exact_abc",
    "mc_abc",
    "qmc_abc",
    "mlae_abc",
    "noisy_mlae_abc",
    "na_qae_fixed_abc",
    "na_qae_abc",
]


def clopper_pearson(successes: int, trials: int, confidence: float) -> tuple[float, float]:
    alpha = 1.0 - confidence
    lower = 0.0 if successes == 0 else float(beta.ppf(alpha / 2, successes, trials - successes + 1))
    upper = 1.0 if successes == trials else float(beta.ppf(1 - alpha / 2, successes + 1, trials - successes))
    return lower, upper


class TrussProbabilityEvaluator:
    def __init__(self, model_path: Path, method: str, config: dict, measurement_seed: int = 0) -> None:
        self.surrogate = joblib.load(model_path)
        self.method = method
        self.config = config
        self.truss = TenBarTruss()
        self.uncertainty = TrussUncertainty()
        bits_per_variable = int(config["oracle_bits_per_variable"])
        rows = bit_rows(3 * bits_per_variable)
        levels, self.weights = state_preparation_probabilities(bits_per_variable, 3)
        self.z = decoded_latent_variables(rows, bits_per_variable, levels)
        self.fast_functional_coefficients = self._prepare_fast_functional_coefficients()
        self.qmc_latents = self._prepare_qmc_latents(measurement_seed)
        self.cache: dict[tuple[int, ...], ReliabilityEvaluation] = {}

    def canonicalize_design(self, design: np.ndarray) -> np.ndarray:
        """Snap areas to the same manufacturing grid used by the result cache."""

        tolerance = float(self.config["cache_tolerance_m2"])
        return np.rint(np.asarray(design, dtype=float) / tolerance) * tolerance

    @staticmethod
    def _effective_linear_coefficients(pipeline) -> tuple[float, np.ndarray]:
        scaler = pipeline.named_steps["scale"]
        linear = pipeline.named_steps["linear"]
        coefficients = np.asarray(linear.coef_, dtype=float) / np.asarray(scaler.scale_)
        intercept = float(linear.intercept_ - np.dot(coefficients, scaler.mean_))
        return intercept, coefficients

    def _prepare_fast_functional_coefficients(self):
        if not hasattr(self.surrogate, "stress") or not hasattr(self.surrogate, "displacement"):
            return None
        stress_intercept, stress = self._effective_linear_coefficients(self.surrogate.stress)
        displacement_intercept, displacement = self._effective_linear_coefficients(
            self.surrogate.displacement
        )
        z_load, z_yield, z_modulus = self.z.T
        stress_latent = (
            stress[0]
            + stress[1] * z_load
            + stress[2] * z_yield
            + stress[3] * z_load**2
            + stress[4] * z_load * z_yield
            + stress[5] * z_yield**2
        )
        displacement_latent = (
            displacement[0]
            + displacement[1] * z_load
            + displacement[2] * z_modulus
            + displacement[3] * z_load**2
            + displacement[4] * z_load * z_modulus
            + displacement[5] * z_modulus**2
        )
        return stress_intercept, stress_latent, displacement_intercept, displacement_latent

    def _prepare_qmc_latents(self, seed: int) -> dict[int, tuple[np.ndarray, np.ndarray]]:
        if self.method != "qmc_abc" or self.fast_functional_coefficients is None:
            return {}
        maximum_budget = int(max(self.config["dynamic_query_budgets"]))
        exponent = int(np.floor(np.log2(maximum_budget)))
        unit = qmc.Sobol(d=3, scramble=True, seed=seed).random_base2(exponent)
        z = norm.ppf(np.clip(unit, 1e-12, 1 - 1e-12))
        stress_intercept, stress = self._effective_linear_coefficients(self.surrogate.stress)
        displacement_intercept, displacement = self._effective_linear_coefficients(
            self.surrogate.displacement
        )
        z_load, z_yield, z_modulus = z.T
        stress_latent = (
            stress[0]
            + stress[1] * z_load
            + stress[2] * z_yield
            + stress[3] * z_load**2
            + stress[4] * z_load * z_yield
            + stress[5] * z_yield**2
        )
        displacement_latent = (
            displacement[0]
            + displacement[1] * z_load
            + displacement[2] * z_modulus
            + displacement[3] * z_load**2
            + displacement[4] * z_load * z_modulus
            + displacement[5] * z_modulus**2
        )
        return {
            int(budget): (
                stress_latent[: int(budget)],
                displacement_latent[: int(budget)],
            )
            for budget in self.config["dynamic_query_budgets"]
        }

    def qmc_failures(self, design: np.ndarray, budget: int) -> np.ndarray:
        nominal = self.truss.solve(design)
        stress_ratio = 1.0 - nominal.stress_limit_state
        displacement_ratio = 1.0 - nominal.displacement_limit_state
        stress_intercept, _, displacement_intercept, _ = self.fast_functional_coefficients
        stress_latent, displacement_latent = self.qmc_latents[int(budget)]
        return np.minimum(
            stress_intercept + stress_ratio * stress_latent,
            displacement_intercept + displacement_ratio * displacement_latent,
        ) <= 0.0

    def surrogate_probability(self, design: np.ndarray) -> float:
        if self.fast_functional_coefficients is None:
            prediction = self.surrogate.predict(truss_feature_matrix(self.truss, design, self.z))
        else:
            nominal = self.truss.solve(design)
            stress_ratio = 1.0 - nominal.stress_limit_state
            displacement_ratio = 1.0 - nominal.displacement_limit_state
            stress_intercept, stress_latent, displacement_intercept, displacement_latent = (
                self.fast_functional_coefficients
            )
            prediction = np.minimum(
                stress_intercept + stress_ratio * stress_latent,
                displacement_intercept + displacement_ratio * displacement_latent,
            )
        return float(self.weights[prediction <= 0.0].sum())

    def continuous_surrogate_failures(self, design: np.ndarray, z: np.ndarray) -> np.ndarray:
        if self.fast_functional_coefficients is None:
            return self.surrogate.predict(truss_feature_matrix(self.truss, design, z)) <= 0.0
        nominal = self.truss.solve(design)
        stress_ratio = 1.0 - nominal.stress_limit_state
        displacement_ratio = 1.0 - nominal.displacement_limit_state
        stress_intercept, stress_coefficients = self._effective_linear_coefficients(
            self.surrogate.stress
        )
        displacement_intercept, displacement_coefficients = self._effective_linear_coefficients(
            self.surrogate.displacement
        )
        z_load, z_yield, z_modulus = np.asarray(z).T
        stress_basis = np.column_stack(
            [
                np.ones(len(z)),
                z_load,
                z_yield,
                z_load**2,
                z_load * z_yield,
                z_yield**2,
            ]
        )
        displacement_basis = np.column_stack(
            [
                np.ones(len(z)),
                z_load,
                z_modulus,
                z_load**2,
                z_load * z_modulus,
                z_modulus**2,
            ]
        )
        stress = stress_intercept + stress_ratio * (stress_basis @ stress_coefficients)
        displacement = displacement_intercept + displacement_ratio * (
            displacement_basis @ displacement_coefficients
        )
        return np.minimum(stress, displacement) <= 0.0

    def __call__(self, design: np.ndarray, rng: np.random.Generator) -> ReliabilityEvaluation:
        tolerance = float(self.config["cache_tolerance_m2"])
        key = tuple(np.rint(np.asarray(design) / tolerance).astype(int))
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

        if self.method == "exact_abc":
            probability = self.truss.exact_failure_probability(design, self.uncertainty)["pf_system"]
            evaluation = ReliabilityEvaluation(probability, probability, probability, probability, 0, 1)
            self.cache[key] = evaluation
            return evaluation

        if self.method == "lookup_abc":
            probability = self.surrogate_probability(design)
            evaluation = ReliabilityEvaluation(
                probability,
                probability,
                probability,
                probability,
                int(len(self.weights)),
                1,
            )
            self.cache[key] = evaluation
            return evaluation

        probability = (
            self.truss.exact_failure_probability(design, self.uncertainty)["pf_system"]
            if self.method == "qmc_abc"
            else self.surrogate_probability(design)
        )
        total_queries = 0
        calls = 0
        estimate = lower = upper = np.nan
        budgets = (
            [self.config["dynamic_query_budgets"][-1]]
            if self.method == "na_qae_fixed_abc"
            else self.config["dynamic_query_budgets"]
        )
        for budget in budgets:
            calls += 1
            if self.method == "mc_abc":
                successes = int(rng.binomial(int(budget), probability))
                estimate = successes / int(budget)
                lower, upper = clopper_pearson(successes, int(budget), self.config["confidence_level"])
                total_queries += int(budget)
            elif self.method == "qmc_abc":
                failures = self.qmc_failures(design, int(budget))
                successes = int(np.count_nonzero(failures))
                trials = len(failures)
                estimate = successes / trials
                # Used as a conservative decision interval; QMC coverage is
                # reported as descriptive rather than an exact binomial claim.
                lower, upper = clopper_pearson(successes, trials, self.config["confidence_level"])
                total_queries += trials
            else:
                depths, shots = allocate_schedule(int(budget), self.config["grover_depths"])
                noise_lambda = float(self.config["noise_lambda"]) if self.method != "mlae_abc" else 0.0
                schedule = simulate_counts(
                    probability,
                    depths,
                    shots,
                    rng,
                    noise_lambda=noise_lambda,
                )
                fitted = (
                    fit_visibility_calibrated_qae(
                        schedule,
                        noise_lambda=float(self.config["calibrated_noise_lambda"]),
                        confidence_level=self.config["confidence_level"],
                    )
                    if self.method in {"na_qae_abc", "na_qae_fixed_abc"}
                    else fit_mlae(schedule, self.config["confidence_level"])
                )
                estimate, lower, upper = fitted.amplitude, fitted.lower, fitted.upper
                total_queries += schedule.oracle_queries
            if upper <= self.config["target_probability"] or lower > self.config["target_probability"]:
                break
        evaluation = ReliabilityEvaluation(
            float(estimate),
            float(lower),
            float(upper),
            probability,
            total_queries,
            calls,
        )
        self.cache[key] = evaluation
        return evaluation


def run_one(method: str, run: int, config: dict, model_path: Path) -> tuple[dict, list[dict]]:
    started = time.perf_counter()
    # All estimators use the same optimizer seed for a paired-run design.  The
    # optimizer maintains an independent measurement stream internally.
    seed = int(np.random.SeedSequence([config["master_seed"], run]).generate_state(1)[0])
    evaluator = TrussProbabilityEvaluator(model_path, method, config, measurement_seed=seed)
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
    )
    best, history = optimizer.optimize(seed)
    exact = evaluator.truss.exact_failure_probability(best.design, evaluator.uncertainty)["pf_system"]
    discrete = evaluator.surrogate_probability(best.design)
    result = {
        "method": method,
        "run": run,
        "seed": seed,
        **{f"area_group_{index + 1}_m2": float(value) for index, value in enumerate(best.design)},
        "mass_kg": best.mass,
        "estimated_probability": best.reliability.estimate,
        "estimated_lower": best.reliability.lower,
        "estimated_upper": best.reliability.upper,
        "discrete_pce_probability": discrete,
        "exact_continuous_probability": exact,
        "exact_beta": float(-norm.ppf(exact)) if exact > 0 else np.inf,
        "estimated_safe": bool(best.reliability.upper <= config["target_probability"]),
        "exact_feasible": bool(exact <= config["target_probability"]),
        "cumulative_oracle_queries": int(history[-1]["cumulative_oracle_queries"]),
        "cumulative_estimator_calls": int(history[-1]["cumulative_estimator_calls"]),
        "unique_cached_designs": len(evaluator.cache),
        "elapsed_seconds": time.perf_counter() - started,
    }
    for row in history:
        row.update({"method": method, "run": run, "seed": seed})
    return result, history


def summarize(results: pd.DataFrame) -> pd.DataFrame:
    records = []
    for method, group in results.groupby("method", sort=False):
        feasible = group["exact_feasible"]
        feasible_mass = group.loc[feasible, "mass_kg"]
        records.append(
            {
                "method": method,
                "runs": len(group),
                "exact_feasibility_rate": float(feasible.mean()),
                "median_mass_kg": float(group["mass_kg"].median()),
                "iqr_mass_kg": float(group["mass_kg"].quantile(0.75) - group["mass_kg"].quantile(0.25)),
                "median_feasible_mass_kg": float(feasible_mass.median()) if len(feasible_mass) else np.nan,
                "median_exact_probability": float(group["exact_continuous_probability"].median()),
                "median_exact_beta": float(group["exact_beta"].replace(np.inf, np.nan).median()),
                "median_oracle_queries": float(group["cumulative_oracle_queries"].median()),
                "median_estimator_calls": float(group["cumulative_estimator_calls"].median()),
                "median_elapsed_seconds": float(group["elapsed_seconds"].median()),
            }
        )
    return pd.DataFrame(records)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "configs" / "truss.yaml")
    parser.add_argument(
        "--model",
        type=Path,
        default=PROJECT_ROOT / "results" / "raw" / "truss_surrogate" / "sparse_pce.joblib",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "results" / "raw" / "truss_optimization",
    )
    parser.add_argument("--jobs", type=int, default=-1)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    config["target_probability"] = float(norm.cdf(-float(config["target_beta"])))
    if args.smoke:
        config["food_sources"] = 8
        config["iterations"] = 8
        config["independent_runs"] = 2
        config["dynamic_query_budgets"] = [256, 1024]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    tasks = [(method, run) for method in METHODS for run in range(config["independent_runs"])]
    outputs = Parallel(n_jobs=args.jobs, prefer="processes", verbose=5)(
        delayed(run_one)(method, run, config, args.model) for method, run in tasks
    )
    results = pd.DataFrame([output[0] for output in outputs])
    history = pd.DataFrame([row for output in outputs for row in output[1]])
    suffix = "smoke" if args.smoke else "full"
    results.to_parquet(args.output_dir / f"optimization_runs_{suffix}.parquet", index=False)
    history.to_parquet(args.output_dir / f"optimization_history_{suffix}.parquet", index=False)
    summary = summarize(results)
    summary.to_csv(args.output_dir / f"optimization_summary_{suffix}.csv", index=False)
    metadata = {
        "config": config,
        "methods": METHODS,
        "na_qae_optimization_mode": "visibility calibrated once; joint nuisance fitting evaluated in analytic experiment",
        "result_rows": len(results),
        "history_rows": len(history),
        "versions": {
            "python": sys.version,
            "platform": platform.platform(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "sklearn": sklearn.__version__,
            "qiskit": qiskit.__version__,
        },
    }
    (args.output_dir / f"optimization_metadata_{suffix}.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
