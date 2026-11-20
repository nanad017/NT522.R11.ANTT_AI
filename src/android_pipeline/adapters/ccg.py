from __future__ import annotations

import math
from collections import Counter
from pathlib import Path

import numpy as np

from ..ir.ids import stable_id
from ..ir.io import write_json_atomic
from .graph_utils import class_graph, security_concepts


def build_ccg(tables: dict[str, list[dict]], output: str | Path, taxonomy: dict) -> dict:
    """Build a class-call graph while retaining method/callsite evidence."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    graph = class_graph(tables)
    nodes = graph["nodes"]
    node_ids = sorted(nodes)
    node_index = {node_id: index for index, node_id in enumerate(node_ids)}
    incoming = Counter()
    outgoing = Counter()
    for edge in graph["edges"]:
        outgoing[edge["src"]] += len(edge["raw_call_ids"])
        incoming[edge["dst"]] += len(edge["raw_call_ids"])
    class_method_counts = Counter(
        row["class_id"] for row in tables.get("methods", []) if row.get("class_id") in nodes
    )
    concepts = list(taxonomy.get("concepts", {}))
    feature_names = [
        "node_type.internal",
        "node_type.external",
        "structure.log1p_methods",
        "structure.log1p_in_calls",
        "structure.log1p_out_calls",
    ] + [f"security.{concept}" for concept in concepts]
    x = np.zeros((len(node_ids), len(feature_names)), dtype=np.float32)
    for node_id, index in node_index.items():
        row = nodes[node_id]
        descriptor = row.get("descriptor") or ""
        x[index, 0 if row["is_defined"] else 1] = 1
        x[index, 2] = math.log1p(class_method_counts[node_id])
        x[index, 3] = math.log1p(incoming[node_id])
        x[index, 4] = math.log1p(outgoing[node_id])
        for offset, concept in enumerate(security_concepts(descriptor, taxonomy), start=5):
            x[index, feature_names.index(f"security.{concept}")] = 1
    edge_rows = []
    for edge in graph["edges"]:
        edge_rows.append(
            {
                "edge_id": stable_id("ccg-edge", "ccg-v1", edge["src"], edge["dst"]),
                **edge,
            }
        )
    edge_index = np.asarray(
        [
            [node_index[row["src"]] for row in edge_rows],
            [node_index[row["dst"]] for row in edge_rows],
        ],
        dtype=np.int64,
    ).reshape(2, -1)
    sample = tables["samples"][0]
    np.savez_compressed(
        output / "graph.npz",
        x=x,
        edge_index=edge_index,
        edge_attr=np.asarray(
            [[math.log1p(len(row["raw_call_ids"]))] for row in edge_rows], dtype=np.float32
        ).reshape(-1, 1),
        y=np.asarray([sample["label_binary"]], dtype=np.int64),
    )
    mapping = {
        "view_version": "ccg-v1",
        "fidelity": "canonical_class_collapse",
        "apk_sha256": sample["apk_sha256"],
        "node_ids": node_ids,
        "nodes": [nodes[node_id] for node_id in node_ids],
        "edge_ids": [row["edge_id"] for row in edge_rows],
        "edges": edge_rows,
        "feature_names": feature_names,
        "edge_feature_names": ["log1p_callsite_multiplicity"],
    }
    write_json_atomic(output / "mapping.json", mapping)
    return {"nodes": len(node_ids), "edges": len(edge_rows), "features": len(feature_names)}
