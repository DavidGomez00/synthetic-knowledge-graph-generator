"""
Cliff's delta: how much one sample tends to be larger than another.

Definition
----------
Draw one value from x and one from y at random. Cliff's delta is the
probability that the x value is larger minus the probability that the y
value is larger:

    delta = P(x > y) - P(x < y)
          = (#{(i, j): x_i > y_j} - #{(i, j): x_i < y_j}) / (n * m)

over all n * m pairs of the n values of x and the m values of y. A tie
counts for neither side. It goes from -1 (every y value is larger) through
0 (neither sample tends to be larger) to +1 (every x value is larger). It
assumes nothing about the distributions, a few extreme values don't distort
it, and it doesn't grow with the sample sizes (Cliff, 1993).

Computation
-----------
`pairwise_cliffs_delta` follows the definition literally, comparing all n * m
pairs. `cliffs_delta` gets the same number from the Mann-Whitney U statistic
of x, which already counts those pairs, a tie as half:

    U = #{x_i > y_j} + 1/2 * #{x_i = y_j}

Every pair is larger, smaller or tied, so the three counts add up to n * m,
and

    delta = 2 * U / (n * m) - 1

`scipy.stats.mannwhitneyu` computes U by ranking both samples together, in
O((n + m) log(n + m)) time instead of n * m comparisons. It returns the U of
its first argument since SciPy 1.7. Older versions return the smaller of the
two samples' U values, with which delta would never be positive.

The same number goes by other names: it is the rank-biserial correlation,
and 2A - 1 for A = U / (n * m), the common-language effect size (the
Vargha-Delaney A, or AUC). Neither SciPy nor NumPy has a Cliff's delta
function. pingouin's `pingouin.mwu(x, y)["RBC"]` returns the same value.

Use in report_results.py
------------------------
`compare_relations` in report_results.py calls `cliffs_delta(reference,
compared)` on the normalized ranks of two graphs' test predictions,
(rank - 1) / (candidates - 1). A lower rank is better, so `x > y` means the
compared graph ranks better:

    delta = P(compared ranks better) - P(reference ranks better)

and delta is negative when the compared graph does worse. `effect_size`
labels its size for the `effect` column of relation_comparison.csv.

Run this file to check `cliffs_delta` against `pairwise_cliffs_delta`:

    python cliffs_delta.py

References
----------
N. Cliff, "Dominance statistics: Ordinal analyses to answer ordinal
questions", Psychological Bulletin 114(3), 1993.

J. Romano, J. D. Kromrey, J. Coraggio and J. Skowronek, "Appropriate
statistics for ordinal level data: Should we really be using t-test and
Cohen's d for evaluating group differences on the NSSE and other surveys?",
Annual meeting of the Florida Association of Institutional Research, 2006.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike
from scipy.stats import mannwhitneyu

# Upper bounds of |Cliff's delta| for each effect size label (Romano et al.,
# 2006); anything above the last bound is "large".
EFFECT_SIZES = [(0.147, "negligible"), (0.33, "small"), (0.474, "medium")]


def cliffs_delta(x: ArrayLike, y: ArrayLike) -> float:
    """
    Cliff's delta of x against y, P(x > y) - P(x < y), from the Mann-Whitney
    U statistic of x: 2U / (n * m) - 1.
    """
    x, y = np.asarray(x), np.asarray(y)
    u = mannwhitneyu(x, y, alternative="two-sided").statistic
    return float(2 * u / (len(x) * len(y)) - 1)


def pairwise_cliffs_delta(x: ArrayLike, y: ArrayLike) -> float:
    """
    Cliff's delta by its definition: the sign of x_i - y_j (+1, -1 or 0 for a
    tie) averaged over all n * m pairs. It builds an n * m matrix, so it is
    only meant to check `cliffs_delta`.
    """
    x, y = np.asarray(x), np.asarray(y)
    return float(np.sign(x[:, None] - y[None, :]).mean())


def effect_size(delta: float) -> str:
    """Label the size of a Cliff's delta: negligible, small, medium or large."""
    for bound, label in EFFECT_SIZES:
        if abs(delta) < bound:
            return label
    return "large"


if __name__ == "__main__":
    # The example of docs/kgc.md: normalized ranks, lower is better. The
    # compared graph is better in 1 of the 4 pairs and worse in 3.
    reference, compared = [0.1, 0.3], [0.2, 0.5]
    delta = cliffs_delta(reference, compared)
    print(f"docs example: delta = {delta:+.2f} ({effect_size(delta)})")
    assert delta == pairwise_cliffs_delta(reference, compared) == -0.5

    # Random samples of different sizes, with many ties.
    rng = np.random.default_rng(0)
    for _ in range(100):
        x = rng.integers(0, 30, rng.integers(1, 400)) / 29
        y = rng.integers(0, 30, rng.integers(1, 400)) / 29
        assert np.isclose(cliffs_delta(x, y), pairwise_cliffs_delta(x, y))
    print("cliffs_delta matches the pairwise definition on 100 random samples")
