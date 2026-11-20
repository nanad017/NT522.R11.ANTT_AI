# Android detector modeling quickstart

The modeling namespace consumes extraction artifacts read-only. All fitted state,
checkpoints, predictions, and reports are written below `model-artifacts/`.

## Current gate

Do not run or report a full-cohort benchmark until `preflight` reports complete
coverage and the D0-D3 gates are accepted. The current local artifacts are suitable
for contract tests and mini-pilots only.

## Foundation and classical models

```bash
uv sync --extra classical --extra gnn --extra dev
uv run android-models preflight --config configs/models/data.yaml --output model-artifacts/preflight
uv run android-models build-catalog --config configs/models/data.yaml
uv run android-models audit-leakage --config configs/models/data.yaml
uv run android-models fit-classical-features --config configs/models/classical.yaml
uv run android-models transform-classical-features --config configs/models/classical.yaml
uv run android-models train-classical --config configs/models/classical.yaml --model linear_svm --seed 13
uv run android-models train-classical --config configs/models/classical.yaml --model random_forest --seed 13
uv run android-models train-classical --config configs/models/classical.yaml --model xgboost --seed 13
```

Repeat benchmark runs for seeds `13`, `42`, and `2026`; never select only the best seed.
The commands fail when train/validation do not both contain both labels.

## Common graph models

```bash
uv run android-models train-common-gnn --config configs/models/common_gnn.yaml --model gcn --view fcg --seed 13
uv run android-models train-common-gnn --config configs/models/common_gnn.yaml --model gat --view fcg --seed 13
uv run android-models train-common-gnn --config configs/models/common_gnn.yaml --model gin --view fcg --seed 13
uv run android-models train-common-gnn --config configs/models/common_gnn.yaml --model gcn --view ccg --seed 13
```

`gcn`, `gat`, and `gin` are representation-controlled and ignore `edge_attr`.
Use the separately named `gine` experiment when edge semantics are enabled.

## Evaluation, explanation, resume, and status

```bash
uv run android-models evaluate --config configs/models/data.yaml --predictions MODEL_RUN/predictions.parquet --output MODEL_RUN/evaluation
uv run android-models explain --config configs/models/xai.yaml --model-dir MODEL_RUN --apk-sha256 SHA --view fcg --algorithm gnnexplainer
uv run android-models status --config configs/models/data.yaml
```

Pass `--resume` only with the identical resolved configuration. A config hash mismatch
is rejected. GRED, SIGDroid, and ANAKIN are fidelity-gated and are implemented after
the common baselines per the requested delivery order.
