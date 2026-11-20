# Reproducibility

1. Kiểm tra checksum nguồn theo `data/raw/source-checksums.sha256` nếu cần tải
   và trích xuất lại APK.
2. Kiểm tra artifact đã xử lý bằng `python scripts/verify_dataset.py
   --full-checksum`.
3. Dùng Python 3.12 và dependency khóa trong `pyproject.toml`/`uv.lock`.
4. Chạy đúng config và seed đã ghi trong `experiments/*/seed_*/logs/run.json`.
5. So sánh prediction/metric mới với file trong thư mục `metrics/`.

Các file cấu hình và catalog dùng đường dẫn tương đối. Metadata tải nguồn không
chứa token, password hoặc OAuth secret.
