# Temporal evaluation protocol

- Train: năm 2019–2021, 8.368 mẫu usable.
- Validation: năm 2022, 584 mẫu usable.
- Test: năm 2023–2026, 903 mẫu usable.

Vocabulary, scaler, embedding và tham số model chỉ được fit trên train. Best
epoch, hyperparameter và threshold chỉ được chọn trên validation. Test chỉ được
dùng một lần sau khi mọi lựa chọn đã khóa. Split được join theo SHA-256 từ
manifest, không suy ra bằng cách quét thư mục.

Manifest ban đầu có 10.000 APK; 145 APK thiếu đồng thời classical record hoặc
FCG hợp lệ và được ghi trong `data/processed/metadata/exclusions.parquet`. Vì vậy
mọi metric trong repository đều báo trên usable cohort 9.855, không tuyên bố là
kết quả của đủ 10.000 APK.

Metric chính là test PR-AUC; ROC-AUC, F1 và MCC là metric bổ sung. Báo cáo dùng
trung bình và độ lệch chuẩn của ba seed 13, 42 và 2026.
