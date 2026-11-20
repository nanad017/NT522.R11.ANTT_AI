from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np


def _distribution(values: list[int]) -> dict[str, int | float | None]:
    if not values:
        return {"p50": None, "p95": None, "p99": None, "max": None}
    array = np.asarray(values)
    return {
        "p50": float(np.quantile(array, 0.50)),
        "p95": float(np.quantile(array, 0.95)),
        "p99": float(np.quantile(array, 0.99)),
        "max": int(array.max()),
    }


def graph_quality(rows: list[dict[str, Any]], view: str) -> dict[str, Any]:
    nodes: list[int] = []
    edges: list[int] = []
    widths: Counter[int] = Counter()
    fidelities: Counter[str] = Counter()
    invalid: list[dict[str, str]] = []
    zero_edge = non_finite = truncated = unresolved = 0
    available = [row for row in rows if row.get(f"{view}_path")]
    for row in available:
        path = Path(row[f"{view}_path"])
        try:
            with np.load(path, allow_pickle=False) as raw:
                x = raw["x"]
                edge_index = raw["edge_index"]
                edge_attr = raw["edge_attr"] if "edge_attr" in raw.files else None
                node_count, edge_count = int(x.shape[0]), int(edge_index.shape[1])
                nodes.append(node_count)
                edges.append(edge_count)
                widths[int(x.shape[1])] += 1
                zero_edge += edge_count == 0
                non_finite += int(not np.isfinite(x).all())
                if edge_attr is not None:
                    non_finite += int(not np.isfinite(edge_attr).all())
            mapping = json.loads((path.parent / "mapping.json").read_text())
            fidelities[mapping.get("fidelity", "common_baseline")] += 1
            truncated += int(bool(mapping.get("truncated", False)))
            unresolved += int(mapping.get("unresolved_call_count", 0) or 0)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            invalid.append({"apk_sha256": row["apk_sha256"], "reason": f"{type(exc).__name__}: {exc}"})
    return {
        "view": view,
        "cohort_rows": len(rows),
        "available": len(available),
        "missing_view": len(rows) - len(available),
        "invalid": len(invalid),
        "invalid_examples": invalid[:20],
        "empty_graph": sum(value == 0 for value in nodes),
        "zero_edge": zero_edge,
        "nodes": _distribution(nodes),
        "edges": _distribution(edges),
        "feature_widths": {str(key): value for key, value in sorted(widths.items())},
        "non_finite_feature_arrays": non_finite,
        "truncation_count": truncated,
        "unresolved_call_count": unresolved,
        "fidelity": dict(fidelities),
    }


def all_graph_quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {view: graph_quality(rows, view) for view in ("fcg", "ccg", "gred", "sigdroid")}
