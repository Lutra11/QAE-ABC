"""Execute complete state-preparation + functional-oracle QAE circuits on Aer."""

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
import qiskit
from qiskit import qpy, transpile
from qiskit_aer import AerSimulator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qae_abc.evaluation.metrics import summarize_estimates
from qae_abc.quantum.functional_oracle import (
    bit_rows,
    build_functional_amplitude_circuit,
    build_functional_oracle,
    build_product_state_preparation,
    decoded_latent_variables,
    evaluate_quantized,
    quadratic_from_function,
    quantize_polynomial,
    state_preparation_probabilities,
)
from qae_abc.quantum.likelihood import CountSchedule, allocate_schedule, fit_mlae
from qae_abc.structural.truss import TenBarTruss
from qae_abc.surrogate.truss_surrogates import truss_feature_matrix


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_truss_problem(model_path: Path, design_area: float, bits_per_variable: int, scale: int):
    surrogate = joblib.load(model_path)
    truss = TenBarTruss()
    design = np.full(4, design_area)
    rows = bit_rows(3 * bits_per_variable)
    levels, probabilities = state_preparation_probabilities(bits_per_variable, 3)

    def component_score(component: int):
        def score(bits: np.ndarray) -> float:
            latent = decoded_latent_variables(
                np.asarray(bits, dtype=int)[None, :], bits_per_variable, levels
            )
            prediction = surrogate.predict_components(
                truss_feature_matrix(truss, design, latent)
            )[component]
            return -float(prediction[0])

        return score

    polynomials = [
        quadratic_from_function(rows.shape[1], component_score(0)),
        quadratic_from_function(rows.shape[1], component_score(1)),
    ]
    quantized_scores = np.column_stack(
        [evaluate_quantized(rows, *quantize_polynomial(polynomial, scale)) for polynomial in polynomials]
    )
    failures = np.any(quantized_scores >= 0, axis=1)
    probability = float(probabilities[failures].sum())
    oracle = build_functional_oracle(polynomials, scale)
    preparation, preparation_probabilities = build_product_state_preparation(bits_per_variable, 3)
    if not np.allclose(probabilities, preparation_probabilities, atol=1e-14):
        raise RuntimeError("State-preparation probability ordering mismatch")
    return oracle, preparation, probability


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, default=PROJECT_ROOT / "results" / "raw" / "truss_surrogate" / "sparse_pce.joblib")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "results" / "raw" / "functional_qae")
    parser.add_argument("--design-area", type=float, default=0.020)
    parser.add_argument("--bits-per-variable", type=int, default=2)
    parser.add_argument("--scale", type=int, default=256)
    parser.add_argument("--budget", type=int, default=8192)
    parser.add_argument("--replicates", type=int, default=100)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    candidate_depths = [0, 1] if args.smoke else [0, 1, 2, 4]
    budget = 512 if args.smoke else args.budget
    replicates = 2 if args.smoke else args.replicates
    depths, shots = allocate_schedule(budget, candidate_depths, minimum_shots_per_depth=20)
    oracle, preparation, true_probability = build_truss_problem(
        args.model, args.design_area, args.bits_per_variable, args.scale
    )
    ideal_probabilities = np.sin(
        (2 * depths + 1) * np.arcsin(np.sqrt(true_probability))
    ) ** 2
    logical = [
        build_functional_amplitude_circuit(oracle, preparation, int(depth), measure=True)
        for depth in depths
    ]
    qpy_path = args.output_dir / ("functional_qae_circuits_smoke.qpy" if args.smoke else "functional_qae_circuits.qpy")
    with qpy_path.open("wb") as stream:
        qpy.dump([item.circuit for item in logical], stream)

    backend = AerSimulator(method="matrix_product_state")
    resource_rows = []
    execution_circuits = None
    transpile_started = time.perf_counter()
    for optimization_level in (0, 1, 2, 3):
        transpiled = transpile(
            [item.circuit for item in logical],
            backend,
            optimization_level=optimization_level,
            seed_transpiler=20260903,
        )
        if optimization_level == 1:
            execution_circuits = transpiled
        for depth, item, circuit in zip(depths, logical, transpiled, strict=True):
            operations = circuit.count_ops()
            resource_rows.append(
                {
                    "grover_depth": int(depth),
                    "oracle_queries_per_shot": item.oracle_queries,
                    "optimization_level": optimization_level,
                    "logical_qubits": item.circuit.num_qubits,
                    "logical_depth": item.circuit.depth(),
                    "logical_size": item.circuit.size(),
                    "transpiled_qubits": circuit.num_qubits,
                    "transpiled_depth": circuit.depth(),
                    "transpiled_size": circuit.size(),
                    "cx_gates": int(operations.get("cx", 0)),
                    "two_qubit_gates": int(
                        sum(1 for instruction in circuit.data if len(instruction.qubits) == 2)
                    ),
                }
            )
    assert execution_circuits is not None
    transpile_seconds = time.perf_counter() - transpile_started
    execution_started = time.perf_counter()
    successes = np.empty((replicates, len(depths)), dtype=int)
    for depth_index, (circuit, shot_count) in enumerate(zip(execution_circuits, shots, strict=True)):
        result = backend.run(
            [circuit] * replicates,
            shots=int(shot_count),
            seed_simulator=20260903 + 1009 * depth_index,
        ).result()
        successes[:, depth_index] = [
            result.get_counts(index).get("1", 0) for index in range(replicates)
        ]
    execution_seconds = time.perf_counter() - execution_started

    rows = []
    for replicate in range(replicates):
        schedule = CountSchedule(
            grover_depths=depths,
            shots=shots,
            effective_depths=np.array(
                [execution_circuits[index].depth() for index in range(len(depths))], dtype=float
            ),
            successes=successes[replicate],
        )
        estimate = fit_mlae(schedule)
        rows.append(
            {
                "model": "10_bar_truss_functional_oracle",
                "experiment": "ideal_aer_full_functional_circuit",
                "noise_lambda_true": 0.0,
                "method": "MLAE-functional",
                "true_probability": true_probability,
                "query_budget": budget,
                "replicate": replicate,
                "seed": 20260903,
                "estimate": estimate.amplitude,
                "lower": estimate.lower,
                "upper": estimate.upper,
                "oracle_queries": schedule.oracle_queries,
                "shots": schedule.total_shots,
                "converged": estimate.converged,
                "grover_depths": ",".join(map(str, depths)),
                "shots_by_depth": ",".join(map(str, shots)),
                "successes": ",".join(map(str, successes[replicate])),
                "interval_method": estimate.interval_method,
            }
        )
    suffix = "smoke" if args.smoke else "full"
    raw = pd.DataFrame(rows)
    summary = summarize_estimates(raw)
    raw_path = args.output_dir / f"functional_qae_raw_{suffix}.parquet"
    summary_path = args.output_dir / f"functional_qae_summary_{suffix}.csv"
    resource_path = args.output_dir / f"functional_qae_resources_{suffix}.csv"
    raw.to_parquet(raw_path, index=False)
    summary.to_csv(summary_path, index=False)
    pd.DataFrame(resource_rows).to_csv(resource_path, index=False)
    observed_probabilities = successes.mean(axis=0) / shots
    report = {
        "scope": "Complete functional state-preparation/oracle/Grover circuits executed on ideal Aer MPS; not hardware.",
        "bits_per_variable": args.bits_per_variable,
        "state_qubits": 3 * args.bits_per_variable,
        "total_logical_qubits": oracle.circuit.num_qubits,
        "fixed_point_scale": args.scale,
        "true_discrete_probability": true_probability,
        "depths": depths.tolist(),
        "shots_by_depth": shots.tolist(),
        "oracle_query_budget_actual": int(np.sum(shots * (2 * depths + 1))),
        "ideal_success_probabilities": ideal_probabilities.tolist(),
        "observed_success_probabilities": observed_probabilities.tolist(),
        "maximum_success_probability_absolute_error": float(np.max(np.abs(observed_probabilities - ideal_probabilities))),
        "replicates": replicates,
        "transpile_seconds": transpile_seconds,
        "execution_seconds": execution_seconds,
        "qiskit_version": qiskit.__version__,
        "platform": platform.platform(),
        "circuit_qpy": str(qpy_path.relative_to(PROJECT_ROOT)),
        "circuit_qpy_sha256": sha256(qpy_path),
        "raw_sha256": sha256(raw_path),
        "resources_sha256": sha256(resource_path),
    }
    (args.output_dir / f"functional_qae_report_{suffix}.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
