from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import torch
from safetensors.torch import save_file
from sklearn.metrics import average_precision_score

from ..contracts import ContractError, canonical_hash
from ..evaluation import evaluate_predictions
from ..features.gred import load_gred_graph
from ..metrics import choose_threshold
from ..models.gred import GREDApprox
from ..registry import write_json_atomic
from ..reproducibility import seed_everything


def train_gred_approx(
    rows: list[dict[str, Any]], output: str | Path, config: dict[str, Any], seed: int
) -> dict[str, Any]:
    seed_everything(seed)
    examples = {
        split: [
            (row, *load_gred_graph(row))
            for row in rows
            if row["split"] == split and row.get("gred_path")
        ]
        for split in ("train", "validation", "test")
    }
    for split in ("train", "validation"):
        if {int(row["label"]) for row, *_ in examples[split]} != {0, 1}:
            raise ContractError(f"GRED approximation {split} requires both classes")
    first = examples["train"][0][1]
    model_config = {
        "input_dim": int(first.x.shape[1]),
        "hidden_dim": int(config.get("hidden_dim", 64)),
        "layers": int(config.get("layers", 2)),
        "top_k_regions": int(config.get("top_k_regions", 5)),
        "dropout": float(config.get("dropout", 0.2)),
    }
    model = GREDApprox(**model_config)
    optimizer = torch.optim.Adam(model.parameters(), lr=float(config.get("learning_rate", 1e-3)))
    best_ap = -np.inf
    best_state = None
    best_epoch = -1
    history = []

    @torch.no_grad()
    def predict(split: str):
        model.eval()
        result = []
        for row, graph, regions, _ in examples[split]:
            logit, scores, selected = model(graph, regions)
            result.append((row, float(torch.sigmoid(logit)), scores.tolist(), selected.tolist()))
        return result

    for epoch in range(int(config.get("epochs", 30))):
        model.train()
        losses = []
        for row, graph, regions, _ in examples["train"]:
            optimizer.zero_grad(set_to_none=True)
            logit, _, _ = model(graph, regions)
            loss = torch.nn.functional.binary_cross_entropy_with_logits(
                logit, torch.tensor(float(row["label"]))
            )
            loss.backward()
            optimizer.step()
            losses.append(float(loss.detach()))
        validation = predict("validation")
        ap = float(average_precision_score([r[0]["label"] for r in validation], [r[1] for r in validation]))
        history.append({"epoch": epoch, "loss": float(np.mean(losses)), "validation_pr_auc": ap})
        if ap > best_ap:
            best_ap, best_epoch = ap, epoch
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
    if best_state is None:
        raise ContractError("GRED approximation produced no checkpoint")
    model.load_state_dict(best_state)
    output_path = Path(output)
    output_path.mkdir(parents=True, exist_ok=True)
    save_file(best_state, output_path / "checkpoint.safetensors")
    checkpoint = {
        "format": "safetensors-state-dict",
        "model": model_config,
        "model_name": "gred_approx_v1",
        "fidelity": "structural_approximation",
        "watermark": "NOT_PAPER_FAITHFUL",
        "missing_assets": ["exact_247_permissions", "exact_224_opcodes", "exact_445_abstract_apis", "verified_mapping"],
        "best_epoch": best_epoch,
        "seed": seed,
    }
    write_json_atomic(output_path / "checkpoint.json", checkpoint)
    validation = predict("validation")
    threshold = choose_threshold(
        np.asarray([row["label"] for row, *_ in validation]),
        np.asarray([probability for _, probability, *_ in validation]),
    )
    write_json_atomic(output_path / "threshold.json", threshold)
    predictions = []
    region_scores = []
    view_hash = canonical_hash({"model": model_config, "feature_width": first.x.shape[1]})
    for split in examples:
        for row, probability, scores, selected in predict(split):
            predictions.append(
                {
                    "apk_sha256": row["apk_sha256"], "split": split, "year": row["year"],
                    "label": row["label"], "score": probability, "probability": probability,
                    "prediction": int(probability >= threshold["threshold"]),
                    "threshold": threshold["threshold"], "model_run_id": output_path.name,
                    "view_hash": view_hash, "coverage_status": "included",
                }
            )
            region_scores.append(
                {"apk_sha256": row["apk_sha256"], "split": split, "scores": scores, "selected_region_indices": selected}
            )
    pq.write_table(pa.Table.from_pylist(predictions), output_path / "predictions.parquet")
    write_json_atomic(output_path / "region-scores.json", region_scores)
    evaluate_predictions(output_path / "predictions.parquet", output_path, "test")
    fit = {"status": "complete", "history": history, **checkpoint}
    write_json_atomic(output_path / "fit.json", fit)
    return fit
