#!/usr/bin/env python3
"""Runner-side case preparation and metrics extraction for the R3-Major-5 campaign
on GitHub Actions. Self-contained mirror of experiments/run_openfast_oc4_doe.py
(v5.0.0 contract): r-test v5.0.0 template case, steady wind (WindType=1),
SeaState wave rewrite, SubDyn/HydroDyn member D/t rewrite, five screening
responses with 20%-tail statistics. R3M5 variant: TMax=660 s, statistics window
60-660 s, plus transient windows (0-10 full, 2-10 original, 60-70 fixed)."""
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

# Per-case wall-clock limit (s). E2 evidence: healthy 660 s cases finish in
# ~2 h; anything past 4 h is a stuck case, so record it as failed and move on
# instead of burning the 6 h GitHub job limit on one case.
CASE_TIMEOUT_S = int(os.environ.get("CASE_TIMEOUT_S", "14400"))

BASELINE = {
    "D_L1": 1.2, "t_L1": 0.05, "D_L2": 1.2, "t_L2": 0.035,
    "D_B1": 0.8, "t_B1": 0.02, "D_B2": 0.8, "t_B2": 0.02,
}
DESIGN_COLUMNS = list(BASELINE)
FIVE_RESPONSES = [
    "max_tower_base_moment_knm", "max_top_displacement_m",
    "max_hydrodynamic_force_kn", "max_tracked_member_force_kn",
    "max_platform_pitch_deg",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def replace_labeled_value(text: str, label: str, value) -> str:
    pattern = re.compile(rf"^(\s*)\S+(\s+{re.escape(label)}(?=\s|$).*)$", re.MULTILINE)
    updated, count = pattern.subn(rf"\g<1>{value}\g<2>", text, count=1)
    if count != 1:
        raise ValueError(f"Expected exactly one {label!r} field, found {count}")
    return updated


def modify_member_properties(path: Path, design: dict, hydro: bool) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    in_members = in_circular = False
    seen = set()
    output = []
    for line in lines:
        if "MEMBERS " in line and line.lstrip().startswith("-"):
            in_members, in_circular = True, False
        elif ("CIRCULAR" in line or "CYLINDRICAL" in line) and "CROSS-SECTION PROPERTIES" in line:
            in_members, in_circular = False, True
        elif in_circular and "RECTANGULAR" in line and "CROSS-SECTION PROPERTIES" in line:
            in_circular = False
        tokens = line.split()
        if in_members and tokens and tokens[0].isdigit() and len(tokens) >= 5:
            member_id = int(tokens[0])
            if 69 <= member_id <= 100:
                tokens[3] = tokens[4] = "7"
                line = "  " + "  ".join(tokens)
        if in_circular and "NPropSetsCyl" in line:
            line = replace_labeled_value(line, "NPropSetsCyl", 7)
        elif in_circular and tokens and tokens[0].isdigit() and len(tokens) >= 3:
            prop_id = int(tokens[0])
            seen.add(prop_id)
            mapping = {
                1: (design["D_B1"], design["t_B1"]),
                2: (design["D_L1"], design["t_L1"]),
                3: (design["D_L2"], design["t_L2"]),
            }
            if prop_id in mapping:
                diameter, thickness = mapping[prop_id]
                if hydro:
                    line = f"{prop_id:5d} {diameter:14.8f} {thickness:14.8f}"
                else:
                    line = (
                        f"{prop_id:5d} {float(tokens[1]):16.8e} {float(tokens[2]):16.8e} "
                        f"{float(tokens[3]):14.6f} {diameter:14.8f} {thickness:14.8f}"
                    )
            if prop_id == 6:
                diameter, thickness = design["D_B2"], design["t_B2"]
                if hydro:
                    output.append(line)
                    line = f"{7:5d} {diameter:14.8f} {thickness:14.8f}"
                else:
                    output.append(line)
                    line = (
                        f"{7:5d} {2.1e11:16.8e} {8.0769e10:16.8e} {7850.0:14.6f} "
                        f"{diameter:14.8f} {thickness:14.8f}"
                    )
        output.append(line)
    if not {1, 2, 3, 6}.issubset(seen):
        raise ValueError(f"Failed to locate expected circular properties in {path}")
    path.write_text("\n".join(output) + "\n", encoding="utf-8")


def ensure_shared_baseline(workspace: Path) -> Path:
    """Copy r-test 5MW_Baseline (with the Linux DISCON.dll built by the workflow)
    to runs/5MW_Baseline once; case inputs reference it via ../5MW_Baseline."""
    rtest_baseline = workspace / "r-test" / "glue-codes" / "openfast" / "5MW_Baseline"
    shared = workspace / "runs" / "5MW_Baseline"
    if not shared.exists():
        shutil.copytree(rtest_baseline, shared)
    return shared


def prepare_case(row: dict, workspace: Path, tmax: float = 660.0) -> Path:
    """Build one case directory from the r-test v5.0.0 template."""
    rtest = workspace / "r-test" / "glue-codes" / "openfast"
    source_case = rtest / "5MW_OC4Jckt_DLL_WTurb_WavesIrr_MGrowth"
    ensure_shared_baseline(workspace)
    case_dir = workspace / "runs" / str(row["case_id"])
    case_dir.mkdir(parents=True, exist_ok=True)
    for source in source_case.iterdir():
        if source.is_file() and source.suffix.lower() in {".fst", ".dat", ".md"}:
            destination = case_dir / ("case.fst" if source.suffix.lower() == ".fst" else source.name)
            shutil.copy2(source, destination)
    inflow_source = rtest / "5MW_Baseline" / "NRELOffshrBsline5MW_InflowWind_12mps.dat"
    shutil.copy2(inflow_source, case_dir / "InflowWind.dat")

    fst = (case_dir / "case.fst").read_text(encoding="utf-8")
    fst = replace_labeled_value(fst, "TMax", f"{tmax:.3f}")
    fst = replace_labeled_value(fst, "ModCoupling", 3)
    fst = replace_labeled_value(fst, "InflowFile", '"InflowWind.dat"')
    fst = replace_labeled_value(fst, "SttsTime", 99999)
    fst = replace_labeled_value(fst, "OutFileFmt", 2)
    (case_dir / "case.fst").write_text(fst, encoding="utf-8")

    inflow = (case_dir / "InflowWind.dat").read_text(encoding="utf-8")
    inflow = replace_labeled_value(inflow, "WindType", 1)
    inflow = replace_labeled_value(inflow, "HWindSpeed", f"{float(row['wind_speed_used']):.6f}")
    inflow = replace_labeled_value(inflow, "PropagationDir", 0)
    (case_dir / "InflowWind.dat").write_text(inflow, encoding="utf-8")

    sea = (case_dir / "SeaState.dat").read_text(encoding="utf-8")
    sea = replace_labeled_value(sea, "WaveTMax", f"{max(60.0, tmax):.3f}")
    sea = replace_labeled_value(sea, "WaveHs", f"{float(row['wave_height_used']):.6f}")
    sea = replace_labeled_value(sea, "WaveTp", f"{float(row['wave_period_used']):.6f}")
    sea = replace_labeled_value(sea, "WaveDir", f"{float(row['wave_direction_used']):.6f}")
    sea = replace_labeled_value(sea, "WaveSeed(1)", int(row["wave_seed"]))
    (case_dir / "SeaState.dat").write_text(sea, encoding="utf-8")

    design = {c: float(row[c]) for c in DESIGN_COLUMNS}
    modify_member_properties(case_dir / "NRELOffshrBsline5MW_OC4Jacket_SubDyn.dat", design, hydro=False)
    subdyn = (case_dir / "NRELOffshrBsline5MW_OC4Jacket_SubDyn.dat").read_text(encoding="utf-8")
    subdyn = replace_labeled_value(subdyn, "SumPrint", "False")
    (case_dir / "NRELOffshrBsline5MW_OC4Jacket_SubDyn.dat").write_text(subdyn, encoding="utf-8")
    modify_member_properties(case_dir / "NRELOffshrBsline5MW_OC4Jacket_HydroDyn.dat", design, hydro=True)
    return case_dir


def five_responses(names, data, mask):
    index = {name: i for i, name in enumerate(names)}

    def values(name):
        return data[mask, index[name]].astype(float)

    tower_moment = np.hypot(values("TwrBsMxt"), values("TwrBsMyt"))
    top_displacement = np.hypot(values("TTDspFA"), values("TTDspSS"))
    hydro_force = np.sqrt(values("HydroFxi") ** 2 + values("HydroFyi") ** 2 + values("HydroFzi") ** 2)
    member_channels = ["M5N1FKXe", "M5N1FKYe", "M5N1FKZe", "M6N1FKXe", "M6N1FKYe", "M6N1FKZe"]
    member = np.column_stack([values(c) for c in member_channels])
    member_resultant = np.linalg.norm(member.reshape(len(member), 2, 3), axis=2)
    return {
        "max_tower_base_moment_knm": float(np.max(tower_moment)),
        "max_top_displacement_m": float(np.max(top_displacement)),
        "max_hydrodynamic_force_kn": float(np.max(hydro_force) / 1000.0),
        "max_tracked_member_force_kn": float(np.max(member_resultant) / 1000.0),
        "max_platform_pitch_deg": float(np.max(np.abs(values("PtfmPitch")))),
    }


def analyze(case_dir: Path, row: dict, elapsed: float, return_code: int) -> dict:
    result = {**row, "return_code": return_code, "wall_time_s": elapsed, "success": False}
    out = case_dir / "case.outb"
    if return_code != 0 or not out.exists():
        return result
    from openfast_io import read_openfast_binary
    output = read_openfast_binary(out)
    names = list(output.channel_names)
    times = output.data[:, 0]
    data = output.data
    # R3M5 statistical windows (names align with the local E1 runner)
    windows = {
        "W1_full_0_10": (times >= 0.0) & (times <= 10.0),
        "W1_orig_2_10": (times >= 2.0) & (times <= 10.0),
        "W2_fix_60_70": (times >= 60.0) & (times <= 70.0),
        "W3_600_60_660": (times >= 60.0) & (times <= 660.0),
    }
    # original 10 s DOE caliber (20% tail of a 10 s run) for lineage comparison
    result["success"] = True
    result["output_sha256"] = sha256(out)
    result["output_rows"] = int(len(times))
    result["output_dt_s"] = float(np.median(np.diff(times))) if len(times) > 1 else 0.05
    for key, mask in windows.items():
        if mask.sum() == 0:
            continue
        metrics = five_responses(names, data, mask)
        for name, value in metrics.items():
            result[f"{name}_{key}"] = value
    return result


def main() -> None:
    manifest_path = Path(sys.argv[1])
    # batch files are plain JSON arrays of case rows (or {"cases": [...]} dicts)
    loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = loaded["cases"] if isinstance(loaded, dict) else loaded
    workspace = Path(sys.argv[2])
    exe = Path(sys.argv[3])
    out_path = Path(sys.argv[4])
    shard = int(sys.argv[5]) if len(sys.argv) > 5 and sys.argv[5] else 0
    total_shards = int(sys.argv[6]) if len(sys.argv) > 6 and sys.argv[6] else 1
    tmax = float(sys.argv[7]) if len(sys.argv) > 7 and sys.argv[7] else 660.0
    # E3: after a build-cache hit the openfast/ source is not present; the
    # runner only needs the exe plus the r-test template, so check those.
    if not exe.exists():
        raise SystemExit(f"openfast executable missing: {exe}")
    if not (workspace / "r-test" / "glue-codes" / "openfast" / "5MW_OC4Jckt_DLL_WTurb_WavesIrr_MGrowth").exists():
        raise SystemExit(f"r-test template missing under {workspace} - cannot prepare cases")
    # shard slice: deterministic round-robin for even load
    rows = [r for i, r in enumerate(rows) if i % total_shards == shard]
    # checkpoint dir: per-case result.json inside the workspace, resumable.
    # Lives under r3m5/ so the artifact upload glob (r3m5_checkpoints/**)
    # captures incremental per-case progress even if the job is cancelled.
    checkpoint_dir = workspace / "r3m5" / "r3m5_checkpoints" / manifest_path.stem / f"shard_{shard}"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for row in rows:
        ckpt = checkpoint_dir / f"{row['case_id']}.json"
        if ckpt.exists():
            previous = json.loads(ckpt.read_text(encoding="utf-8"))
            if previous.get("success"):
                results.append(previous)
                print(f"{row['cause_id'] if 'cause_id' in row else row['case_id']}: already done (checkpoint)", flush=True)
                continue
        case_dir = prepare_case(row, workspace, tmax=tmax)
        start = time.perf_counter()
        try:
            process = subprocess.run(
                [str(exe), "case.fst"], cwd=case_dir,
                capture_output=True, text=True, errors="replace", check=False,
                timeout=CASE_TIMEOUT_S)
            return_code = process.returncode
            stdout_text = process.stdout or ""
            stderr_text = process.stderr or ""
        except subprocess.TimeoutExpired:
            return_code = -1
            stdout_text = ""
            stderr_text = f"[runner] case exceeded CASE_TIMEOUT_S={CASE_TIMEOUT_S}s and was killed"
        elapsed = time.perf_counter() - start
        (case_dir / "openfast_stdout.log").write_text(stdout_text, encoding="utf-8")
        result = analyze(case_dir, row, elapsed, return_code)
        if not result["success"]:
            (case_dir / "openfast_stderr.log").write_text(stderr_text, encoding="utf-8")
        ckpt.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
        results.append(result)
        print(f"{row['case_id']}: rc={return_code} ok={result['success']} "
              f"wall={elapsed:.0f}s", flush=True)
    out_path.write_text(json.dumps(results, skipkeys=True, indent=1, default=str), encoding="utf-8")
    ok = sum(1 for r in results if r["success"])
    print(f"campaign shard {shard}: {ok}/{len(results)} succeeded -> {out_path}", flush=True)
    if ok < len(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
