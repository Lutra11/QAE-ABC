"""Metric aggregation without hiding failed or non-finite runs."""

from __future__ import annotations

import numpy as np
import pandas as pd


def summarize_estimates(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"method", "true_probability", "query_budget", "estimate", "lower", "upper"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")
    work = frame.copy()
    work["error"] = work["estimate"] - work["true_probability"]
    work["squared_error"] = work["error"] ** 2
    work["covered"] = (work["lower"] <= work["true_probability"]) & (
        work["true_probability"] <= work["upper"]
    )
    work["interval_width"] = work["upper"] - work["lower"]
    # Pandas 3 excludes grouping columns from ``apply`` by default.  Preserve
    # the reference value under a non-grouping name for version-stable metrics.
    work["_true_probability_value"] = work["true_probability"]
    # Keep benchmark/scenario identity.  Otherwise distinct nonlinear limit
    # states with the same target probability are silently pooled.
    group_cols = [
        c
        for c in [
            "model",
            "scenario",
            "experiment",
            "noise_lambda_true",
            "method",
            "true_probability",
            "query_budget",
        ]
        if c in work
    ]

    def aggregate(group: pd.DataFrame) -> pd.Series:
        true_probability = float(group["_true_probability_value"].iloc[0])
        return pd.Series(
            {
                "n": len(group),
                "bias": group["error"].mean(),
                "rmse": np.sqrt(group["squared_error"].mean()),
                "relative_rmse": np.sqrt(group["squared_error"].mean()) / true_probability,
                "coverage": group["covered"].mean(),
                "mean_interval_width": group["interval_width"].mean(),
                "mean_oracle_queries": group["oracle_queries"].mean(),
                "mean_shots": group["shots"].mean() if "shots" in group else np.nan,
                "fit_failure_rate": 1.0 - group.get("converged", pd.Series(True, index=group.index)).mean(),
            }
        )

    return work.groupby(group_cols, dropna=False, sort=True).apply(aggregate, include_groups=False).reset_index()
