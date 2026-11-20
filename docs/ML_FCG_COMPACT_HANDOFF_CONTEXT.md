# Handoff context: compact extraction cho ML, GCN, GAT và GIN

**Ngày chuyển chế độ:** 2026-11-13 UTC  
**Project đang chạy:** `/home/rl/Data`  
**Trạng thái:** full provenance extraction đã bị khóa; compact extraction giữ-local đang chạy  
**Config active:** `/home/rl/Data/configs/full-ml-fcg-compact.yaml`  
**Pipeline active:** `android-ml-fcg-v1`  

## 1. Quyết định hiện hành của người dùng

Tạm ngừng pipeline extraction đầy đủ phục vụ GRED, SIGDroid, ANAKIN và XAI provenance. Ưu tiên hoàn thành dữ liệu cho:

- Classical ML: Linear SVM, Random Forest, XGBoost.
- Common graph classifiers: GCN, GAT và GIN.
- Graph view active duy nhất: directed FCG.

Mọi artifact full đã upload và remote-verified phải giữ nguyên. Không xóa hoặc ghi đè chúng. Ngày 2026-11-13 người dùng yêu cầu materialize lại toàn bộ 10.000 APK bằng compact profile, giữ kết quả extraction trên máy và tự động upload thêm một bundle hoàn chỉnh lên một Drive duy nhất. Sau này nếu người dùng muốn khôi phục paper branches, các APK compact-only phải được phân tích lại từ source archive.

## 2. Vì sao chuyển sang compact

Full run dừng do ổ `/` đầy 100%, chỉ còn khoảng 4,7 MB. Lỗi cuối là:

```text
OSError: [Errno 28] No space left on device
```

Đo trực tiếp một batch 48 APK full:

| Thành phần | Dung lượng |
|---|---:|
| Toàn batch | 11,4 GB |
| Các `mapping.json` | 7,74 GB |
| Instructions/basic blocks/CFG/calls | khoảng 3,15 GB |
| Tất cả `graph.npz` | khoảng 70 MB |
| Classical record + FCG tensor + metadata tối thiểu | khoảng 81 MB |

Phần nặng chủ yếu là provenance JSON và canonical row-level evidence, không phải tensor dùng để train GNN.

## 3. Snapshot trước khi chuyển chế độ

State DB trước cleanup:

- Train remote-verified: 2.721 APK.
- `train-2019-benign`: 1.340 verified, 60 failed.
- `train-2019-malware`: 1.381 verified, 19 failed.
- `train-2020-benign`: 132 validated, 48 pending, 12 failed trong các batch local chưa upload.
- Tổng global còn có 2 sample pilot/test remote-verified, nên summary global hiển thị 2.723.
- Full config hash cũ: `19e33572b86f92a497ec23d3bb464cf6af2543a42f06e7127d3ea4ba0668486b`.
- Full remote prefix: `processed/android-static-v1` trên các remote `nad`, `nad2`, `nad3`.

Các artifact full remote giữ nguyên. Compact config vẫn ghi lại full hash cũ để bảo toàn provenance cấu hình, nhưng `runtime_tuning.skip_verified_samples: false`; vì vậy cả 2.721 train sample full đã verified cũng được trích lại thành compact. Mục đích là tạo một local dataset và một final bundle tự chứa, không buộc người dùng ghép dữ liệu từ prefix full cũ.

## 4. Cleanup đã thực hiện

Đã dừng process full `android-pipeline run-dataset --config configs/full-p0-p1.yaml`. Đã dừng rclone con và xóa file tải dở; không xóa remote object.

Đã xóa ba local batch archives chưa upload, tổng khoảng 16 GB:

```text
train-2020-benign...b00001.tar.zst
train-2020-benign...b00002.tar.zst
train-2020-benign...b00003.tar.zst
```

Đã xóa bốn full local batch directories chưa upload, tổng khoảng 38 GB, gồm `b00001` đến `b00004`. Các sample này sẽ được compact pipeline xử lý lại. Đã dọn managed spool tạm khoảng 854 MB.

Sau cleanup, ổ còn khoảng 93 GB. Không có dữ liệu remote-verified nào bị xóa.

## 5. Khóa chống chạy nhầm full pipeline

Marker đang tồn tại:

```text
/home/rl/Data/state/PAUSE_FULL_EXTRACTION
```

CLI từ chối `run-dataset` với artifact profile `full` khi marker này còn tồn tại. Compact profile không bị chặn. Không xóa marker nếu người dùng chưa yêu cầu rõ khôi phục full extraction.

## 6. Compact artifact contract

Mỗi APK compact chỉ giữ:

```text
samples.parquet
classical-records.json
views/fcg/<sha256>/graph.npz
views/fcg/<sha256>/mapping.json
quality-report.json
inventory.json
validation.json
schema.json
checksums.sha256
```

### Classical record

`classical-records.json` được aggregate trong memory trước khi bỏ canonical tables. Nó chứa:

- requested permissions;
- component type/exported/enabled;
- intent action/category;
- provider/app-config indicators;
- normalized 256 opcode bins và opcode counts;
- code/decode/call-resolution coverage;
- external API signature/package, target kind và invoke kind;
- security concept counts;
- FCG node/edge/density, in/out degree quantiles, SCC/WCC, isolates, self-loop, reciprocal-edge ratio, internal/external ratio, sensitive nodes/edges và callsite multiplicity.

SHA, label, split và year nằm ngoài feature mapping để join/evaluate; identity/time metadata không được dùng làm predictor mặc định.

### FCG tensor

`graph.npz` giữ:

- `x [N,274]`;
- directed `edge_index [2,E]` caller → callee;
- `edge_attr [E,9]` gồm callsite multiplicity và tám invoke-kind indicators;
- `y [1]`.

274 node features gồm internal/external, missing code, instruction/in/out-call structure, 12 security concepts và 256 opcode bins.

GCN, GAT và standard GIN dùng chung `x`/`edge_index` ở representation-controlled track. Standard GIN không đọc edge vector; edge-aware experiment phải mang tên riêng.

### Compact mapping

`mapping.json` chỉ giữ feature names, edge feature names, node count, edge count, APK SHA và view version. Nó không giữ node IDs, edge IDs hoặc raw call IDs. Đây là chủ ý để giảm dung lượng; output compact dùng để train detector, không đủ cho raw-callsite XAI.

## 7. Những artifact không còn persist

Compact worker vẫn parse method/instruction/callsite trong memory để tạo classical feature và FCG, nhưng không ghi lâu dài:

- full instructions, methods, classes, fields;
- basic blocks và CFG;
- strings;
- entrypoints/API sequences;
- signing/certificate/resource/native inventories;
- CCG, GRED, SIGDroid và ANAKIN views;
- full FCG node/edge/raw-call provenance mapping.

Hệ quả:

- Đủ train/evaluate Classical ML và GCN/GAT/GIN.
- Không đủ paper-faithful GRED/SIGDroid/ANAKIN.
- Không đủ explanation drill-down về raw method/callsite cho compact-only sample.
- Muốn khôi phục các nhánh trên phải re-extract APK từ source archives với full config hoặc một profile provenance mới.

## 8. Config, version và compatibility

Compact config hash:

```text
6606a5fb5d4de7df15f621530ab240334eaf84a658bdec64e75145468c95469e
```

Checkpoint batch prefix mới, tập trung trên `nad2`:

```text
processed/android-ml-fcg-v1-complete/checkpoints
```

Final single-file bundle:

```text
nad2:processed/android-ml-fcg-v1-complete/android-ml-fcg-v1-complete.tar.zst
```

Drive folder ID: `1ev9qB-pU7KCLVRzkws5lMnZdbOU1aHBK`  
Owner-visible URL: `https://drive.google.com/drive/folders/1ev9qB-pU7KCLVRzkws5lMnZdbOU1aHBK`

Compatible verified hash:

```text
19e33572b86f92a497ec23d3bb464cf6af2543a42f06e7127d3ea4ba0668486b
```

Runtime dùng batch tối đa 48 APK/6 GiB raw và tối đa 16 extraction workers theo RAM hiện tại. Từng checkpoint batch upload vào `nad2`, sau khi remote MD5 pass thì chỉ xóa archive batch tạm; thư mục sample đã trích được giữ trong `work/dataset-batches/`. Source archive local tự xóa sau khi archive hoàn tất để không tích lũy khoảng 100 GB download.

Sau khi toàn bộ dataset run thành công, wrapper tự chạy `scripts/build-upload-ml-fcg-complete-bundle.sh`. Script tạo `work/ml-fcg-complete/samples/` bằng hard link để không nhân đôi payload local, rồi nén và stream thẳng một file `.tar.zst` lên Drive. Nó lưu SHA-256/MD5 sidecar, lấy MD5 từ Drive và chỉ ghi `state/bundle/COMPLETE` khi MD5 khớp. Không tạo thêm bản archive 30–40 GB trên local disk.

## 9. Smoke và kiểm thử đã chạy

- Targeted ruff trên code thay đổi: pass.
- Toàn bộ pytest: 35 passed.
- Compact preflight: `status=ok`, đủ 16 source archives, khoảng 5,27 TB Drive free, khoảng 93 GB local free sau cleanup.
- Real APK smoke SHA `ce3b3c98...1fcab8`:
  - runtime khoảng 83 giây;
  - FCG 130.819 nodes, 268.546 edges, 274 node features, 9 edge features;
  - total compact output khoảng 5,5 MB;
  - compact mapping khoảng 8 KB;
  - validation/checksum pass.

## 10. Lệnh vận hành

Khởi chạy/resume compact:

```bash
cd /home/rl/Data
scripts/launch-ml-fcg-compact.sh
```

Launcher tạo transient user service `android-ml-fcg-v1.service` bằng `systemd-run`; job không phụ thuộc phiên AI hoặc SSH. Không thay launcher này bằng một background process gắn với terminal ngắn hạn.

Xem status:

```bash
cd /home/rl/Data
scripts/status-ml-fcg-compact.sh
```

Theo dõi không cần nhập prompt:

```bash
watch -n 15 'cd /home/rl/Data; pgrep -af "android-pipeline run-dataset|rclone copyto|rclone rcat"; df -h /home/rl/Data; tail -n 15 state/logs/android-ml-fcg-v1.log'
```

Kiểm tra preflight:

```bash
uv run android-pipeline preflight --config configs/full-ml-fcg-compact.yaml
```

Không dùng `scripts/launch-full.sh`, `scripts/resume-b42-and-full.sh` hoặc `configs/full-p0-p1.yaml` trong giai đoạn compact.

## 11. Cách đọc state hiện tại

`android-pipeline status` đang báo global SQLite state, bao gồm batch full cũ và compact mới. Vì vậy các row `validated/pending` của batch full đã bị supersede có thể còn hiển thị cho tới khi cùng SHA được compact run cập nhật.

Để đếm remote-verified train thực tế:

```bash
sqlite3 -header -column state/pipeline.sqlite3 "
select count(*) as verified_train
from samples s join archives a on a.archive_id=s.archive_id
where a.remote_path like 'train/%' and s.state='remote_verified';"
```

Phân biệt batch theo `batches.config_hash`. Full remote và compact remote nằm ở hai output prefixes khác nhau.

## 12. Nếu compact job dừng

1. Kiểm tra PID/log/disk/rclone.
2. Không xóa batch đã đóng gói nếu chưa biết remote verification state.
3. Nếu archive local hoàn chỉnh còn tồn tại, resume sẽ verify checksum và tiếp tục.
4. Nếu chỉ có file `.partial`, bảo đảm không còn rclone ghi file trước khi xóa và tải lại.
5. Chạy lại `scripts/launch-ml-fcg-compact.sh`. Chế độ hiện tại cố ý materialize đủ local nên không skip verified sample; các checkpoint remote dùng để phục hồi nếu cần, còn bundle chỉ chạy sau khi dataset command kết thúc thành công.

## 13. Cách khôi phục full extraction sau này

Chỉ khi người dùng yêu cầu rõ:

1. Dừng compact job an toàn.
2. Giữ nguyên cả hai remote prefixes.
3. Xóa marker `state/PAUSE_FULL_EXTRACTION` có chủ đích.
4. Chạy preflight full và bảo đảm local/remote capacity đủ cho provenance-rich output.
5. Dùng `configs/full-p0-p1.yaml`.
6. Full config chỉ skip sample đã verified bằng full hash; sample compact-only sẽ được re-extract, đúng vì compact artifact không phải full superset.
7. Không đổi fidelity: GRED/SIGDroid vẫn chỉ là structural approximation cho tới khi có exact paper assets.

## 14. Việc tiếp theo cho AI

Ưu tiên theo thứ tự:

1. Theo dõi compact process, download/checksum, extraction, checkpoint packing, upload và receipt.
2. Xác minh checkpoint đầu tiên trên `nad2` có remote MD5 khớp, archive tạm đã xóa nhưng sample root local vẫn còn.
3. Báo throughput và disk high-water sau ít nhất hai batch; dừng an toàn nếu free space tiến gần reserve 12 GiB.
4. Hoàn tất materialization compact cho toàn bộ manifest 10.000 APK; giữ failure inventory nếu APK nào không thể trích.
5. Để wrapper tự tạo hard-link dataset, stream final bundle lên `nad2` và xác minh `state/bundle/COMPLETE`.
6. Sau khi extraction đủ mới chạy modeling pipeline train-only vectorizer cho SVM/RF/XGBoost và common FCG loaders cho GCN/GAT/GIN.
7. Không train benchmark cuối cùng khi coverage/split/leakage gate chưa đạt.

## 15. Recovery 145 APK extraction lỗi (2026-11-15)

Sau full compact pass, state có 9.855 mẫu `remote_verified` và 145 mẫu
`failed`. Phân tích toàn bộ worker log xác định:

- 140 APK lỗi `IndexError: list index out of range` do extractor cũ không
  nhận `classes0.dex`/`classes1.dex` trong khi Androguard vẫn parse chúng,
  khiến DEX name/object bị lệch.
- 3 APK có `InvalidInstruction` do bytecode malformed/anti-analysis.
- 1 APK có inner-ZIP entry tên `..`; entry này không bao giờ được extract ra
  filesystem.
- 1 APK có CRC lỗi ở asset phụ `assets/SignatureKiller/origin.apk`, không phải
  Manifest hay DEX dùng cho ML/FCG.

Extractor đã được sửa để đồng bộ tên DEX với Androguard, cô lập lỗi decode theo
method, dùng declared-invoke fallback có provenance khi Androguard XREF không
khởi tạo được, và chỉ yêu cầu CRC hợp lệ cho Manifest/DEX bắt buộc. Outer source
archive vẫn giữ path/link validation nghiêm ngặt. Không có feature hoặc sample
tổng hợp giả.

Recovery chỉ scan lại 145 SHA lỗi bằng command:

```bash
uv run android-pipeline retry-failed \
  --config configs/full-ml-fcg-compact.yaml --confirm-full
```

Wrapper persistent sau đó dựng và upload final bundle:

```bash
scripts/recover-ml-fcg-failed-and-bundle.sh
```

Service dự kiến: `android-ml-fcg-recovery.service`. Model đang chạy trước đó vẫn
dùng catalog 9.855 mẫu; muốn benchmark đủ 10.000 phải dựng lại catalog/features
và rerun model sau khi recovery đạt 0 failed.
