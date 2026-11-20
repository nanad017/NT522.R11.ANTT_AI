from __future__ import annotations

import math
from collections import Counter, defaultdict, deque
from pathlib import Path

import numpy as np

from ..ir.ids import stable_id
from ..ir.io import write_json_atomic
from .graph_utils import collect_call_graph, security_concepts


def _region(nodes: list[str], edges: list[dict], seeds: set[str], radius: int) -> set[str]:
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        adjacency[edge["src_method_id"]].add(edge["dst_method_id"])
        adjacency[edge["dst_method_id"]].add(edge["src_method_id"])
    retained = set(seeds)
    queue = deque((seed, 0) for seed in seeds)
    while queue:
        node, distance = queue.popleft()
        if distance >= radius:
            continue
        for neighbor in sorted(adjacency[node]):
            if neighbor not in retained:
                retained.add(neighbor)
                queue.append((neighbor, distance + 1))
    return retained & set(nodes)


def build_gred(
    tables: dict[str, list[dict]],
    output: str | Path,
    taxonomy: dict,
    config: dict | None = None,
) -> dict:
    """Export a traceable GRED-shaped approximation from canonical FCG evidence.

    Exact GRED dictionaries/assets are intentionally not fabricated.  The
    output is useful for extraction and provenance checks, but is marked
    blocked for paper-faithful training/consumption.
    """
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    config = config or {}
    graph = collect_call_graph(tables)
    methods = graph["methods"]
    target_signatures = graph["external_signatures"]
    concepts = list(taxonomy.get("concepts", {}))
    sensitive_targets = {
        target_id
        for target_id, signature in target_signatures.items()
        if security_concepts(signature, taxonomy)
    }
    seeds = {
        edge["src_method_id"]
        for edge in graph["edges"]
        if edge["dst_method_id"] in sensitive_targets
    } | sensitive_targets
    radius = max(0, int(config.get("radius", 2)))
    retained = _region(graph["node_ids"], graph["edges"], seeds, radius)
    sensitive_regions = {
        seed: sorted(_region(graph["node_ids"], graph["edges"], {seed}, radius))
        for seed in sorted(seeds)
    }
    node_ids = sorted(retained)
    node_index = {node_id: index for index, node_id in enumerate(node_ids)}
    incoming = Counter()
    outgoing = Counter()
    for edge in graph["edges"]:
        if edge["src_method_id"] in retained and edge["dst_method_id"] in retained:
            outgoing[edge["src_method_id"]] += len(edge["raw_call_ids"])
            incoming[edge["dst_method_id"]] += len(edge["raw_call_ids"])
    feature_names = [
        "node_type.internal",
        "node_type.external",
        "missing.code",
        "structure.log1p_instructions",
        "structure.log1p_in_calls",
        "structure.log1p_out_calls",
    ] + [f"security.{concept}" for concept in concepts]
    x = np.zeros((len(node_ids), len(feature_names)), dtype=np.float32)
    for node_id, index in node_index.items():
        method = methods.get(node_id)
        signature = method["signature"] if method else target_signatures.get(node_id, "")
        x[index, 0 if method else 1] = 1
        x[index, 2] = 0 if method and method.get("code_available") else 1
        x[index, 3] = math.log1p(method.get("instruction_count") or 0) if method else 0
        x[index, 4] = math.log1p(incoming[node_id])
        x[index, 5] = math.log1p(outgoing[node_id])
        for concept in security_concepts(signature, taxonomy):
            x[index, feature_names.index(f"security.{concept}")] = 1
    edge_rows = []
    for edge in graph["edges"]:
        if edge["src_method_id"] not in node_index or edge["dst_method_id"] not in node_index:
            continue
        edge_rows.append(
            {
                "edge_id": stable_id(
                    "gred-edge", "gred-approx-v1", edge["src_method_id"], edge["dst_method_id"]
                ),
                "src": edge["src_method_id"],
                "dst": edge["dst_method_id"],
                "raw_call_ids": edge["raw_call_ids"],
                "retention_reason": "sensitive_seed_k_hop",
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
    missing_assets = {
        "permission_dictionary_247": "missing",
        "opcode_dictionary_224": "missing",
        "abstract_api_dictionary_445": "missing",
        "pscout_or_axplorer_snapshot": "missing",
    }
    mapping = {
        "view_version": "gred-approx-v1",
        "fidelity": "structural_approximation",
        "status": "blocked_missing_asset",
        "apk_sha256": sample["apk_sha256"],
        "radius": radius,
        "neighborhood_policy": "weakly_connected_undirected_for_region_directed_edges_retained",
        "sensitive_seed_node_ids": sorted(seeds),
        "sensitive_regions": sensitive_regions,
        "node_ids": node_ids,
        "retained_node_ids": node_ids,
        "dropped_node_ids": sorted(set(graph["node_ids"]) - retained),
        "feature_names": feature_names,
        "edge_ids": [row["edge_id"] for row in edge_rows],
        "edges": edge_rows,
        "missing_assets": missing_assets,
        "exact_feature_width": None,
    }
    write_json_atomic(output / "mapping.json", mapping)
    return {
        "status": "blocked_missing_asset",
        "fidelity": "structural_approximation",
        "nodes": len(node_ids),
        "edges": len(edge_rows),
        "features": len(feature_names),
        "sensitive_seeds": len(seeds),
    }
