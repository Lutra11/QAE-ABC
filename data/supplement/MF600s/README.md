# Full-Chain Reconstruction (600 s) — Supplement Data

This directory contains the compact, publication-facing data release for the complete
600 s reconstruction of the response-surrogate, reliability and optimization chain.
The `FC` prefix means **Full Chain**. Numbering follows the analytical data flow and is
independent of the experiment numbers used in the manuscript.

## Files

| File | Role in the evidence chain | Rows |
|---|---|---:|
| `FC-1_response_reconstruction.csv` | Response-level evidence joining the paired 10 s/600 s bias test, rank fidelity, startup-window increment, 600 s surrogate validation, LOCO audit and recalibrated response limits. | 5 |
| `FC-2_multiseed_600s_runs.csv` | Canonical OpenFAST campaign: four structural designs, 20 design-environment cells and 60 wave seeds per cell (1,200 successful 600 s runs). All four analysis windows are retained. | 1,200 |
| `FC-3_environment_samples.csv` | The 8,192 environmental samples used by the reliability chain. Headers include units and distinguish scalar wind speeds from vector components. | 8,192 |
| `FC-4_reliability_training.csv` | Final 4,137-design reliability-metamodel dataset joined to the 828 independent-holdout predictions. `data_split` identifies training and holdout rows. | 4,137 |
| `FC-5_reliability_model_metrics.csv` | Cross-validation and independent-holdout metrics for the three metamodel development stages. | 48 |
| `FC-6_optimization_runs.csv` | All optimization runs from the three reconstruction stages and the safety-factor sensitivity campaign. `selected_candidate=True` marks the candidate chosen from each method/campaign. | 514 |
| `FC-7_final_design_verification.csv` | Independent verification of the selected design at eight representative environmental-envelope conditions and three wave seeds per condition. | 24 |
|                                      |                                                              |       |

## Data flow

`FC-2` establishes the 600 s response basis. `FC-1` summarizes the diagnostic and
response-surrogate evidence obtained from that basis. `FC-3` supplies the environmental
sample, while `FC-4` and `FC-5` document reliability-metamodel construction and testing.
`FC-6` records the optimization campaigns, `FC-7` independently verifies the selected
design, and `FC-8` provides a compact trace from the released data to the manuscript tables.

## Conventions

- CSV files are UTF-8, comma-separated, with one header row.
- Each row is one observation or one reported statistic; units appear in column names or
  in the `unit` field of `FC-8`.
- `W1_full_0_10` and `W1_orig_2_10` denote the two 10 s windows;
  `W2_fix_60_70` is the post-start 10 s control window; and
  `W3_600_60_660` is the 600 s production window.
- `FC-2` contains 1,200 independent design-environment-seed configurations. Earlier
  cross-platform repeats are not counted as additional independent seeds.
- The eight conditions in `FC-7` are representative envelope conditions (`calm`, `low`,
  `mid`, `high`, `harsh`, `max_wind`, `max_hs`, and `max_angle`), not all mathematical
  corners of the environmental hypercube.
- Intermediate version-specific extracts and one-table-per-method files are intentionally
  omitted because their information is consolidated in the FC tables above.
