"""Run QAE estimators against an archived IBM calibration snapshot.

The fake backend is a calibrated local noise model.  It is never labeled as a
live IBM device, and its calibration timestamp is written beside every result.
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

import numpy as np
import pandas as pd
import qiskit
import qiskit_ibm_runtime
from qiskit import QuantumCircuit, transpile
from qiskit_aer import AerSimulator
from qiskit_ibm_runtime.fake_provider import FakeNairobiV2
from scipy.optimize import minimize_scalar

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from qae_abc.evaluation.metrics import summarize_estimates
from qae_abc.quantum.likelihood import (
    CountSchedule,
    allocate_schedule,
    estimate_iterative_qae_from_sampler,
    fit_mlae,
    fit_noise_aware_qae,
    fit_visibility_calibrated_qae,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def amplitude_circuit(amplitude: float, grover_depth: int) -> QuantumCircuit:
    theta = float(np.arcsin(np.sqrt(amplitude)))
    circuit = QuantumCircuit(1, 1)
    circuit.ry(2 * theta, 0)
    for _ in range(grover_depth):
        circuit.ry(4 * theta, 0)
    circuit.measure(0, 0)
    return circuit


def best_qubit(backend) -> int:
    properties = backend.properties()
    return min(
        range(backend.num_qubits),
        key=lambda qubit: properties.readout_error(qubit) + properties.gate_error("sx", qubit),
    )


def fit_calibration_lambda(schedule: CountSchedule, known_amplitude: float) -> float:
    theta = float(np.arcsin(np.sqrt(known_amplitude)))
    ideal = np.sin((2 * schedule.grover_depths + 1) * theta) ** 2

    def objective(value: float) -> float:
        probability = np.clip(
            0.5 + np.exp(-value * schedule.effective_depths) * (ideal - 0.5),
            1e-12,
            1 - 1e-12,
        )
        return float(
            -np.sum(
                schedule.successes * np.log(probability)
                + (schedule.shots - schedule.successes) * np.log1p(-probability)
            )
        )

    return float(minimize_scalar(objective, bounds=(0.0, 0.5), method="bounded").x)


def calibration_tables(backend, output_dir: Path) -> tuple[Path, Path, Path]:
    properties = backend.properties()
    qubit_rows = []
    for qubit in range(backend.num_qubits):
        qubit_rows.append(
            {
                "qubit": qubit,
                "t1_s": properties.t1(qubit),
                "t2_s": properties.t2(qubit),
                "frequency_hz": properties.frequency(qubit),
                "readout_error": properties.readout_error(qubit),
                "sx_error": properties.gate_error("sx", qubit),
                "sx_length_s": properties.gate_length("sx", qubit),
            }
        )
    gate_rows = []
    for gate in properties.gates:
        if gate.gate == "cx":
            gate_rows.append(
                {
                    "gate": gate.gate,
                    "qubits": ",".join(map(str, gate.qubits)),
                    "gate_error": properties.gate_error(gate.gate, gate.qubits),
                    "gate_length_s": properties.gate_length(gate.gate, gate.qubits),
                }
            )
    qubit_path = output_dir / "fake_nairobi_qubit_calibration.csv"
    gate_path = output_dir / "fake_nairobi_cx_calibration.csv"
    snapshot_path = output_dir / "fake_nairobi_properties_snapshot.json"
    pd.DataFrame(qubit_rows).to_csv(qubit_path, index=False)
    pd.DataFrame(gate_rows).to_csv(gate_path, index=False)
    snapshot_path.write_text(json.dumps(properties.to_dict(), indent=2, default=str), encoding="utf-8")
    return qubit_path, gate_path, snapshot_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "results" / "raw" / "fake_backend")
    parser.add_argument("--replicates", type=int, default=100)
    parser.add_argument("--iqae-replicates", type=int, default=30)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    amplitudes = [0.001, 0.01]
    budgets = [1024, 4096]
    candidate_depths = [0, 1, 2, 4, 8, 16]
    replicates = 2 if args.smoke else args.replicates
    iqae_replicates = 1 if args.smoke else args.iqae_replicates
    if args.smoke:
        amplitudes = [0.01]
        budgets = [1024]
        candidate_depths = [0, 1, 2]

    fake_backend = FakeNairobiV2()
    simulator = AerSimulator.from_backend(fake_backend)
    physical_qubit = best_qubit(fake_backend)
    qubit_path, gate_path, snapshot_path = calibration_tables(fake_backend, args.output_dir)
    calibration_timestamp = str(fake_backend.properties().last_update_date)
    rows = []
    resource_rows = []
    started = time.perf_counter()

    for budget in budgets:
        depths, shots = allocate_schedule(budget, candidate_depths)
        calibration_templates = [
            transpile(
                amplitude_circuit(0.2, int(depth)),
                fake_backend,
                optimization_level=1,
                seed_transpiler=20260903,
                initial_layout=[physical_qubit],
            )
            for depth in depths
        ]
        calibration_shots = np.full(len(depths), max(5000, int(max(shots))), dtype=int)
        calibration_successes = []
        for index, (template, shot_count) in enumerate(zip(calibration_templates, calibration_shots, strict=True)):
            result = simulator.run(template, shots=int(shot_count), seed_simulator=82000 + index).result()
            calibration_successes.append(result.get_counts().get("1", 0))
        calibration_schedule = CountSchedule(
            depths,
            calibration_shots,
            (2 * depths + 1).astype(float),
            np.asarray(calibration_successes, dtype=int),
        )
        calibrated_lambda = fit_calibration_lambda(calibration_schedule, known_amplitude=0.2)

        for amplitude in amplitudes:
            templates = [
                transpile(
                    amplitude_circuit(amplitude, int(depth)),
                    fake_backend,
                    optimization_level=1,
                    seed_transpiler=20260903,
                    initial_layout=[physical_qubit],
                )
                for depth in depths
            ]
            for depth, template in zip(depths, templates, strict=True):
                operations = template.count_ops()
                resource_rows.append(
                    {
                        "backend": fake_backend.name,
                        "calibration_timestamp": calibration_timestamp,
                        "physical_qubit": physical_qubit,
                        "amplitude": amplitude,
                        "query_budget": budget,
                        "grover_depth": int(depth),
                        "transpiled_qubits": template.num_qubits,
                        "transpiled_depth": template.depth(),
                        "transpiled_size": template.size(),
                        "cx_gates": int(operations.get("cx", 0)),
                        "sx_gates": int(operations.get("sx", 0)),
                    }
                )
            fixed_successes = np.empty((replicates, len(depths)), dtype=int)
            for depth_index, (template, shot_count) in enumerate(zip(templates, shots, strict=True)):
                result = simulator.run(
                    [template] * replicates,
                    shots=int(shot_count),
                    seed_simulator=20260903 + 1000 * depth_index + budget,
                ).result()
                fixed_successes[:, depth_index] = [
                    result.get_counts(index).get("1", 0) for index in range(replicates)
                ]
            for replicate in range(replicates):
                schedule = CountSchedule(
                    depths,
                    shots,
                    (2 * depths + 1).astype(float),
                    fixed_successes[replicate],
                )
                fitted = {
                    "MLAE": fit_mlae(schedule),
                    "NA-QAE": fit_noise_aware_qae(schedule),
                    "CAL-QAE": fit_visibility_calibrated_qae(schedule, calibrated_lambda),
                }
                for method, estimate in fitted.items():
                    rows.append(
                        {
                            "model": "one_qubit_amplitude",
                            "experiment": "archived_ibm_calibration_aer",
                            "backend": fake_backend.name,
                            "calibration_timestamp": calibration_timestamp,
                            "physical_qubit": physical_qubit,
                            "noise_lambda_true": np.nan,
                            "calibration_lambda": calibrated_lambda,
                            "method": method,
                            "true_probability": amplitude,
                            "query_budget": budget,
                            "replicate": replicate,
                            "seed": 20260903 + budget,
                            "estimate": estimate.amplitude,
                            "lower": estimate.lower,
                            "upper": estimate.upper,
                            "oracle_queries": schedule.oracle_queries,
                            "shots": schedule.total_shots,
                            "converged": estimate.converged,
                            "noise_lambda_estimate": estimate.noise_lambda,
                            "grover_depths": ",".join(map(str, depths)),
                            "shots_by_depth": ",".join(map(str, shots)),
                            "successes": ",".join(map(str, fixed_successes[replicate])),
                            "interval_method": estimate.interval_method,
                        }
                    )

            iqae_template_cache = {}
            for replicate in range(iqae_replicates):
                def sampler(k: int, shot_count: int, round_index: int) -> int:
                    if k not in iqae_template_cache:
                        iqae_template_cache[k] = transpile(
                            amplitude_circuit(amplitude, k),
                            fake_backend,
                            optimization_level=1,
                            seed_transpiler=20260903,
                            initial_layout=[physical_qubit],
                        )
                    result = simulator.run(
                        iqae_template_cache[k],
                        shots=shot_count,
                        seed_simulator=930000 + budget + replicate * 101 + round_index,
                    ).result()
                    return int(result.get_counts().get("1", 0))

                estimate = estimate_iterative_qae_from_sampler(
                    query_budget=budget,
                    sample_successes=sampler,
                    shots_per_round=100,
                )
                rows.append(
                    {
                        "model": "one_qubit_amplitude",
                        "experiment": "archived_ibm_calibration_aer",
                        "backend": fake_backend.name,
                        "calibration_timestamp": calibration_timestamp,
                        "physical_qubit": physical_qubit,
                        "noise_lambda_true": np.nan,
                        "calibration_lambda": calibrated_lambda,
                        "method": "IQAE",
                        "true_probability": amplitude,
                        "query_budget": budget,
                        "replicate": replicate,
                        "seed": 930000 + budget + replicate * 101,
                        "estimate": estimate.amplitude,
                        "lower": estimate.lower,
                        "upper": estimate.upper,
                        "oracle_queries": estimate.oracle_queries,
                        "shots": sum(estimate.shots),
                        "converged": estimate.converged,
                        "noise_lambda_estimate": np.nan,
                        "grover_depths": ",".join(map(str, estimate.grover_depths)),
                        "shots_by_depth": ",".join(map(str, estimate.shots)),
                        "successes": ",".join(map(str, estimate.successes)),
                        "interval_method": "iterative Clopper-Pearson; noise-naive",
                    }
                )

    raw = pd.DataFrame(rows)
    summary = summarize_estimates(raw)
    suffix = "smoke" if args.smoke else "full"
    raw_path = args.output_dir / f"fake_backend_raw_{suffix}.parquet"
    summary_path = args.output_dir / f"fake_backend_summary_{suffix}.csv"
    resource_path = args.output_dir / f"fake_backend_resources_{suffix}.csv"
    raw.to_parquet(raw_path, index=False)
    summary.to_csv(summary_path, index=False)
    pd.DataFrame(resource_rows).to_csv(resource_path, index=False)
    report = {
        "scope": "AerSimulator.from_backend using archived IBM FakeNairobiV2 calibration; not live hardware.",
        "backend": fake_backend.name,
        "backend_qubits": fake_backend.num_qubits,
        "selected_physical_qubit": physical_qubit,
        "calibration_timestamp": calibration_timestamp,
        "replicates_fixed_schedule": replicates,
        "replicates_iqae": iqae_replicates,
        "elapsed_seconds": time.perf_counter() - started,
        "qiskit_version": qiskit.__version__,
        "qiskit_ibm_runtime_version": qiskit_ibm_runtime.__version__,
        "platform": platform.platform(),
        "files": {
            str(path.relative_to(PROJECT_ROOT)): sha256(path)
            for path in [raw_path, summary_path, resource_path, qubit_path, gate_path, snapshot_path]
        },
    }
    (args.output_dir / f"fake_backend_report_{suffix}.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
