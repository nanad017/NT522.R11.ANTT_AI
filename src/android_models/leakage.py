from __future__ import annotations

from collections import defaultdict
from typing import Any

from .contracts import ContractError

FORBIDDEN_FEATURE_PREFIXES = (
    "sha",
    "path",
    "package",
    "pkg",
    "signer",
    "certificate",
    "split",
    "year",
    "market",
    "vt_detection",
    "label",
    "archive",
    "filename",
)


def audit_catalog(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_sha: dict[str, set[tuple[str, int]]] = defaultdict(set)
    for row in rows:
        by_sha[row["apk_sha256"]].add((row["split"], int(row["label"])))
    conflicts = {sha: sorted(values) for sha, values in by_sha.items() if len(values) != 1}
    if conflicts:
        raise ContractError(f"split/label conflicts: {len(conflicts)}")
    return {"rows": len(rows), "unique_sha": len(by_sha), "conflicts": conflicts}


def validate_feature_names(names: list[str]) -> None:
    bad = [
        name
        for name in names
        if name.lower().replace("::", ".").split(".", 1)[0] in FORBIDDEN_FEATURE_PREFIXES
    ]
    if bad:
        raise ContractError(f"identity/leakage features are forbidden: {bad[:10]}")

