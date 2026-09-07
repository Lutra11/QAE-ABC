"""Summarize frozen-design 600 s OpenFAST spot-validation runs."""

from __future__ import annotations

import hashlib
import json
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


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "results" / "raw" / "openfast_oc4_doe" / "oc4_hf_results_final600.parquet"
OUTPUT = ROOT / "results" / "processed" / "oc4_final_validation"
RESPONSES = [
    "max_tower_base_moment_knm",
    "tower_base_moment_del4_knm",
    "max_top_displacement_m",
    "max_hydrodynamic_force_kn",
    "max_tracked_member_force_kn",
    "mean_generator_power_kw",
    "max_platform_pitch_deg",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    data = pd.read_parquet(INPUT)
    if len(data) != 12 or not data["success"].astype(bool).all():
        raise RuntimeError("Final validation requires exactly 12 successful OpenFAST runs")
    if "source_method" not in data:
        raise RuntimeError("Final validation result lacks frozen-design source labels")
    summary_rows = []
    for method, group in data.groupby("source_method", sort=False):
        row = {
            "source_method": method,
            "runs": len(group),
            "structural_mass_kg": float(group["structural_mass_kg"].median()),
            "median_wall_time_s": float(group["wall_time_s"].median()),
            "minimum_output_rows": int(group["output_rows"].min()),
            "minimum_analysis_start_s": float(group["analysis_start_s"].min()),
        }
        for response in RESPONSES:
            row[f"{response}__mean"] = float(group[response].mean())
            row[f"{response}__sd"] = float(group[response].std(ddof=1))
            row[f"{response}__max"] = float(group[response].max())
        summary_rows.append(row)
    summary = pd.DataFrame(summary_rows)
    baseline = summary[summary["source_method"] == "original_oc4"].iloc[0]
    for response in RESPONSES:
        denominator = float(baseline[f"{response}__mean"])
        summary[f"{response}__mean_change_vs_original_pct"] = (
            100.0 * (summary[f"{response}__mean"] - denominator) / max(abs(denominator), 1e-15)
        )
    OUTPUT.mkdir(parents=True, exist_ok=True)
    run_path = OUTPUT / "oc4_final600_runs.csv"
    summary_path = OUTPUT / "oc4_final600_summary.csv"
    data.to_csv(run_path, index=False)
    summary.to_csv(summary_path, index=False)
    report = {
        "scope": "Twelve 600 s, ModCoupling=3 OpenFAST spot-validation runs: four frozen designs times three independent irregular-wave seeds.",
        "all_successful": True,
        "runs": len(data),
        "designs": int(data["source_method"].nunique()),
        "seeds_per_design": data.groupby("source_method")["wave_seed"].nunique().astype(int).to_dict(),
        "minimum_analyzed_duration_s": float(600.0 - data["analysis_start_s"].max()),
        "fatigue_caveat": "DEL4 is computed only over the post-transient portion of these spot checks; no S-N curve, SCF, Miner lifetime, turbulence ensemble, or certification DLC set is claimed.",
        "non_certification_statement": "These runs are independent engineering spot checks, not an IEC/DNV certification analysis.",
        "input_sha256": sha256(INPUT),
        "files": {
            str(run_path.relative_to(ROOT)): sha256(run_path),
            str(summary_path.relative_to(ROOT)): sha256(summary_path),
        },
    }
    report_path = OUTPUT / "oc4_final600_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(summary.to_string(index=False))
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
