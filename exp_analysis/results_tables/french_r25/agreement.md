## french, r25: Spearman's rho with the original

| copy    | metric   | spearman_rho   |   spearman_p |   models |
|:--------|:---------|:---------------|-------------:|---------:|
| PyGraft | Hits@1   | 0.866          |        0.012 |        7 |
| PyGraft | Hits@3   | 0.162          |        0.728 |        7 |
| PyGraft | Hits@5   | 0.214          |        0.645 |        7 |
| PyGraft | Hits@10  | -0.054         |        0.908 |        7 |
| PyGraft | Hits@15  | -0.162         |        0.728 |        7 |
| PyGraft | MRR      | 0.250          |        0.589 |        7 |
| kbgen   | Hits@1   | 0.893          |        0.007 |        7 |
| kbgen   | Hits@3   | 0.964*         |        0.000 |        7 |
| kbgen   | Hits@5   | 0.893          |        0.007 |        7 |
| kbgen   | Hits@10  | 1.000*         |        0.000 |        7 |
| kbgen   | Hits@15  | 0.991*         |        0.000 |        7 |
| kbgen   | MRR      | 0.964*         |        0.000 |        7 |
| Ours    | Hits@1   | 0.964*         |        0.000 |        7 |
| Ours    | Hits@3   | 1.000*         |        0.000 |        7 |
| Ours    | Hits@5   | 0.750          |        0.052 |        7 |
| Ours    | Hits@10  | 0.893          |        0.007 |        7 |
| Ours    | Hits@15  | 0.893          |        0.007 |        7 |
| Ours    | MRR      | 1.000*         |        0.000 |        7 |
