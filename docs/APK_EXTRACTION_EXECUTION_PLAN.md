# Kế hoạch thực thi pipeline trích xuất APK

**Trạng thái:** chờ người dùng duyệt — chưa triển khai  
**Ngày khảo sát:** 2026-11-07 UTC  
**Project root dự kiến:** `/home/rl/Data`  
**Pipeline/schema đích ban đầu:** `android-static-v1` / `ir-v1`

## 1. Quyết định và phạm vi của kế hoạch

Sau khi được duyệt, dự án sẽ được xây mới vì workspace hiện không có code, IR hay APK cục bộ để tái sử dụng. Một APK chỉ được parse thành canonical IR một lần cho cùng cache key; classical features, FCG/CCG, GRED, SIGDroid và ANAKIN là các view dẫn xuất. Pipeline xử lý tuần tự từng archive và từng batch nhỏ, upload rồi xác minh trước khi dọn cache.

Phạm vi triển khai được đề xuất:

1. P0 đầy đủ: ingestion an toàn, APK/ZIP validation, metadata/signing, Manifest, multi-DEX, class/field/method, instructions/opcodes/raw invokes, callsites/call targets, resources/native inventory, knowledge matches, canonical FCG, classical/common-GNN adapters và provenance.
2. P1 trong cùng đợt code nhưng bật sau khi primitive P0 ổn định: CFG/basic blocks, entrypoints, API sequences, CCG, diagnostics và ANAKIN transition graphs.
3. P2 tách feature flag: JADX/source alignment và semantic embedding. JADX/source có thể pilot trên subset; LongCoder chỉ bật khi xác minh đúng model revision và đo đủ RAM/runtime.
4. Consumer smoke cho 9 đích: Linear SVM, RF, XGBoost, GCN, GAT, GIN, GRED, SIGDroid, ANAKIN. Đây chỉ là kiểm tra contract/load/forward/backprop nhỏ, không phải benchmark nghiên cứu.
5. Không train toàn cohort, không chạy explainer đầy đủ và không chạy 10.000 APK nếu chưa có chỉ định riêng sau smoke.

## 2. Hiện trạng VM và dữ liệu

### 2.1. VM

| Hạng mục | Kết quả khảo sát | Hệ quả thiết kế |
|---|---|---|
| OS/arch | Ubuntu 24.04.4 LTS, kernel 7.0.0-42, x86_64, VMware | Linux được hỗ trợ; pin binary x86_64 |
| CPU | 48 vCPU, 2 NUMA nodes | Có tiềm năng song song nhưng bắt đầu 1 worker vì parser có peak RAM không đều |
| RAM/swap | 62 GiB RAM, khoảng 58 GiB available; 4 GiB swap | Đủ P0 CPU, không coi swap là RAM; concurrency dựa trên p95 peak pilot |
| GPU | Không có `nvidia-smi` hay `/dev/nvidia*` | PyTorch/PyG CPU; LongCoder full-cohort không được giả định khả thi |
| Disk | ext4 duy nhất `/`, 195.8 GiB; còn 99.4 GiB; inode còn nhiều | `work` và OS chung filesystem; phải giữ reserve và không tải toàn cohort |
| Quyền | user `rl`, thuộc nhóm sudo/docker; sudo không non-interactive | Ưu tiên uv/project-local tools; bước apt/JDK cần người dùng nhập sudo nếu dùng apt |
| Workspace | Chỉ có `task.md` (67,813 bytes); chưa là Git repo | Không có implementation/fixture/lockfile để reuse |
| Python | CPython 3.12.3; uv 0.11.5 | Phù hợp contract; tạo `.venv` và `uv.lock` |
| CLI sẵn có | git 2.43, GNU tar 1.35, zstd 1.5.5, rclone 1.60.1-DEV, Docker 29.1.3 | tar/zstd dùng được; rclone phải nâng cấp trước upload vì quá cũ |
| Thiếu | Java/JDK, Android SDK, apksigner, apkanalyzer, JADX, Apktool, baksmali; mọi core Python package trừ PyYAML | Bootstrap là phase riêng, có version manifest |

Các thư mục code khác dưới `/home/rl` không nằm trong workspace và không được người dùng cung cấp cho nhiệm vụ này; kế hoạch không đọc, sửa hay coi chúng là dependency.

### 2.2. Drive và cohort

Remote rclone `nad` đã tồn tại, config có mode `0600`, backend `drive`, và probe read-only vào đúng folder ID thành công. Chưa thử quyền ghi và chưa tải manifest/APK.

Remote inventory read-only cho thấy:

| Chỉ số | Quan sát 2026-11-07 |
|---|---:|
| Tổng object dưới source prefix | 63 |
| `final-manifest.csv` | 1 file, 2,356,031 bytes |
| Archive `.tar.zst` hiện có | **14**, không phải 16 |
| Sidecar | 48 = đủ 3 sidecar cho 16 tên archive |
| Tổng kích thước 14 archive hiện có | 98,621,531,284 bytes = 91.85 GiB |
| Archive lớn nhất | `train/2021/train-2021-malware.tar.zst`, 15,462,694,173 bytes = 14.40 GiB |

Hai source archive bị thiếu trong listing dù sidecar tương ứng có mặt:

- `test/2024/test-2024-malware.tar.zst`
- `test/2026/test-2026-malware.tar.zst`

Đây là **hard gate cho full run**, không phải lý do cản xây code hoặc smoke bằng archive còn lại. `plan-input` phải trả lỗi bất biến `expected_archives=16, discovered_archives=14`, không suy sidecar thành archive và không âm thầm giảm cohort. Sau khi được duyệt, bước đầu tiên mới tải `final-manifest.csv` nhỏ, khóa SHA-256, parse CSV đúng quoting và đối chiếu 10.000 SHA/split/label/status với inventory.

### 2.3. Credential hygiene

`task.md` đang chứa OAuth client secret dạng rõ. Secret này không được copy vào code, config, log hay tài liệu. Trước khi khởi tạo/commit Git, cần xoay (rotate) OAuth credential hoặc redaction khỏi file đặc tả; tối thiểu không được commit `task.md` khi còn secret. Token rclone tiếp tục ở file ngoài project, mode `0600`. Project chỉ nhận đường dẫn qua `RCLONE_CONFIG` và tên remote.

## 3. Kiến trúc đích và phần tái sử dụng

### 3.1. Luồng dữ liệu

```text
remote inventory + sidecars + locked manifest
  -> preflight (dependency/auth/disk/archive completeness)
  -> one compressed archive download + SHA-256/MD5 verify
  -> zstd/tar sequential reader with safe-member policy
  -> bounded APK spool
  -> APK hash/ZIP/Manifest/DEX validation
  -> one Androguard analysis session per APK/cache key
  -> canonical primitive IR (Parquet + content-addressed sidecars)
  -> deterministic adapters/views (records/NPZ/mappings)
  -> schema + semantic validation + quality report
  -> immutable batch bundle + checksums
  -> Drive upload + remote size/MD5 verify + receipt
  -> delete only pipeline-owned verified cache
  -> next batch/archive
```

### 3.2. Module build/reuse matrix

| Trách nhiệm | Xây mới trong project | Tái sử dụng | Mức |
|---|---|---|---|
| Config/CLI/provenance | Typed config, config hash, CLI, tool manifest | Pydantic/YAML/Typer hoặc argparse | P0 |
| Drive inventory/transfer | Immutable input plan, upload receipt, idempotency | rclone subprocess + Drive remote hiện có | P0 |
| State/locking | SQLite state, migrations, leases, atomic receipts | Python sqlite3, file lock | P0 |
| Archive reader | zstd stream, tar safety, bounded spool, resume-by-SHA | `zstandard`, `tarfile` | P0 |
| APK validation | Hash, ZIP CRC/limits/duplicates, Manifest/DEX presence | hashlib/zipfile | P0 |
| APK/DEX analyzer | Adapter bọc tool, normalize lỗi và offsets | Androguard 4.1.4 | P0 |
| Signing verifier | Structured capture stdout/stderr/exit/version | apksigner; Androguard certificate inventory | P0 |
| Cross-check tools | Subset comparison exporters | apkanalyzer, Apktool, baksmali/dexlib2 | P0/P1 verification |
| Canonical IR | Arrow schemas, stable IDs, writers/readers/validators | PyArrow | P0/P1 |
| Knowledge | Versioned taxonomy and mapping loader | Project taxonomy; verified PScout/Axplorer/APIGraph assets when available | P0 |
| Graphs/adapters | FCG, CCG, common tensors, classical, GRED/SIGDroid/ANAKIN | NumPy/SciPy/NetworkX/PyG | P0/P1 |
| Source semantics | JADX runner, method mapping, content hashes | JADX; optional LongCoder | P2 |
| Verification CG | Subset runner and edge diff | FlowDroid/Soot | Optional |
| Quality/report | Coverage, failures, percentiles, leakage audit contracts | PyArrow/NumPy/NetworkX | P0/P1 |
| Consumer smoke | Tiny fit/forward/backprop/load tests | sklearn/XGBoost/PyTorch/PyG | Acceptance |

Không gọi cross-check tool trên toàn bộ 10.000 APK. Androguard là nguồn extraction chính; tool phụ tạo bảng agreement/disagreement trên subset có chủ đích.

## 4. Cây file sẽ tạo

```text
/home/rl/Data/
├── task.md                         # giữ nguyên; không commit khi còn secret
├── pyproject.toml
├── uv.lock
├── .gitignore
├── configs/
│   ├── pipeline.yaml
│   ├── smoke.yaml
│   └── full-p0-p1.yaml
├── schemas/
│   ├── ir-v1.json
│   ├── graph-v1.json
│   ├── sequence-v1.json
│   ├── inventory-v1.json
│   └── state-v1.json
├── assets/
│   ├── security-taxonomy-v1.json
│   └── sources.json
├── knowledge/
│   └── README.md                   # snapshots đi kèm license/source/checksum
├── src/android_pipeline/
│   ├── cli.py
│   ├── config.py
│   ├── provenance.py
│   ├── drive.py
│   ├── archive_reader.py
│   ├── state.py
│   ├── extractor/
│   │   ├── apk_validation.py
│   │   ├── metadata.py
│   │   ├── manifest.py
│   │   ├── dex.py
│   │   ├── instructions.py
│   │   ├── calls.py
│   │   ├── cfg.py
│   │   ├── entrypoints.py
│   │   ├── resources_native.py
│   │   └── source.py
│   ├── ir/{schema.py,ids.py,io.py,validation.py}
│   ├── knowledge/{loader.py,matcher.py}
│   ├── adapters/{classical.py,fcg.py,ccg.py,gred.py,sigdroid.py,anakin.py}
│   ├── consumers/{classical.py,common_gnn.py,paper_views.py}
│   └── quality.py
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── fixtures/                   # tự sinh/tiny legal fixtures, không cohort APK
│   └── fault_injection/
├── scripts/{bootstrap.sh,smoke.sh,launch.sh,status.sh}
├── docs/
│   ├── APK_EXTRACTION_EXECUTION_PLAN.md
│   ├── SCHEMA_AND_FEATURES.md
│   ├── QUICKSTART.md
│   └── APK_EXTRACTION_IMPLEMENTATION_REPORT.md
├── state/                          # SQLite/receipts; giữ sau cleanup, không credential
└── work/                           # cache có owner marker, có thể dọn có điều kiện
```

## 5. IR/schema và khả năng dựng lại

### 5.1. Bảng primitive

Mỗi batch có `inventory.json`, `schema.json`, `quality-report.json`, `checksums.sha256`, `module_runs.parquet`, `failures.parquet` và tối thiểu các bảng sau:

| Nhóm | Bảng |
|---|---|
| Sample/input | `samples`, `apk_zip_entries`, `archive_members`, `signing` |
| Manifest | `manifest_documents`, `permissions`, `components`, `intent_filters`, `provider_rules`, `app_config` |
| DEX | `dex_files`, `classes`, `fields`, `methods`, `instructions` |
| Calls/CFG | `callsites`, `call_targets`, `basic_blocks`, `cfg_edges`, `entrypoints`, `api_sequences` |
| Other evidence | `resources`, `native_libraries`, `payload_indicators`, `knowledge_matches`, `source_records`, `source_alignments` |

Schema JSON ghi cho từng field: Arrow type, nullable, semantic definition, unit, direct/inferred/derived source, tool/version, enum version và missing-reason contract. P1/P2 bị tắt xuất hiện trong inventory với `not_enabled`, không tạo bảng rỗng giả thành công.

### 5.2. Stable IDs và offsets

- SHA APK normalize lowercase chỉ sau khi validate 64 hex characters.
- ID dùng `SHA-256` trên tuple UTF-8 length-prefixed, prefix domain/version (`android-ir-id-v1`) và tag riêng cho `null`; không ghép chuỗi bằng delimiter.
- `class_id`, `method_id`, `instruction_id`, `call_id`, `block_id` theo tuple trong đặc tả; DEX identity là DEX SHA-256 + entry path/index.
- External ref dùng domain `external-ref` + full descriptor/name/prototype; alias/resolution evidence tách riêng.
- Offset canonical là byte offset trong method. Offset gốc của tool giữ trong cột `source_offset`, `source_offset_unit` và conversion status.
- Local tensor index luôn có sidecar `index -> stable_id`; derived edge có relation, transformation version và `raw_call_ids[]`/provenance DAG.

### 5.3. Derived artifacts

- Primitive IR là nguồn chuẩn và đọc được không cần PyTorch.
- Classical raw named records được lưu trước fit; CSR/dictionary/scaler chỉ sinh bởi job fit train 2019–2021 rồi freeze.
- FCG là directed method/API graph. Parallel callsites có thể collapse ở view nhưng multiplicity và raw IDs vẫn giữ.
- CCG coarsen deterministic, lưu class-to-method và edge-to-call mappings.
- NPZ là tensor chuẩn; PyG `.pt` chỉ là cache tái tạo được.
- Cache key gồm APK SHA, analyzer/toolchain digest, IR schema, Android/API policy, knowledge version, view config và feature config. Training artifacts thêm train-manifest hash.

## 6. State machine, resume và remote integrity

SQLite dùng WAL, migration version, một process-level lock và lease có TTL. Unique constraints trên `(source_archive_id, member_path)`, `apk_sha256`, `(batch_id, artifact_name)` và remote target ngăn duplicate. State archive/batch:

```text
pending -> downloaded -> checksum_verified -> streaming
-> extracted -> validated -> uploaded -> remote_verified -> cache_cleaned
```

Sample/module có trạng thái riêng: `pending`, `running`, `succeeded`, `partial`, `failed`, `timeout`, `oom`, `not_enabled`, `blocked_missing_asset`. Mọi transition commit kèm timestamp, attempt, tool/config/cache key và lỗi có cấu trúc.

Resume rules:

1. File local chỉ được reuse khi size + checksum + expected input ID khớp.
2. Crash giữa tar stream có thể decompress lại từ đầu; member đã có remote-verified receipt được hash/join rồi skip, không dùng byte-offset giả như zstd seek point.
3. Upload target là immutable path chứa pipeline/schema/config hash và batch ID. Retry trước hết tìm receipt/object ID; không tạo file cùng tên mơ hồ.
4. Google Drive cung cấp size/MD5 cho binary object: receipt yêu cầu size và remote MD5 khớp local. Bundle còn có SHA-256 manifest; profile audit có thể stream-read remote để tính lại SHA-256 trước cleanup. Không cleanup nếu verify chưa đạt.
5. Cleanup chỉ đi dưới canonical `work_root` có owner marker và resolved path check; không theo symlink, không xóa source remote, `state`, file người dùng hoặc output chưa verify.
6. Summary invariant theo unique SHA: `expected = succeeded + failed + pending`; complete chỉ khi required modules/views đều remote-verified và không failed/pending.

## 7. Disk-bounded ingestion và ngân sách

### 7.1. Công thức preflight

Tại mọi thời điểm cần:

```text
free_required = compressed_archive
              + raw_spool_batch
              + primitive_IR_pending
              + derived_views_pending
              + optional_source_pending
              + upload_pack_or_copy_overhead
              + quarantine_cap
              + disk_reserve
```

Preflight dùng remote size thực và hệ số expansion đo từ pilot theo module/config, không dùng con số hardcode làm bảo đảm. Nếu không có measurement, áp conservative cap và hạ batch. Disk watermark được kiểm tra trước download, trước spool từng member và trước materialize view.

### 7.2. Giá trị khởi đầu

- `max_inflight_archives=1`, `workers=1`, `upload_transfers=2`.
- Archive lớn nhất hiện đo 14.40 GiB.
- `batch_max_apks=6`, `batch_max_raw_gib=0.5` cho pilot đầu; tăng tối đa 10/1 GiB chỉ sau measurement.
- `disk_reserve_gib=12` vì `/home`, OS và work dùng cùng root filesystem; có thể hạ về 8 sau pilot nếu an toàn.
- `quarantine_cap_gib=2`; mẫu vượt cap chỉ giữ diagnostics/hash, không nhân bản APK.
- P2 source/LongCoder tắt trong run P0/P1 đầu tiên; source batch có budget riêng.
- Không download đồng thời archive kế tiếp khi archive hiện tại chưa cleanup.

Envelope bảo thủ cho pilot với archive lớn nhất là khoảng 44.9 GiB: 14.4 GiB archive + 0.5 GiB raw spool + 8 GiB IR/views cap + 8 GiB upload/copy headroom + 2 GiB quarantine + 12 GiB reserve. Các cap 8 GiB chưa phải dự báo output thực; pilot sẽ đo `IR_bytes/APK_bytes` và `view_bytes/IR_bytes`, sau đó preflight thay chúng bằng p95 có safety factor 1.5. Source transfer hiện tối thiểu 91.85 GiB cho 14 archive đang có, chưa tính hai archive thiếu và output uploads.

Với 99.4 GiB trống và archive lớn nhất 14.40 GiB, VM đủ headroom cho smoke/P0 streaming. VM **không đủ** để giữ đồng thời toàn bộ 91.85 GiB compressed hiện thấy, càng không đủ để giải nén raw cohort khoảng 100.86 GiB cùng IR. Full extraction chỉ hợp lệ theo batch/stream.

## 8. Toolchain và asset matrix

### 8.1. Phiên bản đề xuất

Các version dưới là candidate freeze. Sau bootstrap sẽ chạy import/CLI compatibility test, ghi checksum và tạo `uv.lock`; không tự nâng giữa batch.

| Thành phần | Candidate | Hiện trạng/ghi chú |
|---|---|---|
| Python / uv | 3.12.3 / 0.11.5 | Có sẵn, required |
| rclone | **1.76.0** project-local | Installed 1.60.1 quá cũ và bỏ lỡ nhiều security fixes; phải nâng trước write |
| GNU tar / zstd CLI | 1.35 / 1.5.5 | Có sẵn; Python stream dùng `zstandard==0.25.0` |
| Androguard | 4.1.4 | Stable PyPI; không dùng unreleased v5 |
| NumPy / SciPy / PyArrow | 2.5.4 / 1.18.1 / 26.0.0 | Candidate Python 3.12 wheels; lock sau compatibility smoke |
| NetworkX / Pydantic | 3.7 / 2.14.3 | Candidate |
| scikit-learn / XGBoost | 1.9.1 / 3.4.1 | Consumer smoke CPU |
| PyTorch / PyG | **2.12.1 CPU / 2.8.0** | Chọn cặp được PyG 2.8 hỗ trợ; không lấy Torch latest nếu vượt matrix |
| JDK | OpenJDK 21 LTS | Một JDK cho Android/Java tools; record vendor/build |
| Android command-line tools | Linux package build 15859902 | Pin archive SHA-256 sau download |
| Android SDK | build-tools 36.0.0 + platform android-36 | Cung cấp apksigner/platform JAR; apkanalyzer availability xác minh trong bootstrap |
| JADX | 1.5.6 | Bản vá path sanitization; P2 |
| Apktool | 3.0.3 | Verification subset |
| baksmali/dexlib2 | 3.0.10 maintained fork | Verification subset; pin JAR checksum |
| FlowDroid | 2.15.1 | Optional subset, timeout/heap riêng |

ANAKIN public repo khai báo Torch 2.5.0 + PyG 2.6.1. Nếu cần tái chạy code tác giả, tạo compatibility env/container riêng; không hạ core environment và không coi repo đó là extractor APK.

Nguồn đã kiểm tra: [Androguard releases](https://github.com/androguard/androguard/releases), [rclone changelog](https://rclone.org/changelog/), [Android command-line tools](https://developer.android.com/studio), [Build Tools](https://developer.android.com/tools/releases/build-tools), [JADX releases](https://github.com/skylot/jadx/releases), [Apktool releases](https://github.com/iBotPeaches/Apktool/discussions/categories/releases), [smali maintained releases](https://github.com/baksmali/smali/releases), [FlowDroid releases](https://github.com/secure-software-engineering/FlowDroid/releases), [PyG releases](https://github.com/pyg-team/pytorch_geometric/releases), [ANAKIN repo](https://github.com/TheBlakeOmega/ANAKIN). Mọi source/revision/license/checksum cuối cùng được ghi vào `assets/sources.json`.

### 8.2. Paper fidelity và blocker

| Nhánh | Có thể bàn giao sau core implementation | Fidelity dự kiến | Blocker để gọi paper-faithful |
|---|---|---|---|
| Common GCN/GAT/GIN | Shared FCG/CCG tensors, mapping, forward/backprop smoke | Project baseline | Không phải paper-specific |
| GRED | Sensitive k-hop view, project taxonomy blocks, region/provenance/retention metadata | `structural_approximation` | Exact ordered 247 permissions + 224 opcodes + 445 abstract APIs, exact PScout/Axplorer snapshots và policy Top-K/paper config |
| SIGDroid | Internal graph transform, propagation and derived-edge evidence, source/missing blocks | `structural_approximation` | Exact APIGraph asset, noise rules, verified LongCoder checkpoint/tokenizer/revision, source mapping policy, original training/teacher assets |
| ANAKIN | Canonical sequences, weighted API-transition graphs, APK->subgraphs mapping, train-only fit contract | `reimplementation` | Original APK-to-sequence generator/data không có trong repo; phải đối chiếu exact singleton/filter/traversal behavior |
| FlowDroid verifier | Edge/callback diff on selected subset | Optional verifier | Android platforms/config/time budget |
| LongCoder | Cache/job interface và `blocked_missing_asset` behavior | Not enabled initially | Model identity, license/cache size, CPU benchmark; không có GPU |

Không pad/truncate feature tùy ý để đạt 916 chiều, không gọi Smali là Java semantics và không đổi `blocked_missing_asset` thành `ok` chỉ vì generic consumer chạy được.

## 9. OAuth/Drive procedure sau duyệt

1. Nâng rclone project-local lên 1.76.0 và kiểm tra binary checksum; không sửa system binary.
2. Ưu tiên reuse remote `nad` hiện có vì read probe đúng folder đã thành công. `RCLONE_CONFIG` trỏ file ngoài repo mode 0600; log chỉ tên remote, không config dump/token.
3. Chạy `plan-input --dry-run`: list bằng file IDs, sizes, modification time, MD5; tải manifest/sidecar nhỏ; không tải archive.
4. Test write bằng một marker/bundle nhỏ dưới `processed/android-static-v1/_probe/<uuid>/`, verify size/MD5/object ID, rồi xóa đúng probe sau receipt. Không đụng source prefix.
5. Nếu token hiện tại không có write scope, dùng headless OAuth của rclone; client secret nhập ngoài command history/log. Credential plaintext hiện có phải rotate trước production.
6. Mọi upload dùng `copyto` tới immutable batch path; source archive luôn read-only.

## 10. CLI dự kiến

Tên cuối sẽ khớp `--help` và QUICKSTART; lệnh dự kiến:

```bash
uv run android-pipeline preflight --config configs/smoke.yaml
uv run android-pipeline plan-input --config configs/smoke.yaml --dry-run
uv run android-pipeline extract-one --config configs/smoke.yaml --apk PATH --manifest-row PATH
uv run android-pipeline run-batch --config configs/smoke.yaml --archive-id ID --limit 10
uv run android-pipeline validate --config configs/smoke.yaml --batch-id ID
uv run android-pipeline smoke-consumers --config configs/smoke.yaml --batch-id ID
uv run android-pipeline run-dataset --config configs/full-p0-p1.yaml
uv run android-pipeline status --config configs/full-p0-p1.yaml --json
uv run android-pipeline resume --config configs/full-p0-p1.yaml
./scripts/launch.sh configs/full-p0-p1.yaml   # nohup, PID/log; chỉ dùng khi được lệnh full-run
./scripts/status.sh
```

`preflight`/`plan-input --dry-run` không tải archive và không mutate Drive. `run-dataset` từ chối mode full nếu input plan không đủ 16 archive/10.000 unique SHA hoặc disk formula không đạt.

## 11. Giai đoạn triển khai, đầu ra và acceptance

### Giai đoạn 0 — bootstrap và khóa input

**Thay đổi:** scaffold project, `.venv`, lockfile, tool download manifest/checksum, typed configs, redaction/logging policy; tải và khóa manifest/sidecar nhỏ; tạo remote input plan.

**Acceptance:** mọi required tool `--version`/import pass; source IDs/sizes/checksums immutable; manifest parse đủ quoting; counts/split/label được báo; hai archive thiếu làm preflight fail rõ. Không archive lớn nào được tải.

### Giai đoạn 1 — state, archive và fault-safe orchestration

**Thay đổi:** SQLite state/lock/leases, Drive transfer wrapper, safe zstd/tar reader, ZIP validator, disk watermark, idempotent receipts và cleanup guard.

**Acceptance:** unit/fault tests cho checksum sai, path escape, symlink/hardlink, duplicate member, expansion limit, low disk, interrupted download/upload, retry/resume, duplicate orchestrator và cleanup safety. Artifact chưa verify luôn được giữ.

### Giai đoạn 2 — canonical P0 extraction

**Thay đổi:** Manifest/metadata/signing, multi-DEX structure, classes/fields/methods, all instructions, opcode/operands, raw invokes, XREF/call resolution, resource/native/payload inventory, exact hashes, knowledge matcher, Arrow writers/validators.

**Acceptance:** synthetic/tiny fixtures cover Multi-DEX, overload, no-code/external method, unresolved invoke, duplicate callsite, malformed ZIP/DEX/AXML. Counts reconcile raw invokes/XREF; null+reason semantics; module runtime/rows/bytes/checksum/provenance present.

### Giai đoạn 3 — P0 adapters và consumer smoke

**Thay đổi:** FCG/common tensors, classical named records, train-only tiny fit, GCN/GAT/GIN loader/step, XAI sidecars, diagnostics.

**Acceptance:** finite float32 features; `edge_index` valid; mappings complete; graph-empty policy explicit; SVM/RF/XGBoost tiny fit/predict; GCN/GAT/GIN forward + one loss/backprop; index-to-method/callsite round-trip tested.

### Giai đoạn 4 — P1 and paper views

**Thay đổi:** basic blocks/CFG/entrypoints/reachability/sequences, CCG, GRED/SIGDroid/ANAKIN adapters và transformation history.

**Acceptance:** branch/switch/exception/recursion/truncation tests; derived edge traces to raw calls; ANAKIN transition weights/token provenance; GRED seed/region mapping; SIGDroid removed-node/shortcut evidence. Consumer result reports exact fidelity enum.

### Giai đoạn 5 — P2 optional

**Thay đổi:** JADX job, Java/Smali distinction, method-source alignment and hashes; optional LongCoder cache interface.

**Acceptance:** source coverage/error by APK/method; ambiguous mapping explicit; model/source/chunk/truncation metadata complete. Nếu asset/model chưa đủ, status là `blocked_missing_asset`, core run vẫn thành công.

### Giai đoạn 6 — end-to-end smoke/pilot

**Thay đổi:** 1 APK, rồi 6–10 APK đại diện hai nhãn/năm/kích thước và Multi-DEX nếu tìm được; tối đa 100 APK chỉ khi smoke nhỏ đạt. Một upload->verify->cleanup và crash/resume thật.

**Acceptance:** inventory/checksums/receipts reconstruct được; không silent drop; required consumer smoke pass; coverage/failures/peak RAM/runtime/disk thực đo được ghi vào implementation report và dùng tune worker/batch.

### Giai đoạn 7 — full run, chỉ theo lệnh riêng

**Gate:** đủ 16 archive, manifest đúng 10.000 SHA, source checksum pass, smoke/pilot approved, disk formula pass, write/verify receipt pass. Chạy `nohup`, giao PID/log/status một lần và không polling liên tục.

## 12. Test matrix chi tiết

### Data/schema

- CSV quoted commas/newlines, unknown columns, SHA case normalization, invalid split/label, duplicate SHA, row mismatch archive path.
- Stable ID deterministic/collision-domain tests, overloaded methods, same descriptor in multiple DEX, external->resolved alias.
- Null versus valid zero/empty; unsupported opcode; offset unit conversion; list/struct Arrow round-trip.

### APK/static analysis

- Invalid ZIP, bad CRC, duplicate/unsafe entries, zip bomb limits, missing Manifest, missing/extra DEX, malformed DEX, hidden DEX-like asset.
- Multi-DEX definitions, abstract/native/external methods, field read/write, invoke range variants, unresolved/virtual candidates.
- Basic block fallthrough/conditional/switch/exception; manifest alias/components/exported semantics; provider config.
- Certificate inventory versus actual apksigner verify status kept separate.

### Graph/view/XAI

- Empty/no-edge/isolated-node/parallel-call/self-call/oversized graphs.
- Caller->callee direction, multiplicity collapse, explicit synthetic self-loop/reverse policy.
- CCG and all derived edges map back to methods/calls; ANAKIN tokens/transitions map to sequence positions/callsites.
- Finite features/shapes/dtypes; feature dictionary alignment; missing semantic block; no metadata leakage.

### Orchestrator

- Source SHA/MD5 mismatch, archive path/link attack, download timeout, parser timeout/OOM, disk watermark, upload failure, verify mismatch.
- Kill after each state transition; resume cannot lose unverified artifact or duplicate remote batch.
- Two orchestrators contend for same batch; stale lease recovery; corrupted local cache forces rebuild.
- Cleanup refuses paths outside `work_root`, symlink escapes and batches lacking verified receipt.

### Temporal/fit isolation

- Train rows only for vocabulary/scaler/selector/TF-IDF/Word2Vec; freeze hash applied to validation/test.
- Split boundaries fixed 2019–2021/2022/2023–2026; all subgraphs of an APK retain its split.
- Duplicate/code-clone audit only reports/proposes versioned protocol, never rewrites labels/splits silently.

## 13. Quality reports và điều kiện hoàn tất

Mỗi batch và tổng hợp cuối báo theo split/year/label:

- expected/succeeded/partial/failed/pending unique SHA;
- ZIP/Manifest/DEX/signature status, DEX/class/method/reference/instruction/invoke/callsite counts;
- call resolution/mapping/source/sequence provenance coverage;
- empty/no-edge/oversized graph, N/E/degree/SCC/isolate và p50/p95/p99;
- runtime, timeout/OOM, measured peak memory scope, bytes per APK/module/view;
- truncation, skipped metric/reason và tool disagreement subset;
- checksums, config/schema/tool/knowledge versions, remote object ID and receipt.

Implementation chỉ được gọi hoàn tất khi:

1. Code/config/schema/tests/QUICKSTART/report được bàn giao và lệnh documented đã chạy đúng.
2. P0 required tests và 9 consumer smoke contracts đạt, hoặc paper-faithful branch được ghi chính xác `blocked_missing_asset` trong khi approximation contract đạt.
3. Round-trip Drive và resume/cleanup được kiểm chứng trên pipeline-owned target.
4. Không có credential trong repo/log/artifact.
5. Pilot report ghi số mẫu thực, không suy diễn thành kết quả research.
6. Full run chỉ gọi `complete` khi 10.000 manifest SHA có required remote-verified outputs; có lỗi thì là `completed_with_failures` + failure manifest.

## 14. Ước tính thời gian và rủi ro

Đây là ước tính kỹ thuật trước pilot, không phải số runtime đã đo:

| Công việc | Ước tính |
|---|---:|
| Bootstrap/input lock | 0.5–1 ngày kỹ sư |
| Orchestrator/archive/state/fault tests | 1.5–3 ngày |
| P0 extractor/schema/validation | 3–5 ngày |
| P0 adapters + consumer smoke | 2–3 ngày |
| P1 CFG/entrypoints/sequences/paper views | 2–4 ngày |
| P2 source pilot | 1–3 ngày, chưa gồm LongCoder full |
| End-to-end pilot/report/hardening | 1–2 ngày |
| Tổng trước full run | khoảng 10–18 ngày kỹ sư |

ETA full run chỉ được tính từ pilot bằng `sum(measured per-APK stage time)/effective_workers + transfer/upload`. Ví dụ minh họa: nếu P0/P1 thực đo 2–10 phút/APK và giữ 2 worker hiệu dụng thì riêng analysis khoảng 7–35 ngày; đây không phải cam kết và FlowDroid/JADX/LongCoder không nằm trong ví dụ. Worker được chọn từ p95 RAM/runtime, không từ 48 vCPU danh nghĩa.

Rủi ro ưu tiên:

1. Hai archive nguồn thiếu: chặn full run; cần khôi phục/giải thích trước phase C.
2. OAuth secret đã xuất hiện plaintext: rotate/redact trước Git/production.
3. rclone quá cũ: nâng project-local trước mọi upload.
4. Instruction-level IR/source có thể nở lớn: batch động và measurement gate.
5. Androguard/XREF có sai khác/timeout trên APK obfuscated: raw invoke là evidence nền, verifier subset và failure taxonomy.
6. Exact paper assets không công khai/không xác minh: tách fidelity, không giả shape là semantics.
7. Không GPU: LongCoder có thể quá chậm; giữ deferred job interface và report blocker.

## 15. Điểm cần người dùng duyệt

Phê duyệt kế hoạch này cho phép triển khai Giai đoạn 0–6, cài dependency/project-local tools, tải manifest/sidecar và tối đa 100 APK cho smoke/pilot, đồng thời tạo/xóa đúng probe/output cache do pipeline quản lý. Nó **không** cho phép chạy toàn bộ 10.000 APK.

Trước full run cần một chỉ định riêng và hai archive thiếu phải được khôi phục hoặc có quyết định nguồn chuẩn có version. Đề xuất mặc định là triển khai P0+P1 trước, P2 chỉ pilot JADX; LongCoder/FlowDroid giữ optional cho đến khi core smoke và resource measurement hoàn tất.
