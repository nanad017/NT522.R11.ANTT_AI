from __future__ import annotations

import math
from collections import Counter
from pathlib import Path

import numpy as np

from ..ir.ids import stable_id
from ..ir.io import write_json_atomic
from .graph_utils import collect_call_graph, security_concepts


def build_sigdroid(
    tables: dict[str, list[dict]],
    output: str | Path,
    taxonomy: dict,
    config: dict | None = None,
) -> dict:
    """Export an internal-function SIGDroid-shaped approximation.

    JADX/APIGraph/LongCoder assets are not available on this VM, therefore the
    semantic source block is explicitly absent and never replaced by a fake
    embedding.
    """
    del config
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    graph = collect_call_graph(tables)
    methods = graph["methods"]
    node_ids = sorted(methods)
    node_index = {node_id: index for index, node_id in enumerate(node_ids)}
    concepts = list(taxonomy.get("concepts", {}))
    incoming = Counter()
    outgoing = Counter()
    edges = []
    removed_external_nodes = set()
    for edge in graph["edges"]:
        src, dst = edge["src_method_id"], edge["dst_method_id"]
        if dst not in methods:
            removed_external_nodes.add(dst)
            continue
        if src not in methods:
            removed_external_nodes.add(src)
            continue
        incoming[dst] += len(edge["raw_call_ids"])
        outgoing[src] += len(edge["raw_call_ids"])
        edges.append(
            {
                "edge_id": stable_id("sigdroid-edge", "sigdroid-approx-v1", src, dst),
                "src": src,
                "dst": dst,
                "raw_call_ids": edge["raw_call_ids"],
                "edge_kind": "internal-call",
            }
        )
    feature_names = [
        "missing.code",
        "structure.log1p_instructions",
        "structure.log1p_in_calls",
        "structure.log1p_out_calls",
    ] + [f"security.{concept}" for concept in concepts]
    x = np.zeros((len(node_ids), len(feature_names)), dtype=np.float32)
    for node_id, index in node_index.items():
        method = methods[node_id]
        x[index, 0] = 0 if method.get("code_available") else 1
        x[index, 1] = math.log1p(method.get("instruction_count") or 0)
        x[index, 2] = math.log1p(incoming[node_id])
        x[index, 3] = math.log1p(outgoing[node_id])
        for concept in security_concepts(method.get("signature", ""), taxonomy):
            x[index, feature_names.index(f"security.{concept}")] = 1
    edge_index = np.asarray(
        [[node_index[row["src"]] for row in edges], [node_index[row["dst"]] for row in edges]],
        dtype=np.int64,
    ).reshape(2, -1)
    sample = tables["samples"][0]
    np.savez_compressed(
        output / "graph.npz",
        x=x,
        edge_index=edge_index,
        edge_attr=np.asarray(
            [[math.log1p(len(row["raw_call_ids"]))] for row in edges], dtype=np.float32
        ).reshape(-1, 1),
        y=np.asarray([sample["label_binary"]], dtype=np.int64),
    )
    mapping = {
        "view_version": "sigdroid-approx-v1",
        "fidelity": "structural_approximation",
        "status": "blocked_missing_asset",
        "apk_sha256": sample["apk_sha256"],
        "node_ids": node_ids,
        "edge_ids": [row["edge_id"] for row in edges],
        "edges": edges,
        "feature_names": feature_names,
        "source_semantics": {
            "status": "blocked_missing_asset",
            "source_tool": "JADX",
            "embedding_model": "LongCoder",
            "source_mapping": "not_enabled",
        },
        "removed_external_node_ids": sorted(removed_external_nodes),
        "derived_edges": [],
        "missing_assets": ["JADX", "APIGraph", "LongCoder", "noise_rules"],
    }
    write_json_atomic(output / "mapping.json", mapping)
    return {
        "status": "blocked_missing_asset",
        "fidelity": "structural_approximation",
        "nodes": len(node_ids),
        "edges": len(edges),
        "features": len(feature_names),
        "semantic_source": "blocked_missing_asset",
    }
