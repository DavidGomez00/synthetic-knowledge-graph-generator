## frenchN_M, r100: Spearman's rho with the original

| copy    | metric   | spearman_rho   |   spearman_p |   models |
|:--------|:---------|:---------------|-------------:|---------:|
| PyGraft | Hits@1   | 0.045          |        0.924 |        7 |
| PyGraft | Hits@3   | -0.433         |        0.331 |        7 |
| PyGraft | Hits@5   | -0.500         |        0.253 |        7 |
| PyGraft | Hits@10  | -0.703         |        0.078 |        7 |
| PyGraft | Hits@15  | -0.400         |        0.374 |        7 |
| PyGraft | MRR      | -0.679         |        0.094 |        7 |
| kbgen   | Hits@1   | 0.857          |        0.014 |        7 |
| kbgen   | Hits@3   | 0.929*         |        0.003 |        7 |
| kbgen   | Hits@5   | 0.883          |        0.008 |        7 |
| kbgen   | Hits@10  | 0.964*         |        0.000 |        7 |
| kbgen   | Hits@15  | 0.964*         |        0.000 |        7 |
| kbgen   | MRR      | 0.857          |        0.014 |        7 |
| Ours    | Hits@1   | 0.889          |        0.007 |        7 |
| Ours    | Hits@3   | 0.893          |        0.007 |        7 |
| Ours    | Hits@5   | 0.991*         |        0.000 |        7 |
| Ours    | Hits@10  | 0.964*         |        0.000 |        7 |
| Ours    | Hits@15  | 0.964*         |        0.000 |        7 |
| Ours    | MRR      | 0.893          |        0.007 |        7 |
