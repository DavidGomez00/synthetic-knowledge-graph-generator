## french1_1, r75: Spearman's rho with the original

| copy    | metric   | spearman_rho   |   spearman_p |   models |
|:--------|:---------|:---------------|-------------:|---------:|
| PyGraft | Hits@1   | -0.223         |        0.631 |        7 |
| PyGraft | Hits@3   | -0.727         |        0.064 |        7 |
| PyGraft | Hits@5   | -0.309         |        0.500 |        7 |
| PyGraft | Hits@10  | -0.164         |        0.726 |        7 |
| PyGraft | Hits@15  | -0.286         |        0.535 |        7 |
| PyGraft | MRR      | -0.321         |        0.482 |        7 |
| kbgen   | Hits@1   | 0.893          |        0.007 |        7 |
| kbgen   | Hits@3   | 0.679          |        0.094 |        7 |
| kbgen   | Hits@5   | 0.964*         |        0.000 |        7 |
| kbgen   | Hits@10  | 1.000*         |        0.000 |        7 |
| kbgen   | Hits@15  | 0.991*         |        0.000 |        7 |
| kbgen   | MRR      | 0.857          |        0.014 |        7 |
| Ours    | Hits@1   | 0.607          |        0.148 |        7 |
| Ours    | Hits@3   | 0.214          |        0.645 |        7 |
| Ours    | Hits@5   | 0.607          |        0.148 |        7 |
| Ours    | Hits@10  | 0.643          |        0.119 |        7 |
| Ours    | Hits@15  | 0.643          |        0.119 |        7 |
| Ours    | MRR      | 0.179          |        0.702 |        7 |
