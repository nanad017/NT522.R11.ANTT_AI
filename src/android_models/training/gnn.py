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
from torch import nn
from torch_geometric.loader import DataLoader

from ..contracts import ContractError, canonical_hash
from ..evaluation import evaluate_predictions
from ..features.graph import CatalogGraphDataset, DynamicGraphBatchSampler
from ..metrics import binary_metrics, choose_threshold
from ..models.common_gnn import CommonGNN
from ..registry import write_json_atomic
from ..reproducibility import seed_everything


def _sizes(dataset: CatalogGraphDataset) -> list[tuple[int, int]]:
    result = []
    for row in dataset.rows:
        with np.load(row[f"{dataset.view}_path"], allow_pickle=False) as raw:
            result.append((int(raw["x"].shape[0]), int(raw["edge_index"].shape[1])))
    return result


def _loader(
    dataset: CatalogGraphDataset,
    max_nodes: int,
    max_edges: int,
    shuffle: bool,
    seed: int,
    num_workers: int,
) -> DataLoader:
    sampler = DynamicGraphBatchSampler(
        _sizes(dataset), max_nodes=max_nodes, max_edges=max_edges, shuffle=shuffle, seed=seed
    )
    return DataLoader(dataset, batch_sampler=sampler, num_workers=num_workers, pin_memory=False)


def _forward(model: CommonGNN, batch, device: torch.device) -> torch.Tensor:
    batch = batch.to(device)
    edge_attr = getattr(batch, "edge_attr", None) if model.kind == "gine" else None
    return model(batch.x, batch.edge_index, batch.batch, edge_attr=edge_attr)


@torch.no_grad()
def _predict(model: CommonGNN, loader: DataLoader, device: torch.device):
    model.eval()
    rows: list[dict[str, Any]] = []
    logits: list[float] = []
    labels: list[int] = []
    for batch in loader:
        output = _forward(model, batch, device)
        batch_labels = batch.y.reshape(-1).cpu().numpy().astype(int)
        batch_logits = output.cpu().numpy()
        shas = list(batch.apk_sha256)
        years = batch.year.cpu().numpy().tolist() if torch.is_tensor(batch.year) else list(batch.year)
        for index, sha in enumerate(shas):
            rows.append({"apk_sha256": sha, "year": int(years[index])})
            logits.append(float(batch_logits[index]))
            labels.append(int(batch_labels[index]))
    return rows, np.asarray(logits), np.asarray(labels)


def train_common_gnn(
    catalog_rows: list[dict[str, Any]],
    view: str,
    kind: str,
    output: str | Path,
    config: dict[str, Any],
    seed: int,
) -> dict[str, Any]:
    seed_metadata = seed_everything(seed)
    torch.set_num_threads(int(config.get("torch_num_threads", torch.get_num_threads())))
    device = torch.device(config.get("device", "cpu"))
    if device.type == "cuda" and not torch.cuda.is_available():
        raise ContractError("CUDA requested but unavailable")
    datasets = {
        split: CatalogGraphDataset(catalog_rows, view, split)
        for split in ("train", "validation", "test")
    }
    if len(datasets["train"]) == 0 or len(datasets["validation"]) == 0:
        raise ContractError("GNN training requires nonempty train and validation graph sets")
    train_labels = {int(row["label"]) for row in datasets["train"].rows}
    validation_labels = {int(row["label"]) for row in datasets["validation"].rows}
    if train_labels != {0, 1} or validation_labels != {0, 1}:
        raise ContractError("GNN training/selection requires both classes in train and validation")
    first = datasets["train"][0]
    model_config = {
        "input_dim": int(first.x.shape[1]),
        "kind": kind,
        "hidden_dim": int(config.get("hidden_dim", 64)),
        "layers": int(config.get("layers", 2)),
        "dropout": float(config.get("dropout", 0.2)),
        "heads": int(config.get("heads", 2)),
        "train_eps": bool(config.get("train_eps", True)),
        "edge_dim": int(first.edge_attr.shape[1]) if kind == "gine" else None,
        "add_self_loops": bool(config.get("add_self_loops", True)),
    }
    model = CommonGNN(**model_config).to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=float(config.get("learning_rate", 1e-3)),
        weight_decay=float(config.get("weight_decay", 0.0)),
    )
    loss_fn = nn.BCEWithLogitsLoss()
    max_nodes = int(config.get("max_nodes_per_batch", 50_000))
    max_edges = int(config.get("max_edges_per_batch", 200_000))
    workers = int(config.get("num_workers", 0))
    loaders = {
        split: _loader(
            dataset,
            max_nodes,
            max_edges,
            shuffle=split == "train",
            seed=seed,
            num_workers=workers,
        )
        for split, dataset in datasets.items()
    }
    epochs = int(config.get("epochs", 50))
    patience = int(config.get("patience", 8))
    clip = float(config.get("gradient_clip", 2.0))
    best_ap = -np.inf
    best_epoch = -1
    best_state: dict[str, torch.Tensor] | None = None
    history: list[dict[str, Any]] = []
    stale = 0
    started = time.perf_counter()
    output_path = Path(output)
    output_path.mkdir(parents=True, exist_ok=True)
    for epoch in range(epochs):
        model.train()
        total_loss = 0.0
        total_graphs = 0
        for batch in loaders["train"]:
            optimizer.zero_grad(set_to_none=True)
            logits = _forward(model, batch, device)
            targets = batch.y.reshape(-1).to(device)
            loss = loss_fn(logits, targets)
            if not torch.isfinite(loss):
                raise ContractError("non-finite GNN training loss")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), clip)
            optimizer.step()
            total_loss += float(loss.detach()) * targets.numel()
            total_graphs += targets.numel()
        _, validation_logits, validation_y = _predict(model, loaders["validation"], device)
        validation_probability = 1.0 / (1.0 + np.exp(-validation_logits))
        ap = float(average_precision_score(validation_y, validation_probability))
        history.append(
            {"epoch": epoch, "train_loss": total_loss / total_graphs, "validation_pr_auc": ap}
        )
        if ap > best_ap:
            best_ap, best_epoch, stale = ap, epoch, 0
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
        else:
            stale += 1
            if stale >= patience:
                write_json_atomic(output_path / "history.json", history)
                break
        write_json_atomic(output_path / "history.json", history)
        write_json_atomic(
            output_path / "progress.json",
            {
                "status": "training",
                "epoch": epoch,
                "epochs_configured": epochs,
                "stale_epochs": stale,
                "best_epoch": best_epoch,
                "best_validation_pr_auc": best_ap,
                "elapsed_seconds": time.perf_counter() - started,
            },
        )
    if best_state is None:
        raise ContractError("GNN produced no checkpoint")
    checkpoint = output_path / "checkpoint.safetensors"
    save_file(best_state, checkpoint)
    checkpoint_metadata = {
        "format": "safetensors-state-dict",
        "model": model_config,
        "view": view,
        "seed": seed,
        "best_epoch": best_epoch,
        "primary_metric": "validation_pr_auc",
        "best_validation_pr_auc": best_ap,
        "train_sha_hash": canonical_hash(
            [row["apk_sha256"] for row in datasets["train"].rows]
        ),
        "batch_policy": {
            "max_nodes": max_nodes,
            "max_edges": max_edges,
            "oversized": "single_graph_fallback",
        },
        "directedness": "preserved",
        "self_loop_policy": "layer_internal" if model_config["add_self_loops"] else "none",
        "edge_use": kind == "gine",
        "readout": "global_mean_plus_global_max",
        "node_feature_names": list(first.feature_names),
        "edge_feature_names": list(first.edge_feature_names),
        "seed_metadata": seed_metadata,
        "torch_num_threads": torch.get_num_threads(),
    }
    write_json_atomic(output_path / "checkpoint.json", checkpoint_metadata)
    write_json_atomic(output_path / "history.json", history)
    reloaded = CommonGNN(**model_config).to(device)
    reloaded.load_state_dict(load_file(checkpoint, device=str(device)))
    prediction_rows: list[dict[str, Any]] = []
    view_hash = canonical_hash(
        {
            "view": view,
            "features": checkpoint_metadata["node_feature_names"],
            "edges": checkpoint_metadata["edge_feature_names"],
            "directedness": "preserved",
        }
    )
    split_values: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    split_meta: dict[str, list[dict[str, Any]]] = {}
    for split, loader in loaders.items():
        metadata, logits, labels = _predict(reloaded, loader, device)
        split_values[split] = (logits, labels)
        split_meta[split] = metadata
    validation_logits, validation_y = split_values["validation"]
    validation_probability = 1.0 / (1.0 + np.exp(-validation_logits))
    threshold = choose_threshold(validation_y, validation_probability)
    write_json_atomic(output_path / "threshold.json", threshold)
    metrics: dict[str, Any] = {}
    for split in ("train", "validation", "test"):
        logits, labels = split_values[split]
        probability = 1.0 / (1.0 + np.exp(-logits))
        if labels.size:
            metrics[split] = binary_metrics(labels, probability, threshold["threshold"])
        for index, metadata in enumerate(split_meta[split]):
            prediction_rows.append(
                {
                    **metadata,
                    "split": split,
                    "label": int(labels[index]),
                    "logit": float(logits[index]),
                    "probability": float(probability[index]),
                    "prediction": int(probability[index] >= threshold["threshold"]),
                    "threshold": float(threshold["threshold"]),
                    "view": view,
                    "model_run_id": output_path.name,
                    "view_hash": view_hash,
                    "coverage_status": "included",
                }
            )
    pq.write_table(pa.Table.from_pylist(prediction_rows), output_path / "predictions.parquet")
    write_json_atomic(output_path / "metrics-all-splits.json", metrics)
    if any(row["split"] == "test" for row in prediction_rows):
        evaluate_predictions(output_path / "predictions.parquet", output_path, "test")
    else:
        write_json_atomic(output_path / "metrics.json", metrics)
    fit = {
        "status": "complete",
        "runtime_seconds": time.perf_counter() - started,
        **checkpoint_metadata,
        "metrics": metrics,
    }
    write_json_atomic(output_path / "fit.json", fit)
    write_json_atomic(
        output_path / "progress.json",
        {
            "status": "complete",
            "best_epoch": best_epoch,
            "epochs_ran": len(history),
            "best_validation_pr_auc": best_ap,
            "runtime_seconds": fit["runtime_seconds"],
        },
    )
    return fit


def load_trained_gnn(directory: str | Path, device: str = "cpu") -> tuple[CommonGNN, dict[str, Any]]:
    directory = Path(directory)
    metadata = json.loads((directory / "checkpoint.json").read_text())
    if metadata.get("format") != "safetensors-state-dict":
        raise ContractError("unsupported GNN checkpoint format")
    model = CommonGNN(**metadata["model"])
    model.load_state_dict(load_file(directory / "checkpoint.safetensors", device=device))
    model.to(device).eval()
    return model, metadata
