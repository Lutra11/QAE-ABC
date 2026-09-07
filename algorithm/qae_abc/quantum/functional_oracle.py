"""Gate-level functional threshold oracle for a quadratic fixed-design PCE.

The circuit evaluates a quantized pseudo-Boolean polynomial with reversible
ANDs, a weighted adder, and an integer comparator.  No table of failure labels
is stored in the circuit construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from math import ceil, log2
from typing import Callable, Sequence

import numpy as np
from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister
from qiskit.circuit.library import IntegerComparator, StatePreparation, WeightedAdder


@dataclass(frozen=True)
class QuadraticPseudoBoolean:
    constant: float
    linear: np.ndarray
    quadratic: np.ndarray

    def __post_init__(self) -> None:
        linear = np.asarray(self.linear, dtype=float)
        quadratic = np.asarray(self.quadratic, dtype=float)
        if linear.ndim != 1 or quadratic.shape != (linear.size, linear.size):
            raise ValueError("Incompatible linear and quadratic coefficient shapes")

    @property
    def num_bits(self) -> int:
        return int(np.asarray(self.linear).size)

    def evaluate(self, bits: np.ndarray) -> np.ndarray:
        bits = np.asarray(bits, dtype=float)
        return self.constant + bits @ self.linear + np.einsum("...i,ij,...j->...", bits, self.quadratic, bits)


@dataclass(frozen=True)
class SparsePseudoBoolean:
    """Sparse pseudo-Boolean polynomial with arbitrary interaction order."""

    constant: float
    coefficients: tuple[tuple[tuple[int, ...], float], ...]
    num_bits: int

    def __post_init__(self) -> None:
        if self.num_bits <= 0:
            raise ValueError("num_bits must be positive")
        for variables, _ in self.coefficients:
            if not variables or len(set(variables)) != len(variables):
                raise ValueError("Each term must contain distinct bit indices")
            if min(variables) < 0 or max(variables) >= self.num_bits:
                raise ValueError("Term bit index is out of range")

    def evaluate(self, bits: np.ndarray) -> np.ndarray:
        bits = np.asarray(bits, dtype=float)
        result = np.full(bits.shape[:-1], self.constant, dtype=float)
        for variables, coefficient in self.coefficients:
            result += coefficient * np.prod(bits[..., list(variables)], axis=-1)
        return result


def quadratic_from_function(num_bits: int, function: Callable[[np.ndarray], float]) -> QuadraticPseudoBoolean:
    """Recover an exactly quadratic Boolean expansion by finite differences."""

    zero = np.zeros(num_bits, dtype=float)
    constant = float(function(zero))
    linear = np.empty(num_bits, dtype=float)
    quadratic = np.zeros((num_bits, num_bits), dtype=float)
    units = np.eye(num_bits)
    for index in range(num_bits):
        linear[index] = float(function(units[index])) - constant
    for first, second in combinations(range(num_bits), 2):
        interaction = (
            float(function(units[first] + units[second]))
            - constant
            - linear[first]
            - linear[second]
        )
        # evaluate() sums symmetric off-diagonal entries twice.
        quadratic[first, second] = interaction / 2.0
        quadratic[second, first] = interaction / 2.0
    return QuadraticPseudoBoolean(constant, linear, quadratic)


def bit_rows(num_bits: int) -> np.ndarray:
    integers = np.arange(2**num_bits, dtype=np.uint64)
    return ((integers[:, None] >> np.arange(num_bits, dtype=np.uint64)) & 1).astype(int)


def decoded_latent_variables(bits: np.ndarray, bits_per_variable: int, levels: np.ndarray) -> np.ndarray:
    bits = np.asarray(bits, dtype=int)
    variable_count = bits.shape[-1] // bits_per_variable
    decoded = np.empty(bits.shape[:-1] + (variable_count,), dtype=float)
    powers = 2 ** np.arange(bits_per_variable)
    for variable in range(variable_count):
        start = variable * bits_per_variable
        indices = bits[..., start : start + bits_per_variable] @ powers
        decoded[..., variable] = levels[indices]
    return decoded


def quantize_polynomial(
    polynomial: QuadraticPseudoBoolean | SparsePseudoBoolean,
    scale: int,
) -> tuple[int, list[tuple[tuple[int, ...], int]]]:
    """Return integer constant and nonzero linear/pair coefficients."""

    if scale <= 0:
        raise ValueError("scale must be positive")
    constant = int(np.rint(scale * polynomial.constant))
    terms: list[tuple[tuple[int, ...], int]] = []
    if isinstance(polynomial, SparsePseudoBoolean):
        for variables, coefficient in polynomial.coefficients:
            quantized = int(np.rint(scale * coefficient))
            if quantized:
                terms.append((tuple(variables), quantized))
    else:
        for index, coefficient in enumerate(np.asarray(polynomial.linear)):
            quantized = int(np.rint(scale * coefficient))
            if quantized:
                terms.append(((index,), quantized))
        quadratic = np.asarray(polynomial.quadratic)
        for first, second in combinations(range(polynomial.num_bits), 2):
            quantized = int(np.rint(scale * 2.0 * quadratic[first, second]))
            if quantized:
                terms.append(((first, second), quantized))
    return constant, terms


def evaluate_quantized(
    bits: np.ndarray,
    constant: int,
    terms: Sequence[tuple[tuple[int, ...], int]],
) -> np.ndarray:
    bits = np.asarray(bits, dtype=int)
    result = np.full(bits.shape[:-1], constant, dtype=np.int64)
    for variables, coefficient in terms:
        term = np.prod(bits[..., list(variables)], axis=-1)
        result += coefficient * term
    return result


@dataclass
class _Evaluator:
    terms: list[tuple[tuple[int, ...], int]]
    complemented: list[bool]
    weights: list[int]
    constant_shifted: int
    feature: QuantumRegister | None
    total: QuantumRegister | None
    carry: QuantumRegister | None
    control: QuantumRegister | None
    comparator_ancilla: QuantumRegister | None
    adder: WeightedAdder | None
    comparator: IntegerComparator | None
    always: bool | None


def _make_evaluator(
    circuit: QuantumCircuit,
    constant: int,
    terms: list[tuple[tuple[int, ...], int]],
    name: str,
) -> _Evaluator:
    nonzero_terms = [(variables, coefficient) for variables, coefficient in terms if coefficient]
    complemented = [coefficient < 0 for _, coefficient in nonzero_terms]
    weights = [abs(coefficient) for _, coefficient in nonzero_terms]
    shifted = constant + sum(coefficient for _, coefficient in nonzero_terms if coefficient < 0)
    threshold = -shifted
    maximum = sum(weights)
    if threshold <= 0:
        return _Evaluator(nonzero_terms, complemented, weights, shifted, None, None, None, None, None, None, None, True)
    if threshold > maximum or not weights:
        return _Evaluator(nonzero_terms, complemented, weights, shifted, None, None, None, None, None, None, None, False)

    adder = WeightedAdder(num_state_qubits=len(weights), weights=weights, name=f"add_{name}")
    comparator = IntegerComparator(
        num_state_qubits=adder.num_sum_qubits,
        value=threshold,
        geq=True,
        name=f"geq_{name}",
    )
    feature = QuantumRegister(len(weights), f"feat_{name}")
    total = QuantumRegister(adder.num_sum_qubits, f"sum_{name}")
    carry = QuantumRegister(adder.num_carry_qubits, f"carry_{name}") if adder.num_carry_qubits else None
    control = QuantumRegister(adder.num_control_qubits, f"ctrl_{name}") if adder.num_control_qubits else None
    comparator_ancilla = (
        QuantumRegister(comparator.num_ancillas, f"cmpa_{name}") if comparator.num_ancillas else None
    )
    for register in (feature, total, carry, control, comparator_ancilla):
        if register is not None:
            circuit.add_register(register)
    return _Evaluator(
        nonzero_terms,
        complemented,
        weights,
        shifted,
        feature,
        total,
        carry,
        control,
        comparator_ancilla,
        adder,
        comparator,
        None,
    )


def _feature_operations(
    circuit: QuantumCircuit,
    state: QuantumRegister,
    evaluator: _Evaluator,
    inverse: bool,
) -> None:
    assert evaluator.feature is not None
    items = list(enumerate(zip(evaluator.terms, evaluator.complemented, strict=True)))
    if inverse:
        items.reverse()
    for feature_index, ((variables, _), complemented) in items:
        target = evaluator.feature[feature_index]
        operations: list[tuple[str, tuple]] = []
        if complemented:
            operations.append(("x", (target,)))
        if len(variables) == 1:
            operations.append(("cx", (state[variables[0]], target)))
        elif len(variables) == 2:
            operations.append(("ccx", (state[variables[0]], state[variables[1]], target)))
        else:
            operations.append(("mcx", ([state[index] for index in variables], target)))
        if inverse:
            operations.reverse()
        for operation, qubits in operations:
            getattr(circuit, operation)(*qubits)


def _adder_qubits(evaluator: _Evaluator) -> list:
    assert evaluator.feature is not None and evaluator.total is not None
    registers = [evaluator.feature, evaluator.total, evaluator.carry, evaluator.control]
    return [qubit for register in registers if register is not None for qubit in register]


def _comparator_qubits(evaluator: _Evaluator, flag) -> list:
    assert evaluator.total is not None
    registers = [evaluator.total, [flag], evaluator.comparator_ancilla]
    return [qubit for register in registers if register is not None for qubit in register]


def _apply_evaluator(
    circuit: QuantumCircuit,
    state: QuantumRegister,
    flag,
    evaluator: _Evaluator,
    inverse: bool = False,
) -> None:
    if evaluator.always is not None:
        if evaluator.always:
            circuit.x(flag)
        return
    assert evaluator.adder is not None and evaluator.comparator is not None
    if not inverse:
        _feature_operations(circuit, state, evaluator, inverse=False)
        circuit.append(evaluator.adder, _adder_qubits(evaluator))
        circuit.append(evaluator.comparator, _comparator_qubits(evaluator, flag))
    else:
        circuit.append(evaluator.comparator.inverse(), _comparator_qubits(evaluator, flag))
        circuit.append(evaluator.adder.inverse(), _adder_qubits(evaluator))
        _feature_operations(circuit, state, evaluator, inverse=True)


@dataclass(frozen=True)
class FunctionalOracle:
    circuit: QuantumCircuit
    state_register: QuantumRegister
    objective_qubit: object
    quantized_polynomials: tuple[tuple[int, list[tuple[tuple[int, ...], int]]], ...]
    scale: int


@dataclass(frozen=True)
class FunctionalAmplitudeCircuit:
    """A fully composed ``A Q^m`` circuit and explicit oracle-query count."""

    circuit: QuantumCircuit
    grover_depth: int
    oracle_queries: int
    objective_qubit_index: int


def build_functional_oracle(
    polynomials: Sequence[QuadraticPseudoBoolean | SparsePseudoBoolean],
    scale: int,
    name: str = "functional_pce_oracle",
) -> FunctionalOracle:
    """Build an OR-of-thresholds bit-flip oracle for ``score >= 0``."""

    if not polynomials:
        raise ValueError("At least one polynomial is required")
    num_bits = polynomials[0].num_bits
    if any(polynomial.num_bits != num_bits for polynomial in polynomials):
        raise ValueError("All polynomials must use the same state bits")
    state = QuantumRegister(num_bits, "state")
    flags = QuantumRegister(len(polynomials), "flags")
    objective = QuantumRegister(1, "objective")
    circuit = QuantumCircuit(state, flags, objective, name=name)
    quantized = tuple(quantize_polynomial(polynomial, scale) for polynomial in polynomials)
    evaluators = [
        _make_evaluator(circuit, constant, terms, f"p{index}")
        for index, (constant, terms) in enumerate(quantized)
    ]
    for index, evaluator in enumerate(evaluators):
        _apply_evaluator(circuit, state, flags[index], evaluator)
    # Reversible OR for one or two component limit states.
    if len(evaluators) == 1:
        circuit.cx(flags[0], objective[0])
    elif len(evaluators) == 2:
        circuit.cx(flags[0], objective[0])
        circuit.cx(flags[1], objective[0])
        circuit.ccx(flags[0], flags[1], objective[0])
    else:
        raise ValueError("This implementation supports one or two component limit states")
    for index in reversed(range(len(evaluators))):
        _apply_evaluator(circuit, state, flags[index], evaluators[index], inverse=True)
    return FunctionalOracle(circuit, state, objective[0], quantized, scale)


def build_functional_amplitude_circuit(
    oracle: FunctionalOracle,
    state_preparation: QuantumCircuit,
    grover_depth: int,
    measure: bool = True,
) -> FunctionalAmplitudeCircuit:
    """Compose state preparation, arithmetic oracle, and Grover iterates.

    The zero reflection can act on the state register alone because the
    functional oracle uncomputes all arithmetic work registers before every
    reflection.  It is therefore exact on the reachable subspace.
    """

    if grover_depth < 0:
        raise ValueError("grover_depth must be nonnegative")
    if state_preparation.num_qubits != len(oracle.state_register):
        raise ValueError("state preparation width must match the oracle state register")
    qregs = list(oracle.circuit.qregs)
    amplitude_operator = QuantumCircuit(*qregs, name="A")
    amplitude_operator.compose(
        state_preparation,
        qubits=list(oracle.state_register),
        inplace=True,
    )
    amplitude_operator.compose(oracle.circuit, inplace=True)

    iterate = QuantumCircuit(*qregs, name="Q")
    iterate.z(oracle.objective_qubit)
    iterate.compose(amplitude_operator.inverse(), inplace=True)
    state_qubits = list(oracle.state_register)
    iterate.x(state_qubits)
    if len(state_qubits) == 1:
        iterate.z(state_qubits[0])
    else:
        target = state_qubits[-1]
        iterate.h(target)
        iterate.mcx(state_qubits[:-1], target)
        iterate.h(target)
    iterate.x(state_qubits)
    iterate.compose(amplitude_operator, inplace=True)

    circuit = QuantumCircuit(*qregs, name=f"AQ{grover_depth}")
    circuit.compose(amplitude_operator, inplace=True)
    for _ in range(grover_depth):
        circuit.compose(iterate, inplace=True)
    objective_index = circuit.find_bit(oracle.objective_qubit).index
    if measure:
        readout = ClassicalRegister(1, "objective_readout")
        circuit.add_register(readout)
        circuit.measure(circuit.qubits[objective_index], readout[0])
    return FunctionalAmplitudeCircuit(
        circuit=circuit,
        grover_depth=int(grover_depth),
        oracle_queries=2 * int(grover_depth) + 1,
        objective_qubit_index=objective_index,
    )


def state_preparation_probabilities(bits_per_variable: int, variable_count: int, truncation: float = 4.0) -> tuple[np.ndarray, np.ndarray]:
    """Return midpoint levels and normalized product probabilities."""

    from scipy.stats import norm

    bins = 2**bits_per_variable
    edges = np.linspace(-truncation, truncation, bins + 1)
    levels = (edges[:-1] + edges[1:]) / 2.0
    marginal = np.diff(norm.cdf(edges))
    marginal /= marginal.sum()
    probability = marginal
    for _ in range(variable_count - 1):
        probability = np.kron(marginal, probability)
    return levels, probability


def build_product_state_preparation(
    bits_per_variable: int,
    variable_count: int,
    truncation: float = 4.0,
) -> tuple[QuantumCircuit, np.ndarray]:
    """Build a tensor product of normalized truncated-normal marginals."""

    from scipy.stats import norm

    _, product_probability = state_preparation_probabilities(
        bits_per_variable, variable_count, truncation
    )
    bins = 2**bits_per_variable
    edges = np.linspace(-truncation, truncation, bins + 1)
    marginal = np.diff(norm.cdf(edges))
    marginal /= marginal.sum()
    circuit = QuantumCircuit(bits_per_variable * variable_count, name="A_P")
    preparation = StatePreparation(np.sqrt(marginal), normalize=True)
    for variable in range(variable_count):
        start = variable * bits_per_variable
        circuit.append(preparation, list(range(start, start + bits_per_variable)))
    return circuit, product_probability
