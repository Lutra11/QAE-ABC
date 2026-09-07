import numpy as np

from qae_abc.structural.truss import TenBarTruss, TrussUncertainty


def test_nominal_truss_equilibrium_and_scaling():
    model = TenBarTruss()
    areas = np.full(4, 0.01)
    response = model.solve(areas)
    applied_vertical = -2 * model.vertical_load_n
    assert np.isclose(response.reactions[:, 1].sum(), -applied_vertical, rtol=1e-10)
    doubled = model.solve(areas, load_factor=2.0)
    assert np.allclose(doubled.axial_stresses, 2 * response.axial_stresses, rtol=1e-10, atol=1e-4)
    assert np.allclose(doubled.displacements, 2 * response.displacements, rtol=1e-10, atol=1e-12)


def test_vectorized_limit_states_match_direct_solves():
    model = TenBarTruss()
    areas = np.array([0.012, 0.009, 0.010, 0.011])
    load = np.array([0.9, 1.1])
    strength = np.array([1.02, 0.97])
    modulus = np.array([1.01, 0.98])
    g_stress, g_disp = model.vectorized_limit_states(areas, load, strength, modulus)
    for index in range(2):
        direct = model.solve(areas, load[index], strength[index], modulus[index])
        assert np.isclose(g_stress[index], direct.stress_limit_state, rtol=1e-10, atol=1e-12)
        assert np.isclose(g_disp[index], direct.displacement_limit_state, rtol=1e-10, atol=1e-12)


def test_discrete_probability_is_valid():
    model = TenBarTruss()
    result = model.discrete_failure_probability(np.full(4, 0.01), TrussUncertainty(), bits_per_variable=2)
    assert result["basis_states"] == 64
    assert 0.0 <= result["pf_system"] <= 1.0


def test_exact_probability_agrees_with_large_monte_carlo():
    model = TenBarTruss()
    uncertainty = TrussUncertainty()
    design = np.full(4, 0.02)
    exact = model.exact_failure_probability(design, uncertainty)["pf_system"]
    monte_carlo = model.reference_failure_probability(
        design, uncertainty, samples=300_000, seed=20260903
    )["pf_system"]
    assert abs(exact - monte_carlo) < 5e-4
