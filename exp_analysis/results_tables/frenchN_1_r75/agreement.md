## frenchN_1, r75: Spearman's rho with the original

| copy    | metric   | spearman_rho   |   spearman_p |   models |
|:--------|:---------|:---------------|-------------:|---------:|
| PyGraft | Hits@1   | 0.179          |        0.701 |        7 |
| PyGraft | Hits@3   | 0.000          |        1.000 |        7 |
| PyGraft | Hits@5   | -0.072         |        0.878 |        7 |
| PyGraft | Hits@10  | -0.318         |        0.487 |        7 |
| PyGraft | Hits@15  | -0.400         |        0.374 |        7 |
| PyGraft | MRR      | 0.000          |        1.000 |        7 |
| kbgen   | Hits@1   | 0.893          |        0.007 |        7 |
| kbgen   | Hits@3   | 0.857          |        0.014 |        7 |
| kbgen   | Hits@5   | 0.883          |        0.008 |        7 |
| kbgen   | Hits@10  | 0.857          |        0.014 |        7 |
| kbgen   | Hits@15  | 0.929*         |        0.003 |        7 |
| kbgen   | MRR      | 0.679          |        0.094 |        7 |
| Ours    | Hits@1   | 0.964*         |        0.000 |        7 |
| Ours    | Hits@3   | 0.714          |        0.071 |        7 |
| Ours    | Hits@5   | 0.786          |        0.036 |        7 |
| Ours    | Hits@10  | 0.893          |        0.007 |        7 |
| Ours    | Hits@15  | 0.893          |        0.007 |        7 |
| Ours    | MRR      | 0.964*         |        0.000 |        7 |
