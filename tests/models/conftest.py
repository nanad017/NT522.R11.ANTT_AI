from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pytest

from android_models.catalog import build_catalog, load_catalog


@pytest.fixture
def synthetic_catalog(tmp_path: Path) -> tuple[Path, list[dict]]:
    manifest = tmp_path / "manifest.csv"
    rows = []
    splits = [("train", 2019), ("validation", 2022), ("test", 2023)]
    for split_index, (split, year) in enumerate(splits):
        for label in (0, 1):
            sha = f"{split_index * 2 + label + 1:064x}"
            rows.append(
                {
                    "sha256": sha,
                    "label": "malware" if label else "benign",
                    "split": split,
                    "year": year,
                }
            )
    with manifest.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sha256", "label", "split", "year"])
        writer.writeheader()
        writer.writerows(rows)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "validation.json").write_text(json.dumps({"status": "ok", "errors": []}))
    records = []
    for row in rows:
        sha = row["sha256"]
        label = int(row["label"] == "malware")
        records.append(
            {
                "apk_sha256": sha,
                "label": label,
                "features": {
                    f"permission::class_{label}": 1.0,
                    "opcode::move": float(label + 1),
                    "graph::nodes": float(label + 2),
                },
            }
        )
        graph_dir = artifacts / "views" / "fcg" / sha
        graph_dir.mkdir(parents=True, exist_ok=True)
        x = np.asarray([[1.0, label, 0.0], [0.0, label, 1.0]], dtype=np.float32)
        edge_index = np.asarray([[0], [1]], dtype=np.int64)
        edge_attr = np.asarray([[1.0, 1.0]], dtype=np.float32)
        np.savez_compressed(
            graph_dir / "graph.npz",
            x=x,
            edge_index=edge_index,
            edge_attr=edge_attr,
            y=np.asarray([label], dtype=np.int64),
        )
        (graph_dir / "mapping.json").write_text(
            json.dumps(
                {
                    "apk_sha256": sha,
                    "fidelity": "common_baseline",
                    "node_ids": [f"{sha}-n0", f"{sha}-n1"],
                    "edge_ids": [f"{sha}-e0"],
                    "feature_names": ["internal", "malicious_signal", "external"],
                    "edge_feature_names": ["weight", "invoke.direct"],
                    "edges": [{"raw_call_ids": [f"{sha}-call"]}],
                }
            )
        )
    (artifacts / "classical-records.json").write_text(json.dumps(records))
    output = tmp_path / "catalog"
    report = build_catalog(manifest, [artifacts], output)
    assert report["rows"] == 6
    return output / "catalog.parquet", load_catalog(output / "catalog.parquet")
