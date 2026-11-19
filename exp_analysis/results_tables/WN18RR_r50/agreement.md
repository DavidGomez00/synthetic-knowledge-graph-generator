## WN18RR, r50: Spearman's rho with the original

| copy    | metric   | spearman_rho   |   spearman_p |   models |
|:--------|:---------|:---------------|-------------:|---------:|
| PyGraft | Hits@1   | 0.418          |        0.350 |        7 |
| PyGraft | Hits@3   | -0.306         |        0.504 |        7 |
| PyGraft | Hits@5   | -0.414         |        0.355 |        7 |
| PyGraft | Hits@10  | -0.296         |        0.518 |        7 |
| PyGraft | Hits@15  | -0.072         |        0.878 |        7 |
| PyGraft | MRR      | -0.250         |        0.589 |        7 |
| kbgen   | Hits@1   | 0.893          |        0.007 |        7 |
| kbgen   | Hits@3   | 0.893          |        0.007 |        7 |
| kbgen   | Hits@5   | 0.893          |        0.007 |        7 |
| kbgen   | Hits@10  | 0.893          |        0.007 |        7 |
| kbgen   | Hits@15  | 0.893          |        0.007 |        7 |
| kbgen   | MRR      | 0.857          |        0.014 |        7 |
| Ours    | Hits@1   | 0.929*         |        0.003 |        7 |
| Ours    | Hits@3   | 1.000*         |        0.000 |        7 |
| Ours    | Hits@5   | 1.000*         |        0.000 |        7 |
| Ours    | Hits@10  | 0.857          |        0.014 |        7 |
| Ours    | Hits@15  | 0.857          |        0.014 |        7 |
| Ours    | MRR      | 0.964*         |        0.000 |        7 |
