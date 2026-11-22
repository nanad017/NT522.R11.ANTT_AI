from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from .contracts import LABELS, SPLITS, CatalogRow, ContractError, canonical_hash, normalize_sha
from .registry import write_json_atomic

VIEW_PATTERNS = {
    "fcg": "views/fcg/*/graph.npz",
    "ccg": "views/ccg/*/graph.npz",
    "gred": "views/gred/*/graph.npz",
    "sigdroid": "views/sigdroid/*/graph.npz",
    "anakin": "views/anakin/*/graphs.json",
}


def read_manifest(path: str | Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    with Path(path).open(newline="", encoding="utf-8") as handle:
        for line, raw in enumerate(csv.DictReader(handle), start=2):
            sha = normalize_sha(raw.get("sha256", ""))
            label_text = raw.get("label", "").lower()
            split = raw.get("split", "").lower()
            if label_text not in LABELS or split not in SPLITS:
                raise ContractError(f"manifest row {line}: invalid label/split")
            normalized = {
                "apk_sha256": sha,
                "label": LABELS[label_text],
                "label_text": label_text,
                "split": split,
                "year": int(raw["year"]),
            }
            if sha in rows and rows[sha] != normalized:
                raise ContractError(f"duplicate/conflicting manifest SHA: {sha}")
            if sha in rows:
                raise ContractError(f"duplicate manifest SHA: {sha}")
            rows[sha] = normalized
    return rows


def _discover_roots(roots: Iterable[str | Path]) -> tuple[dict[str, dict[str, Path]], list[str]]:
    found: dict[str, dict[str, Path]] = defaultdict(dict)
    warnings: list[str] = []
    for raw_root in roots:
        root = Path(raw_root).resolve()
        if not root.exists():
            warnings.append(f"missing artifact root: {root}")
            continue
        for record_file in root.rglob("classical-records.json"):
            validation_file = record_file.parent / "validation.json"
            try:
                validation = json.loads(validation_file.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                warnings.append(f"missing/invalid validation sidecar {validation_file}: {exc}")
                continue
            if validation.get("status") != "ok":
                warnings.append(f"non-usable validation status for {record_file}")
                continue
            try:
                records = json.loads(record_file.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                warnings.append(f"invalid classical record file {record_file}: {exc}")
                continue
            for record in records:
                sha = normalize_sha(record["apk_sha256"])
                previous = found[sha].get("classical")
                if previous and previous != record_file:
                    warnings.append(f"duplicate classical view for {sha}: keeping {previous}")
                else:
                    found[sha]["classical"] = record_file
        for view, pattern in VIEW_PATTERNS.items():
            for artifact in root.rglob(pattern):
                sha = normalize_sha(artifact.parent.name)
                previous = found[sha].get(view)
                if previous and previous != artifact:
                    warnings.append(f"duplicate {view} view for {sha}: keeping {previous}")
                else:
                    found[sha][view] = artifact
    return found, warnings


def build_catalog(
    manifest_path: str | Path,
    artifact_roots: Iterable[str | Path],
    output: str | Path,
) -> dict[str, Any]:
    manifest = read_manifest(manifest_path)
    discovered, warnings = _discover_roots(artifact_roots)
    unknown = sorted(set(discovered) - set(manifest))
    if unknown:
        raise ContractError(f"artifacts contain {len(unknown)} SHA not in manifest")
    rows: list[CatalogRow] = []
    for sha, metadata in manifest.items():
        views = discovered.get(sha, {})
        rows.append(
            CatalogRow(
                **metadata,
                analysis_status="usable" if views else "missing_all_views",
                **{f"{view}_path": str(views[view]) if view in views else None for view in ("classical", *VIEW_PATTERNS)},
            )
        )
    payload = [row.as_dict() for row in rows]
    output_path = Path(output)
    output_path.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(payload)
    pq.write_table(table, output_path / "catalog.parquet", compression="zstd")
    exclusions = []
    for row in payload:
        missing_required = [view for view in ("classical", "fcg") if not row[f"{view}_path"]]
        if missing_required:
            exclusions.append(
                {
                    "apk_sha256": row["apk_sha256"],
                    "split": row["split"],
                    "year": int(row["year"]),
                    "label": int(row["label"]),
                    "reason": "missing_required_compact_views",
                    "missing_views": ",".join(missing_required),
                }
            )
    exclusion_schema = pa.schema(
        [
            ("apk_sha256", pa.string()),
            ("split", pa.string()),
            ("year", pa.int64()),
            ("label", pa.int64()),
            ("reason", pa.string()),
            ("missing_views", pa.string()),
        ]
    )
    pq.write_table(
        pa.Table.from_pylist(exclusions, schema=exclusion_schema),
        output_path / "exclusions.parquet",
        compression="zstd",
    )
    coverage: dict[str, Any] = {}
    for view in ("classical", *VIEW_PATTERNS):
        usable = [row for row in payload if row[f"{view}_path"]]
        coverage[view] = {
            "total": len(usable),
            "by_split": dict(Counter(row["split"] for row in usable)),
            "by_year": {str(k): v for k, v in sorted(Counter(row["year"] for row in usable).items())},
            "by_label": dict(Counter(row["label_text"] for row in usable)),
        }
    report = {
        "catalog_version": "android-model-catalog-v1",
        "rows": len(rows),
        "catalog_hash": canonical_hash(payload),
        "coverage": coverage,
        "warnings": warnings,
        "required_compact_views": ["classical", "fcg"],
        "joint_compact_usable": len(rows) - len(exclusions),
        "excluded": len(exclusions),
        "exclusion_manifest": str(output_path / "exclusions.parquet"),
        "full_training_ready": len(rows) == 10_000
        and all(row.analysis_status == "usable" for row in rows),
    }
    write_json_atomic(output_path / "catalog-report.json", report)
    return report


def load_catalog(
    path: str | Path, *, artifact_root: str | Path | None = None
) -> list[dict[str, Any]]:
    rows = pq.read_table(path).to_pylist()
    root = Path(artifact_root).resolve() if artifact_root is not None else None
    seen: set[str] = set()
    for row in rows:
        sha = normalize_sha(row["apk_sha256"])
        if sha in seen:
            raise ContractError(f"duplicate catalog SHA: {sha}")
        seen.add(sha)
        if root is not None:
            for view in ("classical", *VIEW_PATTERNS):
                key = f"{view}_path"
                if row.get(key) and not Path(row[key]).is_absolute():
                    row[key] = str(root / row[key])
    return rows
