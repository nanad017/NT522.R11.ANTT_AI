from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import torch
from safetensors.torch import load_file, save_file
from sklearn.metrics import average_precision_score

from ..contracts import ContractError, canonical_hash
from ..evaluation import evaluate_predictions
from ..features.anakin import apk_oov_report, transform_anakin_apk
from ..metrics import choose_threshold
from ..models.anakin import AnakinGCN
from ..registry import write_json_atomic
from ..reproducibility import seed_everything


def _apk_graphs(rows: list[dict[str, Any]], feature_dir: str | Path) -> list[tuple[dict, list]]:
    return [(row, transform_anakin_apk(row, feature_dir)) for row in rows if row.get("anakin_path")]


@torch.no_grad()
def _predict(model: AnakinGCN, examples: list[tuple[dict, list]], device: torch.device):
    model.eval()
    output = []
    for row, graphs in examples:
        if not graphs:
            output.append({**row, "coverage_status": "excluded_empty_after_pruning"})
            continue
        graphs = [graph.to(device) for graph in graphs]
        probability, logits = model.aggregate_apk(graphs)
        output.append(
            {
                **row,
                "coverage_status": "included",
                "probability": float(probability.cpu()),
                "graph_logits": [float(value) for value in logits.cpu()],
                "graph_count": len(graphs),
            }
        )
    return output


def train_anakin(
    rows: list[dict[str, Any]],
    feature_dir: str | Path,
    output: str | Path,
    config: dict[str, Any],
    seed: int,
) -> dict[str, Any]:
    seed_metadata = seed_everything(seed)
    device = torch.device(config.get("device", "cpu"))
    split_examples = {
        split: _apk_graphs([row for row in rows if row["split"] == split], feature_dir)
        for split in ("train", "validation", "test")
    }
    for split in ("train", "validation"):
        included_labels = {int(row["label"]) for row, graphs in split_examples[split] if graphs}
        if included_labels != {0, 1}:
            raise ContractError(f"ANAKIN {split} requires both classes after pruning")
    _, _, feature_metadata = __import__(
        "android_models.features.anakin", fromlist=["load_anakin_features"]
    ).load_anakin_features(feature_dir)
    model_config = {
        "input_dim": int(feature_metadata["embedding"]["dimension"]),
        "hidden_dim": int(config.get("hidden_dim", 64)),
        "graph_layers": int(config.get("graph_layers", 2)),
        "fc_layers": int(config.get("fc_layers", 3)),
        "dropout": float(config.get("dropout", 0.19)),
    }
    model = AnakinGCN(**model_config).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=float(config.get("learning_rate", 0.00026)))
    epochs = int(config.get("epochs", 50))
    patience = int(config.get("patience", 8))
    best_ap = -np.inf
    best_epoch = -1
    best_state = None
    stale = 0
    history = []
    started = time.perf_counter()
    for epoch in range(epochs):
        model.train()
        losses = []
        generator = torch.Generator().manual_seed(seed + epoch)
        order = torch.randperm(len(split_examples["train"]), generator=generator).tolist()
        for index in order:
            row, graphs = split_examples["train"][index]
            if not graphs:
                continue
            optimizer.zero_grad(set_to_none=True)
            probability, _ = model.aggregate_apk([graph.to(device) for graph in graphs])
            target = torch.tensor(float(row["label"]), device=device)
            loss = torch.nn.functional.binary_cross_entropy(probability, target)
            loss.backward()
            optimizer.step()
            losses.append(float(loss.detach()))
        validation = _predict(model, split_examples["validation"], device)
        included = [row for row in validation if row["coverage_status"] == "included"]
        ap = float(
            average_precision_score(
                [row["label"] for row in included], [row["probability"] for row in included]
            )
        )
        history.append({"epoch": epoch, "loss": float(np.mean(losses)), "validation_pr_auc": ap})
        if ap > best_ap:
            best_ap, best_epoch, stale = ap, epoch, 0
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
        else:
            stale += 1
            if stale >= patience:
                break
    if best_state is None:
        raise ContractError("ANAKIN produced no checkpoint")
    output_path = Path(output)
    output_path.mkdir(parents=True, exist_ok=True)
    save_file(best_state, output_path / "checkpoint.safetensors")
    model.load_state_dict(best_state)
    model.to(device).eval()
    checkpoint = {
        "format": "safetensors-state-dict",
        "model": model_config,
        "fidelity": "reimplementation",
        "view": "api_transition_graph",
        "apk_aggregation": "mean_soft_probability",
        "training_loss_level": "apk",
        "best_epoch": best_epoch,
        "best_validation_pr_auc": best_ap,
        "feature_fit_hash": canonical_hash(feature_metadata),
        "seed": seed,
        "seed_metadata": seed_metadata,
    }
    write_json_atomic(output_path / "checkpoint.json", checkpoint)
    validation = _predict(model, split_examples["validation"], device)
    included_validation = [row for row in validation if row["coverage_status"] == "included"]
    threshold = choose_threshold(
        np.asarray([row["label"] for row in included_validation]),
        np.asarray([row["probability"] for row in included_validation]),
    )
    write_json_atomic(output_path / "threshold.json", threshold)
    predictions = []
    for split in ("train", "validation", "test"):
        for row in _predict(model, split_examples[split], device):
            base = {
                "apk_sha256": row["apk_sha256"],
                "split": split,
                "year": int(row["year"]),
                "label": int(row["label"]),
                "coverage_status": row["coverage_status"],
                "model_run_id": output_path.name,
                "view_hash": checkpoint["feature_fit_hash"],
                "threshold": float(threshold["threshold"]),
            }
            if row["coverage_status"] == "included":
                base.update(
                    probability=row["probability"],
                    score=row["probability"],
                    prediction=int(row["probability"] >= threshold["threshold"]),
                    graph_count=row["graph_count"],
                )
            else:
                base.update(probability=None, score=None, prediction=None, graph_count=0)
            predictions.append(base)
    pq.write_table(pa.Table.from_pylist(predictions), output_path / "predictions.parquet")
    if any(row["split"] == "test" and row["coverage_status"] == "included" for row in predictions):
        evaluate_predictions(output_path / "predictions.parquet", output_path, "test")
    coverage = {
        split: {
            "source_apks": len(split_examples[split]),
            "included": sum(bool(graphs) for _, graphs in split_examples[split]),
            "oov": apk_oov_report([row for row, _ in split_examples[split]], feature_dir),
        }
        for split in split_examples
    }
    fit = {
        "status": "complete",
        "runtime_seconds": time.perf_counter() - started,
        "history": history,
        "coverage": coverage,
        **checkpoint,
    }
    write_json_atomic(output_path / "fit.json", fit)
    return fit


def load_trained_anakin(directory: str | Path, device: str = "cpu"):
    directory = Path(directory)
    metadata = json.loads((directory / "checkpoint.json").read_text())
    model = AnakinGCN(**metadata["model"])
    model.load_state_dict(load_file(directory / "checkpoint.safetensors", device=device))
    model.to(device).eval()
    return model, metadata
