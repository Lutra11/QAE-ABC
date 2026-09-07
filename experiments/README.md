# Main experiments

The publication package retains **13 main experiment entry points**, named `E<number>-<topic>.py`. The numbering is a navigation aid, not a new experiment-completion count or the numbering of earlier manuscripts.

## Main entry points

| ID | Script | Purpose | Original script |
| --- | --- | --- | --- |
| E1 | [E1-analytic.py](source/E1-analytic.py) | Analytic probability-estimator benchmark | run_analytic.py |
| E2 | [E2-nonlinear.py](source/E2-nonlinear.py) | Nonlinear reliability-estimator comparison | run_nonlinear_estimators.py |
| E3 | [E3-circuit.py](source/E3-circuit.py) | Functional-circuit QAE experiment | run_functional_qae.py |
| E4 | [E4-noise.py](source/E4-noise.py) | Calibration-snapshot noise simulation | run_fake_backend_noise.py |
| E5 | [E5-truss.py](source/E5-truss.py) | Main truss optimizer comparison | run_truss_optimization.py |
| E6 | [E6-ablation.py](source/E6-ablation.py) | Confidence, refinement and lookup ablations | run_truss_ablations.py |
| E7 | [E7-sensitivity.py](source/E7-sensitivity.py) | QAE confidence, budget, noise and depth sensitivity | run_qae_sensitivity.py |
| E8 | [E8-surrogate.py](source/E8-surrogate.py) | OC4 multifidelity response-surrogate training | train_oc4_multifidelity_surrogates.py |
| E9 | [E9-reliability.py](source/E9-reliability.py) | OC4 design-level reliability labels and metamodels | prepare_oc4_reliability_meta.py |
| E10 | [E10-oc4.py](source/E10-oc4.py) | OC4 reliability-constrained optimization | run_oc4_meta_optimization.py |
| E11 | [E11-estimation.py](source/E11-estimation.py) | Estimator comparison for selected OC4 designs | run_oc4_reliability_estimators.py |
| E12 | [E12-oracle.py](source/E12-oracle.py) | OC4 functional-oracle approximation and resources | run_oc4_functional_oracle.py |
| E13 | [E13-validation.py](source/E13-validation.py) | Analyze the existing 600 s OpenFAST validation runs | analyze_oc4_final_validation.py |

The four evidence modules remain: probability estimation (E1–E2), quantum implementation (E3–E4 and E12), truss optimization (E5–E7), and engineering transfer (E8–E13). E8 supplies response models to E9, E9 supports E10, and the selected designs support E11–E13. E13 analyzes an existing campaign; it does not launch OpenFAST.

## Directory structure

```text
experiments/
├── README.md
├── source/       # Only the 13 numbered main experiment scripts
├── configs/      # Retained study configurations
└── tests/        # Computational tests and local import setup
```

### Supporting code

The following six helpers were separated from the main scripts during cleanup. The current publication snapshot no longer includes `common/`; obtain these helpers from the original project before attempting workflows that depend on them:

| Helper | Role |
| --- | --- |
| `run_openfast_oc4_doe.py` | Shared OC4 geometry and OpenFAST campaign execution |
| `run_oc4_low_fidelity_doe.py` | LF response calculation used by E8 and the OC4 tests |
| `run_truss_surrogates.py` | Prepare the truss response models used by E3/E5 |
| `run_nonlinear_reference.py` | Prepare nonlinear reference probabilities |
| `download_open_meteo_era5.py` | Environmental download and preprocessing |
| `download_analyze_ndbc_validation.py` | Observation download and validation |

The legacy `prepare_workspace.py` and its required full archive are also outside this publication snapshot.

## Use and execution boundaries

From `git-content/`, inspect an argument-enabled entry point, for example:

```powershell
python experiments/source/E1-analytic.py --help
```

The source files contain the experiment implementations, not empty wrappers. Their import setup searches `algorithm/`, `common/`, and `source/`, but the shared `common/` modules are absent from this snapshot. E8, E9, E10 and the OC4 tests therefore need their original shared dependencies before they can be imported or run. Hyphenated filenames are command-line entry points; Python cannot use them in an ordinary `from E5-truss import ...` statement. The E6 dependency on E5 uses `importlib.import_module`, and the OC4 test imports E8 the same way.

This cleanup preserves numerical methods, experiment settings, and historical input/output defaults. Those defaults were written for the original `src/data/results/external` workspace: some now point to locations absent from this reduced package. Reproduction still requires the original inputs, frozen splits, model metadata, OpenFAST tools and appropriate path configuration. No complete runnable workspace is implied by renaming the files.

Read the script before executing it. Some entry points do not parse command-line arguments and can start a full calculation when invoked; `--help` is not a universal dry run. E13 writes validation summaries when run.

For computational unit tests, first provide the original shared helpers and required external model inputs in addition to the Python dependencies:

```powershell
python -m pytest experiments/tests -q -p no:cacheprovider
```

Historical verification with the shared helpers present produced 34 passes and one missing-model failure; supplying the original OpenFAST SubDyn model read-only gave 35 passes, and all 13 numbered imports passed at that stage. The subsequent removal of `common/` means those results do not certify the current clone as self-contained. Restore the required helpers and configure `SOURCE_CASE` before repeating that check.

See [DATASET.md](../datasets/DATASET.md) for downloadable samples, schemas and read-only examples, and the [algorithm guide](../algorithm/README.md) for current model filenames. E4 is calibrated-noise simulation, not real-QPU execution; E13 is limited dynamic validation, not a certification campaign.

## What was removed from the publication source directory?

The original 50 scripts were first reduced to 13 main scripts plus six separated helpers; the current publication snapshot retains only the 13 main scripts and excludes the helper directory. The remaining 31 scripts—additional comparisons, separate statistical/plotting scripts, access audits, report generators and orchestration utilities—were moved out of `git-content/`. They remain recoverable in the local backup at `C:/QAE-ABC/outputs/experiment_source_simplification_20260907/`, which includes the complete pre-cleanup experiment directory and a move audit.

This selection simplifies navigation; it does not claim that the omitted experiments were never performed or that their results are invalid. Existing result tables, figures, datasets and model contents were not changed. Full historical reproduction and report rebuilding should use the original complete project.
