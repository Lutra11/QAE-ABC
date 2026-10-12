# Supplement Data

This directory hosts the publication-facing supplement packages for the QAE-ABC manuscript.
Each subdirectory is a self-contained release with its own `README.md` describing scope, files, and units.

## Packages

| Directory | Content |
|---|---|
| `MF600s/` | Full-chain reconstruction of the response-surrogate, reliability, and optimization chain on 600 s simulations (`FC` prefix, numbered by analytical data flow). |
| `Multi-Site_Value/` | Multi-site transferability under site-specific recalibration: inputs, calibration evidence, and out-of-time (2020–2023) validation for NDBC 44025 / 41025 / 46026. |
| `OpenFAST_Value/` | Cross-platform OpenFAST numerical-reproducibility checks (GNU/Linux Atlas & OpenBLAS, GNU/Windows, Intel/Windows result tables with raw case data). |
| `Foundation_Flexibility/` | Foundation-flexibility sensitivity campaign: 1,200 paired 10 s simulations (400 designs × 3 flexible supports), 24 × 600 s validation runs, and 30 reoptimization runs per support (`FF` prefix). |
| `Quantum_Module_Contribution/` | Controlled ablation isolating the probability-estimation module (Meta-reference / MC / NA-QAE) under matched surrogate, ABC, seeds, budgets, and noise-free final re-evaluation (`QM` prefix). |

## Conventions

- Short data-file names (`FC-*`, `FF-*.csv`, `QM-*.csv`, ...) with per-package `README.md` tables explaining each file's role.
- Masses in tonnes (t), probabilities dimensionless, structural responses in SI units as noted per package.
