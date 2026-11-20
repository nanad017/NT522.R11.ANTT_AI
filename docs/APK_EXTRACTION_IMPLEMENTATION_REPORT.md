# APK extraction implementation report

Date: 2026-11-07 (UTC)  
Pipeline: `android-static-v1`  
IR schema: `ir-v1`

## Outcome

The project now has a locked Python environment, safe `.tar.zst` ingestion,
manifest/hash validation, one-pass Androguard extraction, Parquet IR, FCG/CCG,
GRED/SIGDroid approximation and ANAKIN views, classical/GNN consumer smoke tests, durable SQLite state, isolated
APK workers with hard subprocess timeouts, Drive upload verification, receipts,
bounded cleanup, and `nohup` launch/status scripts.

The 10,000-APK run was deliberately not started. Two required source archives
are not visible to the OAuth account, and the output Drive has only about 5 GiB
total capacity. Both are hard preflight gates, not extraction failures.

## Locked input and access findings

- `final-manifest.csv` SHA-256:
  `91b2c19c8f8596f82616efa82178c4e12da496a4cde07d803a8db44ab4557bc3`.
- Parsed rows: 10,000 unique SHA-256 values; 5,000 benign and 5,000 malware.
- Split counts match the task contract: train 8,422, validation 600, test 978.
- Drive inventory: 14 of 16 archives visible. Sidecars for all expected names
  are present.
- Missing source objects:
  `test/2024/test-2024-malware.tar.zst` and
  `test/2026/test-2026-malware.tar.zst`.
- Direct IDs `1n9I0yIroivmOd3PuZVLB2fMrlW5H2eHt` and
  `1wwThr78n67TsArYBFqF6gEaGL-MaEaLx` both return Drive API `404 notFound` to
  the configured OAuth identity; unauthenticated download also returns HTTP
  404. Seeing them in another browser account does not grant this identity
  access.
- Latest measured quota after smoke uploads: 5 GiB total, roughly 4.8 GiB
  free. Source files appear to be shared and therefore do not represent usable
  output quota for this OAuth account.

The immutable plan is stored in `state/input-plan.json` with `complete=false`.
`run-dataset` refuses to start until it contains all 16 source objects.

## Environment and pinned tools

- Ubuntu 24.04, x86-64, kernel 7.0.0-42.
- 48 vCPU, 62 GiB RAM, 4 GiB swap; about 94–96 GiB local disk free during
  implementation; no GPU required by the extractor.
- Python 3.12.3 and uv 0.11.5.
- Androguard 4.1.4, PyArrow 26.0.0, NumPy 2.5.4, SciPy 1.18.1,
  NetworkX 3.7, scikit-learn 1.9.1, XGBoost 3.4.1,
  PyTorch 2.12.1+cpu, PyG 2.8.0, zstandard 0.25.0.
- Project-pinned rclone 1.76.0; downloaded archive checksum was verified against
  the official `SHA256SUMS` file. The remote was moved from rclone's retiring
  shared client ID to the user-provided private OAuth client without storing
  credentials in the repository.
- Project-local OpenJDK 21.0.12.1 and Debian apksigner 31.0.2/package tool
  version 0.9. A real APK verified successfully with v2/v3 signatures and one
  signer.

Optional tools not installed or not enabled for the cohort run: apkanalyzer,
JADX, Apktool/baksmali verifier, FlowDroid/Soot, Android platform JARs, and
LongCoder. Their absence is recorded as a fidelity limitation; it is not
reported as successful verification.

## Implemented extraction and artifacts

The extractor validates APK SHA-256 against the locked manifest, ZIP paths,
duplicate entries, CRC, entry/expanded-size limits, Manifest presence, and all
`classes*.dex` entries. It analyzes the APK once and emits typed Parquet tables
for:

- samples, signing verification and certificate metadata;
- requested permissions, components, grouped intent filters, provider rules,
  and application/platform configuration;
- DEX inventory, classes, fields with encoded values, overloaded methods and
  stable IDs;
- instruction/opcode records, const-string usage, raw callsites, XREF targets;
- basic blocks, CFG edges, lifecycle entrypoints and bounded API sequences;
- resource/native inventory and versioned security-taxonomy matches.

Every output includes `schema.json`, `inventory.json`, `quality-report.json`,
`validation.json`, and `checksums.sha256`. Stable IDs use length-prefixed
SHA-256 tuples. FCG mappings preserve local node/edge indices and raw call IDs;
ANAKIN transition edges preserve supporting sequence positions and call IDs.

The isolated scheduler spools a bounded group from the sequential `.tar.zst`
stream, launches one subprocess per APK, enforces a timeout that can terminate
the worker, uploads only validated output, compares the complete remote path
set plus sizes and MD5 values, writes a durable receipt, and then removes only
its managed batch directory. A retry skips only `remote_verified` samples.

## Measured smoke and pilot results

### Real extraction

- 2024 benign representative: 3,579,262-byte APK; 4,463 classes, 33,249
  methods, 464,908 instructions, 114,619 callsites, 118,249 basic blocks.
  Extraction took about 26 seconds and produced about 117 MiB. Its FCG has
  41,388 nodes, 88,127 edges and 274 node features.
- 2025 malware pilot: 6 APKs, 0 failures, 10 DEX files, 39,969 classes,
  279,561 methods, 3,995,724 instructions, 1,029,583 callsites, and 973,224
  basic blocks. Sequential wall time was 3m44s; peak RSS was 6,158,760 KiB;
  output was about 1,009 MiB. The large peak includes retained analysis state
  across six APKs and motivated isolated subprocesses.
- The two-worker isolated pilot showed the parent near 156 MiB and the large
  worker near 2.46 GiB while the small worker completed independently. Full
  `workers=auto` is capped by the batch size and a conservative 4 GiB/worker
  memory budget; on this VM it selects 10 workers only after gates pass.

The six-APK output average extrapolates to roughly 1.68 TB for 10,000 APKs
before safety headroom. This is only a planning estimate—the distribution is
very wide—but it proves that 5 GiB cannot hold the required provenance-rich
IR. The full profile therefore requires at least 2 TiB reported free space
before launch.

### Consumers and transport

- Classical record smoke: 2 records, 2 classes, 219 sparse features;
  LinearSVC, Random Forest and XGBoost all fit and produced finite predictions.
- Common FCG smoke: GCN, GAT and GIN all produced finite logits/loss and
  gradients in a real forward/backward step.
- ANAKIN reimplementation smoke: 7 graphs, 99 transitions, 99/99 transitions
  with callsite provenance. TF-IDF/top-k/CBOW remain correctly marked
  `raw_pre_fit`; no vocabulary was fit on test data.
- Real Drive round-trip: 26 files and 122,318,005 bytes uploaded; every path,
  byte size and MD5 matched. Receipt:
  `state/receipts/test-2024-benign.tar-ed0a6298d3ee-1794044141.json`.
- Automated tests: 15 passed; ruff passed. Tests cover stable IDs, manifest
  contracts, unsafe archives, state/audit durability, artifact checksums, FCG,
  ANAKIN weighted provenance, and config inheritance.

## Fidelity matrix

| Branch | Status | Evidence / blocker |
|---|---|---|
| Classical SVM/RF/XGBoost input | project baseline implemented | Named raw records and meaningful sparse consumer smoke pass; train-only frozen vocabulary job remains a later training-stage task. |
| GCN/GAT/GIN common FCG | project baseline implemented | Shared directed FCG, mappings and real forward/backward smoke pass. |
| ANAKIN | `reimplementation` | Entrypoint/CFG/FCG traversal and weighted API-transition graphs implemented with provenance; original sequence generator/assets and train-only TF-IDF/CBOW are unavailable. |
| CCG | `canonical_class_collapse` | Class-level graph, method-edge and raw-callsite provenance are emitted from canonical IR. |
| GRED | `blocked_missing_asset` / structural approximation only | Sensitive seeds, k-hop region, directed retained edges and provenance are emitted; exact 247+224+445 dictionaries and paper-faithful training stack are absent. |
| SIGDroid | `blocked_missing_asset` / structural approximation only | Internal graph, removed external nodes and raw-call provenance are emitted; JADX mapping, verified APIGraph assets, LongCoder revision/embeddings and SCKDG training are absent. |
| P2 source semantics | `not_enabled` | JADX/Smali/LongCoder artifacts were not approved/provisioned and are not silently replaced by opcode features. |

## Remaining limitations

- API-to-permission mappings (PScout/Axplorer), exact API category dictionaries,
  framework resolution and library detection need versioned knowledge assets.
- CFG edge kinds currently use Androguard successor evidence and do not yet
  distinguish every conditional/switch/exception relation.
- Entrypoint sequencing is a bounded static DFS reimplementation, not a
  FlowDroid callback/path reconstruction and not a runtime trace.
- CCG, GRED and SIGDroid transformation artifacts are now emitted from the
  canonical IR. GRED/SIGDroid remain explicitly non-paper-faithful and report
  `blocked_missing_asset`; a generic graph is never labelled equivalent.
- Full-cohort fit/transform jobs, leakage/clone audit, training, benchmark
  metrics and explainers are outside the extraction phase and have not been
  claimed as results.

## Conditions required to start the full run

1. Share both missing file IDs directly with `txlogimqs.ovs@gmail.com` as
   Viewer, or move/copy them into the source folder owned/visible by that
   identity. Re-run `plan-input --no-dry-run` until `complete=true`.
2. Point `drive.remote`/`output_prefix` at storage with at least 2 TiB free, or
   provide another object-storage destination with equivalent checksum and
   immutable-write semantics.
3. Run `preflight`; it must return `status=ok`. Then use
   `scripts/launch-full.sh` once and `scripts/status-full.sh` when a status is
   requested.

No source archive has been modified or deleted, and no 10,000-sample run has
been falsely marked complete.
