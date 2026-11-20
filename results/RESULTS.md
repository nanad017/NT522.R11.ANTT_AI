# Temporal test results

Protocol: train 2019–2021, validation 2022, frozen test 2023–2026.
Usable cohort: 9,855/10,000; test coverage: 903/978.

| Model | Seeds | PR-AUC mean ± std | ROC-AUC mean ± std | F1 mean ± std | MCC mean ± std |
|---|---|---:|---:|---:|---:|
| linear_svm | 13,42,2026 | 0.810221 ± 0.000000 | 0.796154 ± 0.000000 | 0.648233 ± 0.000000 | 0.520221 ± 0.000000 |
| random_forest | 13,42,2026 | 0.862002 ± 0.004561 | 0.863984 ± 0.003749 | 0.637352 ± 0.006790 | 0.546492 ± 0.005944 |
| xgboost | 13,42,2026 | 0.834248 ± 0.009978 | 0.841995 ± 0.009047 | 0.655100 ± 0.002572 | 0.556744 ± 0.004758 |
| gcn | 13,42,2026 | 0.831905 ± 0.007128 | 0.832435 ± 0.014124 | 0.655711 ± 0.003498 | 0.532093 ± 0.011263 |
| gat | 13,42,2026 | 0.814382 ± 0.019861 | 0.805952 ± 0.024148 | 0.638119 ± 0.009121 | 0.543995 ± 0.005195 |
| gin | 13,42,2026 | 0.795746 ± 0.014590 | 0.774428 ± 0.019343 | 0.641063 ± 0.006470 | 0.546549 ± 0.003576 |

All thresholds were selected on validation only. Complete per-seed metrics, predictions,
timestamps, and safe checkpoints are under `experiments/<model>/seed_<seed>/`.
