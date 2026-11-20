from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch.utils.data import Sampler
from torch_geometric.data import Data, Dataset

from ..contracts import ContractError, enforce_fidelity

DEFAULT_FIDELITY = {"fcg": "common_baseline", "ccg": "canonical_class_collapse"}


def load_graph(row: dict[str, Any], view: str, requested_fidelity: str | None = None) -> Data:
    path_value = row.get(f"{view}_path")
    if not path_value:
        raise ContractError(f"missing {view} view for {row['apk_sha256']}")
    path = Path(path_value)
    mapping_path = path.parent / "mapping.json"
    if not mapping_path.exists():
        raise ContractError(f"missing mapping sidecar: {mapping_path}")
    mapping = json.loads(mapping_path.read_text())
    actual_fidelity = mapping.get("fidelity", DEFAULT_FIDELITY.get(view, "unknown"))
    if requested_fidelity:
        enforce_fidelity(view, actual_fidelity, requested_fidelity)
    with np.load(path, allow_pickle=False) as raw:
        required = {"x", "edge_index", "y"}
        if not required.issubset(raw.files):
            raise ContractError(f"{path} missing arrays {sorted(required - set(raw.files))}")
        x = np.asarray(raw["x"], dtype=np.float32)
        edge_index = np.asarray(raw["edge_index"], dtype=np.int64)
        y = np.asarray(raw["y"], dtype=np.int64).reshape(-1)
        edge_attr = (
            np.asarray(raw["edge_attr"], dtype=np.float32) if "edge_attr" in raw.files else None
        )
    if x.ndim != 2 or x.shape[0] == 0:
        raise ContractError(f"empty or malformed graph is not silently replaced: {path}")
    if edge_index.ndim != 2 or edge_index.shape[0] != 2:
        raise ContractError(f"edge_index must be [2,E]: {path}")
    if edge_index.size and (edge_index.min() < 0 or edge_index.max() >= x.shape[0]):
        raise ContractError(f"edge index out of range: {path}")
    if not np.isfinite(x).all() or (edge_attr is not None and not np.isfinite(edge_attr).all()):
        raise ContractError(f"non-finite graph features: {path}")
    mapping_mode = mapping.get("mapping_mode", "full")
    node_ids = mapping.get("node_ids", mapping.get("retained_node_ids"))
    edge_ids = mapping.get("edge_ids")
    feature_names = mapping.get("feature_names", [])
    edge_feature_names = mapping.get("edge_feature_names", [])
    if node_ids is not None:
        if len(node_ids) != x.shape[0]:
            raise ContractError(f"node mapping mismatch: {path}")
    elif mapping_mode == "model_only":
        if int(mapping.get("node_count", -1)) != x.shape[0]:
            raise ContractError(f"compact node count mismatch: {path}")
    else:
        raise ContractError(f"missing node mapping/count contract: {path}")
    if edge_ids is not None:
        if len(edge_ids) != edge_index.shape[1]:
            raise ContractError(f"edge mapping mismatch: {path}")
    elif mapping_mode == "model_only":
        if int(mapping.get("edge_count", -1)) != edge_index.shape[1]:
            raise ContractError(f"compact edge count mismatch: {path}")
    else:
        raise ContractError(f"missing edge mapping/count contract: {path}")
    if feature_names and len(feature_names) != x.shape[1]:
        raise ContractError(f"feature name width mismatch: {path}")
    if edge_attr is not None:
        if edge_attr.shape[0] != edge_index.shape[1]:
            raise ContractError(f"edge_attr length mismatch: {path}")
        if edge_feature_names and len(edge_feature_names) != edge_attr.shape[1]:
            raise ContractError(f"edge feature width mismatch: {path}")
    if y.shape != (1,) or int(y[0]) != int(row["label"]):
        raise ContractError(f"graph/catalog label mismatch: {path}")
    data = Data(
        x=torch.from_numpy(x),
        edge_index=torch.from_numpy(edge_index),
        y=torch.tensor([int(y[0])], dtype=torch.float32),
    )
    if edge_attr is not None:
        data.edge_attr = torch.from_numpy(edge_attr)
    data.apk_sha256 = row["apk_sha256"]
    data.split = row["split"]
    data.year = int(row["year"])
    data.graph_path = str(path)
    data.mapping_path = str(mapping_path)
    data.mapping_mode = mapping_mode
    data.view = view
    data.fidelity = actual_fidelity
    data.feature_names = feature_names
    data.edge_feature_names = edge_feature_names
    return data


class CatalogGraphDataset(Dataset):
    def __init__(self, rows: list[dict[str, Any]], view: str, split: str) -> None:
        super().__init__()
        self.rows = [row for row in rows if row["split"] == split and row.get(f"{view}_path")]
        self.rows.sort(key=lambda row: row["apk_sha256"])
        self.view = view

    def len(self) -> int:
        return len(self.rows)

    def get(self, index: int) -> Data:
        return load_graph(self.rows[index], self.view)


class DynamicGraphBatchSampler(Sampler[list[int]]):
    """Deterministic batches bounded by total nodes and edges.

    An oversized graph is yielded alone, never silently excluded.
    """

    def __init__(
        self,
        sizes: list[tuple[int, int]],
        max_nodes: int,
        max_edges: int,
        shuffle: bool = False,
        seed: int = 13,
    ) -> None:
        self.sizes = sizes
        self.max_nodes = max_nodes
        self.max_edges = max_edges
        self.shuffle = shuffle
        self.seed = seed
        if max_nodes <= 0 or max_edges <= 0:
            raise ContractError("graph batch budgets must be positive")

    def __iter__(self) -> Iterator[list[int]]:
        indices = np.arange(len(self.sizes))
        if self.shuffle:
            np.random.default_rng(self.seed).shuffle(indices)
        batch: list[int] = []
        nodes = edges = 0
        for raw_index in indices:
            index = int(raw_index)
            graph_nodes, graph_edges = self.sizes[index]
            exceeds = nodes + graph_nodes > self.max_nodes or edges + graph_edges > self.max_edges
            if batch and exceeds:
                yield batch
                batch, nodes, edges = [], 0, 0
            batch.append(index)
            nodes += graph_nodes
            edges += graph_edges
            if graph_nodes > self.max_nodes or graph_edges > self.max_edges:
                yield batch
                batch, nodes, edges = [], 0, 0
        if batch:
            yield batch

    def __len__(self) -> int:
        return sum(1 for _ in self.__iter__())
