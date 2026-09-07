"""Auditable planar 10-bar truss bridge case.

The model uses SI units throughout. The left two nodes are fixed and vertical
loads act at the two right nodes. Four area groups preserve symmetry while
keeping the optimization dimension small enough for exhaustive diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import multivariate_normal, norm


def lognormal_parameters_from_mean_cov(mean: float, cov: float) -> tuple[float, float]:
    if mean <= 0 or cov < 0:
        raise ValueError("mean must be positive and cov nonnegative")
    sigma = float(np.sqrt(np.log1p(cov**2)))
    mu = float(np.log(mean) - 0.5 * sigma**2)
    return mu, sigma


@dataclass(frozen=True)
class TrussResponse:
    displacements: np.ndarray
    axial_stresses: np.ndarray
    reactions: np.ndarray
    mass: float
    stress_limit_state: float
    displacement_limit_state: float

    @property
    def system_limit_state(self) -> float:
        return min(self.stress_limit_state, self.displacement_limit_state)


@dataclass(frozen=True)
class TrussUncertainty:
    load_cov: float = 0.10
    yield_cov: float = 0.05
    modulus_cov: float = 0.03

    def factors_from_standard_normals(self, z: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        z = np.asarray(z, dtype=float)
        if z.ndim != 2 or z.shape[1] != 3:
            raise ValueError("z must have shape (n, 3)")
        mu_load, sigma_load = lognormal_parameters_from_mean_cov(1.0, self.load_cov)
        mu_yield, sigma_yield = lognormal_parameters_from_mean_cov(1.0, self.yield_cov)
        mu_modulus, sigma_modulus = lognormal_parameters_from_mean_cov(1.0, self.modulus_cov)
        return (
            np.exp(mu_load + sigma_load * z[:, 0]),
            np.exp(mu_yield + sigma_yield * z[:, 1]),
            np.exp(mu_modulus + sigma_modulus * z[:, 2]),
        )


class TenBarTruss:
    """Six-node, ten-member planar truss with four grouped areas."""

    node_coordinates = np.array(
        [
            [0.0, 0.0],
            [9.144, 0.0],
            [18.288, 0.0],
            [0.0, 9.144],
            [9.144, 9.144],
            [18.288, 9.144],
        ],
        dtype=float,
    )
    elements = np.array(
        [
            [0, 1],
            [1, 2],
            [3, 4],
            [4, 5],
            [1, 4],
            [2, 5],
            [0, 4],
            [1, 3],
            [1, 5],
            [2, 4],
        ],
        dtype=int,
    )
    area_groups = np.array([0, 0, 0, 0, 1, 1, 2, 3, 2, 3], dtype=int)
    fixed_dofs = np.array([0, 1, 6, 7], dtype=int)

    def __init__(
        self,
        elastic_modulus_pa: float = 200e9,
        yield_strength_pa: float = 355e6,
        density_kg_m3: float = 7850.0,
        vertical_load_n: float = 1.0e6,
        displacement_limit_m: float = 0.050,
    ) -> None:
        self.elastic_modulus_pa = float(elastic_modulus_pa)
        self.yield_strength_pa = float(yield_strength_pa)
        self.density_kg_m3 = float(density_kg_m3)
        self.vertical_load_n = float(vertical_load_n)
        self.displacement_limit_m = float(displacement_limit_m)
        self._lengths = np.linalg.norm(
            self.node_coordinates[self.elements[:, 1]] - self.node_coordinates[self.elements[:, 0]],
            axis=1,
        )

    @property
    def lengths(self) -> np.ndarray:
        return self._lengths.copy()

    def expand_grouped_areas(self, grouped_areas_m2: np.ndarray) -> np.ndarray:
        grouped = np.asarray(grouped_areas_m2, dtype=float)
        if grouped.shape != (4,) or np.any(grouped <= 0):
            raise ValueError("grouped_areas_m2 must be a positive length-4 vector")
        return grouped[self.area_groups]

    def mass(self, grouped_areas_m2: np.ndarray) -> float:
        areas = self.expand_grouped_areas(grouped_areas_m2)
        return float(self.density_kg_m3 * np.sum(areas * self._lengths))

    def _assemble(self, grouped_areas_m2: np.ndarray, elastic_modulus_pa: float) -> np.ndarray:
        areas = self.expand_grouped_areas(grouped_areas_m2)
        dof_count = 2 * len(self.node_coordinates)
        stiffness = np.zeros((dof_count, dof_count), dtype=float)
        for area, length, (node_i, node_j) in zip(areas, self._lengths, self.elements, strict=True):
            delta = self.node_coordinates[node_j] - self.node_coordinates[node_i]
            cosine, sine = delta / length
            direction = np.array([-cosine, -sine, cosine, sine])
            local = elastic_modulus_pa * area / length * np.outer(direction, direction)
            dofs = np.array([2 * node_i, 2 * node_i + 1, 2 * node_j, 2 * node_j + 1])
            stiffness[np.ix_(dofs, dofs)] += local
        return stiffness

    def solve(
        self,
        grouped_areas_m2: np.ndarray,
        load_factor: float = 1.0,
        yield_factor: float = 1.0,
        modulus_factor: float = 1.0,
    ) -> TrussResponse:
        modulus = self.elastic_modulus_pa * float(modulus_factor)
        yield_strength = self.yield_strength_pa * float(yield_factor)
        stiffness = self._assemble(grouped_areas_m2, modulus)
        force = np.zeros(stiffness.shape[0])
        force[2 * 2 + 1] = -self.vertical_load_n * load_factor
        force[2 * 5 + 1] = -self.vertical_load_n * load_factor
        free_dofs = np.setdiff1d(np.arange(stiffness.shape[0]), self.fixed_dofs)
        displacements = np.zeros_like(force)
        displacements[free_dofs] = np.linalg.solve(
            stiffness[np.ix_(free_dofs, free_dofs)], force[free_dofs]
        )
        stresses = np.empty(len(self.elements), dtype=float)
        for index, (length, (node_i, node_j)) in enumerate(zip(self._lengths, self.elements, strict=True)):
            delta = self.node_coordinates[node_j] - self.node_coordinates[node_i]
            cosine, sine = delta / length
            direction = np.array([-cosine, -sine, cosine, sine])
            dofs = np.array([2 * node_i, 2 * node_i + 1, 2 * node_j, 2 * node_j + 1])
            stresses[index] = modulus / length * direction @ displacements[dofs]
        reactions = stiffness @ displacements - force
        max_stress_ratio = float(np.max(np.abs(stresses)) / yield_strength)
        free_node_displacements = displacements.reshape(-1, 2)[[1, 2, 4, 5]]
        max_displacement = float(np.max(np.linalg.norm(free_node_displacements, axis=1)))
        return TrussResponse(
            displacements=displacements.reshape(-1, 2),
            axial_stresses=stresses,
            reactions=reactions.reshape(-1, 2),
            mass=self.mass(grouped_areas_m2),
            stress_limit_state=1.0 - max_stress_ratio,
            displacement_limit_state=1.0 - max_displacement / self.displacement_limit_m,
        )

    def vectorized_limit_states(
        self,
        grouped_areas_m2: np.ndarray,
        load_factors: np.ndarray,
        yield_factors: np.ndarray,
        modulus_factors: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Evaluate random limit states using exact linear response scaling."""

        load_factors, yield_factors, modulus_factors = np.broadcast_arrays(
            np.asarray(load_factors, dtype=float),
            np.asarray(yield_factors, dtype=float),
            np.asarray(modulus_factors, dtype=float),
        )
        nominal = self.solve(grouped_areas_m2)
        nominal_stress_ratio = 1.0 - nominal.stress_limit_state
        nominal_displacement_ratio = 1.0 - nominal.displacement_limit_state
        stress_ratio = nominal_stress_ratio * load_factors / yield_factors
        displacement_ratio = nominal_displacement_ratio * load_factors / modulus_factors
        return 1.0 - stress_ratio, 1.0 - displacement_ratio

    def reference_failure_probability(
        self,
        grouped_areas_m2: np.ndarray,
        uncertainty: TrussUncertainty,
        samples: int,
        seed: int,
        chunk_size: int = 1_000_000,
    ) -> dict[str, float]:
        rng = np.random.default_rng(seed)
        stress_failures = 0
        displacement_failures = 0
        system_failures = 0
        completed = 0
        while completed < samples:
            size = min(chunk_size, samples - completed)
            factors = uncertainty.factors_from_standard_normals(rng.standard_normal((size, 3)))
            g_stress, g_displacement = self.vectorized_limit_states(grouped_areas_m2, *factors)
            stress_failed = g_stress <= 0
            displacement_failed = g_displacement <= 0
            stress_failures += int(np.count_nonzero(stress_failed))
            displacement_failures += int(np.count_nonzero(displacement_failed))
            system_failures += int(np.count_nonzero(stress_failed | displacement_failed))
            completed += size
        return {
            "pf_stress": stress_failures / samples,
            "pf_displacement": displacement_failures / samples,
            "pf_system": system_failures / samples,
            "samples": float(samples),
        }

    def exact_failure_probability(
        self,
        grouped_areas_m2: np.ndarray,
        uncertainty: TrussUncertainty,
    ) -> dict[str, float]:
        """Closed-form bivariate-lognormal probability for this bridge case.

        Both component demand-to-capacity ratios share the load factor, so
        their logarithms form a correlated Gaussian pair.  The resulting
        bivariate normal CDF is a deterministic audit reference for MC and QAE.
        """

        nominal = self.solve(grouped_areas_m2)
        stress_ratio = 1.0 - nominal.stress_limit_state
        displacement_ratio = 1.0 - nominal.displacement_limit_state
        mu_l, sigma_l = lognormal_parameters_from_mean_cov(1.0, uncertainty.load_cov)
        mu_y, sigma_y = lognormal_parameters_from_mean_cov(1.0, uncertainty.yield_cov)
        mu_e, sigma_e = lognormal_parameters_from_mean_cov(1.0, uncertainty.modulus_cov)
        means = np.array([mu_l - mu_y, mu_l - mu_e])
        covariance = np.array(
            [
                [sigma_l**2 + sigma_y**2, sigma_l**2],
                [sigma_l**2, sigma_l**2 + sigma_e**2],
            ]
        )
        thresholds = np.array([-np.log(stress_ratio), -np.log(displacement_ratio)])
        scales = np.sqrt(np.diag(covariance))
        pf_stress = 1.0 - norm.cdf((thresholds[0] - means[0]) / scales[0])
        pf_displacement = 1.0 - norm.cdf((thresholds[1] - means[1]) / scales[1])
        safe_probability = multivariate_normal.cdf(
            thresholds,
            mean=means,
            cov=covariance,
            maxpts=1_000_000,
            abseps=1e-10,
            releps=1e-10,
            rng=np.random.default_rng(20260903),
        )
        return {
            "pf_stress": float(np.clip(pf_stress, 0.0, 1.0)),
            "pf_displacement": float(np.clip(pf_displacement, 0.0, 1.0)),
            "pf_system": float(np.clip(1.0 - safe_probability, 0.0, 1.0)),
        }

    def discrete_failure_probability(
        self,
        grouped_areas_m2: np.ndarray,
        uncertainty: TrussUncertainty,
        bits_per_variable: int = 3,
        truncation: float = 4.0,
    ) -> dict[str, float]:
        """Exact tensor quadrature for the discretized three-variable state."""

        if bits_per_variable < 1:
            raise ValueError("bits_per_variable must be positive")
        bins = 2**bits_per_variable
        edges = np.linspace(-truncation, truncation, bins + 1)
        probabilities = np.diff(norm.cdf(edges))
        probabilities /= probabilities.sum()
        midpoints = (edges[:-1] + edges[1:]) / 2
        z0, z1, z2 = np.meshgrid(midpoints, midpoints, midpoints, indexing="ij")
        weights = (
            probabilities[:, None, None]
            * probabilities[None, :, None]
            * probabilities[None, None, :]
        )
        z = np.column_stack([z0.ravel(), z1.ravel(), z2.ravel()])
        factors = uncertainty.factors_from_standard_normals(z)
        g_stress, g_displacement = self.vectorized_limit_states(grouped_areas_m2, *factors)
        stress_failed = g_stress.reshape(weights.shape) <= 0
        displacement_failed = g_displacement.reshape(weights.shape) <= 0
        return {
            "pf_stress": float(weights[stress_failed].sum()),
            "pf_displacement": float(weights[displacement_failed].sum()),
            "pf_system": float(weights[stress_failed | displacement_failed].sum()),
            "basis_states": float(weights.size),
        }
