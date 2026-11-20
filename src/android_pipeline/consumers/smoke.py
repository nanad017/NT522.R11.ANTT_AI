from __future__ import annotations

import json
from pathlib import Path

import numpy as np


def smoke_classical(batch_dirs: list[Path]) -> dict:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.feature_extraction import DictVectorizer
    from sklearn.svm import LinearSVC
    from xgboost import XGBClassifier

    records = []
    for batch in batch_dirs:
        path = batch / "classical-records.json"
        if path.exists():
            records.extend(json.loads(path.read_text()))
    classes = sorted({r["label"] for r in records})
    if len(records) < 2 or len(classes) < 2:
        return {
            "status": "blocked_insufficient_classes",
            "records": len(records),
            "classes": classes,
        }
    vectorizer = DictVectorizer(sparse=True, dtype=np.float32)
    x = vectorizer.fit_transform([r["features"] for r in records]).tocsr()
    if x.nnz > np.iinfo(np.int32).max or x.shape[1] > np.iinfo(np.int32).max:
        raise ValueError("classical smoke matrix exceeds safe CSR int32 index range")
    x.indices = x.indices.astype(np.int32, copy=False)
    x.indptr = x.indptr.astype(np.int32, copy=False)
    y = np.asarray([r["label"] for r in records], dtype=np.int64)
    models = {
        "linear_svm": LinearSVC(C=0.1, max_iter=100),
        "random_forest": RandomForestClassifier(
            n_estimators=5, max_depth=3, random_state=7, n_jobs=1
        ),
        "xgboost": XGBClassifier(n_estimators=2, max_depth=2, n_jobs=1, random_state=7),
    }
    outputs = {}
    for name, model in models.items():
        model.fit(x, y)
        prediction = model.predict(x)
        outputs[name] = {
            "finite": bool(np.isfinite(prediction).all()),
            "shape": list(prediction.shape),
        }
    return {
        "status": "ok",
        "records": len(records),
        "classes": classes,
        "matrix_shape": list(x.shape),
        "feature_names": len(vectorizer.feature_names_),
        "models": outputs,
    }


def smoke_gnn(graph_path: Path) -> dict:
    import torch
    from torch import nn
    from torch_geometric.nn import GATConv, GCNConv, GINConv, global_mean_pool

    with np.load(graph_path) as raw:
        x = torch.from_numpy(raw["x"]).float()
        edge_index = torch.from_numpy(raw["edge_index"]).long()
        y = torch.from_numpy(raw["y"]).long()
    batch = torch.zeros(x.shape[0], dtype=torch.long)

    class GraphModel(nn.Module):
        def __init__(self, kind: str):
            super().__init__()
            if kind == "gcn":
                self.conv = GCNConv(x.shape[1], 8)
            elif kind == "gat":
                self.conv = GATConv(x.shape[1], 8, heads=1)
            else:
                self.conv = GINConv(
                    nn.Sequential(nn.Linear(x.shape[1], 16), nn.ReLU(), nn.Linear(16, 8))
                )
            self.out = nn.Linear(8, 2)

        def forward(self):
            hidden = self.conv(x, edge_index).relu()
            return self.out(global_mean_pool(hidden, batch))

    result = {}
    for kind in ("gcn", "gat", "gin"):
        torch.manual_seed(7)
        model = GraphModel(kind)
        logits = model()
        loss = nn.functional.cross_entropy(logits, y)
        loss.backward()
        gradients = [p.grad for p in model.parameters() if p.requires_grad]
        result[kind] = {
            "logits_shape": list(logits.shape),
            "loss": float(loss.detach()),
            "finite": bool(torch.isfinite(logits).all() and torch.isfinite(loss)),
            "gradient_present": all(g is not None for g in gradients),
        }
    return {
        "status": "ok",
        "nodes": x.shape[0],
        "edges": edge_index.shape[1],
        "features": x.shape[1],
        "models": result,
    }


def smoke_all(batch_dirs: list[Path]) -> dict:
    graph_paths = []
    anakin_paths = []
    for batch in batch_dirs:
        graph_paths.extend(sorted(batch.rglob("views/fcg/*/graph.npz")))
        anakin_paths.extend(sorted(batch.rglob("views/anakin/*/graphs.json")))
    if not graph_paths:
        raise ValueError("no graph.npz found")
    return {
        "classical": smoke_classical(batch_dirs),
        "common_gnn": smoke_gnn(graph_paths[0]),
        "ccg": _smoke_view(batch_dirs, "ccg", expected_fidelity="canonical_class_collapse"),
        "gred": _smoke_view(
            batch_dirs, "gred", expected_fidelity="structural_approximation", blocked=True
        ),
        "sigdroid": _smoke_view(
            batch_dirs, "sigdroid", expected_fidelity="structural_approximation", blocked=True
        ),
        "anakin": _smoke_anakin(anakin_paths),
    }


def _smoke_view(
    batches: list[Path], view: str, *, expected_fidelity: str, blocked: bool = False
) -> dict:
    paths = sorted(path for batch in batches for path in batch.rglob(f"views/{view}/*/graph.npz"))
    if not paths:
        return {
            "status": "blocked_missing_view",
            "view": view,
            "fidelity": expected_fidelity,
        }
    checked = 0
    for graph in paths:
        with np.load(graph) as raw:
            x, edge_index = raw["x"], raw["edge_index"]
            if not np.isfinite(x).all() or edge_index.shape[0] != 2:
                raise ValueError(f"invalid {view} graph: {graph}")
        mapping = json.loads((graph.parent / "mapping.json").read_text())
        mapping_nodes = mapping.get("node_ids", mapping.get("retained_node_ids", []))
        if len(mapping_nodes) != x.shape[0]:
            raise ValueError(f"{view} node mapping mismatch: {graph}")
        checked += 1
    return {
        "status": "blocked_missing_asset" if blocked else "ok",
        "fidelity": expected_fidelity,
        "artifacts": checked,
        "asset_contract": "approximation_only" if blocked else "available",
    }


def _smoke_anakin(paths: list[Path]) -> dict:
    if not paths:
        return {"status": "blocked_missing_p1_sequence_view", "fidelity": "reimplementation"}
    graph_count = transition_count = provenance_edges = 0
    for path in paths:
        artifact = json.loads(path.read_text())
        if artifact.get("fidelity") != "reimplementation":
            raise ValueError(f"unexpected ANAKIN fidelity: {path}")
        graph_count += len(artifact["graphs"])
        for graph in artifact["graphs"]:
            transition_count += len(graph["edges"])
            provenance_edges += sum(bool(edge["supporting_call_ids"]) for edge in graph["edges"])
    return {
        "status": "ok",
        "fidelity": "reimplementation",
        "graphs": graph_count,
        "transitions": transition_count,
        "transitions_with_call_provenance": provenance_edges,
        "fit_status": "raw_pre_fit",
    }
