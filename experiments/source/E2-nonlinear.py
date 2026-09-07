"""Run S0-2 nonlinear reliability estimators with auditable query accounting."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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
import scipy
import yaml
from joblib import Parallel, delayed

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qae_abc.evaluation.metrics import summarize_estimates
from qae_abc.quantum.likelihood import (
    allocate_schedule,
    fit_mlae,
    fit_noise_aware_qae,
    simulate_counts,
    simulate_iterative_qae,
)
from qae_abc.reliability.nonlinear import (
    ParabolicLimitState,
    QuadraticLimitState,
    estimate_form,
    estimate_mc_limit_state,
    estimate_qmc_limit_state,
    estimate_subset_simulation,
)


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def seed_for(master: int, *parts: int) -> int:
    return int(np.random.SeedSequence([master, *parts]).generate_state(1, dtype=np.uint32)[0])


def make_benchmark(config: dict, model: str, probability: float):
    if model == "g2_parabolic":
        return ParabolicLimitState.from_failure_probability(
            probability,
            kappa=float(config["parabolic"]["kappa"]),
            a=float(config["parabolic"]["a"]),
        )
    if model == "g3_quadratic":
        return QuadraticLimitState.from_failure_probability(
            probability, dimension=int(config["quadratic"]["dimension"])
        )
    raise ValueError(model)


def row_base(model: str, benchmark, probability: float, budget: int, replicate: int, seed: int) -> dict:
    return {
        "model": model,
        "dimension": benchmark.dimension,
        "threshold_c": benchmark.c,
        "true_probability": probability,
        "query_budget": budget,
        "replicate": replicate,
        "seed": seed,
    }


def run_cell(config: dict, model: str, probability: float, budget: int, replicate: int) -> list[dict]:
    benchmark = make_benchmark(config, model, probability)
    model_code = 2 if model == "g2_parabolic" else 3
    probability_code = int(round(-np.log10(probability)))
    seed = seed_for(int(config["master_seed"]), model_code, probability_code, budget, replicate)
    rows: list[dict] = []

    mc = estimate_mc_limit_state(benchmark, budget, np.random.default_rng(seed), config["confidence_level"])
    rows.append({**row_base(model, benchmark, probability, budget, replicate, seed), "experiment": "ideal", "noise_lambda_true": 0.0, "method": "MC", "estimate": mc.estimate, "lower": mc.lower, "upper": mc.upper, "oracle_queries": mc.oracle_queries, "shots": mc.samples, "converged": True, "noise_lambda_estimate": np.nan, "grover_depths": "0", "successes": "", "shots_by_depth": str(mc.samples), "interval_method": "Clopper-Pearson"})

    qmc_est = estimate_qmc_limit_state(benchmark, budget, seed + 1, config["confidence_level"])
    rows.append({**row_base(model, benchmark, probability, budget, replicate, seed + 1), "experiment": "ideal", "noise_lambda_true": 0.0, "method": "QMC", "estimate": qmc_est.estimate, "lower": qmc_est.lower, "upper": qmc_est.upper, "oracle_queries": qmc_est.oracle_queries, "shots": qmc_est.samples, "converged": True, "noise_lambda_estimate": np.nan, "grover_depths": "0", "successes": "", "shots_by_depth": str(qmc_est.samples), "interval_method": "descriptive Clopper-Pearson; QMC coverage not claimed"})

    samples_per_level = max(100, budget // int(config["subset_max_levels"]))
    # Maintain exact chain tiling for p0=0.1.
    samples_per_level -= samples_per_level % 10
    ss = estimate_subset_simulation(
        benchmark,
        samples_per_level=samples_per_level,
        rng=np.random.default_rng(seed + 2),
        conditional_probability=float(config["subset_probability"]),
        proposal_scale=float(config["subset_proposal_scale"]),
        max_levels=int(config["subset_max_levels"]),
        confidence_level=float(config["confidence_level"]),
    )
    rows.append({**row_base(model, benchmark, probability, budget, replicate, seed + 2), "experiment": "ideal", "noise_lambda_true": 0.0, "method": "SS", "estimate": ss.estimate, "lower": ss.lower, "upper": ss.upper, "oracle_queries": ss.model_evaluations, "shots": ss.samples_per_level * ss.levels, "converged": ss.converged, "noise_lambda_estimate": np.nan, "grover_depths": "", "successes": "", "shots_by_depth": "", "interval_method": "descriptive delta-lognormal; replicate dispersion primary", "subset_levels": ss.levels, "subset_thresholds": ",".join(f"{value:.12g}" for value in ss.thresholds), "subset_acceptance_rates": ",".join(f"{value:.8g}" for value in ss.acceptance_rates)})

    for noise_index, noise_lambda in enumerate([0.0, *map(float, config["noise_lambdas"])]):
        qae_seed = seed_for(int(config["master_seed"]), 10 + noise_index, model_code, probability_code, budget, replicate)
        depths, shots = allocate_schedule(budget, config["grover_depths"])
        schedule = simulate_counts(probability, depths, shots, np.random.default_rng(qae_seed), noise_lambda=noise_lambda)
        mlae = fit_mlae(schedule, config["confidence_level"])
        rows.append({**row_base(model, benchmark, probability, budget, replicate, qae_seed), "experiment": "ideal" if noise_lambda == 0 else "controlled_visibility_decay", "noise_lambda_true": noise_lambda, "method": "MLAE", "estimate": mlae.amplitude, "lower": mlae.lower, "upper": mlae.upper, "oracle_queries": schedule.oracle_queries, "shots": schedule.total_shots, "converged": mlae.converged, "noise_lambda_estimate": mlae.noise_lambda, "grover_depths": ",".join(map(str, schedule.grover_depths)), "successes": ",".join(map(str, schedule.successes)), "shots_by_depth": ",".join(map(str, schedule.shots)), "interval_method": mlae.interval_method})
        if noise_lambda > 0:
            noise_aware = fit_noise_aware_qae(schedule, config["confidence_level"])
            rows.append({**row_base(model, benchmark, probability, budget, replicate, qae_seed), "experiment": "controlled_visibility_decay", "noise_lambda_true": noise_lambda, "method": "NA-QAE", "estimate": noise_aware.amplitude, "lower": noise_aware.lower, "upper": noise_aware.upper, "oracle_queries": schedule.oracle_queries, "shots": schedule.total_shots, "converged": noise_aware.converged, "noise_lambda_estimate": noise_aware.noise_lambda, "grover_depths": ",".join(map(str, schedule.grover_depths)), "successes": ",".join(map(str, schedule.successes)), "shots_by_depth": ",".join(map(str, schedule.shots)), "interval_method": noise_aware.interval_method})

        iqae = simulate_iterative_qae(
            probability,
            budget,
            np.random.default_rng(qae_seed + 1),
            confidence_level=float(config["confidence_level"]),
            shots_per_round=int(config["iqae_shots_per_round"]),
            noise_lambda=noise_lambda,
        )
        rows.append({**row_base(model, benchmark, probability, budget, replicate, qae_seed + 1), "experiment": "ideal" if noise_lambda == 0 else "controlled_visibility_decay", "noise_lambda_true": noise_lambda, "method": "IQAE", "estimate": iqae.amplitude, "lower": iqae.lower, "upper": iqae.upper, "oracle_queries": iqae.oracle_queries, "shots": sum(iqae.shots), "converged": iqae.converged, "noise_lambda_estimate": np.nan, "grover_depths": ",".join(map(str, iqae.grover_depths)), "successes": ",".join(map(str, iqae.successes)), "shots_by_depth": ",".join(map(str, iqae.shots)), "interval_method": "iterative Clopper-Pearson (noise-naive under decay)"})
    return rows


def form_rows(config: dict) -> list[dict]:
    rows = []
    for model in ("g2_parabolic", "g3_quadratic"):
        for probability in map(float, config["failure_probabilities"]):
            benchmark = make_benchmark(config, model, probability)
            estimate = estimate_form(benchmark)
            rows.append({**row_base(model, benchmark, probability, estimate.model_evaluations, 0, int(config["master_seed"])), "experiment": "deterministic_approximation", "noise_lambda_true": 0.0, "method": "FORM", "estimate": estimate.estimate, "lower": estimate.estimate, "upper": estimate.estimate, "oracle_queries": estimate.model_evaluations, "shots": 0, "converged": estimate.converged, "noise_lambda_estimate": np.nan, "grover_depths": "", "successes": "", "shots_by_depth": "", "interval_method": "deterministic first-order approximation", "form_beta": estimate.beta_index, "form_design_point": ",".join(f"{value:.12g}" for value in estimate.design_point)})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "configs" / "nonlinear.yaml")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "results" / "raw" / "nonlinear")
    parser.add_argument("--jobs", type=int, default=6)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    if args.smoke:
        config["failure_probabilities"] = [0.01, 0.0001]
        config["query_budgets"] = [1024]
        config["replicates"] = 2
        config["noise_lambdas"] = [0.01]
    cells = [(model, float(probability), int(budget), replicate) for model in ("g2_parabolic", "g3_quadratic") for probability in config["failure_probabilities"] for budget in config["query_budgets"] for replicate in range(int(config["replicates"]))]
    started = time.perf_counter()
    nested = Parallel(n_jobs=args.jobs, prefer="processes", verbose=5)(delayed(run_cell)(config, *cell) for cell in cells)
    rows = [row for group in nested for row in group] + form_rows(config)
    raw = pd.DataFrame(rows)
    summary = summarize_estimates(raw)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    suffix = "smoke" if args.smoke else "full"
    raw_path = args.output_dir / f"nonlinear_raw_{suffix}.parquet"
    summary_path = args.output_dir / f"nonlinear_summary_{suffix}.csv"
    raw.to_parquet(raw_path, index=False)
    summary.to_csv(summary_path, index=False)
    metadata = {"study_id": config["study_id"], "config": config, "elapsed_seconds": time.perf_counter() - started, "python": sys.version, "platform": platform.platform(), "logical_cores": os.cpu_count(), "numpy": np.__version__, "pandas": pd.__version__, "scipy": scipy.__version__, "cells": len(cells), "row_count": len(raw), "raw_file": str(raw_path.relative_to(PROJECT_ROOT)), "summary_file": str(summary_path.relative_to(PROJECT_ROOT)), "raw_sha256": hash_file(raw_path), "summary_sha256": hash_file(summary_path)}
    (args.output_dir / f"nonlinear_metadata_{suffix}.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(metadata, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
