# Android Malware Detection on a Temporal Dataset

Repository phục vụ báo cáo thí nghiệm phát hiện malware Android. Mã nguồn,
cấu hình, dataset đã trích xuất và kết quả được tổ chức lại từ dự án gốc bằng
**bản sao**; các thư mục nguồn cũ không bị sửa hoặc xóa.

## Phạm vi hiện có

- Đã hoàn thành 3 seed (`13`, `42`, `2026`) cho Linear SVM, Random Forest,
  XGBoost, GCN, GAT và GIN.
- Dataset usable gồm 9.855 APK theo thời gian: train 8.368 (2019–2021),
  validation 584 (2022), test 903 (2023–2026).
- Không chứa APK gốc. `data/raw/` chỉ lưu manifest, chỉ mục archive và checksum
  để tái tạo đúng cohort.
- Anakin, GRED và SigDroid chưa có run hoàn chỉnh; trạng thái được ghi rõ trong
  thư mục experiment tương ứng.
- Explainability chưa được triển khai. Các thư mục dưới `src/explainers/` và
  `explanations/` được cố ý để rỗng.

## Tải artifact dung lượng lớn

GitHub không chứa dataset đã xử lý và artifact experiment. Điền link Google
Drive rồi tải theo hai file sau:

- `data/processed/DRIVE_LINK.md`: feature, graph và metadata processed.
- `experiments/DRIVE_LINK.md`: checkpoint, log, metric, prediction và figure.

Sau khi tải, giải nén vào đúng hai thư mục tương ứng trước khi chạy train/test
hoặc kiểm tra checksum.

## Cấu trúc

```text
configs/                 cấu hình model
data/raw/                metadata để tìm/tải lại dữ liệu gốc
data/processed/          feature và graph theo train/validation/test
src/datasets/            đọc dataset và preprocessing
src/models/              facade theo từng model
src/android_models/      implementation train/evaluate dùng thực tế
src/android_pipeline/    pipeline trích xuất APK
src/engine/              train/validation/test/checkpoint
src/utils/               metric, seed, registry và quality
src/explainers/           khung rỗng theo yêu cầu
scripts/                  entry point thực thi
experiments/              checkpoint, log, metric, figure theo model/seed
results/                  bảng tổng hợp và so sánh
tests/                    kiểm thử
docs/                     giao thức và tài liệu thiết kế
```

## Cài đặt và kiểm tra

Yêu cầu Python 3.12. Khuyến nghị dùng `uv`:

```bash
uv sync --extra classical --extra gnn --extra anakin --extra dev
uv run python scripts/verify_dataset.py
uv run pytest
```

Kiểm tra toàn bộ 88.695 checksum artifact (tốn thời gian hơn):

```bash
uv run python scripts/verify_dataset.py --full-checksum
```

## Train và đánh giá

Ví dụ chạy lại model classical và GNN:

```bash
uv run python scripts/train.py train-classical \
  --config configs/models/full_ml_fcg.yaml --model linear_svm --seed 13

uv run python scripts/train.py train-common-gnn \
  --config configs/models/full_ml_fcg.yaml --kind gcn --view fcg --seed 13
```

Mỗi experiment giữ checkpoint, log, metric, figure và thư mục explanations.
Xem `results/RESULTS.md` để đọc kết quả và `docs/EVALUATION_PROTOCOL.md` để biết
quy tắc chống leakage.

## Dataset gốc

Không cần tải APK gốc để đọc kết quả hoặc chạy lại model trên feature đã trích
xuất. Khi cần trích xuất lại, dùng `data/raw/source-archives.csv`,
`source-checksums.sha256`, `final-manifest.csv` và hướng dẫn trong
`data/raw/download-instructions.md`. Repository không chứa credential tải dữ liệu.
