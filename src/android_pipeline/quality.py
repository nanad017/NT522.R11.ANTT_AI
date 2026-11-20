from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq


def validate_batch(root: str | Path) -> dict:
    root = Path(root)
    errors: list[str] = []
    counts = {}
    for path in sorted(root.glob("*.parquet")):
        table = pq.read_table(path)
        counts[path.stem] = table.num_rows
    samples = pq.read_table(root / "samples.parquet").to_pylist()
    if not samples:
        errors.append("samples table is empty")
    for graph in root.rglob("views/*/*/graph.npz"):
        with np.load(graph) as d:
            x, edge_index, edge_attr = d["x"], d["edge_index"], d["edge_attr"]
            if not np.isfinite(x).all() or not np.isfinite(edge_attr).all():
                errors.append(f"non-finite graph feature: {graph}")
            if edge_index.shape[0] != 2:
                errors.append(f"bad edge_index shape: {graph}")
            if edge_index.size and (edge_index.min() < 0 or edge_index.max() >= x.shape[0]):
                errors.append(f"edge index out of range: {graph}")
            mapping = json.loads((graph.parent / "mapping.json").read_text())
            mapping_nodes = mapping.get("node_ids", mapping.get("retained_node_ids", []))
            mapping_edges = mapping.get("edge_ids", [])
            mapped_node_count = mapping.get("node_count", len(mapping_nodes))
            mapped_edge_count = mapping.get("edge_count", len(mapping_edges))
            if mapped_node_count != x.shape[0] or mapped_edge_count != edge_index.shape[1]:
                errors.append(f"mapping alignment failure: {graph}")
    checksum_path = root / "checksums.sha256"
    if checksum_path.exists():
        for line in checksum_path.read_text().splitlines():
            expected, relative = line.split("  ", 1)
            artifact = root / relative
            if not artifact.is_file():
                errors.append(f"checksum artifact missing: {relative}")
                continue
            digest = hashlib.sha256()
            with artifact.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != expected:
                errors.append(f"checksum mismatch: {relative}")
    return {"status": "ok" if not errors else "failed", "errors": errors, "table_rows": counts}
