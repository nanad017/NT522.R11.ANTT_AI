# So sánh model trên temporal test

Giá trị là trung bình ± độ lệch chuẩn của ba seed 13, 42 và 2026.

| Model | PR-AUC | ROC-AUC | F1 | MCC |
|---|---:|---:|---:|---:|
| Linear SVM | 0.8102 ± 0.0000 | 0.7962 ± 0.0000 | 0.6482 ± 0.0000 | 0.5202 ± 0.0000 |
| Random Forest | **0.8620 ± 0.0046** | **0.8640 ± 0.0037** | 0.6374 ± 0.0068 | 0.5465 ± 0.0059 |
| XGBoost | 0.8342 ± 0.0100 | 0.8420 ± 0.0090 | 0.6551 ± 0.0026 | **0.5567 ± 0.0048** |
| GCN | 0.8319 ± 0.0071 | 0.8324 ± 0.0141 | **0.6557 ± 0.0035** | 0.5321 ± 0.0113 |
| GAT | 0.8144 ± 0.0199 | 0.8060 ± 0.0241 | 0.6381 ± 0.0091 | 0.5440 ± 0.0052 |
| GIN | 0.7957 ± 0.0146 | 0.7744 ± 0.0193 | 0.6411 ± 0.0065 | 0.5465 ± 0.0036 |

Metric chính là PR-AUC. Random Forest đạt PR-AUC trung bình cao nhất; đây là
kết quả trên 903 mẫu test usable, không phải toàn bộ 978 APK trong manifest test.
