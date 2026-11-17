"""
Spearman's rho: how much two graphs agree on which models are best.

Definition
----------
Train the same models on two graphs and rank them by one metric in each, e.g.
MRR. Spearman's rho is the Pearson correlation of the two lists of ranks: with
rank differences d_i between the graphs for each of n models and no ties,

    rho = 1 - 6 * sum(d_i ** 2) / (n * (n ** 2 - 1))

It goes from -1 (one graph ranks the models in the reverse order of the
other) through 0 (no relation between the two orders) to +1 (both rank the
models in the same order). It only depends on the order of the values, not
on how far apart they are, and, unlike Kendall's tau, it weighs a model that
moves several places more than one that moves one place (Spearman, 1904).

Two models with the same value of the metric, e.g. two models with Hits@15 =
1, are tied, and both get the mean of the ranks they share; rho is then the
Pearson correlation of those ranks.

Computation
-----------
`spearman_rho` calls `scipy.stats.spearmanr`, which handles ties this way,
and returns its p-value: the probability of a rho at least this far from 0 if
the two rankings were unrelated, from a t distribution with n - 2 degrees of
freedom.

With few models, rho moves in steps and its p-value is a rough guide: with 7
models, swapping two neighbouring models lowers rho by 12 / (7 * 48) = 0.036,
and p falls under 0.05 only from about rho = 0.76. Read rho as a measure of
agreement, and the p-value only as a warning that it rests on few models.

Run this file to check `spearman_rho` against pandas'
`Series.corr(method="spearman")`, a second library implementation:

    python spearman_rho.py

Use in report_results.py
------------------------
`write_kg_analysis` in report_results.py calls `ranking_agreement` on the
metrics table of each KG: for the reference graph (`--reference`) and every
other graph of the KG, it ranks the models trained on both by Hits@k and by
MRR, and writes one rho per graph pair and metric to ranking_agreement.csv.
A graph whose models are ranked like the reference's is a benchmark that
would lead to the same conclusions about which model is best.

References
----------
C. Spearman, "The proof and measurement of association between two things",
The American Journal of Psychology 15(1), 1904.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.typing import ArrayLike
from scipy.stats import spearmanr

# The columns of `ranking_agreement`.
AGREEMENT_COLUMNS = [
    "kg",
    "reference",
    "compared",
    "metric",
    "models",
    "spearman_rho",
    "spearman_p",
]


def spearman_rho(x: ArrayLike, y: ArrayLike) -> tuple[float, float]:
    """
    Spearman's rho of x and y and its p-value, from `scipy.stats.spearmanr`;
    NaN under 2 values or when a side is constant, where rho is undefined.
    """
    x, y = np.asarray(x), np.asarray(y)
    if len(x) < 2 or len(np.unique(x)) < 2 or len(np.unique(y)) < 2:
        return float("nan"), float("nan")
    result = spearmanr(x, y)
    return float(result.statistic), float(result.pvalue)


def ranking_agreement(
    table: pd.DataFrame, reference: str, metrics: list[str]
) -> pd.DataFrame:
    """
    Compare how the reference graph and every other graph of the same KG
    rank their models, by each of `metrics`. `table` has one row per run, with
    the `kg`, `graph` and `model` columns and one column per metric. Each
    graph pair and metric gets one row: the number of models trained on both
    graphs with a value of the metric in each (`models`), and the Spearman's
    rho of their two rankings and its p-value.
    """
    rows = []
    for kg, runs in table.groupby("kg"):
        ref = runs[runs["graph"] == reference].set_index("model")
        if ref.empty:
            continue
        for compared, cmp in runs[runs["graph"] != reference].groupby("graph"):
            cmp = cmp.set_index("model")
            for metric in metrics:
                values = pd.concat(
                    [ref[metric], cmp[metric]], axis=1, join="inner"
                ).dropna()
                rho, p = spearman_rho(values.iloc[:, 0], values.iloc[:, 1])
                rows.append(
                    {
                        "kg": kg,
                        "reference": reference,
                        "compared": compared,
                        "metric": metric,
                        "models": len(values),
                        "spearman_rho": rho,
                        "spearman_p": p,
                    }
                )
    return pd.DataFrame(rows, columns=AGREEMENT_COLUMNS)


if __name__ == "__main__":
    # The MRR of four models in two graphs; the compared graph swaps B and C.
    reference, compared = [0.40, 0.30, 0.20, 0.10], [0.35, 0.15, 0.25, 0.05]
    rho, p = spearman_rho(reference, compared)
    print(f"example: rho = {rho:.2f} (p = {p:.2f})")
    assert np.isclose(rho, pd.Series(reference).corr(pd.Series(compared), "spearman"))
    assert np.isclose(rho, 0.8)

    # Random rankings of 3 to 12 models, with many ties.
    rng = np.random.default_rng(0)
    checked = 0
    for _ in range(100):
        n = rng.integers(3, 13)
        x, y = rng.integers(0, 5, n), rng.integers(0, 5, n)
        rho, _ = spearman_rho(x, y)
        if not np.isnan(rho):
            assert np.isclose(rho, pd.Series(x).corr(pd.Series(y), "spearman"))
            checked += 1
    print(f"spearman_rho matches pandas' Spearman correlation on {checked} samples")
