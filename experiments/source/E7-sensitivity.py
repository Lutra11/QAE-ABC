"""Run confidence, noise-strength, and Grover-depth sensitivity experiments."""

from __future__ import annotations

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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run_cell(spec: dict, replicate: int) -> list[dict]:
    seed = int(
        np.random.SeedSequence(
            [20260903, spec["cell_id"], replicate]
        ).generate_state(1, dtype=np.uint32)[0]
    )
    probability = float(spec["true_probability"])
    budget = int(spec["query_budget"])
    confidence = float(spec["confidence_level"])
    noise = float(spec["noise_lambda_true"])
    maximum_depth = int(spec["maximum_grover_depth"])
    candidate_depths = [depth for depth in [0, 1, 2, 4, 8, 16, 32] if depth <= maximum_depth]
    depths, shots = allocate_schedule(budget, candidate_depths)
    schedule = simulate_counts(
        probability,
        depths,
        shots,
        np.random.default_rng(seed),
        noise_lambda=noise,
    )
    estimates = {
        "MLAE": fit_mlae(schedule, confidence),
        "NA-QAE": fit_noise_aware_qae(schedule, confidence),
    }
    rows = []
    common = {
        "scenario": spec["scenario"],
        "epsilon_proxy": 1 / np.sqrt(budget),
        "confidence_level": confidence,
        "noise_multiplier": spec["noise_multiplier"],
        "noise_lambda_true": noise,
        "maximum_grover_depth": maximum_depth,
        "true_probability": probability,
        "query_budget": budget,
        "replicate": replicate,
        "seed": seed,
    }
    for method, estimate in estimates.items():
        rows.append(
            common
            | {
                "experiment": "controlled_visibility_decay" if noise else "ideal",
                "method": method,
                "estimate": estimate.amplitude,
                "lower": estimate.lower,
                "upper": estimate.upper,
                "oracle_queries": schedule.oracle_queries,
                "shots": schedule.total_shots,
                "converged": estimate.converged,
                "noise_lambda_estimate": estimate.noise_lambda,
                "grover_depths": ",".join(map(str, schedule.grover_depths)),
                "shots_by_depth": ",".join(map(str, schedule.shots)),
                "successes": ",".join(map(str, schedule.successes)),
                "interval_method": estimate.interval_method,
            }
        )
    iqae = simulate_iterative_qae(
        probability,
        budget,
        np.random.default_rng(seed + 1),
        confidence_level=confidence,
        noise_lambda=noise,
    )
    rows.append(
        common
        | {
            "experiment": "controlled_visibility_decay" if noise else "ideal",
            "method": "IQAE",
            "estimate": iqae.amplitude,
            "lower": iqae.lower,
            "upper": iqae.upper,
            "oracle_queries": iqae.oracle_queries,
            "shots": sum(iqae.shots),
            "converged": iqae.converged,
            "noise_lambda_estimate": np.nan,
            "grover_depths": ",".join(map(str, iqae.grover_depths)),
            "shots_by_depth": ",".join(map(str, iqae.shots)),
            "successes": ",".join(map(str, iqae.successes)),
            "interval_method": "iterative Clopper-Pearson; noise-naive",
        }
    )
    return rows


def main() -> None:
    output_dir = PROJECT_ROOT / "results" / "raw" / "sensitivity"
    output_dir.mkdir(parents=True, exist_ok=True)
    specs = []
    cell_id = 0
    for confidence in (0.90, 0.95, 0.99):
        for budget in (2048, 8192, 32768):
            specs.append(
                {
                    "cell_id": cell_id,
                    "scenario": "confidence_and_precision",
                    "true_probability": 0.001,
                    "query_budget": budget,
                    "confidence_level": confidence,
                    "noise_multiplier": 1.0,
                    "noise_lambda_true": 0.005,
                    "maximum_grover_depth": 32,
                }
            )
            cell_id += 1
    for multiplier in (0.0, 0.5, 1.0, 2.0):
        for maximum_depth in (2, 4, 8, 16, 32):
            specs.append(
                {
                    "cell_id": cell_id,
                    "scenario": "noise_and_maximum_depth",
                    "true_probability": 0.001,
                    "query_budget": 8192,
                    "confidence_level": 0.95,
                    "noise_multiplier": multiplier,
                    "noise_lambda_true": 0.005 * multiplier,
                    "maximum_grover_depth": maximum_depth,
                }
            )
            cell_id += 1
    started = time.perf_counter()
    tasks = [(spec, replicate) for spec in specs for replicate in range(100)]
    nested = Parallel(n_jobs=6, prefer="processes", verbose=5)(
        delayed(run_cell)(spec, replicate) for spec, replicate in tasks
    )
    raw = pd.DataFrame([row for group in nested for row in group])
    summary_parts = []
    group_columns = [
        "scenario",
        "confidence_level",
        "noise_multiplier",
        "maximum_grover_depth",
    ]
    for keys, group in raw.groupby(group_columns, dropna=False):
        part = summarize_estimates(group)
        for index, column in enumerate(group_columns):
            if column not in part:
                part.insert(index, column, keys[index])
        summary_parts.append(part)
    summary = pd.concat(summary_parts, ignore_index=True)
    raw_path = output_dir / "qae_sensitivity_raw.parquet"
    summary_path = output_dir / "qae_sensitivity_summary.csv"
    raw.to_parquet(raw_path, index=False)
    summary.to_csv(summary_path, index=False)
    report = {
        "cells": len(specs),
        "replicates_per_cell": 100,
        "raw_rows": len(raw),
        "elapsed_seconds": time.perf_counter() - started,
        "scope": "Statistical amplitude-level sensitivity; not hardware or functional-circuit timing.",
        "platform": platform.platform(),
        "qiskit_version": qiskit.__version__,
        "files": {str(path.relative_to(PROJECT_ROOT)): sha256(path) for path in [raw_path, summary_path]},
    }
    (output_dir / "qae_sensitivity_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
