"""Grouped-design surrogate workflow for the 10-bar truss case."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import qmc
from sklearn.compose import TransformedTargetRegressor
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import LassoCV
from sklearn.linear_model import LinearRegression
from sklearn.metrics import f1_score, mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import PolynomialFeatures, StandardScaler

from qae_abc.structural.truss import TenBarTruss, TrussUncertainty


AREA_COLUMNS = ["area_horizontal", "area_vertical", "area_diag_up", "area_diag_down"]
Z_COLUMNS = ["z_load", "z_yield", "z_modulus"]
PHYSICS_COLUMNS = ["nominal_stress_ratio", "nominal_displacement_ratio"]
FEATURE_COLUMNS = AREA_COLUMNS + PHYSICS_COLUMNS + Z_COLUMNS


def truss_feature_matrix(model: TenBarTruss, grouped_areas_m2: np.ndarray, z: np.ndarray) -> np.ndarray:
    """Construct surrogate features for one design and many latent states."""

    areas = np.asarray(grouped_areas_m2, dtype=float)
    z = np.atleast_2d(np.asarray(z, dtype=float))
    if areas.shape != (4,) or z.shape[1] != 3:
        raise ValueError("Expected four grouped areas and z with three columns")
    nominal = model.solve(areas)
    ratios = np.array(
        [1.0 - nominal.stress_limit_state, 1.0 - nominal.displacement_limit_state]
    )
    return np.column_stack(
        [
            np.broadcast_to(areas, (len(z), 4)),
            np.broadcast_to(ratios, (len(z), 2)),
            z,
        ]
    )


@dataclass(frozen=True)
class SplitIndices:
    train: np.ndarray
    validation: np.ndarray
    test: np.ndarray


@dataclass
class SeparateLimitStateSurrogate:
    """Two surrogates whose minimum defines the system limit state.

    Fitting the non-smooth pointwise minimum directly creates avoidable errors
    around the failure boundary.  The stress and displacement limit states are
    smooth individually, so the protocol models them separately and combines
    them only at prediction time.
    """

    stress: TransformedTargetRegressor
    displacement: TransformedTargetRegressor

    def predict_components(self, features: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return self.stress.predict(features), self.displacement.predict(features)

    def predict(self, features: np.ndarray) -> np.ndarray:
        stress, displacement = self.predict_components(features)
        return np.minimum(stress, displacement)


@dataclass
class FunctionalQuadraticPCESurrogate:
    """A fixed-design quadratic PCE suitable for reversible arithmetic.

    The nominal finite-element response is evaluated classically for each
    design.  Randomness then enters through a six-term quadratic basis.  Thus,
    once a design is fixed, the oracle only needs additions and products of the
    discretized latent variables; it never stores a table of failure labels.
    """

    stress: Pipeline
    displacement: Pipeline

    @staticmethod
    def _basis(features: np.ndarray, response: str) -> np.ndarray:
        features = np.asarray(features, dtype=float)
        r_stress_index = len(AREA_COLUMNS)
        r_displacement_index = r_stress_index + 1
        z_start = len(AREA_COLUMNS) + len(PHYSICS_COLUMNS)
        z_load, z_yield, z_modulus = (features[:, z_start + i] for i in range(3))
        if response == "stress":
            ratio, second = features[:, r_stress_index], z_yield
        elif response == "displacement":
            ratio, second = features[:, r_displacement_index], z_modulus
        else:
            raise ValueError(response)
        return np.column_stack(
            [
                ratio,
                ratio * z_load,
                ratio * second,
                ratio * z_load**2,
                ratio * z_load * second,
                ratio * second**2,
            ]
        )

    def fit(
        self,
        features: np.ndarray,
        stress_target: np.ndarray,
        displacement_target: np.ndarray,
    ) -> "FunctionalQuadraticPCESurrogate":
        self.stress.fit(
            self._basis(features, "stress"),
            stress_target,
            linear__sample_weight=boundary_sample_weights(stress_target),
        )
        self.displacement.fit(
            self._basis(features, "displacement"),
            displacement_target,
            linear__sample_weight=boundary_sample_weights(displacement_target),
        )
        return self

    def predict_components(self, features: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return (
            self.stress.predict(self._basis(features, "stress")),
            self.displacement.predict(self._basis(features, "displacement")),
        )

    def predict(self, features: np.ndarray) -> np.ndarray:
        stress, displacement = self.predict_components(features)
        return np.minimum(stress, displacement)


def make_functional_quadratic_pce() -> FunctionalQuadraticPCESurrogate:
    def linear_pipeline() -> Pipeline:
        return Pipeline([("scale", StandardScaler()), ("linear", LinearRegression())])

    return FunctionalQuadraticPCESurrogate(
        stress=linear_pipeline(),
        displacement=linear_pipeline(),
    )


def generate_truss_doe(
    model: TenBarTruss,
    uncertainty: TrussUncertainty,
    design_count: int,
    states_per_design: int,
    seed: int,
    area_bounds_m2: tuple[float, float] = (0.0035, 0.0200),
) -> pd.DataFrame:
    """Generate a design-grouped LHS data set with exact linear scaling."""

    if design_count < 3 or states_per_design < 1:
        raise ValueError("Need at least three designs and one state per design")
    design_engine = qmc.LatinHypercube(d=4, seed=seed, optimization="random-cd")
    unit_designs = design_engine.random(design_count)
    lower, upper = area_bounds_m2
    designs = qmc.scale(unit_designs, np.full(4, lower), np.full(4, upper))
    rng = np.random.default_rng(seed + 1)
    records: list[dict[str, float | int | str]] = []
    for design_index, areas in enumerate(designs):
        nominal = model.solve(areas)
        nominal_stress_ratio = 1.0 - nominal.stress_limit_state
        nominal_displacement_ratio = 1.0 - nominal.displacement_limit_state
        z = rng.standard_normal((states_per_design, 3))
        load, strength, modulus = uncertainty.factors_from_standard_normals(z)
        g_stress, g_displacement = model.vectorized_limit_states(areas, load, strength, modulus)
        for state_index in range(states_per_design):
            record: dict[str, float | int | str] = {
                "design_id": f"D{design_index:05d}",
                "sample_id": f"D{design_index:05d}_S{state_index:03d}",
                "mass_kg": model.mass(areas),
                "g_stress": float(g_stress[state_index]),
                "g_displacement": float(g_displacement[state_index]),
                "g_system": float(min(g_stress[state_index], g_displacement[state_index])),
                "nominal_stress_ratio": nominal_stress_ratio,
                "nominal_displacement_ratio": nominal_displacement_ratio,
            }
            record.update({column: float(value) for column, value in zip(AREA_COLUMNS, areas, strict=True)})
            record.update({column: float(value) for column, value in zip(Z_COLUMNS, z[state_index], strict=True)})
            records.append(record)
    return pd.DataFrame.from_records(records)


def grouped_train_validation_test_split(
    frame: pd.DataFrame,
    seed: int,
    train_fraction: float = 0.70,
    validation_fraction: float = 0.15,
) -> SplitIndices:
    if not np.isclose(train_fraction + validation_fraction, 0.85):
        raise ValueError("This protocol reserves 15% for the independent test set")
    indices = np.arange(len(frame))
    groups = frame["design_id"].to_numpy()
    first = GroupShuffleSplit(n_splits=1, train_size=train_fraction, random_state=seed)
    train, remainder = next(first.split(indices, groups=groups))
    remainder_groups = groups[remainder]
    validation_share = validation_fraction / (1.0 - train_fraction)
    second = GroupShuffleSplit(n_splits=1, train_size=validation_share, random_state=seed + 1)
    validation_local, test_local = next(second.split(remainder, groups=remainder_groups))
    validation = remainder[validation_local]
    test = remainder[test_local]
    return SplitIndices(train=np.sort(train), validation=np.sort(validation), test=np.sort(test))


def make_reference_surrogate(seed: int) -> TransformedTargetRegressor:
    regressor = HistGradientBoostingRegressor(
        learning_rate=0.06,
        max_iter=500,
        max_leaf_nodes=31,
        l2_regularization=1e-4,
        early_stopping=True,
        random_state=seed,
    )
    return TransformedTargetRegressor(regressor=regressor, transformer=StandardScaler())


def make_sparse_polynomial_surrogate(seed: int, order: int = 2) -> TransformedTargetRegressor:
    regressor = Pipeline(
        steps=[
            ("scale", StandardScaler()),
            ("poly", PolynomialFeatures(degree=order, include_bias=False)),
            ("lasso", LassoCV(cv=5, alphas=100, max_iter=100_000, random_state=seed)),
        ]
    )
    return TransformedTargetRegressor(regressor=regressor, transformer=StandardScaler())


def boundary_sample_weights(target: np.ndarray, width: float = 0.15, multiplier: float = 5.0) -> np.ndarray:
    target = np.asarray(target, dtype=float)
    return 1.0 + multiplier * np.exp(-0.5 * (target / width) ** 2)


def fit_sparse_polynomial(
    model: TransformedTargetRegressor,
    features: np.ndarray,
    target: np.ndarray,
) -> TransformedTargetRegressor:
    weights = boundary_sample_weights(target)
    # TransformedTargetRegressor forwards fit parameters to its wrapped
    # regressor; the Pipeline step name therefore starts directly at ``lasso``.
    model.fit(features, target, lasso__sample_weight=weights)
    return model


def make_separate_sparse_polynomial_surrogate(seed: int, order: int) -> SeparateLimitStateSurrogate:
    return SeparateLimitStateSurrogate(
        stress=make_sparse_polynomial_surrogate(seed, order),
        displacement=make_sparse_polynomial_surrogate(seed + 1, order),
    )


def fit_separate_sparse_polynomial(
    model: SeparateLimitStateSurrogate,
    features: np.ndarray,
    stress_target: np.ndarray,
    displacement_target: np.ndarray,
) -> SeparateLimitStateSurrogate:
    fit_sparse_polynomial(model.stress, features, stress_target)
    fit_sparse_polynomial(model.displacement, features, displacement_target)
    return model


def surrogate_metrics(
    target: np.ndarray,
    prediction: np.ndarray,
    boundary_width: float = 0.15,
) -> dict[str, float]:
    target = np.asarray(target, dtype=float)
    prediction = np.asarray(prediction, dtype=float)
    if target.shape != prediction.shape:
        raise ValueError("target and prediction must have identical shapes")
    scale = max(float(np.ptp(target)), np.finfo(float).eps)
    failed = target <= 0.0
    predicted_failed = prediction <= 0.0
    boundary = np.abs(target) <= boundary_width
    false_safe_count = int(np.count_nonzero(failed & ~predicted_failed))
    failed_count = int(np.count_nonzero(failed))
    boundary_f1 = (
        float(f1_score(failed[boundary], predicted_failed[boundary], zero_division=0.0))
        if np.any(boundary)
        else float("nan")
    )
    return {
        "r2": float(r2_score(target, prediction)),
        "rmse": float(np.sqrt(mean_squared_error(target, prediction))),
        "nrmse": float(np.sqrt(mean_squared_error(target, prediction)) / scale),
        "mae": float(mean_absolute_error(target, prediction)),
        "boundary_f1": boundary_f1,
        "false_safe_rate": float(false_safe_count / failed_count) if failed_count else float("nan"),
        "failure_prevalence": float(failed.mean()),
        "boundary_samples": float(np.count_nonzero(boundary)),
    }
