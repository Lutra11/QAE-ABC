# Full-Chain Reconstruction (600 s) — Supplement Data

This directory contains the compact, publication-facing data release for the complete
600 s reconstruction of the response-surrogate, reliability and optimization chain.
The `FC` prefix means **Full Chain**. Numbering follows the analytical data flow and is
independent of the experiment numbers used in the manuscript.

## Files

| File | Role in the evidence chain | Rows |
|---|---|---:|
| `FC-1.csv` | Response-level evidence joining the paired 10 s/600 s bias test, rank fidelity, startup-window increment, 600 s surrogate validation, LOCO audit and recalibrated response limits. | 5 |
| `FC-2.csv` | Canonical OpenFAST campaign: four structural designs, 20 design-environment cells and 60 wave seeds per cell (1,200 successful 600 s runs). All four analysis windows and the direct design-dependent representative-member DCR are retained. | 1,200 |
| `FC-3.csv` | The 8,192 environmental samples used by the reliability chain. Headers include units and distinguish scalar wind speeds from vector components. | 8,192 |
| `FC-4.csv` | Final 4,137-design reliability-metamodel dataset joined to the 828 independent-holdout predictions. It includes the conservative-envelope DCR screen and the resulting combined feasibility flag. | 4,137 |
| `FC-5.csv` | Cross-validation and independent-holdout metrics for the three metamodel development stages. | 48 |
| `FC-6.csv` | All 514 optimization records, augmented by the conservative-envelope DCR screen and combined temporal/DCR feasibility. `selected_candidate=True` marks the candidate chosen from each method/campaign. | 514 |
| `FC-7.csv` | Independent 600 s verification of the selected design at eight representative environmental-envelope conditions and three wave seeds per condition, including direct representative-member DCR. | 24 |
| `FC-8.csv` | Demand--capacity audit. It combines 1,224 direct 600 s response records, 200 component-level member checks, and a conservative force-envelope reassessment of all 514 recorded optimization candidates. | 1,938 |

## Data flow

`FC-2` establishes the 600 s response basis. `FC-1` summarizes the diagnostic and
response-surrogate evidence obtained from that basis. `FC-3` supplies the environmental
sample, while `FC-4` and `FC-5` document reliability-metamodel construction and testing.
`FC-6` records the optimization campaigns, `FC-7` independently verifies the selected
design, and `FC-8` verifies that the reported mass-reduction result remains feasible after
introducing a design-dependent representative-member demand--capacity constraint.

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
- In `FC-2` and `FC-7`, `representative_member_dcr` uses the observed 600 s
  tracked-member force and a resistance recalculated from each row's `D_L1` and `t_L1`;
  `dcr_feasible` is true when this ratio does not exceed one.
- In `FC-4` and `FC-6`, `conservative_member_dcr` uses the common 1,614.066 kN force
  envelope without crediting load-attraction reductions. `combined_feasible` in `FC-4`
  and `combined_temporal_feasible` in `FC-6` require both the original probability
  criterion and the DCR criterion to pass.
- Intermediate version-specific extracts and one-table-per-method files are intentionally
  omitted because their information is consolidated in the FC tables above.
- In `FC-8`, the direct and component-validation rows retain the observed 600 s demand.
  The optimization-candidate rows apply the largest tracked-member force observed across
  the completed 600 s campaigns to every candidate. This common demand envelope is a
  deliberately conservative reassessment: reductions in hydrodynamic attraction are not
  credited, while the tubular-member resistance changes with each candidate's diameter
  and thickness. `dcr_feasible` denotes a demand--capacity ratio not exceeding one.
- The component-level check in `FC-8` is a representative-member strength screen rather
  than full jacket certification; fatigue, local shell buckling, joints, foundations and
  soil--structure interaction remain outside its scope.
