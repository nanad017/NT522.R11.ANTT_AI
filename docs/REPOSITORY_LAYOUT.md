# Repository layout

`src/android_models` và `src/android_pipeline` là implementation được giữ lại để
các run có thể tái lập. Các package `src/models`, `src/datasets`, `src/engine` và
`src/utils` cung cấp giao diện theo cấu trúc báo cáo, không sao chép logic train
lần thứ hai.

`data/processed/<split>/<year>/<label>/<sha256>` là bản sao vật lý của artifact
đã trích xuất. Catalog dùng đường dẫn tương đối nên toàn bộ repository có thể di
chuyển sang máy khác. `metadata/{train,validation,test}.csv` liệt kê chính xác APK
thuộc từng split.

Mỗi run hoàn chỉnh nằm tại `experiments/<model>/seed_<seed>`. Checkpoint dùng
định dạng an toàn (`.skops`, `.ubj`, `.safetensors`); metric và prediction được
giữ riêng để kiểm toán kết quả.
