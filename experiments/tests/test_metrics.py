import pandas as pd

from qae_abc.evaluation.metrics import summarize_estimates


def test_summarize_estimates_on_pandas_groupby_apply():
    frame = pd.DataFrame(
        {
            "method": ["MC", "MC"],
            "true_probability": [0.1, 0.1],
            "query_budget": [100, 100],
            "estimate": [0.09, 0.11],
            "lower": [0.05, 0.07],
            "upper": [0.13, 0.15],
            "oracle_queries": [100, 100],
            "shots": [100, 100],
            "converged": [True, True],
        }
    )
    summary = summarize_estimates(frame)
    assert summary.loc[0, "n"] == 2
    assert summary.loc[0, "coverage"] == 1.0
    assert abs(summary.loc[0, "bias"]) < 1e-12


def test_summary_does_not_pool_distinct_models():
    frame = pd.DataFrame(
        {
            "model": ["g2", "g3"],
            "method": ["MC", "MC"],
            "true_probability": [0.01, 0.01],
            "query_budget": [100, 100],
            "estimate": [0.0, 0.02],
            "lower": [0.0, 0.0],
            "upper": [0.03, 0.03],
            "oracle_queries": [100, 100],
        }
    )
    summary = summarize_estimates(frame)
    assert set(summary["model"]) == {"g2", "g3"}
    assert len(summary) == 2
