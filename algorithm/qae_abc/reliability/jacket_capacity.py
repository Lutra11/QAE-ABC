"""Mechanics-based tubular-member utilization for preliminary OC4 sizing."""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class TubeSection:
    area_m2: float
    second_moment_m4: float
    section_modulus_m3: float
    radius_gyration_m: float
    median_enclosed_area_m2: float


@dataclass(frozen=True)
class MemberResistances:
    yield_n: float
    euler_n: float
    tension_n: float
    compression_n: float
    bending_nm: float
    shear_n: float
    torsion_nm: float


def tube_section_properties(diameter_m: float, thickness_m: float) -> TubeSection:
    diameter = float(diameter_m)
    thickness = float(thickness_m)
    if not math.isfinite(diameter) or diameter <= 0.0:
        raise ValueError("diameter_m must be finite and positive")
    if not math.isfinite(thickness) or thickness <= 0.0 or 2.0 * thickness >= diameter:
        raise ValueError("thickness_m must satisfy 0 < 2t < D")
    inner = diameter - 2.0 * thickness
    area = math.pi / 4.0 * (diameter**2 - inner**2)
    second_moment = math.pi / 64.0 * (diameter**4 - inner**4)
    section_modulus = second_moment / (diameter / 2.0)
    radius_gyration = math.sqrt(second_moment / area)
    median_area = math.pi / 4.0 * (diameter - thickness) ** 2
    return TubeSection(area, second_moment, section_modulus, radius_gyration, median_area)


def member_resistances(
    diameter_m: float,
    thickness_m: float,
    member_length_m: float,
    *,
    youngs_modulus_pa: float = 210.0e9,
    yield_strength_pa: float = 355.0e6,
    gamma_m: float = 1.10,
    effective_length_factor: float = 1.0,
) -> MemberResistances:
    length = float(member_length_m)
    youngs = float(youngs_modulus_pa)
    yield_strength = float(yield_strength_pa)
    gamma = float(gamma_m)
    effective_factor = float(effective_length_factor)
    if not math.isfinite(length) or length <= 0.0:
        raise ValueError("member_length_m must be finite and positive")
    if youngs <= 0.0 or yield_strength <= 0.0 or gamma <= 0.0 or effective_factor <= 0.0:
        raise ValueError("material and resistance parameters must be positive")
    section = tube_section_properties(diameter_m, thickness_m)
    yield_n = section.area_m2 * yield_strength / gamma
    euler_n = math.pi**2 * youngs * section.second_moment_m4 / (effective_factor * length) ** 2 / gamma
    bending_nm = section.section_modulus_m3 * yield_strength / gamma
    shear_n = section.area_m2 * yield_strength / (math.sqrt(3.0) * gamma)
    torsion_nm = 2.0 * section.median_enclosed_area_m2 * float(thickness_m) * yield_strength / (
        math.sqrt(3.0) * gamma
    )
    return MemberResistances(
        yield_n=yield_n,
        euler_n=euler_n,
        tension_n=yield_n,
        compression_n=min(yield_n, euler_n),
        bending_nm=bending_nm,
        shear_n=shear_n,
        torsion_nm=torsion_nm,
    )
