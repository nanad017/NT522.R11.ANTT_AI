# Handoff context: full Classical ML + FCG GCN/GAT/GIN run

**Prepared:** 2026-11-14 UTC  
**Project:** `/home/rl/Data`  
**Scope:** train/validation/test only for Classical ML and common FCG GCN/GAT/GIN  
**Persistent unit:** `android-ml-fcg-modeling.service`

## 1. User decision and protocol

The user requested full-data training, validation and frozen temporal testing for the currently available compact artifacts. This run intentionally includes only:

- Linear SVM, Random Forest and XGBoost over one shared sparse classical feature matrix.
- Representation-controlled GCN, GAT and standard GIN over directed FCG tensors.
- Seeds `13`, `42` and `2026` for every model.

The immutable temporal protocol is:

- train: 2019–2021;
- validation: 2022, used for early stopping and threshold selection;
- test: 2023–2026, evaluated only after the selected checkpoint/threshold is frozen.

Compact extraction does not contain CCG, GRED, SIGDroid or ANAKIN inputs. Those models are not silently approximated by this workflow.

## 2. Data state at implementation time

Extraction completed its local materialization before the final bundle upload began. The local tree contained 9,900 physical compact sample directories/files, including early duplicate attempts, and approximately 9,855 unique validated SHA artifacts. The original manifest contains 10,000 SHA. Missing APKs are not replaced by synthetic rows.

`build-catalog` deduplicates by SHA, requires each discovered compact sample's `validation.json` to have `status=ok`, and writes:

```text
model-artifacts/full-ml-fcg/catalog/catalog.parquet
model-artifacts/full-ml-fcg/catalog/catalog-report.json
model-artifacts/full-ml-fcg/catalog/exclusions.parquet
```

The modeling gate requires all 10,000 manifest rows to be represented in the catalog, both `classical` and `fcg` for included samples, a declared exclusion manifest, and at least 98% joint usable coverage. Exact final included/excluded counts are written after the service builds the frozen catalog; do not substitute the implementation-time estimate in published results.

## 3. Compatibility and stability fixes made

### Compact FCG mapping

`src/android_models/features/graph.py` now accepts the extraction contract `mapping_mode=model_only`. It validates `node_count` and `edge_count` against `graph.npz` when raw `node_ids` and `edge_ids` are intentionally absent. Full mappings still require and validate ID arrays. This change is required for compact GCN/GAT/GIN training and does not weaken tensor shape/range/label/finite checks.

### Classical full-cohort memory

The previous CLI materialized roughly 6 GB of classical JSON as Python dictionaries before vectorization and could exceed VM RAM due to object overhead. `src/android_models/features/classical.py` now:

- streams named records in SHA order;
- fits `DictVectorizer` without creating/discarding a full training matrix;
- transforms records in bounded chunks;
- keeps the final matrices sparse CSR `float32`;
- preserves train-only vocabulary fitting, row SHA order and unknown-feature accounting.

### GNN progress

`src/android_models/training/gnn.py` now writes `history.json` and `progress.json` after every epoch. Checkpoints remain `safetensors`; model selection remains validation PR-AUC with configured patience.

### Catalog and declared exclusions

`src/android_models/catalog.py` writes `exclusions.parquet` for missing required compact views. `android-models preflight` reports `ready_with_declared_exclusions` only when the configured coverage gate passes.

## 4. Verification completed before launch

- Full test suite: 36 passed.
- Ruff over `src`, `tests` and the new report script: required to pass before launch.
- Real compact graph load: model-only mapping loaded successfully with 274 node features and 9 edge features.
- Real six-APK smoke, one benign and one malware from each split:
  - Linear SVM: complete;
  - Random Forest: complete;
  - XGBoost: complete;
  - GCN: complete;
  - GAT: complete;
  - GIN: complete.

Smoke artifacts are under `model-artifacts/smoke-compact-real/`. Smoke metrics are contract checks only and must not be reported as cohort benchmark results.

## 5. Full workflow

Resolved config:

```text
/home/rl/Data/configs/models/full_ml_fcg.yaml
```

Runner:

```text
/home/rl/Data/scripts/run-full-ml-fcg-modeling.sh
```

The persistent service waits while `android-ml-fcg-v1.service` is active, so final bundle compression/upload does not compete with catalog construction or training. It then runs, in order:

1. catalog and exclusion manifest;
2. graph/data preflight;
3. leakage audit;
4. train-only classical vocabulary fit;
5. frozen train/validation/test sparse transforms;
6. Linear SVM, Random Forest and XGBoost for three seeds;
7. GCN, GAT and GIN on FCG for three seeds;
8. result aggregation and Drive backup.

Independent model failures are recorded and do not prevent later models from running. Critical data/feature gates stop the workflow. Existing complete run directories are skipped on restart; failed/incomplete runs are resumed through the registry guard.

## 6. Outputs and checkpoints

Local root:

```text
/home/rl/Data/model-artifacts/full-ml-fcg/
```

Important files:

```text
workflow-status.json
workflow.log
catalog/catalog-report.json
catalog/exclusions.parquet
preflight/preflight.json
leakage/leakage-audit.json
classical-features/vectorizer.json
classical-features/x-{train,validation,test}.npz
runs/<run-id>/run.json
runs/<run-id>/checkpoint.json
runs/<run-id>/checkpoint.skops       # SVM/RF
runs/<run-id>/checkpoint.ubj         # XGBoost
runs/<run-id>/checkpoint.safetensors # GCN/GAT/GIN
runs/<run-id>/threshold.json
runs/<run-id>/predictions.parquet
runs/<run-id>/metrics.json
runs/<run-id>/metrics-by-year.parquet
results-summary.json
RESULTS.md
```

Remote backup target:

```text
nad2:processed/android-ml-fcg-v1-complete/model-artifacts/full-ml-fcg
```

The runner copies completed run checkpoints to Drive after each model and copies the complete artifact root at finalization.

## 7. Operations

Launch or relaunch:

```bash
cd /home/rl/Data
scripts/launch-full-ml-fcg-modeling.sh
```

One-shot status:

```bash
cd /home/rl/Data
scripts/status-full-ml-fcg-modeling.sh
```

Follow logs only when explicitly desired:

```bash
journalctl --user -u android-ml-fcg-modeling.service -f
```

The user explicitly requested that the service be left unattended after stable launch. Do not poll it continuously. On the next user check, inspect the unit, `workflow-status.json`, latest `progress.json`, `RESULTS.md`, disk/RAM and Drive artifact path.

## 8. Resource expectations and limitations

- VM: CPU-only, 62 GiB RAM, no CUDA.
- Classical models should complete much sooner than GNNs.
- GCN/GAT/GIN run sequentially and use up to 40 PyTorch CPU threads.
- Full FCG training may take many hours or days because the cohort includes large graphs and nine seeded GNN runs.
- Graphs exceeding the node/edge batch budget are retained as deterministic single-graph batches, never silently dropped.
- Published results must report the original 10,000 denominator, included coverage and exclusion reasons.

Do not delete extraction artifacts, the final compact bundle, catalog, feature matrices or run directories while the service is active.

## Parallel completion run (2026-11-16)

At the user's request, the remaining GIN seeds and failed Linear SVM seeds are
run concurrently by `android-ml-fcg-modeling-parallel.service` using
`scripts/run-parallel-remaining-models.sh`. The existing sequential coordinator
is SIGSTOPed while its already-running GAT seed 2026 child continues, preventing
it from starting duplicate GIN runs. The parallel coordinator waits for all six
new runs and the existing GAT run, stops the old coordinator, summarizes results,
uploads artifacts, and writes the final workflow status.

Parallel settings are isolated in `configs/models/parallel_remaining.yaml`:

- GIN: three seeds, 12 Torch threads each.
- Linear SVM: three seeds with `C=0.01`, `dual=auto`, `tol=1e-3`, and
  `max_iter=100000` to address the prior non-convergence at 20,000 iterations.
- Every run has a distinct registry directory and log under
  `model-artifacts/full-ml-fcg/parallel-logs/`.
