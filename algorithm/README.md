# Algorithms and models

`qae_abc/` contains the computational package. Python module names and imports are unchanged.

| Module | Responsibility |
| --- | --- |
| optimization/abc.py | Bee-colony search, candidate caching, and confidence-aware ranking |
| quantum/likelihood.py | Amplitude counts, likelihood estimation, noise fitting, and intervals |
| quantum/functional_oracle.py | Functional reversible failure Oracle |
| reliability/ | Analytic, nonlinear, and OC4 reliability models |
| structural/truss.py | 10-bar truss finite elements |
| surrogate/ | Truss surrogates |
| data/openfast_io.py | OpenFAST binary-output reading |
| evaluation/ | Evaluation metrics |
| figures/style.py | Scientific figure styling |

## Saved models

These are serialized `.joblib` files, not Python scripts. Only filenames are shortened; binary contents are unchanged.

| Current path | Previous filename | Purpose |
| --- | --- | --- |
| [models/oc4_optimization/reliability.joblib](models/oc4_optimization/reliability.joblib) | oc4_reliability_meta_models_final_400.joblib | OC4 reliability metamodel |
| [models/oc4_surrogates/response.joblib](models/oc4_surrogates/response.joblib) | oc4_multifidelity_models_final_400.joblib | OC4 multifidelity response models |
| [models/truss_surrogate/hgbr.joblib](models/truss_surrogate/hgbr.joblib) | reference_hgbr.joblib | Truss reference gradient-boosting model |
| [models/truss_surrogate/pce.joblib](models/truss_surrogate/pce.joblib) | sparse_pce.joblib | Sparse polynomial-chaos truss model |

The OC4 bundles retain their final-400-sample provenance, documented here instead of repeated in their filenames. Historical experiment defaults under `results/raw` refer to different locations and retain their original names. When adapting a loader to this curated package, pass the current model path explicitly.

Load only trusted joblib files, using compatible library versions and original package classes. See the main README for execution-layout limitations.
