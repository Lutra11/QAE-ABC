"""Vectorized reliability helpers for the OC4 multi-fidelity case.

The functions in this module evaluate the fitted OpenFAST response surrogates
over Cartesian products of jacket designs and *observed* metocean states.  They
do not claim to replace a certification load-case analysis; their purpose is a
reproducible operational screening reliability experiment.
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd


DESIGN_COLUMNS = ["D_L1", "t_L1", "D_L2", "t_L2", "D_B1", "t_B1", "D_B2", "t_B2"]
ENV_COLUMNS = ["U100", "Hs", "Tp", "wind_wave_angle"]
SCREENING_TARGETS = [
    "max_tower_base_moment_knm",
    "max_top_displacement_m",
    "max_hydrodynamic_force_kn",
    "max_tracked_member_force_kn",
    "max_platform_pitch_deg",
]


def _as_design_frame(designs: pd.DataFrame | np.ndarray) -> pd.DataFrame:
    if isinstance(designs, pd.DataFrame):
        return designs.loc[:, DESIGN_COLUMNS].reset_index(drop=True).astype(float)
    values = np.atleast_2d(np.asarray(designs, dtype=float))
    if values.shape[1] != len(DESIGN_COLUMNS):
        raise ValueError(f"Expected {len(DESIGN_COLUMNS)} design columns")
    return pd.DataFrame(values, columns=DESIGN_COLUMNS)


def _as_environment_frame(environment: pd.DataFrame) -> pd.DataFrame:
    missing = set(ENV_COLUMNS) - set(environment.columns)
    if missing:
        raise ValueError(f"Missing environment columns: {sorted(missing)}")
    work = environment.loc[:, ENV_COLUMNS].reset_index(drop=True).astype(float)
    work["wind_speed_used"] = work["U100"].clip(3.0, 25.0)
    work["wave_height_used"] = work["Hs"].clip(0.1, 14.0)
    work["wave_period_used"] = work["Tp"].clip(2.0, 20.0)
    work["wave_direction_used"] = work["wind_wave_angle"].clip(0.0, 180.0)
    return work


def stratified_observed_environment(
    environment: pd.DataFrame,
    sample_size: int,
    seed: int,
) -> pd.DataFrame:
    """Select observed rows uniformly across a rank-based severity axis."""

    work = environment.dropna(subset=ENV_COLUMNS).reset_index(drop=True)
    if sample_size <= 0 or sample_size > len(work):
        raise ValueError("sample_size must be positive and no larger than the environment pool")
    severity = (
        work["U100"].rank(pct=True)
        + work["Hs"].rank(pct=True)
        + work["Tp"].rank(pct=True)
    ) / 3.0
    order = np.argsort(severity.to_numpy(), kind="stable")
    rng = np.random.default_rng(seed)
    positions = ((np.arange(sample_size) + rng.random(sample_size)) / sample_size * len(work)).astype(int)
    selected = work.iloc[order[np.minimum(positions, len(work) - 1)]].copy()
    return selected.iloc[rng.permutation(sample_size)].reset_index(drop=True)


def _geometry_arrays(designs: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    # Imported lazily so the package module remains usable without the
    # experiment scripts on sys.path.  Geometry parsing occurs once per design,
    # while all environment-dependent quantities below are vectorized.
    from run_openfast_oc4_doe import geometry_mass_and_frequency_proxy

    values = [geometry_mass_and_frequency_proxy(row) for row in designs.to_dict(orient="records")]
    return np.asarray([value[0] for value in values]), np.asarray([value[1] for value in values])


def response_feature_frame(
    designs: pd.DataFrame | np.ndarray,
    environment: pd.DataFrame,
) -> pd.DataFrame:
    """Create the exact feature table used by the fitted response bundle."""

    design_frame = _as_design_frame(designs)
    env = _as_environment_frame(environment)
    n_design, n_env = len(design_frame), len(env)
    repeated_design = pd.DataFrame(
        np.repeat(design_frame.to_numpy(float), n_env, axis=0), columns=DESIGN_COLUMNS
    )
    tiled_env = pd.DataFrame(
        np.tile(env.to_numpy(float), (n_design, 1)), columns=env.columns
    )
    frame = pd.concat([repeated_design, tiled_env], axis=1)

    mass, frequency = _geometry_arrays(design_frame)
    mass = np.repeat(mass, n_env)
    frequency = np.repeat(frequency, n_env)
    wind = frame["wind_speed_used"].to_numpy(float)
    hs = frame["wave_height_used"].to_numpy(float)
    tp = frame["wave_period_used"].to_numpy(float)
    direction = np.deg2rad(frame["wave_direction_used"].to_numpy(float))

    rho_air = 1.225
    rotor_area = np.pi * 63.0**2
    thrust_coefficient = np.where(wind <= 12.0, 0.82, np.maximum(0.16, 0.82 * (12.0 / wind) ** 2))
    wind_force = 0.5 * rho_air * thrust_coefficient * rotor_area * wind**2 / 1000.0
    wind_moment = wind_force * 90.0

    d_leg = 0.5 * (frame["D_L1"].to_numpy(float) + frame["D_L2"].to_numpy(float))
    d_brace = 0.5 * (frame["D_B1"].to_numpy(float) + frame["D_B2"].to_numpy(float))
    projected_area = 4 * 45.0 * d_leg + 16 * 18.0 * d_brace
    displaced_volume = 4 * 45.0 * np.pi * d_leg**2 / 4 + 16 * 18.0 * np.pi * d_brace**2 / 4
    particle_velocity = np.pi * hs / np.maximum(tp, 1e-9)
    particle_acceleration = 2 * np.pi / np.maximum(tp, 1e-9) * particle_velocity
    wave_drag = 0.5 * 1025.0 * projected_area * particle_velocity**2 / 1000.0
    wave_inertia = 1025.0 * 2.0 * displaced_volume * particle_acceleration / 1000.0
    wave_force = wave_drag + wave_inertia
    wave_moment = wave_force * 22.5
    combined = np.sqrt(
        wind_moment**2 + wave_moment**2 + 2 * wind_moment * wave_moment * np.cos(direction)
    )
    base_moment = combined * (1.0 + 0.12 * hs / np.maximum(tp, 2.0))
    stiffness_ratio = np.maximum((frequency / 2.755477) ** 2 * (mass / 691774.3), 1e-6)

    frame["lf_structural_mass_kg"] = mass
    frame["lf_frequency_proxy_hz"] = frequency
    frame["lf_wind_force_kn"] = wind_force
    frame["lf_wave_force_kn"] = wave_force
    frame["lf_tower_base_moment_knm"] = base_moment
    frame["lf_tower_base_moment_del4_knm"] = 0.52 * base_moment * (1 + 0.08 * hs)
    frame["lf_top_displacement_m"] = 0.31 * (base_moment / 65000.0) / stiffness_ratio
    frame["lf_hydrodynamic_force_kn"] = wave_force
    frame["lf_member_force_kn"] = (wind_force + wave_force) / 4.0 / np.sqrt(stiffness_ratio)
    frame["lf_platform_pitch_deg"] = 0.10 * (wave_moment / 65000.0) / stiffness_ratio
    frame["relative_direction_sin"] = np.sin(direction)
    frame["relative_direction_cos"] = np.cos(direction)
    return frame


def predict_response_cartesian(
    bundle: dict,
    designs: pd.DataFrame | np.ndarray,
    environment: pd.DataFrame,
    targets: Iterable[str] = SCREENING_TARGETS,
    design_chunk_size: int = 16,
) -> dict[str, np.ndarray]:
    """Predict target arrays with shape ``(n_designs, n_environment)``."""

    design_frame = _as_design_frame(designs)
    env = _as_environment_frame(environment)
    target_names = list(targets)
    unknown = set(target_names) - set(bundle["models"])
    if unknown:
        raise ValueError(f"Targets absent from response bundle: {sorted(unknown)}")
    output = {name: np.empty((len(design_frame), len(env)), dtype=float) for name in target_names}
    feature_columns = list(bundle["feature_columns"])
    for start in range(0, len(design_frame), design_chunk_size):
        stop = min(start + design_chunk_size, len(design_frame))
        features = response_feature_frame(design_frame.iloc[start:stop], env)
        x = features.loc[:, feature_columns].to_numpy(float)
        for name in target_names:
            model = bundle["models"][name]["model"]
            prediction = np.maximum(np.asarray(model.predict(x), dtype=float), 0.0)
            output[name][start:stop] = prediction.reshape(stop - start, len(env))
    return output


def system_failure_probability(
    responses: dict[str, np.ndarray],
    capacities: dict[str, float],
) -> tuple[np.ndarray, np.ndarray]:
    """Return empirical system probabilities and capacity-normalized quantiles."""

    names = list(capacities)
    ratios = np.stack(
        [np.asarray(responses[name], dtype=float) / float(capacities[name]) for name in names], axis=2
    )
    system_ratio = np.max(ratios, axis=2)
    probabilities = np.mean(system_ratio > 1.0, axis=1)
    return probabilities, system_ratio
