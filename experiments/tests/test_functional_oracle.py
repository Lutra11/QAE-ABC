import numpy as np
from qiskit import QuantumCircuit
from qiskit.quantum_info import Statevector

from qae_abc.quantum.functional_oracle import (
    QuadraticPseudoBoolean,
    SparsePseudoBoolean,
    bit_rows,
    build_functional_amplitude_circuit,
    build_functional_oracle,
    build_product_state_preparation,
    decoded_latent_variables,
    evaluate_quantized,
    quadratic_from_function,
    quantize_polynomial,
)


def test_quadratic_recovery_and_quantization():
    def score(bits):
        return -0.3 + 0.4 * bits[0] - 0.2 * bits[1] + 0.7 * bits[0] * bits[1]

    polynomial = quadratic_from_function(2, score)
    rows = bit_rows(2)
    assert np.allclose(polynomial.evaluate(rows), [score(row) for row in rows])
    constant, terms = quantize_polynomial(polynomial, 100)
    assert np.array_equal(evaluate_quantized(rows, constant, terms) >= 0, polynomial.evaluate(rows) >= 0)


def test_little_endian_latent_decoding():
    rows = bit_rows(4)
    levels = np.array([-3.0, -1.0, 1.0, 3.0])
    decoded = decoded_latent_variables(rows, 2, levels)
    assert np.array_equal(decoded[:4, 0], levels)
    assert np.all(decoded[:4, 1] == -3.0)


def test_gate_oracle_matches_quantized_classifier():
    polynomial = quadratic_from_function(2, lambda bits: -0.5 + bits[0])
    oracle = build_functional_oracle([polynomial], scale=2)
    objective_index = oracle.circuit.find_bit(oracle.objective_qubit).index
    for row in bit_rows(2):
        circuit = QuantumCircuit(*oracle.circuit.qregs)
        for index, value in enumerate(row):
            if value:
                circuit.x(circuit.qubits[index])
        circuit.compose(oracle.circuit, inplace=True)
        statevector = Statevector.from_instruction(circuit)
        probabilities = statevector.probabilities([objective_index])
        expected = int(polynomial.evaluate(row) >= 0)
        assert np.isclose(probabilities[expected], 1.0)


def test_product_state_preparation_matches_truncated_normal_weights():
    circuit, expected = build_product_state_preparation(2, 3)
    observed = Statevector.from_instruction(circuit).probabilities()
    assert np.allclose(observed, expected, atol=1e-12)


def test_composed_grover_circuit_amplifies_quarter_probability_to_one():
    polynomial = QuadraticPseudoBoolean(
        constant=-0.5,
        linear=np.zeros(2),
        quadratic=np.array([[0.0, 0.5], [0.5, 0.0]]),
    )
    oracle = build_functional_oracle([polynomial], scale=2)
    preparation, _ = build_product_state_preparation(1, 2)
    composed = build_functional_amplitude_circuit(
        oracle, preparation, grover_depth=1, measure=False
    )
    statevector = Statevector.from_instruction(composed.circuit)
    objective = composed.objective_qubit_index
    probability_one = sum(
        probability
        for basis_index, probability in enumerate(statevector.probabilities())
        if (basis_index >> objective) & 1
    )
    assert np.isclose(probability_one, 1.0, atol=1e-12)
    assert composed.oracle_queries == 3


def test_sparse_cubic_gate_oracle_matches_three_bit_and():
    polynomial = SparsePseudoBoolean(
        constant=-0.5,
        coefficients=(((0, 1, 2), 1.0),),
        num_bits=3,
    )
    oracle = build_functional_oracle([polynomial], scale=2)
    objective_index = oracle.circuit.find_bit(oracle.objective_qubit).index
    for row in bit_rows(3):
        circuit = QuantumCircuit(*oracle.circuit.qregs)
        for index, value in enumerate(row):
            if value:
                circuit.x(circuit.qubits[index])
        circuit.compose(oracle.circuit, inplace=True)
        observed = Statevector.from_instruction(circuit).probabilities([objective_index])
        expected = int(np.all(row == 1))
        assert np.isclose(observed[expected], 1.0)
