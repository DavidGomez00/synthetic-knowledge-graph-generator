"""
Kendall's tau: how much two graphs agree on which models are best.

Definition
----------
Train the same models on two graphs and rank them by one metric in each, e.g.
MRR. Take every pair of models: the pair is concordant when both graphs put
the same model ahead, and discordant when they put a different one ahead.
With C concordant and D discordant pairs out of n0 = n (n - 1) / 2 pairs of
n models, Kendall's tau is

    tau = (C - D) / n0

It goes from -1 (one graph ranks the models in the reverse order of the
other) through 0 (no relation between the two orders) to +1 (both rank the
models in the same order). It only depends on the order of the values, not
on how far apart they are (Kendall, 1938).

Two models with the same value of the metric, e.g. two models with Hits@15 =
1, are tied, and their pair is neither concordant nor discordant. Tau-b
divides by the pairs that are not tied, rather than by every pair, so that
two identical rankings with ties still get +1 (Kendall, 1945):

    tau_b = (C - D) / sqrt((n0 - n1) * (n0 - n2))

where n1 and n2 are the numbers of pairs tied in the first and the second
graph. Without ties, tau_b = tau.

Computation
-----------
`kendall_tau` calls `scipy.stats.kendalltau`, which computes tau-b by
default, and returns its p-value: the probability of
a tau at least this far from 0 if the two rankings were unrelated. SciPy
computes the p-value exactly for small samples without ties and from a normal
approximation otherwise.

With few models, few pairs decide tau: 7 models make 21 pairs, and one model
moving one place up changes tau by 2/21 = 0.095. Even a high tau can have a
large p-value: for 7 models without ties, tau = 0.62 has p = 0.069, and p
falls under 0.05 only from tau = 0.71 (at most 3 discordant pairs of 21). Read
tau as a measure of agreement, and the p-value only as a warning that it
rests on few models.

`pairwise_kendall_tau` follows the definition literally, comparing every
pair of models, to check `kendall_tau`. pandas'
`DataFrame.corr(method="kendall")` also gives tau-b, but no p-value.

Use in report_results.py
------------------------
`write_kg_analysis` in report_results.py calls `ranking_agreement` on the
metrics table of each KG: for the reference graph (`--reference`) and every
other graph of the KG, it ranks the models trained on both by Hits@k and by
MRR, and writes one tau per graph pair and metric to ranking_agreement.csv.
A graph whose models are ranked like the reference's is a benchmark that
would lead to the same conclusions about which model is best.

Run this file to check `kendall_tau` against `pairwise_kendall_tau`:

    python kendall_tau.py

References
----------
M. G. Kendall, "A new measure of rank correlation", Biometrika 30(1-2), 1938.

M. G. Kendall, "The treatment of ties in ranking problems", Biometrika
33(3), 1945.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.typing import ArrayLike
from scipy.stats import kendalltau

# The columns of `ranking_agreement`.
AGREEMENT_COLUMNS = [
    "kg",
    "reference",
    "compared",
    "metric",
    "models",
    "kendall_tau",
    "kendall_p",
]


def kendall_tau(x: ArrayLike, y: ArrayLike) -> tuple[float, float]:
    """
    Kendall's tau-b of x and y and its p-value, from `scipy.stats.kendalltau`;
    NaN under 2 values or when a side is constant, where tau is undefined.
    """
    x, y = np.asarray(x), np.asarray(y)
    if len(x) < 2 or len(np.unique(x)) < 2 or len(np.unique(y)) < 2:
        return float("nan"), float("nan")
    result = kendalltau(x, y)
    return float(result.statistic), float(result.pvalue)


def pairwise_kendall_tau(x: ArrayLike, y: ArrayLike) -> float:
    """
    Kendall's tau-b by its definition: over every pair (i, j), the sign of
    x_i - x_j times the sign of y_i - y_j is +1 for a concordant pair, -1 for
    a discordant one and 0 for a tie, and their sum, 2 (C - D) over the
    ordered pairs, is divided by the square root of the untied pairs of each
    side. It builds n * n matrices, so it is only meant to check `kendall_tau`.
    """
    x, y = np.asarray(x), np.asarray(y)
    sx = np.sign(x[:, None] - x[None, :])
    sy = np.sign(y[:, None] - y[None, :])
    return float((sx * sy).sum() / np.sqrt((sx**2).sum() * (sy**2).sum()))


def ranking_agreement(
    table: pd.DataFrame, reference: str, metrics: list[str]
) -> pd.DataFrame:
    """
    Compare how the reference graph and every other graph of the same KG
    rank their models, by each of `metrics`. `table` has one row per run, with
    the `kg`, `graph` and `model` columns and one column per metric. Each
    graph pair and metric gets one row: the number of models trained on both
    graphs with a value of the metric in each (`models`), and the Kendall's
    tau of their two rankings and its p-value.
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
                tau, p = kendall_tau(values.iloc[:, 0], values.iloc[:, 1])
                rows.append(
                    {
                        "kg": kg,
                        "reference": reference,
                        "compared": compared,
                        "metric": metric,
                        "models": len(values),
                        "kendall_tau": tau,
                        "kendall_p": p,
                    }
                )
    return pd.DataFrame(rows, columns=AGREEMENT_COLUMNS)


if __name__ == "__main__":
    # The example of docs/kgc.md: the MRR of four models in two graphs. The
    # compared graph swaps B and C, so 5 of the 6 pairs are concordant.
    reference, compared = [0.40, 0.30, 0.20, 0.10], [0.35, 0.15, 0.25, 0.05]
    tau, p = kendall_tau(reference, compared)
    print(f"docs example: tau = {tau:.2f} (p = {p:.2f})")
    assert np.isclose(tau, pairwise_kendall_tau(reference, compared))
    assert np.isclose(tau, 4 / 6)

    # Random rankings of 2 to 12 models, with many ties.
    rng = np.random.default_rng(0)
    for _ in range(100):
        n = rng.integers(2, 13)
        x, y = rng.integers(0, 5, n), rng.integers(0, 5, n)
        tau, _ = kendall_tau(x, y)
        if not np.isnan(tau):
            assert np.isclose(tau, pairwise_kendall_tau(x, y))
    print("kendall_tau matches the pairwise definition on 100 random samples")
