# Yêu cầu triển khai các mô hình Android malware detector

**Trạng thái tài liệu:** đặc tả bàn giao cho AI/kỹ sư triển khai giai đoạn mô hình  
**Ngày chốt đặc tả:** 2026-11-12 UTC  
**Project trích xuất:** `/home/rl/Data`  
**Cohort chuẩn:** 10.000 APK, binary benign/malware, temporal split cố định  

## 1. Mục tiêu và giới hạn

Hãy xây dựng một pipeline mô hình hóa có thể tái lập cho các detector sau, sử dụng đúng artifact do pipeline Android static extraction sinh ra:

1. Linear SVM.
2. Random Forest.
3. XGBoost.
4. GCN trên FCG và CCG.
5. GAT trên FCG và CCG.
6. GIN trên FCG và CCG.
7. GRED, gồm một nhánh approximation chạy được với dữ liệu hiện có và một nhánh paper-faithful chỉ được mở khi đủ asset.
8. SIGDroid, gồm proxy/approximation và nhánh paper-faithful SCKDG chỉ được mở khi đủ asset.
9. ANAKIN reimplementation trên API-transition graph; có thể thêm nhánh paper-faithful nếu về sau có sequence generator/dataset gốc.
10. GNNExplainer và PGExplainer sau khi classifier tương ứng đã được huấn luyện và đóng băng.

Tài liệu này yêu cầu **triển khai code, test, cấu hình, checkpoint contract và lệnh CLI**. Không được hiểu là yêu cầu chạy benchmark cuối cùng ngay lập tức. Ở thời điểm viết tài liệu, extraction toàn cohort chưa hoàn tất; vì vậy chỉ được chạy unit test, contract test và smoke/mini-pilot trên dữ liệu đã xác minh. Không công bố kết quả toàn cohort cho tới khi các cổng dữ liệu ở mục 4 đều đạt.

Không sửa, xóa hoặc ghi đè canonical extraction artifact. Modeling phải là consumer chỉ đọc. Mọi artifact fit/transform/train/evaluate phải nằm trong cây output riêng và có version/hash.

## 2. Nguồn sự thật bắt buộc phải đọc trước khi code

AI tiếp nhận phải đọc toàn bộ các nguồn sau trước khi triển khai:

- `/home/rl/mlinsec-apk-handoff/VM2_HANDOFF_CONTEXT.md` nếu file được phục hồi. Tại snapshot 2026-11-12, file này không còn hiện diện; context gần nhất đang có là `/home/rl/mlinsec-apk-handoff/PROJECT_CONTEXT.md`. Không được giả vờ đã đọc một file không tồn tại.
- `/home/rl/mlinsec-apk-handoff/task.md`, đặc biệt mục 7, 10, 11, 12 và 13.
- `/home/rl/Data/task.md`; nếu khác bản handoff thì phải diff và ghi rõ bản nào điều khiển modeling contract.
- `/home/rl/Data/docs/APK_EXTRACTION_IMPLEMENTATION_REPORT.md`
- `/home/rl/Data/docs/QUICKSTART.md`
- `/home/rl/Data/src/android_pipeline/ir/schema.py`
- `/home/rl/Data/src/android_pipeline/adapters/fcg.py`
- `/home/rl/Data/src/android_pipeline/adapters/ccg.py`
- `/home/rl/Data/src/android_pipeline/adapters/gred.py`
- `/home/rl/Data/src/android_pipeline/adapters/sigdroid.py`
- `/home/rl/Data/src/android_pipeline/adapters/anakin.py`
- `/home/rl/Data/src/android_pipeline/consumers/smoke.py`
- Ba bài nghiên cứu trong `/home/rl/mlinsec-apk-handoff/papers/`.
- Mã tham chiếu ANAKIN tại `/home/rl/mlinsec-apk-handoff/references/ANAKIN/`.

Ba paper cần đối chiếu:

- GRED: *A novel graph neural network-based approach for Android malware detection*, Applied Soft Computing 192 (2026) 114689, DOI `10.1016/j.asoc.2026.114689`.
- SIGDroid: *Enhancing Android malware detection via knowledge distillation on homogenized function call graphs*, Knowledge-Based Systems 323 (2025) 113687, DOI `10.1016/j.knosys.2025.113687`.
- ANAKIN: *Anakin: explainable Android malware detection with graph neural networks*, Cybersecurity 9, article 116 (2026), DOI `10.1186/s42400-026-00552-z`.

Nếu code và tài liệu mâu thuẫn, không tự đoán. Ghi rõ mâu thuẫn, dùng schema/artifact thực tế làm input contract hiện hành, và tạo migration/version mới nếu cần.

## 3. Snapshot thực tế của VM và dữ liệu

Snapshot này chỉ dùng để tránh giả định sai; AI tiếp nhận phải chạy lại status/preflight vì trạng thái có thể thay đổi.

- Cohort manifest: 10.000 SHA-256 duy nhất, 5.000 benign và 5.000 malware.
- Nhãn: `benign=0`, `malware=1`; không có family label đủ tin cậy để làm family classification.
- Split bất biến:
  - train, năm 2019–2021: 8.422 APK;
  - validation, năm 2022: 600 APK;
  - test, năm 2023–2026: 978 APK.
- Lần kiểm tra ngày 2026-11-12: tiến trình full extraction đang dừng; state DB báo 481 sample `remote_verified`, 13 `failed`, 35 `pending`; 2 archive checksum-verified và 14 pending. Đây **không phải** tập đủ để train/evaluate cuối cùng.
- Smoke hiện tại đã chứng minh LinearSVC/RF/XGBoost có thể fit record nhỏ và GCN/GAT/GIN có forward/backward hữu hạn trên FCG thật. Smoke không phải benchmark.
- Dependencies hiện có trong lock: Python 3.12, NumPy, SciPy, PyArrow; extras có scikit-learn 1.9.1, XGBoost 3.4.1, PyTorch 2.12.1 CPU, PyG 2.8.0.
- VM extraction không có GPU. Code training phải hỗ trợ CPU smoke; full GNN/SIGDroid nên cho phép chạy GPU trên máy khác, không hard-code CUDA.
- Gensim, Optuna/Hyperopt, MLflow và LongCoder chưa phải dependency được khóa. Chỉ thêm dependency thật sự cần, pin version, cập nhật `uv.lock`, và ghi license/model revision.

Lệnh kiểm tra ban đầu:

```bash
cd /home/rl/Data
scripts/status-full.sh
uv run android-pipeline preflight --config configs/full-p0-p1.yaml
uv run pytest -q
uv run ruff check src tests
```

## 4. Các cổng dữ liệu trước huấn luyện cuối cùng

### 4.1. Cổng D0 — catalog và tính toàn vẹn

Chỉ cho phép full training khi:

- Đủ 10.000 SHA trong catalog hoặc có một exclusion manifest được ký/hash, nêu lý do từng APK.
- Mỗi sample được dùng phải có receipt remote-verified, checksum hợp lệ, `analysis_status` chấp nhận được, schema/view version đúng cấu hình.
- Không có SHA trùng hàng, split mâu thuẫn, label mâu thuẫn hoặc row bị join theo path thay vì SHA.
- Mọi archive/batch modeling đọc được đều qua checksum; archive được stream hoặc cache có giới hạn, không unpack toàn bộ 2 TiB cùng lúc.
- Báo coverage theo split/year/label và theo view. Không đánh đồng “có sample row” với “có graph hợp lệ”.

### 4.2. Cổng D1 — split và leakage

- Split lấy nguyên từ manifest, không random split lại.
- Mọi graph/sequence của cùng một APK phải nằm cùng split với APK đó.
- Vocabulary, scaler, selector, TF-IDF, Word2Vec, calibration, threshold, sampler và explainer trainable chỉ fit trên train.
- Validation chỉ dùng để chọn hyperparameter, epoch, threshold và calibration.
- Test 2023–2026 chỉ dùng sau khi cấu hình được khóa; không dùng test để chọn feature, top-k, seed tốt nhất hay checkpoint.
- Chạy audit exact duplicate, certificate/package reuse và near-duplicate/code-clone giữa split. Không được loại mẫu âm thầm; xuất audit và một protocol versioned nếu cần loại.

### 4.3. Cổng D2 — chất lượng graph/feature

Theo từng view, thống kê tối thiểu: missing view, graph rỗng, zero-edge, node/edge p50/p95/p99/max, non-finite feature, feature width, unknown-feature rate, truncation rate, unresolved-call rate và fidelity.

Chính sách mặc định:

- Sample thiếu toàn bộ view cần thiết không được thay bằng graph giả.
- Graph có node nhưng không có edge được giữ với policy rõ ràng; model phải xử lý được singleton/edgeless graph.
- Failure/exclusion phải được báo trong denominator gốc, không chỉ báo metric trên subset thành công.
- Không thêm self-loop vào artifact gốc. Nếu layer thêm self-loop nội bộ, ghi trong model config.

### 4.4. Cổng D3 — fidelity

| View | Trạng thái hiện tại | Được phép gọi tên |
|---|---|---|
| Classical records | project baseline | Classical project baseline |
| FCG | canonical `fcg-v1` | Common FCG baseline |
| CCG | `canonical_class_collapse` | Common CCG baseline |
| ANAKIN | `reimplementation`, `raw_pre_fit` | ANAKIN reimplementation |
| GRED | `structural_approximation`, `blocked_missing_asset` | GRED-shaped approximation, không phải GRED-faithful |
| SIGDroid | `structural_approximation`, `blocked_missing_asset` | SIGDroid-shaped proxy, không phải SIGDroid/SCKDG-faithful |

Một run có input `blocked_missing_asset` phải bị chặn nếu config yêu cầu `fidelity=paper_faithful`. Không có cờ `--force` để đổi tên approximation thành faithful.

## 5. Kiến trúc code yêu cầu

Ưu tiên đặt modeling trong cùng project để dùng chung lock và schema, nhưng tách namespace khỏi extractor:

```text
/home/rl/Data/
├── configs/models/
│   ├── data.yaml
│   ├── classical.yaml
│   ├── common_gnn.yaml
│   ├── gred.yaml
│   ├── sigdroid.yaml
│   ├── anakin.yaml
│   └── xai.yaml
├── src/android_models/
│   ├── cli.py
│   ├── contracts.py
│   ├── catalog.py
│   ├── archive_reader.py
│   ├── leakage.py
│   ├── metrics.py
│   ├── registry.py
│   ├── reproducibility.py
│   ├── features/
│   │   ├── classical.py
│   │   ├── graph.py
│   │   └── anakin.py
│   ├── models/
│   │   ├── classical.py
│   │   ├── common_gnn.py
│   │   ├── gred.py
│   │   ├── sigdroid.py
│   │   └── anakin.py
│   ├── training/
│   │   ├── classical.py
│   │   ├── gnn.py
│   │   ├── distillation.py
│   │   └── tuning.py
│   └── xai/
│       ├── explain.py
│       └── faithfulness.py
├── tests/models/
└── model-artifacts/                 # gitignored; có run manifest/hash
```

Tên cụ thể có thể điều chỉnh, nhưng phải giữ ranh giới: data contract → train-only fit → frozen transform → model → evaluation → explanation. Không đặt logic fit vào APK adapter.

CLI tối thiểu:

```text
android-models preflight
android-models build-catalog
android-models audit-leakage
android-models fit-classical-features
android-models transform-classical-features
android-models train-classical
android-models train-common-gnn
android-models train-gred
android-models train-sigdroid
android-models train-anakin
android-models evaluate
android-models explain
android-models status
```

Mọi lệnh phải có `--config`, `--output`, `--dry-run` khi hợp lý, resume an toàn, log JSONL và exit code khác 0 khi contract/fidelity không đạt.

## 6. Contract dùng chung cho mọi run

Mỗi run phải lưu:

- `run.json`: run ID, thời gian UTC, command, config đầy đủ sau resolve, git commit/dirty flag, Python/platform, package lock hash, device/CUDA/cuDNN nếu có.
- `data.json`: manifest SHA-256, catalog hash, schema/view versions, input archive/receipt IDs, số lượng theo split/year/label, exclusions và lý do.
- `fit.json`: danh sách SHA train hoặc hash của danh sách theo thứ tự, seed, vocabulary/scaler/selector/embedding hashes.
- `checkpoint.*`: model weights ở định dạng an toàn; ưu tiên `safetensors`/state dict cùng schema, không dùng pickle không kiểm soát làm artifact canonical.
- `threshold.json`: cách chọn threshold trên validation và threshold đã khóa.
- `predictions.parquet`: `apk_sha256`, split, year, label, logit/score, probability nếu calibrated, prediction, threshold, model run ID, view hash, coverage status.
- `metrics.json` và `metrics-by-year.parquet`.
- `environment.txt` hoặc lock hash; stdout/stderr log.
- Với graph model: batch/sampler policy, graph-size cap policy, node/edge feature names, directedness, self-loop policy, readout, edge use và aggregation APK-level.

Seed mặc định cho benchmark: ba seed cố định, ví dụ `13, 42, 2026`. Không chọn seed tốt nhất rồi chỉ báo seed đó. Báo mean/std và từng seed. Determinism phải được bật ở mức khả thi; operation không deterministic phải được ghi nhận.

Loss mặc định là binary cross entropy với logits một đầu ra hoặc cross entropy hai logits. Dữ liệu cân bằng tổng thể nhưng có thể lệch theo năm; không tự bật class weight. Nếu dùng weight/sampler, fit từ train và báo ablation.

## 7. Classical detectors

### 7.1. Feature record chung

Ba detector SVM/RF/XGBoost phải so sánh trên **cùng một ma trận feature** cho track chính. Record một APK cần mở rộng `classical_record()` hiện tại, tối thiểu gồm:

- Manifest behavior, không dùng identity metadata:
  - requested/used permissions theo tên và kind;
  - component type/count, exported/enabled status;
  - intent actions/categories;
  - provider rule/config indicator có ý nghĩa hành vi.
- API/call:
  - external API signature/package/category count hoặc binary;
  - internal/external target counts;
  - invoke-kind counts;
  - security taxonomy concept counts/binary.
- Opcode:
  - 256 opcode bins hoặc dictionary đã version;
  - raw count được chuẩn hóa theo tổng instruction, có thể thêm `log1p(count)` trong track riêng;
  - missing-code/parse coverage.
- Graph summary cho FCG, và tùy chọn CCG trong feature block riêng:
  - số node/edge, density;
  - internal/external node ratio;
  - in/out degree mean/std/max/quantiles;
  - SCC count/largest-SCC ratio, weak component count;
  - isolates, self-loop, reciprocal-edge ratio;
  - sensitive node/edge counts;
  - callsite multiplicity statistics.
- Optional, phải là experiment riêng: Weisfeiler–Lehman fingerprint hoặc pooled node feature. Không dùng learned embedding nếu checkpoint học từ ngoài train contract.

Không đưa vào feature mặc định: SHA, path, package name như identity token, signer/certificate ID, split, year, market, VT detection count, archive/batch ID, filename, label hay các trường suy trực tiếp từ label. Year chỉ dùng phân tầng báo cáo, không làm feature baseline.

Pipeline feature:

1. Xuất named sparse record có `apk_sha256`, label, split, year, `features: {name: value}`.
2. Fit `DictVectorizer` hoặc vocabulary tương đương trên toàn train theo stream/batch.
3. Đóng băng feature order; transform train/validation/test thành CSR `float32` cùng chiều.
4. Lưu row SHA/order, labels, feature names, block offsets, nonzero counts và unknown-feature rate.
5. Nếu scale, dùng sparse-safe scaler như `MaxAbsScaler` hoặc `StandardScaler(with_mean=False)`, fit train-only.
6. Nếu select feature, fit train-only và lưu mask/name. Track chính ban đầu không feature selection để giữ khả năng diễn giải.

### 7.2. Linear SVM

Mục tiêu: baseline sparse mạnh, dễ tái lập.

- Dùng `LinearSVC` hoặc linear SVM tương đương; không dùng RBF trên ma trận cực lớn làm baseline mặc định.
- Search trên validation: `C` theo log scale, ví dụ `{1e-3, 1e-2, 1e-1, 1, 10}`; kiểm tra `class_weight=None` và `balanced` như ablation nếu cần.
- `max_iter` đủ lớn; ghi convergence status. Run không convergence không được im lặng lấy metric.
- SVM score là `decision_function`. Nếu cần probability, fit calibrator trên validation hoặc train OOF; không fit calibrator trên test.
- Lưu top positive/negative coefficients theo feature name và block, nhưng không gọi coefficient là causal explanation.

Acceptance: fit được CSR mà không densify, score hữu hạn, row order khớp SHA, load checkpoint dự đoán y hệt trong tolerance.

### 7.3. Random Forest

Mục tiêu: nonlinear tree baseline trên cùng source feature.

- Theo dõi chặt RAM vì sklearn RF có thể tốn bộ nhớ lớn dù input sparse.
- Bắt đầu với range có kiểm soát: `n_estimators` 200–800, `max_depth` `{None, 20, 40}`, `min_samples_leaf` `{1, 2, 5}`, `max_features` `{sqrt, log2, 0.2}`; giới hạn tuning theo budget.
- `n_jobs` là tham số cấu hình, không mặc định lấy toàn bộ CPU trên VM extraction đang chạy.
- Không convert toàn bộ CSR sang dense. Nếu implementation bắt buộc dense, phải estimate bytes trước và fail-fast khi vượt budget.
- Lưu impurity importance chỉ như diagnostic; thêm permutation importance trên validation nếu khả thi.

Acceptance: có preflight RAM, không OOM ở smoke/pilot, probability hữu hạn trong `[0,1]`, deterministic theo seed.

### 7.4. XGBoost

- Dùng `XGBClassifier` với binary objective, nhận CSR trực tiếp.
- CPU mặc định `tree_method=hist`; GPU là config tùy chọn và phải lưu device/software metadata.
- Search có giới hạn: `n_estimators`, `max_depth`, `learning_rate`, `min_child_weight`, `subsample`, `colsample_bytree`, `reg_alpha`, `reg_lambda`.
- Dùng early stopping trên validation; không dùng test làm eval set.
- Không bật `scale_pos_weight` chỉ vì bài toán malware; tính từ train và chỉ dùng nếu protocol yêu cầu.
- Lưu booster config, best iteration và feature gain/cover theo tên.

Acceptance: checkpoint reload cho score ổn định, best iteration được khóa trước test, sparse matrix không bị densify.

## 8. Common GNN trên FCG và CCG

### 8.1. Input hiện có

FCG `graph.npz`:

- `x`: node features gồm internal/external, missing-code, structure, security concepts và 256 opcode `log1p` bins; hiện smoke quan sát width 274.
- `edge_index`: cạnh gọi có hướng.
- `edge_attr`: `log1p` callsite multiplicity + invoke-kind indicators.
- `y`: nhãn APK.
- `mapping.json`: stable node/edge IDs, raw call IDs, feature names.

CCG:

- node class internal/external, method/call degree và security concepts;
- directed class-call graph;
- edge multiplicity;
- mapping class → evidence method/callsite.

Loader phải kiểm tra `len(node_ids)==x.shape[0]`, edge index trong range, feature names đúng width, `y` khớp catalog và không có NaN/Inf.

### 8.2. Track so sánh chính

Để so sánh GCN/GAT/GIN công bằng:

- Dùng đúng cùng view, split, node feature, directedness, graph inclusion và readout.
- Track `representation_controlled` không đọc `edge_attr` cho cả ba model.
- Không symmetrize cạnh âm thầm. Nếu layer/paper cần undirected, tạo experiment config riêng và ghi transform hash.
- Kiến trúc khởi đầu: 2–3 message-passing layers, hidden 64/128, ReLU hoặc GELU, dropout, normalization tùy config, global mean + global max concatenation, MLP classifier.
- Graph label là APK label. Một graph FCG/CCG tương ứng một APK.
- Batching bằng PyG, có dynamic batch budget theo tổng node/edge, không chỉ số graph. Graph oversized có single-graph fallback hoặc deterministic sampling/chunk policy; không tự loại.
- Early stopping theo validation PR-AUC hoặc metric được chốt trước; lưu best epoch.

Model-specific:

- **GCN:** `GCNConv`, ghi rõ normalize/self-loop policy. Cạnh có hướng được dùng theo semantics của PyG; kiểm tra orientation bằng unit test toy graph.
- **GAT:** ghi heads, concat/average, attention dropout. Attention weight chỉ là diagnostic, không được gọi là lời giải thích đáng tin cậy.
- **GIN:** `GINConv` với MLP per layer và trainable/fixed epsilon theo config. GIN chuẩn **không đọc `edge_attr`**.

Search space ban đầu: layers `{2,3}`, hidden `{64,128}`, dropout `[0,0.5]`, learning rate log-uniform `[1e-4,3e-3]`, weight decay `[0,1e-3]`; batch theo node budget. Không grid toàn tổ hợp nếu vượt compute budget; lưu mọi trial, kể cả fail/OOM.

### 8.3. Track edge-aware tách biệt

Nếu dùng edge semantics, đặt tên experiment khác:

- weighted GCN dùng callsite multiplicity đã chuẩn hóa;
- edge-aware GAT với edge dimension hỗ trợ thực sự;
- GINE cho `edge_attr`, không gọi là GIN chuẩn.

Không so sánh edge-aware model với baseline rồi quy kết chênh lệch chỉ do architecture nếu input khác nhau.

### 8.4. FCG so với CCG

Chạy hai dataset/view riêng, cùng protocol và metric. CCG là class-collapse, không phải FCG nén lossless. Báo coverage, graph-size/runtime/memory và chất lượng riêng. Explanation CCG phải drill-down về methods/raw callsites qua mapping.

## 9. GRED

### 9.1. Contract paper cần tái hiện

Pipeline paper cần đối chiếu:

1. APK → full directed FCG.
2. Xác định sensitive API bằng mapping PScout/Axplorer đã version.
3. Tạo sensitive regions k-hop, paper dùng `k=2`; cần ghi rõ neighborhood direction. Adapter hiện dùng weak/undirected để chọn region nhưng giữ directed edges.
4. Node feature chính xác 916 chiều:
   - 247 permission indicators;
   - 224 opcode frequencies;
   - 445 abstract API/package indicators.
5. Graph learning layer.
6. Pool node trong từng sensitive region bằng max + mean; tính score region, chọn/threshold vùng quan trọng theo contract Top-K cải tiến.
7. Aggregate vùng và phân loại bằng MLP hai lớp.
8. Lưu region score/node selection cho intrinsic interpretation; post-hoc edge mask là nhánh riêng.

Không được tạo vector 916 chiều bằng padding/truncating vocabulary tự chọn. External API không có bytecode thì opcode block bằng zero phải kèm missing-code metadata ngoài vector hoặc policy được ghi rõ.

### 9.2. Nhánh chạy với dữ liệu hiện có

Tên bắt buộc: `gred_approx_v1`, fidelity `structural_approximation`.

- Đọc `views/gred/.../graph.npz` và `mapping.json`.
- Giữ nguyên sensitive seed, radius, retained/dropped node, raw-call provenance.
- Có thể xây GCN/GIN + region-aware pooling theo topology hiện có để kiểm tra cơ chế model.
- Feature width là feature dictionary dự án, không phải 916.
- Metric/report/checkpoint phải in watermark `NOT_PAPER_FAITHFUL` và liệt kê missing assets.
- Không dùng kết quả nhánh này như reproduction score của paper.

### 9.3. Cổng mở paper-faithful

Chỉ mở `gred_faithful_v1` khi đủ:

- ordered vocabulary và provenance của 247 permission, 224 opcode, 445 abstract API;
- exact/versioned PScout hoặc Axplorer mapping;
- unit test mapping signature → feature index;
- verified region/Top-K formulation và model hyperparameter contract từ paper/code;
- ít nhất một golden APK/graph hoặc synthetic fixture kiểm được 916 chiều và region membership.

Thiếu bất kỳ asset nào phải trả `blocked_missing_asset`, không fallback im lặng sang approximation.

## 10. SIGDroid và SCKDG

### 10.1. Contract paper cần tái hiện

SIGDroid không chỉ là một GAT/GCN trên internal FCG. Pipeline paper gồm:

1. Full FCG với internal và external functions.
2. JADX Java source và mapping source/method chính xác, có coverage/failure.
3. API category từ APIGraph và permission mapping, gồm thông tin inheritance/content-provider theo paper.
4. Sensitive/noise filtering; lưu node bị loại và rule/version.
5. Parent-child shortcut khi loại external/noise node; mỗi derived edge phải chỉ ra raw path tạo ra nó. Có cap cho tích parents × children và log truncation.
6. Propagate category/permission của external callee vào internal caller.
7. Homogeneous internal graph.
8. Node feature tích hợp API category + permission + LongCoder Java embedding; lưu model/tokenizer ID, revision, source hash, token/chunk/truncation/pooling, dtype/dim.
9. SCKDG student-centered, multi-stage distillation; cấu hình chính paper dùng chuỗi GIN → GCN → GAT. Student của stage trước thành teacher cho stage sau.
10. Targeted teacher training và differentiated student learning phải được triển khai theo công thức/algorithm paper, không thay bằng KD logits thông thường rồi gọi là SCKDG.

“Dynamic subset sampling” trong paper là lấy subset của training data phục vụ cơ chế teacher/student, không phải dynamic APK execution.

### 10.2. Nhánh hiện có

`views/sigdroid` hiện chỉ là internal-function graph structural approximation:

- external nodes bị loại;
- chưa có verified noise rules/derived shortcuts;
- chưa có propagation đầy đủ;
- chưa có JADX source mapping;
- chưa có APIGraph/permission assets chuẩn;
- chưa có LongCoder semantic block;
- chưa có SCKDG.

Có thể tạo `sigdroid_proxy_v1` để smoke loader và common GNN trên internal graph. Report phải mang `NOT_SIGDROID_FAITHFUL`; không gọi đây là SIGDroid detector trong bảng kết quả chính.

### 10.3. Cổng paper-faithful

Yêu cầu đủ và versioned: JADX, Java→method map, APIGraph taxonomy, permission/inheritance mappings, noise rules, homogenization algorithm, LongCoder checkpoint/tokenizer revision, embedding pipeline và đầy đủ SCKDG algorithms/hyperparameters. LongCoder thiếu không được thay bằng opcode vector dưới tên LongCoder.

Training SCKDG phải lưu checkpoint cho từng stage, teacher snapshot, subset SHA, soft targets/logits, temperature/loss weights, validation decision và lineage stage. Test chỉ chạy trên final locked student.

## 11. ANAKIN

### 11.1. Input và fidelity

Adapter hiện có xuất `anakin-reimplementation-v1`, `fit_status=raw_pre_fit`:

- một APK có nhiều sequence/graph theo entrypoint;
- node là API token phân biệt;
- directed transition edge có frequency/weight;
- edge giữ source positions và supporting call IDs;
- graph giữ entry ID, component type và truncation status.

Đây là API-transition graph, không phải method FCG. Custom DFS/entrypoint traversal hiện tại là reimplementation vì public repo không chứng minh tái tạo sequence generator gốc.

### 11.2. Fit train-only bắt buộc

1. Lọc critical Android packages theo danh sách/rule versioned từ paper; kiểm tra typo/normalization bằng test.
2. Mỗi API sequence là một document cho TF-IDF.
3. Tính average TF-IDF trên **train sequences/APKs**, chọn top-k API.
4. Top-k mặc định reproduction là 140; nếu chọn trong `{20,60,100,140,180}`, chỉ dùng train/validation và khóa trước test.
5. Fit Word2Vec CBOW chỉ trên train sequences sau policy đã chốt; paper dùng embedding dimension 100, window 5.
6. Freeze selected API set và Word2Vec. Validation/test OOV phải dùng một policy cố định và báo OOV rate; không update embedding bằng test.
7. Sau pruning, tạo graph directed weighted. Không tự thêm synthetic loop cho singleton nếu không có experiment policy riêng.
8. Tất cả graph con của một APK đi cùng nhau qua sampler và aggregation; không random-split graph con.

Lưu các artifact có định dạng an toàn: selected API list/order, TF-IDF configuration/statistics, Word2Vec parameters/vectors, corpus hash, OOV policy, sequence inclusion/truncation report. Mã repo tham chiếu dùng pickle và `random_split`; không được copy nguyên hai hành vi này vào pipeline canonical.

### 11.3. Detector

Paper chính dùng weighted GCN, global mean readout, MLP/FC và trung bình soft prediction của các graph thuộc một APK. Yêu cầu:

- Edge weight phải đi vào GCN theo đúng normalization đã test.
- Mỗi graph cho một logit/probability; aggregation APK mặc định là mean soft probability. Thử mean logits/max/attention chỉ là ablation.
- Training loss phải được định nghĩa ở level rõ ràng. Ưu tiên APK-level aggregation rồi loss để APK có nhiều sequence không chi phối; nếu dùng graph-level loss, sampler/reweight phải cho mỗi APK trọng số tổng bằng nhau.
- Validation/checkpoint/threshold/evaluation đều ở APK level.
- Empty-after-pruning APK phải có policy rõ: exclude có coverage report hoặc learned/zero empty representation; không gán benign mặc định.

Các hyperparameter paper dùng làm điểm bắt đầu, không phải kết quả được phép chép sang cohort này:

| Architecture | Batch | LR | Dropout | Hidden đầu | Graph layers | FC layers |
|---|---:|---:|---:|---:|---:|---:|
| GCN | 512 | 0.00026 | 0.19 | 64 | 2 | 3 |
| GAT | 512 | 0.0036 | 0.20 | 256 | 2 GAT | 2 |
| GraphSAGE | 128 | 0.0036 | 0.067 | 64 | 3 | 1 |
| GIN | 64 | 0.00072 | 0.37 | 32 | 1 | 1 |

Phạm vi bắt buộc của project là ANAKIN-GCN; GAT/GraphSAGE/GIN trên ANAKIN là ablation tùy budget. Repo tham chiếu có config ví dụ khác paper và có `random_split`; temporal split của project luôn ưu tiên.

## 12. Tuning, checkpoint và sử dụng tài nguyên

- Mọi search chỉ đọc train + validation. Sau khi chọn config, có thể refit train+validation **chỉ nếu protocol được định nghĩa trước**; khi đó threshold/calibration cần một chiến lược không dùng test. Track mặc định nên train trên train và early-stop bằng validation để dễ audit.
- Mỗi trial có seed, config hash, status, runtime, peak RAM/VRAM, best epoch và metric. OOM là kết quả trial, không xóa log.
- Có budget theo wall time/trial count. Random/TPE search được phép nếu dependency được pin; không cần thêm framework nặng nếu một tuner nhỏ đủ dùng.
- GNN checkpoint chọn theo một metric primary đã khóa. Không “cherry-pick” checkpoint khác nhau cho từng test year.
- Gradient clipping, mixed precision, accumulation và distributed training là config; smoke CPU không phụ thuộc chúng.
- DataLoader không được mở quá nhiều worker gây cạnh tranh với extraction. `num_workers`, CPU threads, prefetch và pinned memory phải cấu hình được.
- Archive reader/cache cần LRU/disk budget và atomic extraction. Không tải/unpack toàn corpus vào local disk.

## 13. Evaluation contract

### 13.1. Đơn vị đánh giá

Đơn vị cuối cùng luôn là APK SHA-256. ANAKIN nhiều graph phải aggregate trước metric. Không báo graph-level metric như APK detector metric.

### 13.2. Metrics

Primary đề xuất: PR-AUC cho class malware. Secondary:

- ROC-AUC;
- F1, precision, recall/TPR;
- specificity/TNR, FPR/FAR, FNR;
- balanced accuracy, MCC;
- confusion matrix;
- Brier score/ECE nếu output đã calibrated;
- coverage và extraction/model failure rate.

Chọn threshold trên validation theo rule chốt trước, ví dụ maximize F1 hoặc đạt FPR mục tiêu. Báo cả threshold 0.5 khi probability có nghĩa và locked threshold. Không tối ưu threshold riêng cho từng test year.

### 13.3. Báo cáo temporal

- Aggregate test 2023–2026.
- Từng năm 2023, 2024, 2025, 2026.
- Theo label và các nhóm quality có ý nghĩa, nhưng không dùng subgroup quá nhỏ để khẳng định mạnh.
- Bootstrap confidence interval theo APK; so sánh hai model bằng paired predictions trên cùng tập SHA.
- Báo denominator toàn cohort, số included/excluded/failed và lý do.

Không so trực tiếp score với paper như cùng dataset/protocol. Nếu ghi reproduction, phải phân biệt `paper-reported`, `our temporal cohort`, `approximation` và `paper-faithful`.

## 14. XAI sau huấn luyện

Chỉ chạy explainer sau khi model/checkpoint đã freeze và `eval()`.

### 14.1. GNNExplainer

- Hỗ trợ FCG, CCG, GRED và ANAKIN khi model differentiable.
- Output node/edge/feature masks và map local index → stable ID → raw method/class/API/callsite/instruction evidence.
- ANAKIN aggregate nhiều graph: lưu graph score, APK aggregate và explanation từng graph; không ghép mask khác graph mà mất provenance.
- Dense embedding dimension không được diễn giải thành hành vi nếu không có token/source mapping; aggregate dimension score thành node score khi phù hợp.

### 14.2. PGExplainer

- Explainer trainable chỉ fit trên train graphs/APKs.
- Chọn explainer hyperparameter/budget trên validation.
- Test chỉ inference explainer; lưu explainer checkpoint và parent classifier hash.

### 14.3. Faithfulness

Mỗi explanation lưu tối thiểu:

- `apk_sha256`, model/checkpoint/view/feature hashes, target class;
- prediction trước mask;
- prediction khi chỉ giữ explanation (`keep`/sufficiency);
- prediction khi loại explanation (`remove`/necessity);
- node/edge/feature scores và budget;
- sparsity, stability qua seed/perturbation nếu có;
- mapping coverage và missing/derived evidence.

Attention weights hoặc Top-K score có thể là intrinsic signal nhưng không tự động là causal explanation. Không có ground-truth localization thì không được tuyên bố explanation xác định đúng hành vi độc hại; chỉ đánh giá faithfulness/stability/sparsity.

## 15. Test bắt buộc

### 15.1. Unit/contract tests

- Split guard: fit object từ chối validation/test SHA.
- Catalog join chỉ bằng normalized SHA; phát hiện duplicate/conflict.
- Feature vectorizer fit train rồi transform val/test cùng width/order.
- Sparse pipeline không densify ngoài giới hạn đã cho phép.
- Toy directed graph xác minh orientation/message passing.
- Empty/singleton/zero-edge/oversized graph.
- FCG/CCG mapping round-trip local index → stable ID → raw evidence.
- Standard GIN không nhận edge_attr; GINE có nhận.
- ANAKIN: weighted transitions, top-k train-only, OOV, APK-level aggregation và không split subgraph.
- GRED: approximation bị chặn khi yêu cầu faithful; faithful fixture đúng width 916.
- SIGDroid: missing LongCoder/APIGraph trả blocked; derived edge có complete path provenance.
- Checkpoint reload cho prediction trong tolerance.
- Metrics trên fixture nhỏ đối chiếu giá trị biết trước; threshold chỉ fit validation.
- XAI keep/remove giữ đúng graph mapping.

### 15.2. Integration tests

- Stream một batch archive đã verified → catalog → feature/graph loader → một epoch/tiny fit → predictions → metrics.
- Cùng một benign và malware sample cho classical và common GNN.
- Resume run không nhân đôi prediction hoặc ghi đè checkpoint khác config.
- Corrupt checksum/schema/view version phải fail-fast.

### 15.3. Performance tests

- Đo peak RSS khi vectorize classical records và khi RF fit pilot.
- Đo node/edge budget của PyG batch, hành vi OOM fallback.
- Đo archive cache/disk high-water mark.
- Không cần benchmark tốc độ full cohort trong CI.

## 16. Trình tự triển khai bắt buộc

### P0 — nền tảng dữ liệu và classical

1. Build read-only catalog từ receipt/archive/inventory.
2. Data/fidelity preflight và leakage audit skeleton.
3. Mở rộng named classical record.
4. Train-only sparse fit/transform.
5. SVM, RF, XGBoost trainer/evaluator/checkpoint.
6. Unit + integration smoke.

### P1 — common GNN

1. FCG/CCG loader và dynamic graph batching.
2. Representation-controlled GCN/GAT/GIN.
3. Edge-aware experiments tách biệt.
4. Temporal evaluator, artifact registry và resume.

### P2 — ANAKIN reimplementation

1. Train-only critical filter/average TF-IDF/top-k.
2. Train-only CBOW Word2Vec và frozen transform.
3. Weighted GCN, APK-level aggregation.
4. Mapping/explanation readiness.

### P3 — paper branches

1. GRED approximation model để kiểm tra region pooling.
2. GRED faithful chỉ sau asset gate.
3. SIGDroid proxy chỉ cho shape/loader smoke.
4. SIGDroid/SCKDG faithful chỉ sau toàn bộ semantic/distillation gate.

### P4 — XAI

GNNExplainer trước; PGExplainer sau khi có đủ train graph và checkpoint ổn định. Thêm faithfulness evaluation và raw-evidence export.

Không để P3 bị thiếu asset chặn P0/P1/P2. Nhưng cũng không hạ fidelity label để “hoàn tất” P3.

## 17. Definition of Done

Giai đoạn **implementation** hoàn tất khi:

- Có CLI/config/schema cho mọi detector trong phạm vi.
- Classical và common GNN chạy end-to-end trên verified mini-pilot, có checkpoint reload và predictions APK-level.
- ANAKIN fit artifacts chứng minh train-only và chạy end-to-end trên mini-pilot.
- GRED/SIGDroid có fidelity gate tự động; approximation/proxy không thể bị báo nhầm faithful.
- Unit/integration tests qua; ruff qua; dependencies/lock cập nhật.
- Run artifacts có manifest/hash/provenance đủ để tái lập.
- Có hướng dẫn lệnh smoke, full train, resume, evaluate, explain và status.

Giai đoạn **benchmark toàn cohort** chỉ hoàn tất khi:

- extraction/catalog coverage đạt protocol đã khóa;
- leakage/quality audit được chấp nhận;
- mọi model đã chọn config chỉ bằng train/validation;
- test 2023–2026 được chạy đúng một protocol, có predictions và per-year metrics;
- coverage/failure/fidelity được báo cùng metrics;
- không có claim paper-faithful sai.

## 18. Những việc không được làm

- Không random split lại cohort hoặc graph con.
- Không fit vocabulary/scaler/TF-IDF/Word2Vec/calibration trên validation/test.
- Không dùng SHA/path/package identity/year/VT/signature certificate làm shortcut feature baseline.
- Không giả feature 916 cho GRED bằng padding.
- Không thay LongCoder bằng opcode/smali rồi giữ tên LongCoder.
- Không gọi generic GCN trên internal graph là SIGDroid/SCKDG.
- Không gọi consumer smoke là model benchmark.
- Không bỏ failure khỏi denominator.
- Không unpack toàn bộ corpus vào local disk.
- Không dùng attention/feature importance như bằng chứng causal hoặc malware localization ground truth.
- Không chạy full training trong khi extraction đang dừng/chưa đủ cohort, trừ khi người dùng duyệt rõ một partial-cohort experiment và report gắn nhãn `partial/non-final`.

## 19. Yêu cầu báo cáo của AI tiếp nhận

Trước khi sửa code, trả một bản kiểm tra ngắn gồm: trạng thái extraction mới nhất, số sample/view usable theo split, asset blockers, dependency/device, thiết kế file và thứ tự P0–P4. Sau đó triển khai liên tục trong phạm vi đã xác định, không yêu cầu xác nhận cho từng file nhỏ.

Mỗi mốc phải báo theo mẫu:

```text
Implemented:
Verified:
Data/fidelity status:
Artifacts:
Blocked:
Next:
```

Nếu extraction vẫn chưa đủ, hãy hoàn thiện code + tests + mini-pilot trước, rồi bàn giao lệnh full training. Không tự tuyên bố kết quả cuối cùng và không tự khởi chạy một job GPU/chi phí lớn ngoài tài nguyên người dùng đã cho phép.

## 20. Dữ liệu thực tế được trích xuất cho từng model

### 20.1. Nguyên tắc “trích xuất một lần, tạo nhiều view”

Pipeline hiện tại không chạy một decompiler riêng cho từng model. Mỗi APK được Androguard phân tích một lần thành canonical IR, sau đó các adapter chỉ đọc IR này để tạo input view phù hợp. Vì vậy cần phân biệt:

1. **Canonical IR đã trích xuất:** bằng chứng gốc dùng chung cho mọi model.
2. **Model view đã sinh:** graph/record có thể load trực tiếp.
3. **Train-only transform chưa chạy:** vocabulary, scaler, TF-IDF, Word2Vec hoặc model parameters chỉ được fit sau khi toàn bộ train data sẵn sàng.
4. **Missing paper asset:** phần chưa có và không được giả lập dưới tên paper-faithful.

Mỗi sample hiện có các bảng/file sau:

| Artifact | Nội dung chính |
|---|---|
| `samples.parquet` | SHA-256, binary label, split, year, package/version/SDK, APK size, analyzer/config, trạng thái phân tích và archive provenance |
| `signing_verification.parquet`, `certificates.parquet` | Kết quả verify scheme/signers và certificate metadata; giữ để audit, không dùng signer identity làm baseline feature |
| `permissions.parquet` | Permission name, kind, max SDK, source |
| `components.parquet` | Activity/service/receiver/provider, exported/enabled/process/permission và stable component ID |
| `intent_filters.parquet`, `provider_rules.parquet`, `app_config.parquet` | Intent actions/categories/data, provider access rules, app config/features |
| `dex_files.parquet`, `classes.parquet`, `fields.parquet`, `methods.parquet` | Multi-DEX inventory, class hierarchy, fields và method signature/code metadata |
| `instructions.parquet` | Opcode, opcode name, operands/output, ordinal và byte offset theo method |
| `string_constants.parquet` | String literal, method/instruction/offset provenance |
| `callsites.parquet`, `call_targets.parquet` | Caller, invoke kind, declared/resolved target, internal/external kind và evidence policy |
| `basic_blocks.parquet`, `cfg_edges.parquet` | Basic blocks, instruction membership và control-flow edges |
| `entrypoints.parquet`, `api_sequences.parquet` | Lifecycle/component entrypoint, bounded API sequence, token order, callsite provenance và truncation status |
| `knowledge_matches.parquet` | Versioned security-taxonomy matches |
| `resources.parquet`, `native_libraries.parquet` | Resource/native library inventory; chưa phải native-code semantic analysis |
| `quality-report.json`, `validation.json`, `inventory.json`, `checksums.sha256` | Quality, schema/checksum validation và file inventory |

### 20.2. Linear SVM, Random Forest và XGBoost

Ba model này dùng cùng một record sparse trên mỗi APK để so sánh công bằng.

**Đã trích xuất trực tiếp trong `classical-records.json`:**

- Binary requested-permission feature: `permission::<permission_name> = 1`.
- Raw opcode count theo opcode name: `opcode::<opcode_name> = count`.
- `graph::method_nodes`: số method row.
- `graph::callsites`: số invoke/callsite.
- `graph::resolved_targets`: số resolved call target.
- `apk_sha256`, label, split và year chỉ để join/evaluate; không đưa các metadata này vào feature vector.

**Có trong canonical IR nhưng classical record hiện tại chưa aggregate hết:**

- component count/type/exported/enabled;
- intent action/category và provider access rules;
- API/package/category counts, invoke-kind counts, internal/external call ratio;
- normalized opcode histogram;
- graph density, degree statistics, SCC/WCC, isolates, reciprocal edges, sensitive-node ratio;
- CCG summaries và optional WL fingerprint.

AI modeling phải bổ sung aggregator từ canonical IR; không cần phân tích lại APK. Sau đó fit `DictVectorizer` trên toàn bộ train, freeze dictionary và transform validation/test thành CSR `float32`. SVM, RF và XGBoost phải nhận cùng row order và cùng feature names. Scaler/selector/calibrator đều là train-only artifact, không phải extraction output.

### 20.3. GCN, GAT và GIN trên FCG

Mỗi APK có:

```text
views/fcg/<apk_sha256>/graph.npz
views/fcg/<apk_sha256>/mapping.json
```

**Node là:**

- internal method được định nghĩa trong APK; hoặc
- external target method/API được callsite tham chiếu.

**Node feature `x`, hiện đúng 274 chiều:**

- 2 chiều loại node: internal, external;
- 1 chiều `missing.code`;
- `log1p(instruction_count)`;
- `log1p(in_calls)` và `log1p(out_calls)`;
- 12 security concept indicators: SMS, contacts, device identifiers, location, network, crypto, reflection, dynamic loading, file I/O, process execution, accessibility, package management;
- 256 chiều opcode count `log1p` cho internal method; external/missing-code node không có opcode bytecode.

**Topology:**

- `edge_index [2,E]` là directed caller → callee;
- nhiều callsite cùng cặp method được gộp thành một graph edge nhưng không mất raw-call IDs.

**Edge feature `edge_attr`, 9 chiều:**

- `log1p_callsite_multiplicity`;
- indicator cho `direct`, `static`, `virtual`, `interface`, `super`, `custom`, `polymorphic`, `unknown`.

**Mapping/XAI sidecar:**

- local node index → stable method/external-target ID;
- local edge index → stable edge ID;
- edge → source/destination stable IDs và toàn bộ raw call IDs;
- ordered node/edge feature names.

GCN/GAT/GIN track chính dùng cùng `x` và `edge_index`; standard GIN không dùng `edge_attr`. Weighted GCN, edge-aware GAT và GINE là track khác. Model hidden vectors, readout và checkpoint chưa phải extraction data.

### 20.4. GCN, GAT và GIN trên CCG

Mỗi APK có `views/ccg/<sha>/graph.npz` và `mapping.json`.

**Node là class**, được collapse từ method FCG. External class/package target vẫn được giữ như external node khi có evidence.

**Node feature `x`, hiện 17 chiều:**

- internal/external;
- `log1p(method_count)` trong class;
- `log1p(in_calls)` và `log1p(out_calls)`;
- 12 security concept indicators.

**Edge:** directed class caller → class callee. `edge_attr` một chiều là `log1p` số raw method callsites đã collapse. Mapping giữ class descriptor/definition status, class edge và raw call IDs để explanation ở class level có thể drill-down về method/callsite.

CCG có fidelity `canonical_class_collapse`; nó là view khác FCG, không phải graph nén lossless.

### 20.5. GRED

Mỗi APK hiện có `views/gred/<sha>/graph.npz` và `mapping.json`, nhưng đây là `gred-approx-v1`, không phải feature/model chính xác của paper.

**Đang trích xuất thật:**

- Full canonical FCG làm nguồn.
- Sensitive external API target được nhận diện bằng 12 security taxonomy concepts của project.
- Sensitive seed gồm sensitive external target và internal caller gọi target đó.
- Region radius mặc định `k=2`.
- Khi chọn region, adapter coi adjacency hai chiều/weakly connected; sau khi chọn node vẫn giữ directed caller → callee edges.
- Lưu `sensitive_seed_node_ids`, retained nodes, dropped nodes, radius, neighborhood policy và raw call IDs.
- Node feature 18 chiều: internal/external, missing code, instruction/in/out-call structure và 12 security concepts.
- Edge feature một chiều: `log1p` callsite multiplicity.

**Chưa trích xuất được theo paper:**

- exact PScout/Axplorer snapshot để xác định sensitive API/permission;
- ordered permission dictionary 247 chiều;
- ordered Smali opcode dictionary 224 chiều theo paper;
- ordered abstract API/package dictionary 445 chiều;
- do đó chưa có exact 916-dimensional node vector;
- paper-faithful sensitive-region pooling/Top-K là model training layer, chưa chạy trong extractor.

Status bắt buộc là `blocked_missing_asset`, fidelity `structural_approximation`. Không được padding feature hiện tại lên 916 rồi gọi là GRED.

### 20.6. SIGDroid

Mỗi APK hiện có `views/sigdroid/<sha>/graph.npz` và `mapping.json`, nhưng chỉ là `sigdroid-approx-v1`.

**Đang trích xuất thật:**

- Node là internal method có trong APK.
- Chỉ giữ direct internal → internal call edge.
- Lưu danh sách external nodes bị loại.
- Node feature 16 chiều: missing-code, `log1p` instruction/in/out calls và 12 security concepts.
- Edge feature một chiều: `log1p` raw-call multiplicity.
- Edge mapping giữ stable IDs và raw call IDs.

**Chưa có:**

- JADX Java source và Java range → canonical method mapping;
- verified APIGraph category assets;
- exact API-to-permission/inheritance/content-provider propagation;
- paper noise-removal rules;
- parent-child shortcut/derived edges với evidence chain;
- LongCoder checkpoint/tokenizer/source embeddings;
- homogeneous integrated feature block của paper;
- SCKDG multi-stage GIN → GCN → GAT training.

Vì vậy output hiện tại chỉ dùng để test loader/provenance hoặc generic internal-graph proxy. Nó luôn phải ghi `blocked_missing_asset` và `NOT_SIGDROID_FAITHFUL`.

### 20.7. ANAKIN

Mỗi APK có `views/anakin/<sha>/graphs.json`, fidelity `reimplementation`, fit status `raw_pre_fit`.

**Nguồn sequence đã trích xuất:**

- component/lifecycle entrypoint;
- bounded static traversal từ CFG/FCG;
- ordered API tokens;
- mỗi token giữ call ID/provenance;
- component type, entry ID, truncation flag/reason và traversal config hash.

**Graph đã sinh cho từng sequence:**

- node là distinct API token trong sequence;
- directed transition `API_i → API_{i+1}`;
- edge `weight` là số lần transition lặp;
- `source_positions` chỉ ra vị trí transition trong sequence;
- `supporting_call_ids` map transition về callsite gốc;
- singleton graph không được thêm synthetic self-loop;
- một APK có thể có nhiều graph, giữ mapping APK → entrypoint/sequence → graph.

**Chưa fit trong extraction:**

- critical-package filter cuối cùng;
- average TF-IDF và top-k API vocabulary;
- Word2Vec CBOW embedding (paper: dimension 100, window 5);
- weighted GCN weights;
- mean soft prediction của nhiều graph về APK prediction.

TF-IDF/top-k/Word2Vec bắt buộc fit trên train 2019–2021 rồi freeze. Sequence generator hiện là custom reimplementation, không được gọi là sequence gốc của paper.

### 20.8. Dữ liệu phục vụ GNNExplainer và PGExplainer

Extractor không chạy explainer và không tạo explanation mask. Nó đang trích xuất prerequisite:

- stable APK/class/method/instruction/callsite IDs;
- node/edge local index mappings;
- raw call IDs cho direct graph edges;
- class → method/callsite evidence ở CCG;
- GRED seed/retained/dropped membership;
- ANAKIN sequence position/transition → callsite evidence;
- ordered interpretable feature names;
- graph/view/config hashes và checksums.

Sau khi model được train, explainer mới tạo node/edge/feature scores, keep/remove predictions, sparsity và stability. SIGDroid derived-edge chain chưa có; LongCoder dense dimensions cũng chưa có source-token mapping, nên nhánh SIGDroid XAI paper-faithful vẫn bị chặn.

### 20.9. Ví dụ shape của một APK đang được xử lý

Một APK benign 2019 kiểm tra trực tiếp trong batch hiện tại cho kết quả minh họa:

| View | Nodes | Edges | Node features | Edge features/status |
|---|---:|---:|---:|---|
| FCG | 14.893 | 22.666 | 274 | 9 |
| CCG | 2.075 | 6.821 | 17 | 1, canonical class collapse |
| GRED approximation | 5.248 | 11.562 | 18 | 1, blocked missing assets |
| SIGDroid approximation | 11.350 | 9.755 | 16 | 1, blocked missing assets |
| ANAKIN reimplementation | 6 sequence graphs | phụ thuộc sequence | embedding chưa fit | raw pre-fit |

Đây chỉ là shape của một APK, không phải kích thước cố định. Loader/model phải hỗ trợ graph-size distribution rất rộng và báo p50/p95/p99/max trên toàn cohort.
