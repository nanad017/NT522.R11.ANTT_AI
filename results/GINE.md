# GINE trên Function Call Graph

Train 8.368 APK (2019–2021), validation 584 APK (2022), test 903 APK (2023–2026).
Test bao phủ 903/978 APK trong manifest gốc. Validation chọn checkpoint theo PR-AUC
và chọn ngưỡng theo F1. GINE dùng 9 đặc trưng cạnh.

| Seed | Validation PR-AUC | Test PR-AUC | Test ROC-AUC | Test F1 | Test MCC | Giờ chạy |
|---:|---:|---:|---:|---:|---:|---:|
| 13 | 0.9829 | 0.7694 | 0.7798 | 0.6510 | 0.5423 | 8.41 |
| 42 | 0.9826 | 0.7813 | 0.7802 | 0.6300 | 0.5401 | 4.59 |
| 2026 | 0.9882 | 0.8459 | 0.8369 | 0.7259 | 0.5039 | 8.27 |

Trung bình ± độ lệch chuẩn trên ba seed.

| Model | PR-AUC | ROC-AUC | F1 | MCC |
|---|---:|---:|---:|---:|
| GINE | 0.7989 ± 0.0336 | 0.7989 ± 0.0268 | 0.6690 ± 0.0412 | 0.5288 ± 0.0176 |
| GIN | 0.7957 ± 0.0146 | 0.7744 ± 0.0193 | 0.6411 ± 0.0065 | 0.5465 ± 0.0036 |
