# QAE Independent-Contribution Ablation Supplement

Controlled ablation isolating the probability-estimation module (Meta-reference / MC / NA-QAE)
under matched surrogate, ABC, seeds, budgets, and noise-free final re-evaluation.

## Files

| File | Content |
|---|---|
| QM-1.csv | Engineering-level endpoints summary from the frozen 3 x 30 paired-run chain |
| QM-2.csv | Estimation-level metrics (RMSE, MAE, coverage, decision agreement, false-safe/unsafe, queries) |
| QM-3.csv | Final ablation table (all cells of the manuscript table) |
| QM-4.csv | Full long-format estimation frame (665 designs x 30 reps x 2 arms) |
| QM-5.csv | Per-candidate reference probabilities and design coordinates |

## Claim boundary

Measurement counts are simulated from the classical surrogate probability. This package
attributes the statistical contribution of the QAE estimation module; it does not demonstrate
real-quantum speedup or quantum-hardware advantage.
