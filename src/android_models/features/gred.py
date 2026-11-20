from __future__ import annotations

import json
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import torch

from ..contracts import ContractError, enforce_fidelity
from .graph import load_graph


def load_gred_graph(row: dict[str, Any], requested_fidelity: str = "structural_approximation"):
    graph = load_graph(row, "gred", requested_fidelity=requested_fidelity)
    mapping = json.loads(Path(graph.mapping_path).read_text())
    enforce_fidelity("gred", mapping.get("fidelity", "unknown"), requested_fidelity)
    if requested_fidelity == "paper_faithful" and graph.x.shape[1] != 916:
        raise ContractError("GRED faithful graph must have exact feature width 916")
    node_index = {node_id: index for index, node_id in enumerate(mapping["node_ids"])}
    raw_regions = mapping.get("sensitive_regions")
    if raw_regions is None:
        adjacency: dict[str, set[str]] = defaultdict(set)
        for edge in mapping.get("edges", []):
            adjacency[edge["src"]].add(edge["dst"])
            adjacency[edge["dst"]].add(edge["src"])
        raw_regions = {}
        radius = int(mapping.get("radius", 2))
        for seed in mapping.get("sensitive_seed_node_ids", []):
            seen = {seed}
            queue = deque([(seed, 0)])
            while queue:
                node, distance = queue.popleft()
                if distance >= radius:
                    continue
                for neighbor in adjacency[node]:
                    if neighbor not in seen:
                        seen.add(neighbor)
                        queue.append((neighbor, distance + 1))
            raw_regions[seed] = sorted(seen)
    regions = [
        torch.tensor([node_index[node] for node in nodes if node in node_index], dtype=torch.long)
        for nodes in raw_regions.values()
    ]
    graph.region_seed_ids = list(raw_regions)
    return graph, regions, mapping


def gred_asset_gate(asset_manifest: dict[str, Any] | None) -> dict[str, Any]:
    required = (
        "permission_dictionary_247",
        "opcode_dictionary_224",
        "abstract_api_dictionary_445",
        "pscout_or_axplorer_snapshot",
        "topk_contract",
        "golden_fixture",
    )
    manifest = asset_manifest or {}
    missing = [name for name in required if not manifest.get(name)]
    return {
        "status": "paper_faithful_ready" if not missing else "blocked_missing_asset",
        "missing_assets": missing,
        "expected_feature_width": 916,
    }
