## SGKG3, r100: Spearman's rho with the original

| copy    | metric   | spearman_rho   |   spearman_p |   models |
|:--------|:---------|:---------------|-------------:|---------:|
| PyGraft | Hits@1   | -0.408         |        0.363 |        7 |
| PyGraft | Hits@3   | -0.265         |        0.566 |        7 |
| PyGraft | Hits@5   | -0.270         |        0.558 |        7 |
| PyGraft | Hits@10  | -0.334         |        0.465 |        7 |
| PyGraft | Hits@15  | -0.200         |        0.667 |        7 |
| PyGraft | MRR      | -0.500         |        0.253 |        7 |
| kbgen   | Hits@1   | 0.179          |        0.701 |        7 |
| kbgen   | Hits@3   | 0.631          |        0.129 |        7 |
| kbgen   | Hits@5   | 0.667          |        0.102 |        7 |
| kbgen   | Hits@10  | 0.487          |        0.268 |        7 |
| kbgen   | Hits@15  | 0.571          |        0.180 |        7 |
| kbgen   | MRR      | 0.571          |        0.180 |        7 |
| Ours    | Hits@1   | 0.857          |        0.014 |        7 |
| Ours    | Hits@3   | 0.964*         |        0.000 |        7 |
| Ours    | Hits@5   | 0.964*         |        0.000 |        7 |
| Ours    | Hits@10  | 1.000*         |        0.000 |        7 |
| Ours    | Hits@15  | 1.000*         |        0.000 |        7 |
| Ours    | MRR      | 0.964*         |        0.000 |        7 |
