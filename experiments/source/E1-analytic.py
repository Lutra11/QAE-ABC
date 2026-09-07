"""Run the frozen L0 analytic reliability experiment."""

from __future__ import annotations

import argparse
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
import qiskit
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
from qae_abc.reliability.analytic import (
    GaussianDifferenceBenchmark,
    estimate_mc,
    estimate_scrambled_qmc,
)


def seed_for(master_seed: int, *parts: int) -> int:
    sequence = np.random.SeedSequence([master_seed, *parts])
    return int(sequence.generate_state(1, dtype=np.uint32)[0])


def append_classical_rows(rows: list[dict], config: dict, probability: float, budget: int, replicate: int) -> None:
    benchmark = GaussianDifferenceBenchmark.from_failure_probability(probability)
    seed = seed_for(config["master_seed"], 0, int(round(-np.log10(probability))), budget, replicate)
    mc = estimate_mc(benchmark, budget, np.random.default_rng(seed), config["confidence_level"])
    rows.append(
        {
            "experiment": "ideal",
            "noise_lambda_true": 0.0,
            "method": "MC",
            "true_probability": probability,
            "query_budget": budget,
            "replicate": replicate,
            "seed": seed,
            "estimate": mc.estimate,
            "lower": mc.lower,
            "upper": mc.upper,
            "oracle_queries": mc.oracle_queries,
            "shots": mc.samples,
            "converged": True,
            "noise_lambda_estimate": np.nan,
            "grover_depths": "0",
            "successes": "",
            "shots_by_depth": str(mc.samples),
            "interval_method": "Clopper-Pearson",
        }
    )
    qmc_estimate = estimate_scrambled_qmc(benchmark, budget, seed + 1, config["confidence_level"])
    rows.append(
        {
            "experiment": "ideal",
            "noise_lambda_true": 0.0,
            "method": "QMC",
            "true_probability": probability,
            "query_budget": budget,
            "replicate": replicate,
            "seed": seed + 1,
            "estimate": qmc_estimate.estimate,
            "lower": qmc_estimate.lower,
            "upper": qmc_estimate.upper,
            "oracle_queries": qmc_estimate.oracle_queries,
            "shots": qmc_estimate.samples,
            "converged": True,
            "noise_lambda_estimate": np.nan,
            "grover_depths": "0",
            "successes": "",
            "shots_by_depth": str(qmc_estimate.samples),
            "interval_method": "descriptive Clopper-Pearson; QMC coverage not claimed",
        }
    )


def append_qae_row(
    rows: list[dict],
    config: dict,
    probability: float,
    budget: int,
    replicate: int,
    noise_lambda: float,
    method: str,
) -> None:
    depths, shots = allocate_schedule(budget, config["grover_depths"])
    seed = seed_for(
        config["master_seed"],
        1,
        int(round(-np.log10(probability))),
        budget,
        replicate,
        int(round(noise_lambda * 1e6)),
    )
    schedule = simulate_counts(
        probability,
        depths,
        shots,
        np.random.default_rng(seed),
        noise_lambda=noise_lambda,
    )
    if method == "MLAE":
        estimate = fit_mlae(schedule, config["confidence_level"])
    elif method == "NA-QAE":
        estimate = fit_noise_aware_qae(schedule, config["confidence_level"])
    else:
        raise ValueError(method)
    rows.append(
        {
            "experiment": "ideal" if noise_lambda == 0 else "controlled_visibility_decay",
            "noise_lambda_true": noise_lambda,
            "method": method,
            "true_probability": probability,
            "query_budget": budget,
            "replicate": replicate,
            "seed": seed,
            "estimate": estimate.amplitude,
            "lower": estimate.lower,
            "upper": estimate.upper,
            "oracle_queries": schedule.oracle_queries,
            "shots": schedule.total_shots,
            "converged": estimate.converged,
            "noise_lambda_estimate": estimate.noise_lambda,
            "grover_depths": ",".join(map(str, schedule.grover_depths)),
            "successes": ",".join(map(str, schedule.successes)),
            "shots_by_depth": ",".join(map(str, schedule.shots)),
            "interval_method": estimate.interval_method,
        }
    )


def append_iqae_row(
    rows: list[dict],
    config: dict,
    probability: float,
    budget: int,
    replicate: int,
    noise_lambda: float,
) -> None:
    seed = seed_for(
        config["master_seed"],
        2,
        int(round(-np.log10(probability))),
        budget,
        replicate,
        int(round(noise_lambda * 1e6)),
    )
    estimate = simulate_iterative_qae(
        probability,
        budget,
        np.random.default_rng(seed),
        confidence_level=config["confidence_level"],
        shots_per_round=100,
        noise_lambda=noise_lambda,
    )
    rows.append(
        {
            "experiment": "ideal" if noise_lambda == 0 else "controlled_visibility_decay",
            "noise_lambda_true": noise_lambda,
            "method": "IQAE",
            "true_probability": probability,
            "query_budget": budget,
            "replicate": replicate,
            "seed": seed,
            "estimate": estimate.amplitude,
            "lower": estimate.lower,
            "upper": estimate.upper,
            "oracle_queries": estimate.oracle_queries,
            "shots": sum(estimate.shots),
            "converged": estimate.converged,
            "noise_lambda_estimate": np.nan,
            "grover_depths": ",".join(map(str, estimate.grover_depths)),
            "successes": ",".join(map(str, estimate.successes)),
            "shots_by_depth": ",".join(map(str, estimate.shots)),
            "interval_method": "iterative Clopper-Pearson (noise-naive under decay)",
        }
    )


def run_cell(config: dict, probability: float, budget: int, replicate: int) -> list[dict]:
    rows: list[dict] = []
    append_classical_rows(rows, config, probability, budget, replicate)
    append_qae_row(rows, config, probability, budget, replicate, 0.0, "MLAE")
    append_iqae_row(rows, config, probability, budget, replicate, 0.0)
    for noise_lambda in config["noise_lambdas"]:
        if noise_lambda > 0:
            append_qae_row(rows, config, probability, budget, replicate, noise_lambda, "MLAE")
            append_qae_row(rows, config, probability, budget, replicate, noise_lambda, "NA-QAE")
            append_iqae_row(rows, config, probability, budget, replicate, noise_lambda)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "configs" / "analytic.yaml")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "results" / "raw" / "analytic")
    parser.add_argument("--smoke", action="store_true", help="Run a two-probability, two-budget, two-replicate validation")
    parser.add_argument("--jobs", type=int, default=-1)
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    if args.smoke:
        config["failure_probabilities"] = [0.1, 0.001]
        config["query_budgets"] = [256, 1024]
        config["replicates"] = 2
        config["noise_lambdas"] = [0.0, 0.01]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    cells = [
        (probability, budget, replicate)
        for probability in config["failure_probabilities"]
        for budget in config["query_budgets"]
        for replicate in range(config["replicates"])
    ]
    nested_rows = Parallel(n_jobs=args.jobs, prefer="processes", verbose=5)(
        delayed(run_cell)(config, probability, budget, replicate)
        for probability, budget, replicate in cells
    )
    rows = [row for cell_rows in nested_rows for row in cell_rows]

    raw = pd.DataFrame(rows)
    summary = summarize_estimates(raw)
    suffix = "smoke" if args.smoke else "full"
    raw_path = args.output_dir / f"analytic_raw_{suffix}.parquet"
    summary_path = args.output_dir / f"analytic_summary_{suffix}.csv"
    raw.to_parquet(raw_path, index=False)
    summary.to_csv(summary_path, index=False)
    metadata = {
        "study_id": config["study_id"],
        "config": config,
        "elapsed_seconds": time.perf_counter() - started,
        "python": sys.version,
        "platform": platform.platform(),
        "logical_cores": os.cpu_count(),
        "versions": {
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scipy": scipy.__version__,
            "qiskit": qiskit.__version__,
        },
        "raw_file": raw_path.name,
        "summary_file": summary_path.name,
        "row_count": len(raw),
    }
    (args.output_dir / f"analytic_metadata_{suffix}.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(metadata, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
