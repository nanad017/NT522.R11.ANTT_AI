from __future__ import annotations

import json
from collections import Counter
from itertools import pairwise
from pathlib import Path

from ..ir.ids import stable_id
from ..ir.io import write_json_atomic


def build_anakin(tables: dict[str, list[dict]], output: str | Path) -> dict:
    """Export pre-fit API-transition graphs; TF-IDF/CBOW remain train-only jobs."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    graphs = []
    for sequence in tables.get("api_sequences", []):
        tokens = sequence["tokens"]
        nodes = sorted(set(tokens))
        transitions = Counter(pairwise(tokens))
        provenance = json.loads(sequence["token_provenance_json"])
        edges = []
        for (src, dst), weight in sorted(transitions.items()):
            positions = [
                i for i in range(len(tokens) - 1) if tokens[i] == src and tokens[i + 1] == dst
            ]
            edges.append(
                {
                    "edge_id": stable_id("anakin-transition", sequence["sequence_id"], src, dst),
                    "src": src,
                    "dst": dst,
                    "weight": weight,
                    "source_positions": positions,
                    "supporting_call_ids": sorted(
                        {
                            provenance[i]["call_id"]
                            for pos in positions
                            for i in (pos, pos + 1)
                            if i < len(provenance)
                        }
                    ),
                }
            )
        graphs.append(
            {
                "sequence_id": sequence["sequence_id"],
                "entry_id": sequence["entry_id"],
                "component_type": sequence["component_type"],
                "nodes": nodes,
                "edges": edges,
                "singleton_policy": "no_synthetic_loop",
                "truncated": sequence["truncated"],
                "tokens": tokens,
                "token_provenance": provenance,
            }
        )
    artifact = {
        "view_version": "anakin-reimplementation-v1",
        "fidelity": "reimplementation",
        "fit_status": "raw_pre_fit",
        "graphs": graphs,
    }
    write_json_atomic(output / "graphs.json", artifact)
    return {
        "graphs": len(graphs),
        "nonempty_graphs": sum(bool(g["nodes"]) for g in graphs),
        "transitions": sum(len(g["edges"]) for g in graphs),
        "fidelity": "reimplementation",
    }
