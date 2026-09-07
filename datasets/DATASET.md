# QAE-ABC Datasets

This directory contains the data used to connect metocean characterization, structural response modeling, and reliability-constrained optimization in QAE-ABC. It is a collection of related datasets, not a single independent benchmark table.

## 1. Download and installation

**Fixed dataset download link:** [Download the QAE-ABC datasets from Google Drive](https://drive.google.com/drive/folders/1Md0APmG8a8DiTAL7MSG1-5Nc7lLaEEDF)

This is the project owner's designated Google Drive folder, not a direct single-file download URL.

1. Open the link and download the dataset files or folder using Google Drive.
2. If Drive creates a ZIP archive, extract it.
3. Place the extracted contents under `git-content/datasets/`, keeping `raw/`, `processed/`, `Truss10/`, and `OC4/` at the level shown below.
4. Avoid an accidental extra level such as `datasets/datasets/OC4/`.
5. Run the loading and validation examples in Section 6.

If Drive requests access, use the access-request option or contact the folder owner. Access permissions and the remote file inventory were not verified during this documentation update. The inventory and counts below were checked against the **local dataset snapshot on 2026-09-07**; they are not a checksum certification of the remote folder.

## 2. Dataset overview

| Component | Source / construction | Size in the local snapshot | Main use |
| --- | --- | --- | --- |
| Raw environmental cache | Open-Meteo reanalysis responses and NDBC station records | 105 compressed JSON files and 11 compressed text files | Trace source downloads and preprocessing |
| North Sea environment | Reanalysis at 54.0 degrees N, 6.5 degrees E, 2000–2024 | 219,168 hourly rows; 218,136 complete-core rows | Fit environmental distributions and provide engineering inputs |
| Observation-validation data | NDBC 44025 and reanalysis near 40.258 degrees N, 73.175 degrees W, 2014–2023 | 87,648 reanalysis rows; 81,459 buoy rows and paired rows | Check preprocessing and distribution agreement |
| 10-bar truss | Finite-element DOE with four grouped cross-sectional areas and three uncertain inputs | 800 designs x 10 realizations = 8,000 rows | Surrogate training, validation and structural transfer |
| OC4 response data | Low-fidelity approximations and coupled OpenFAST screening simulations | 5,000 LF cases; 300 initial + 100 active-learning HF cases | Multifidelity response modeling |
| OC4 reliability labels | Response-surrogate evaluation over sampled historical environments | 1,500 designs; 8,192 environmental samples per design | Design-level reliability metamodeling |
| OC4 final dynamic checks | Four selected designs x three wave seeds | 12 runs of 600 s | Limited post-optimization dynamic validation |

There are **18 Parquet files** in this snapshot. Two pairs are byte-identical aliases; several other files intentionally overlap, such as the combined 400-case HF table and its 300/100-case components. Do not sum every file's row count to estimate the number of independent experiments.

Input and sample data belong here. Aggregated experimental tables are in [data/](../data/), figures in [images/](../images/README.md), and serialized models in [algorithm/models](../algorithm/README.md).

## 3. Directory structure

```text
datasets/
├── DATASET.md
├── raw/
│   └── metocean/
│       ├── open_meteo_era5_north_sea/
│       │   └── YYYY_{wave,wind_direction,wind_speed}.json.gz
│       │       # 2000–2024: 75 cached files
│       ├── open_meteo_era5_ndbc44025/
│       │   └── YYYY_{wave,wind_direction,wind_speed}.json.gz
│       │       # 2014–2023: 30 cached files
│       └── ndbc_44025/
│           └── 44025hYYYY.txt.gz
│               # 2014–2024: 11 cached files
├── processed/
│   └── metocean/
│       ├── era5_open_meteo_54N_6.5E_2000_2024.parquet
│       ├── era5_open_meteo_40.258N_-73.175E_2014_2023.parquet
│       ├── ndbc_44025_hourly_2014_2023.parquet
│       └── era5_ndbc_44025_paired_2014_2023.parquet
├── Truss10/
│   └── truss_doe_full.parquet
└── OC4/
    ├── oc4_lf_results_5000.parquet
    ├── oc4_hf_doe_300.parquet
    ├── oc4_hf_results_300.parquet
    ├── oc4_active_learning_doe_100.parquet
    ├── oc4_hf_doe_active100.parquet
    ├── oc4_hf_results_active100.parquet
    ├── oc4_hf_results_final_400.parquet
    ├── oc4_meta_environment_final_400.parquet
    ├── oc4_reliability_meta_dataset_final_400.parquet
    ├── oc4_reliability_meta_holdout_final_400.parquet
    ├── oc4_final_validation_doe_12.parquet
    ├── oc4_hf_doe_final600.parquet
    └── oc4_hf_results_final600.parquet
```

`YYYY` and the brace notation above describe filename patterns, not literal filenames. The NDBC raw cache includes 2024; the retained processed observation-validation tables cover **2014–2023 only**.

### File inventory

Paths in this table are relative to `datasets/`.

| File | Stored rows | Description |
| --- | ---: | --- |
| `processed/metocean/era5_open_meteo_54N_6.5E_2000_2024.parquet` | 219,168 | Complete hourly grid, including rows with missing core variables |
| `processed/metocean/era5_open_meteo_40.258N_-73.175E_2014_2023.parquet` | 87,648 | Reanalysis at the observation-validation location |
| `processed/metocean/ndbc_44025_hourly_2014_2023.parquet` | 81,459 | Processed hourly buoy observations |
| `processed/metocean/era5_ndbc_44025_paired_2014_2023.parquet` | 81,459 | Time-paired buoy/reanalysis records; usable counts depend on the compared variables |
| `Truss10/truss_doe_full.parquet` | 8,000 | Design-realization samples with stored train/validation/test labels |
| `OC4/oc4_lf_results_5000.parquet` | 5,000 | LF DOE inputs and approximate responses |
| `OC4/oc4_hf_doe_300.parquet` | 300 | Initial HF simulation inputs |
| `OC4/oc4_hf_results_300.parquet` | 300 | Successful initial HF results |
| `OC4/oc4_active_learning_doe_100.parquet` | 100 | Selected active-learning inputs and selection metadata |
| `OC4/oc4_hf_doe_active100.parquet` | 100 | Byte-identical alias of the preceding active-learning DOE file |
| `OC4/oc4_hf_results_active100.parquet` | 100 | Successful added HF results |
| `OC4/oc4_hf_results_final_400.parquet` | 400 | Combined initial and added HF results; preferred response-data entry point |
| `OC4/oc4_meta_environment_final_400.parquet` | 8,192 | Historical fit-period environmental rows used to construct reliability labels |
| `OC4/oc4_reliability_meta_dataset_final_400.parquet` | 1,500 | Design geometry, mass, failure counts and probability labels |
| `OC4/oc4_reliability_meta_holdout_final_400.parquet` | 1,500 | Design-indexed predictions; each holdout prediction column has only 300 non-null entries |
| `OC4/oc4_final_validation_doe_12.parquet` | 12 | Final design/seed validation inputs |
| `OC4/oc4_hf_doe_final600.parquet` | 12 | Byte-identical alias of the preceding final-validation DOE file |
| `OC4/oc4_hf_results_final600.parquet` | 12 | Successful 600 s validation summaries, not the complete time histories |

## 4. Variables and preprocessing

### 4.1 Environmental records

| Column / group | Meaning | Unit / convention |
| --- | --- | --- |
| `time` | Hourly timestamp | UTC in the North Sea Parquet table |
| `U100`, `U10` | Wind speed at 100 m and 10 m | m/s |
| `Hs` | Significant wave height | m |
| `Tp`, `Tm` | Peak and mean wave periods | s; distinct quantities |
| `wind_direction_100m`, `wind_direction_10m`, `wave_direction` | Wind and wave directions | degrees |
| `wind_wave_angle` | Circular relative wind-wave direction | degrees |
| `u100`, `v100`, `u10`, `v10` | Derived wind-vector components | m/s |
| `complete_core` | Completeness of U100, Hs, Tp and the two required directions | Boolean |
| `year`, `split` | Year and stored temporal-partition label | `fit` or `temporal_test` |
| `WSPD`, `WSPD_10m` | Buoy wind speed and its 10 m-adjusted counterpart | m/s; do not confuse with U100 |
| `WVHT`, `DPD`, `APD` | Buoy wave height, dominant period and average period | m, s, s |
| `WDIR`, `MWD` | Buoy wind and mean wave directions | degrees |

The preprocessing script assembles annual downloads, checks duplicate timestamps, reindexes to an hourly grid, derives wind components and relative direction, flags physically invalid speeds/heights/periods as missing, and creates completeness and split labels. Missing values are retained for auditing; they are not zero-valued environmental observations. See `download_open_meteo_era5.py` and `download_analyze_ndbc_validation.py` in the original project (these preprocessing helpers are not included in the current publication snapshot) for implementation details.

For variable-specific buoy comparisons, remove missing values only from the variables being compared. The 81,459 time-paired rows are not 81,459 valid measurements for every variable pair. Also, the generic reanalysis `split` column is not a substitute for the observation-validation protocol; inspect `period` in the paired table and the validation script.

### 4.2 Truss samples

One row represents one uncertain realization of a structural design. `design_id` identifies the design, and `sample_id` identifies the realization.

- `area_horizontal`, `area_vertical`, `area_diag_up`, `area_diag_down`: four grouped cross-sectional areas, in square metres.
- `z_load`, `z_yield`, `z_modulus`: standardized uncertain inputs for load, yield strength and elastic modulus.
- `nominal_stress_ratio`, `nominal_displacement_ratio`: physics-derived nominal response ratios.
- `mass_kg`: structural mass.
- `g_stress`, `g_displacement`: dimensionless limit-state values; `g_system` is their minimum. The structural implementation uses `g <= 0` for failure.
- `split`: stored design-grouped partition.

The existing surrogate feature order is four area columns, two nominal ratios, then three uncertainty columns. Use [truss_surrogates.py](../algorithm/qae_abc/surrogate/truss_surrogates.py) when reproducing its models.

### 4.3 OC4 inputs, responses and reliability labels

`case_id` is the simulation join key. `D_L1`, `D_L2`, `D_B1`, and `D_B2` are grouped member diameters; matching `t_*` columns are wall thicknesses. Geometry uses metres. Environmental records are traced through `metocean_time`; `wave_seed` records the wave realization.

Keep historical `U100/Hs/Tp/wind_wave_angle` separate from `wind_speed_used/wave_height_used/wave_period_used/wave_direction_used`: the latter record the inputs actually applied by the simulation workflow. Do not silently substitute one set for the other.

The seven HF response targets are:

| Column | Quantity | Unit |
| --- | --- | --- |
| `max_tower_base_moment_knm` | Maximum tower-base moment | kN m |
| `tower_base_moment_del4_knm` | Tower-base moment damage-equivalent load, exponent 4 | kN m |
| `max_top_displacement_m` | Maximum top displacement | m |
| `max_hydrodynamic_force_kn` | Maximum hydrodynamic force | kN |
| `max_tracked_member_force_kn` | Maximum tracked-member force | kN |
| `mean_generator_power_kw` | Mean generator power | kW |
| `max_platform_pitch_deg` | Maximum platform pitch | degrees |

Columns beginning with `lf_` are low-fidelity quantities, not HF measurements. The training script recomputes LF features and direction sine/cosine features; using every stored numeric column as an input would mix predictors, responses and execution metadata. See [E8-surrogate.py](../experiments/source/E8-surrogate.py).

Execution metadata includes `success`, `return_code`, `wall_time_s`, `output_rows`, `analysis_start_s`, and `output_sha256`. Some batch-specific columns are absent or null in other batches. Do not apply a blanket `dropna()` across the combined 400-case table.

For the design-level reliability dataset:

- `failure_count` is the number of surrogate-evaluated system exceedances among `environment_samples`.
- `empirical_probability = failure_count / environment_samples`.
- `smoothed_probability = (failure_count + 0.5) / (environment_samples + 1)`.
- `system_ratio_quantile` summarizes a normalized system-response ratio.
- `empirical_feasible` records the study threshold comparison; retain the original capacity/threshold definitions when reproducing it.

The holdout prediction columns beginning with `logit_probability__` are on the **logit scale**, not the probability scale. Their null values outside the held-out designs are intentional. See [E9-reliability.py](../experiments/source/E9-reliability.py).

## 5. Splits and leakage prevention

| Level | Stored / study partition | Required handling |
| --- | --- | --- |
| North Sea environment | Fit: 2000–2019, 175,320 rows, all complete; temporal test: 2020–2024, 43,848 rows, 42,816 complete | Filter completeness and retain chronology; do not randomly mix years for temporal evaluation |
| Truss | Train: 560 designs / 5,600 rows; validation: 119 / 1,190; test: 121 / 1,210 | Preserve `split`; keep all realizations of each design together |
| OC4 response surrogate | Study evaluation: 340 training cases and 60 frozen holdout cases | Reuse the original frozen case IDs; the combined response table itself has no split column |
| OC4 reliability metamodel | 1,200 training designs and 300 holdout designs | Recover held-out IDs from non-null holdout predictions and join on `design_id` |
| Final dynamic validation | 12 separate 600 s runs | Keep separate from the short-window response-training campaign |

The response-surrogate holdout-ID JSON is not included in this dataset directory. Exact reproduction of the reported 60-case split requires that artifact from the original project; inventing a new split is a new evaluation, not reproduction. Deployment models were subsequently refit, so loading a final model bundle alone does not recreate the original held-out evaluation.

## 6. Usage examples

### 6.1 Minimal setup and schema inspection

The examples below run from the **`git-content/` directory** and only read data. A minimal Parquet-reading environment needs pandas and pyarrow:

```powershell
Set-Location C:\QAE-ABC\git-content
python -m pip install pandas pyarrow
```

This is a minimal reading setup, not a version-locked reproduction environment. Full experiment dependencies are listed in [requirements.txt](../requirements.txt).

```python
from pathlib import Path
import pandas as pd
import pyarrow.parquet as pq

DATASETS = Path("datasets")
assert (DATASETS / "DATASET.md").exists(), "Run from git-content/"
files = sorted(DATASETS.rglob("*.parquet"))
assert len(files) == 18, "Inventory differs from the documented local snapshot"
for path in files:
    parquet = pq.ParquetFile(path)
    print(path.relative_to(DATASETS), parquet.metadata.num_rows)
    print(parquet.schema_arrow)
```

### 6.2 Load complete North Sea data and retain the temporal split

```python
environment = pd.read_parquet(
    DATASETS / "processed/metocean/era5_open_meteo_54N_6.5E_2000_2024.parquet"
)
environment["time"] = pd.to_datetime(environment["time"], utc=True)
assert not environment["time"].duplicated().any()

core = ["U100", "Hs", "Tp", "wind_wave_angle"]
usable = environment.loc[environment["complete_core"].eq(True)]
usable = usable.dropna(subset=core)
fit = usable.loc[usable["split"].eq("fit")].copy()
temporal_test = usable.loc[usable["split"].eq("temporal_test")].copy()
assert len(environment) == 219168
assert (len(fit), len(temporal_test)) == (175320, 42816)
print(fit[core].describe())
```

Fit transformations or models using the fit partition; apply them to the temporal test without refitting on that test. Historical-row sampling retains the observed joint environment; it is not automatically equivalent to sampling a fitted copula.

### 6.3 Read truss features without mixing design groups

```python
truss = pd.read_parquet(DATASETS / "Truss10/truss_doe_full.parquet")
features = [
    "area_horizontal", "area_vertical", "area_diag_up", "area_diag_down",
    "nominal_stress_ratio", "nominal_displacement_ratio",
    "z_load", "z_yield", "z_modulus",
]
assert truss.groupby("design_id")["split"].nunique().eq(1).all()
train = truss.loc[truss["split"].eq("train")]
validation = truss.loc[truss["split"].eq("validation")]
test = truss.loc[truss["split"].eq("test")]
assert (len(train), len(validation), len(test)) == (5600, 1190, 1210)
X_train = train[features]
y_train = train[["g_stress", "g_displacement"]]
test_failure_labels = test["g_system"].le(0)
print(X_train.shape, y_train.shape)
```

### 6.4 Load the combined HF table and separate dynamic validation

```python
hf = pd.read_parquet(DATASETS / "OC4/oc4_hf_results_final_400.parquet")
assert len(hf) == 400 and hf["case_id"].is_unique
assert hf["success"].eq(True).all()
targets = [
    "max_tower_base_moment_knm", "tower_base_moment_del4_knm",
    "max_top_displacement_m", "max_hydrodynamic_force_kn",
    "max_tracked_member_force_kn", "mean_generator_power_kw",
    "max_platform_pitch_deg",
]
assert hf[targets].notna().all().all()
print(hf[["case_id", "structural_mass_kg"] + targets].head())

final_runs = pd.read_parquet(
    DATASETS / "OC4/oc4_hf_results_final600.parquet"
)
assert len(final_runs) == 12 and final_runs["success"].eq(True).all()
print(final_runs.groupby("source_method").size())
```

Use either the combined 400-case table **or** its 300/100-case components, not both. Join DOE and results by `case_id`, with one-to-one validation, rather than by row position. Keep final validation separate; the first 120 s of each 600 s run were excluded from response analysis.

### 6.5 Recover reliability training and holdout designs

```python
meta = pd.read_parquet(
    DATASETS / "OC4/oc4_reliability_meta_dataset_final_400.parquet"
)
predictions = pd.read_parquet(
    DATASETS / "OC4/oc4_reliability_meta_holdout_final_400.parquet"
)
column = "logit_probability__selected_holdout"
held_out = predictions.loc[predictions[column].notna(), ["design_id", column]]
assert meta["design_id"].is_unique and held_out["design_id"].is_unique
meta_test = meta.merge(held_out, on="design_id", validate="one_to_one")
meta_train = meta.loc[~meta["design_id"].isin(held_out["design_id"])].copy()
assert (len(meta_train), len(meta_test)) == (1200, 300)
assert meta["environment_samples"].eq(8192).all()
print(meta_test[["design_id", "empirical_probability", column]].head())
```

Use geometry as design-level predictors and the appropriate reliability quantity as the target. Failure counts, feasibility labels, and probability/response summaries are not independent design inputs.

## 7. Scope, limitations and provenance

- NDBC 44025 is near Long Island, New York, **not colocated with the North Sea engineering site**. Its comparison supports processing/distribution checks, not local validation of North Sea weather.
- The 400 HF cases are **10 s screening runs**. Their DEL values do not establish certification-level fatigue performance.
- The 12 runs of 600 s are limited dynamic spot checks, not all design load cases or a complete certification campaign.
- The 1,500 x 8,192 combinations are **surrogate evaluations**, not 12,288,000 OpenFAST runs. Their probabilities describe modeled system-response exceedance under the study's operating-environment and calibrated-capacity definitions, not directly measured annual physical failure probabilities.
- This directory contains tabular samples and response summaries, not all raw OpenFAST time histories, executable binaries, frozen split manifests, capacity reports or calibration artifacts.
- The scripts under [experiments/](../experiments/README.md) expect the original project's runtime layout. Reading these datasets works independently; rerunning the entire study requires the original inputs, tools, configuration and path contract.
- Raw caches preserve downloaded source material. Check source-specific attribution and redistribution terms before public release. This document does not assign a new license to third-party data.
- The Google Drive folder is a distribution location, not a DOI or immutable version identifier. For a publication release, record the downloaded version/date and file hashes; do not assume unchanged contents solely because the folder URL is fixed.

All counts and examples above describe the retained local snapshot. This documentation update does not modify, regenerate or upload any dataset.
