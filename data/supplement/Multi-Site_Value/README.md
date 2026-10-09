# Multi-site transferability under site-specific recalibration

## Purpose

This directory records the inputs, calibration evidence, intermediate data, and final outputs for the manuscript subsection **“Multi-site transferability under site-specific recalibration.”** The assessment covers three target sites: NDBC 44025, NDBC 41025, and NDBC 46026. For each site, data from 2014–2019 are used for site-specific recalibration of the reliability surrogate, while the untouched 2020–2023 period is reserved for out-of-time validation.

## File inventory

| File | Rows | Content |
| --- | ---: | --- |
| `MS-1.csv` | 4 | Site registry, coordinates, temporal partitions, record counts hashes of the input files. |
| `MS-2.csv` | 32 | Environmental summaries for the source and target sites, coverage of the high-fidelity OpenFAST training domain, and operational clipping fractions. |
| `MS-3.csv` | 24 | Independent ERA5–NDBC validation metrics for the three target sites, including uncorrected and quantile-mapped results. |
| `MS-4.csv` | 15 | Five fixed response limits inherited from the source site and baseline structural reliability results for each target site. |
| `MS-5.csv` | 24,576 | The 8,192 environmental samples used for reliability evaluation at each target site. |
| `MS-6.csv` | 3,000 | Member dimensions, structural mass, failure counts, and reliability responses for 1,000 designs at each site. |
| `MS-7.csv` | 48 | Cross-validation and independent-holdout metrics for the candidate reliability surrogate models. |
| `MS-8.csv` | 600 | Predictions for 200 independent holdout designs at each target site. |
| `MS-9.csv` | 3 | Model selection, holdout residuals, conservative threshold factors, and the 30-run optimization configuration. |
| `MS-10.csv` | 93 | Final results for the three baseline designs and 90 independent QAE–ABC runs. |
| `MS-11.csv` | 3,600 | Iteration-level best solutions and cumulative query counts for all 90 independent runs. |
| `MS-12.csv` | 6 | Site-level summary statistics for the baseline design and QAE–ABC results. |
| `MS-13.csv` | 6 | The baseline design and the lightest design passing out-of-time validation at each site. |
| `MS-14.csv` | 262,944 | Complete hourly ERA5/Open-Meteo environmental inputs for the three target sites from 2014 to 2023. |
| `MS-15.csv` | 256,755 | Hourly paired ERA5 and NDBC observations used for independent environmental-data validation. |

## Data flow

```text
MS-14 hourly site records
  -> MS-2 environmental and domain audit
  -> MS-5 sampled environmental states
  -> MS-6 design-level reliability responses
  -> MS-7 model comparison + MS-8 independent holdout predictions
  -> MS-9 residual-based conservative threshold
  -> MS-10 endpoints from 30 runs per site + MS-11 convergence histories
  -> MS-12 summary statistics + MS-13 selected designs

MS-15 paired buoy records -> MS-3 independent ERA5–NDBC validation
MS-4 source-site fixed response limits -> reliability evaluations at all three target sites
```

## Experimental protocol

- The three target sites use the same prescribed OC4 jacket geometry, design-variable bounds, fixed seabed supports, and optimization settings.
- The five structural response limits are fixed at the values calibrated for the source site, as reported in `MS-4.csv`. Site-specific recalibration updates the environmental distribution and reliability surrogate; it does not redefine the structural response limits.
- At each target site, 1,000 designs are used to train and validate the reliability surrogate. Of these, 800 designs are used in five-fold cross-validation and 200 are retained as an independent design holdout set.
- Each design is evaluated using 8,192 site-specific environmental samples.
- QAE–ABC is run independently 30 times at each target site, giving 90 optimization runs in total. Each run uses a distinct random seed. The seeds are reported in `MS-10.csv` and `MS-11.csv`.
- The period 2014–2019 is used for fitting. The 2020–2023 out-of-time data are evaluated only after optimization and are not used to fit the reliability surrogate or search for candidate designs.
- Optimization uses the conservative probability threshold obtained from independent-holdout residuals in `MS-9.csv`. Final feasibility is evaluated on the out-of-time data using the nominal target failure probability of `0.001349898031630093`, corresponding to `beta = 3`.
- NDBC observations provide an independent check of the environmental inputs. The quantile-mapping results in `MS-3.csv` are diagnostic only. All structural calculations use uncorrected ERA5/Open-Meteo data to maintain a consistent input definition across sites.

## Conventions and units

- `time`: UTC in ISO 8601 format.
- `D_*` and `t_*`: m.
- `mass_kg`, `structural_mass_kg`, and `best_mass_kg`: kg.
- `U100`, `U10`, `WSPD`, `WSPD_10m`, `u100`, `v100`, `u10`, and `v10`: m/s.
- `Hs` and `WVHT`: m.
- `Tp`, `Tm`, `DPD`, and `APD`: s.
- Wind directions, wave directions, and relative-direction angles: degrees.
- Probabilities, feasibility rates, and coverage fractions are dimensionless values between 0 and 1. `beta` is a dimensionless reliability index.
- `split=fit` denotes 2014–2019, and `split=temporal_test` denotes 2020–2023.
- Boolean fields use `True` and `False`. Missing values are left blank rather than replaced with zero.
- `original_oc4` denotes the prescribed baseline design, and `na_qae_abc` denotes the QAE–ABC configuration used in this study.

## Interpretation boundary

These data assess whether the computational workflow can be transferred across different wind–wave environments after **site-specific recalibration**. They do not support direct, calibration-free extrapolation of a probability model from one site to another, nor do they establish universal validity across offshore environments. Application to a new site requires a new environmental model, retraining of the reliability surrogate, and a new assessment of coverage relative to the response-surrogate training domain. The reported conclusions remain conditional on the prescribed geometry, fixed seabed supports, and operational limit states considered in this study.

