# Foundation-Flexibility Sensitivity Supplement

Paired OpenFAST sensitivity campaign quantifying how foundation flexibility alters
the OC4-jacket screening chain: 1,200 paired 10 s simulations (400 paired design
points x 3 flexible support cases), 24 new 600 s validation simulations, and 30
independent reoptimization runs per support case under the frozen five-response
screening limits.

## Scope

Representative coupled-spring foundation-flexibility sensitivity, not geotechnical
capacity verification. Stiffness matrices are the reference matrix scaled by
lambda_K in {2.0, 1.0, 0.5}; the fixed case reuses the frozen campaign data.

## Files

| File | Content |
|---|---|
| FF-1.csv | 21 SubDyn SSI stiffness entries per support case (lambda_K scaled) |
| FF-2.csv | Paired 400-design five-response deltas vs the frozen fixed-base baseline |
| FF-3.csv | 24 new 600 s validation cases (3 supports x 8 environment anchors) |
| FF-4.csv | 90 flexible endpoints + 30 fixed-chain endpoints (mass in tonnes) |
| FF-5.csv | The manuscript table (identical to the LaTeX source) |

## Headline numbers

- Fixed (lambda_K=inf): fixed-design P_f,out=0.001331, beta=3.0042, reoptimized M=546.981 +/- 8.472, median P_f,out=0.000222, feasible=25/30
- Stiff (lambda_K=2.0): fixed-design P_f,out=0.671501, beta=-0.4441, reoptimized M=698.077 +/- 25.057, median P_f,out=0.610613, feasible=0/30
- Reference (lambda_K=1.0): fixed-design P_f,out=0.864864, beta=-1.1024, reoptimized M=589.631 +/- 23.306, median P_f,out=0.865109, feasible=0/30
- Soft (lambda_K=0.5): fixed-design P_f,out=0.958403, beta=-1.7325, reoptimized M=615.013 +/- 25.363, median P_f,out=0.970070, feasible=0/30

## Reproducibility

All runs use OpenFAST 5.0.0 double precision with ModCoupling=1, matching the frozen
DOE byte-for-byte. Input manifests and output SHA-256 hashes are recorded per case
in the campaign stage files; the paired design, validation DOE, and optimization
seeds are fixed in the orchestrator source.

## Units

- Tower-base moment: kNm; displacement: m; hydrodynamic force: kN; member force: kN;
platform pitch: deg; mass: tonnes (t) in FF-4/FF-5, kg in raw parquet files.
