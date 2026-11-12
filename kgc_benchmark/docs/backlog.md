# Backlog

Ideas that aren't implemented yet, with enough context to pick them up later.

## Mark graphs that behave like the reference, not only those with no detected difference

**Where:** `graph_notes` in `report_results.py`, which sets the bold lines of `<model>_hits_at_k.png`.

**Now:** a graph's line is bold when its Holm-adjusted Mann-Whitney p against the reference is
`SIGNIFICANCE` (0.05) or more. That means the test found no difference. It doesn't show that the graph
behaves like the reference, because a small sample can also hide a real difference. For example,
CompGCN on `father` in French Royalty is bold for SKGG (PCA 0.9) with only n=62 predictions.

**Options:**

- **Effect-size rule.** Make the line bold when |Cliff's δ| < 0.147, the "negligible" bound already in
  `EFFECT_SIZES`. This doesn't depend on n, so it is easy to explain. It can be combined with the current
  rule (bold only when p ≥ 0.05 and |δ| < 0.147).
- **Equivalence test (TOST).** Two one-sided tests check whether the difference stays within a margin
  chosen in advance, for example |δ| < 0.147 or an MRR difference within ±0.02. A small p then supports
  similarity directly, which is the claim the benchmark makes. The `mrr_difference_low` and
  `mrr_difference_high` bootstrap interval in `relation_comparison.csv` already gives a simple version:
  the graphs are equivalent when the whole interval lies within the margin.

Whichever rule is chosen, update the figure's subtitle and its entry in [kgc.md](kgc.md). Consider adding
the result as a column of `relation_comparison.csv`, for example `equivalent`.
