"""Construct and audit an OC4-specific functional sparse-polynomial oracle.

For a frozen design, independent uniform latent variables are mapped through
the fitted R-vine inverse Rosenblatt transform and fitted marginals.  A
quadratic Boolean response approximation is identified from O(n^2) functional
evaluations, then exhaustively validated on the discrete state space.  The
circuit stores polynomial coefficients, never a table of failure labels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
from functools import lru_cache
from itertools import combinations
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
import pyvinecopulib as pv
import qiskit
from qiskit import QuantumCircuit, transpile
from qiskit.circuit.library import StatePreparation
from scipy import stats
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "experiments"))

from qae_abc.quantum.functional_oracle import (
    bit_rows,
    build_functional_amplitude_circuit,
    build_functional_oracle,
    decoded_latent_variables,
    evaluate_quantized,
    SparsePseudoBoolean,
)
from qae_abc.reliability.oc4 import (
    DESIGN_COLUMNS,
    SCREENING_TARGETS,
    predict_response_cartesian,
    system_failure_probability,
)


METOCEAN_PATH = PROJECT_ROOT / "data" / "processed" / "metocean" / "era5_open_meteo_54N_6.5E_2000_2024.parquet"
MARGINAL_REPORT = PROJECT_ROOT / "results" / "processed" / "metocean" / "metocean_analysis_report.json"
VINE_PATH = PROJECT_ROOT / "results" / "processed" / "metocean" / "selected_vine_model.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@lru_cache(maxsize=1)
def fit_direction_values() -> np.ndarray:
    metocean = pd.read_parquet(METOCEAN_PATH, columns=["wind_wave_angle", "complete_core", "split"])
    return np.sort(
        metocean[(metocean["complete_core"]) & (metocean["split"] == "fit")][
            "wind_wave_angle"
        ].dropna().to_numpy(float)
    )


def tail_adaptive_latent_scheme(bits_per_variable: int, exponent: float = 4.0) -> tuple[np.ndarray, np.ndarray]:
    bins = 2**bits_per_variable
    uniform = np.linspace(0.0, 1.0, bins + 1)
    edges = 1.0 - (1.0 - uniform) ** exponent
    levels = 0.5 * (edges[:-1] + edges[1:])
    probabilities = np.diff(edges)
    return levels, probabilities


def inverse_environment(bits: np.ndarray, bits_per_variable: int) -> pd.DataFrame:
    bits = np.atleast_2d(np.asarray(bits, dtype=int))
    uniform_levels, _ = tail_adaptive_latent_scheme(bits_per_variable)
    independent_uniform = decoded_latent_variables(bits, bits_per_variable, uniform_levels)
    vine = pv.Vinecop.from_json(VINE_PATH.read_text(encoding="utf-8"))
    dependent_uniform = vine.inverse_rosenblatt(np.asfortranarray(independent_uniform[:, :3]))
    report = json.loads(MARGINAL_REPORT.read_text(encoding="utf-8"))
    distributions = {
        "weibull": stats.weibull_min,
        "lognormal": stats.lognorm,
        "gamma": stats.gamma,
        "normal": stats.norm,
    }
    values = {}
    for index, name in enumerate(["U100", "Hs", "Tp"]):
        marginal = report["selected_marginals"][name]
        parameters = json.loads(marginal["parameters"])
        values[name] = distributions[marginal["distribution"]].ppf(
            np.clip(dependent_uniform[:, index], 1e-9, 1 - 1e-9), *parameters
        )
    direction = fit_direction_values()
    direction_index = np.minimum(
        (independent_uniform[:, 3] * len(direction)).astype(int), len(direction) - 1
    )
    values["wind_wave_angle"] = direction[direction_index]
    return pd.DataFrame(values)


def safe_basis_transpile(circuit: QuantumCircuit) -> tuple[QuantumCircuit, str]:
    try:
        compiled = transpile(
            circuit,
            basis_gates=["u", "cx"],
            optimization_level=0,
            seed_transpiler=20260903,
        )
        return compiled, "success_basis_only_optimization_level_0"
    except Exception as exc:
        return circuit, f"failed: {type(exc).__name__}: {exc}"


def fit_sparse_polynomial(
    features: np.ndarray,
    labels: np.ndarray,
    state_probability: np.ndarray,
    train_indices: np.ndarray,
    num_bits: int,
    feature_terms: list[tuple[int, ...]],
) -> SparsePseudoBoolean:
    classifier = LogisticRegression(
        l1_ratio=1.0,
        solver="saga",
        C=0.25,
        class_weight={0: 1.0, 1: 8.0},
        max_iter=10000,
        random_state=20260903,
    ).fit(
        features[train_indices],
        labels[train_indices].astype(int),
        sample_weight=np.maximum(state_probability[train_indices] * len(features), 1e-8),
    )
    raw_training_score = classifier.decision_function(features[train_indices])
    training_weights = state_probability[train_indices]
    training_labels = labels[train_indices]
    true_training_probability = float(
        np.sum(training_weights[training_labels]) / np.sum(training_weights)
    )
    failure_weight = max(float(np.sum(training_weights[training_labels])), 1e-15)
    threshold_diagnostics = []
    for threshold in np.unique(raw_training_score):
        predicted = raw_training_score >= threshold
        false_safe_rate = float(
            np.sum(training_weights[(~predicted) & training_labels]) / failure_weight
        )
        predicted_probability = float(np.sum(training_weights[predicted]) / np.sum(training_weights))
        if false_safe_rate <= 0.05:
            threshold_diagnostics.append(
                (abs(predicted_probability - true_training_probability), false_safe_rate, threshold)
            )
    conservative_threshold = float(
        min(threshold_diagnostics)[2]
        if threshold_diagnostics
        else np.min(raw_training_score[training_labels])
    )
    coefficients = np.asarray(classifier.coef_[0], dtype=float)
    sparse_coefficients = tuple(
        (term, float(coefficient))
        for term, coefficient in zip(feature_terms, coefficients, strict=True)
        if abs(coefficient) > 1e-10
    )
    return SparsePseudoBoolean(
        float(classifier.intercept_[0] - conservative_threshold),
        sparse_coefficients,
        num_bits,
    )


def build_row(
    bits_per_variable: int,
    design: pd.DataFrame,
    response_bundle: dict,
    capacities: dict[str, float],
    continuous_reference: float,
    scale: int,
) -> dict:
    num_bits = 4 * bits_per_variable
    rows = bit_rows(num_bits)
    _, marginal_probability = tail_adaptive_latent_scheme(bits_per_variable)
    state_probability = marginal_probability
    for _ in range(3):
        state_probability = np.kron(marginal_probability, state_probability)
    environment = inverse_environment(rows, bits_per_variable)
    response = predict_response_cartesian(
        response_bundle, design, environment, SCREENING_TARGETS, design_chunk_size=1
    )
    _, ratio = system_failure_probability(response, capacities)
    exact_scores = ratio[0] - 1.0
    exact_failure = exact_scores >= 0
    feature_terms = [
        term
        for order in (1, 2, 3)
        for term in combinations(range(num_bits), order)
    ]
    classifier_features = np.column_stack(
        [np.prod(rows[:, list(term)], axis=1) for term in feature_terms]
    )
    train_indices, holdout_indices = train_test_split(
        np.arange(len(rows)),
        test_size=0.5,
        random_state=20260903 + bits_per_variable,
        stratify=exact_failure,
    )
    polynomials = [
        fit_sparse_polynomial(
            classifier_features,
            exact_failure,
            state_probability,
            train_indices,
            num_bits,
            feature_terms,
        )
    ]
    polynomial_failure = np.any(
        np.column_stack([polynomial.evaluate(rows) >= 0 for polynomial in polynomials]), axis=1
    )
    oracle = build_functional_oracle(polynomials, scale=scale, name=f"oc4_b{bits_per_variable}")
    quantized_component_failure = []
    nonzero_terms = 0
    for constant, terms in oracle.quantized_polynomials:
        quantized_component_failure.append(evaluate_quantized(rows, constant, terms) >= 0)
        nonzero_terms += len(terms)
    quantized_failure = np.any(np.column_stack(quantized_component_failure), axis=1)
    preparation = QuantumCircuit(num_bits, name="A_tail_adaptive_latent")
    state_preparation = StatePreparation(np.sqrt(marginal_probability), normalize=True)
    for variable in range(4):
        start = variable * bits_per_variable
        preparation.append(state_preparation, list(range(start, start + bits_per_variable)))
    amplitude = build_functional_amplitude_circuit(oracle, preparation, 1, measure=True)
    compiled, status = safe_basis_transpile(amplitude.circuit)
    operations = compiled.count_ops()
    discrete_probability = float(np.sum(state_probability[exact_failure]))
    polynomial_probability = float(np.sum(state_probability[polynomial_failure]))
    quantized_probability = float(np.sum(state_probability[quantized_failure]))
    false_safe = (~quantized_failure) & exact_failure
    return {
        "case": "OC4_system_operational_screening",
        "random_dimensions": 4,
        "bits_per_variable": bits_per_variable,
        "state_qubits": num_bits,
        "basis_states": len(rows),
        "smallest_marginal_bin_probability": float(marginal_probability.min()),
        "largest_latent_midpoint": float(tail_adaptive_latent_scheme(bits_per_variable)[0].max()),
        "functional_training_evaluations": len(train_indices),
        "independent_holdout_evaluations": len(holdout_indices),
        "exhaustive_qa_evaluations": len(rows),
        "continuous_temporal_reference_probability": continuous_reference,
        "functional_component_constraints": "system_OR_all_five_screening_responses",
        "polynomial_order": 3,
        "omitted_component_failure_probability": 0.0,
        "exact_discrete_probability": discrete_probability,
        "polynomial_probability": polynomial_probability,
        "quantized_probability": quantized_probability,
        "relative_discretization_error": abs(discrete_probability - continuous_reference) / max(continuous_reference, 1e-15),
        "polynomial_misclassification_rate": float(np.mean(polynomial_failure != exact_failure)),
        "quantized_misclassification_rate": float(np.mean(quantized_failure != exact_failure)),
        "quantized_probability_weighted_absolute_error": abs(quantized_probability - discrete_probability),
        "quantized_weighted_misclassification_probability": float(
            np.sum(state_probability[quantized_failure != exact_failure])
        ),
        "holdout_quantized_misclassification_rate": float(
            np.mean(quantized_failure[holdout_indices] != exact_failure[holdout_indices])
        ),
        "quantized_false_safe_probability": float(np.sum(state_probability[false_safe])),
        "quantized_false_safe_rate_failures": float(
            np.sum(state_probability[false_safe]) / max(discrete_probability, 1e-15)
        ),
        "holdout_quantized_false_safe_rate_failures": float(
            np.sum(state_probability[holdout_indices][false_safe[holdout_indices]])
            / max(np.sum(state_probability[holdout_indices][exact_failure[holdout_indices]]), 1e-15)
        ),
        "quantization_scale": scale,
        "nonzero_quantized_terms": nonzero_terms,
        "total_qubits": oracle.circuit.num_qubits,
        "work_qubits": oracle.circuit.num_qubits - num_bits - 1,
        "logical_oracle_depth": oracle.circuit.depth(),
        "logical_oracle_size": oracle.circuit.size(),
        "aq1_transpiled_depth": compiled.depth(),
        "aq1_transpiled_size": compiled.size(),
        "aq1_cx_gates": int(operations.get("cx", 0)),
        "transpile_status": status,
        "functional_not_lookup": True,
    }


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
    parser.add_argument("--candidate-method", type=str, default="na_qae_abc")
    parser.add_argument("--scale", type=int, default=64)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "results" / "processed" / "oc4_oracle")
    args = parser.parse_args()
    args.candidates = args.candidates.resolve()
    args.response_bundle = args.response_bundle.resolve()
    args.meta_bundle = args.meta_bundle.resolve()
    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    candidates = pd.read_csv(args.candidates)
    selected = candidates[candidates["method"] == args.candidate_method]
    if len(selected) != 1:
        raise RuntimeError(f"Expected exactly one {args.candidate_method} candidate")
    design = selected[DESIGN_COLUMNS].reset_index(drop=True)
    continuous_reference = float(selected["temporal_test_probability"].iloc[0])
    response_bundle = joblib.load(args.response_bundle)
    meta_bundle = joblib.load(args.meta_bundle)
    rows = [
        build_row(
            bits,
            design,
            response_bundle,
            meta_bundle["capacities"],
            continuous_reference,
            args.scale,
        )
        for bits in (2, 3)
    ]
    result = pd.DataFrame(rows)
    output_path = args.output_dir / "oc4_functional_oracle_resources.csv"
    result.to_csv(output_path, index=False)
    report = {
        "scope": "OC4-specific functional sparse-cubic oracle construction, exhaustive discrete validation, and actual circuit transpilation.",
        "candidate_method": args.candidate_method,
        "candidate_run": int(selected["run"].iloc[0]),
        "environment_transform": "four independent tail-adaptive nonuniform latents; first three -> fitted R-vine inverse Rosenblatt -> fitted marginals; fourth -> empirical fit-period wind-wave angle quantile",
        "state_preparation": "tensor product of exact nonuniform marginal amplitudes; bin edges 1-(1-s)^4 to retain rare upper tails",
        "functional_construction": "sparse cubic Boolean classifier fitted on 50% of discrete response states and audited on a frozen 50% holdout; no lookup table stored in the circuit",
        "continuous_reference_role": "untouched temporal-test response-surrogate probability",
        "limitations": "four environmental dimensions with discretized empirical direction; operational non-certification limits",
        "elapsed_seconds": time.perf_counter() - started,
        "platform": platform.platform(),
        "qiskit_version": qiskit.__version__,
        "files": {str(output_path.relative_to(PROJECT_ROOT)): sha256(output_path)},
    }
    report_path = args.output_dir / "oc4_functional_oracle_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(result.to_string(index=False))
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
