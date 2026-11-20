#!/usr/bin/env python3
"""Validate split manifests, artifact paths, SHA uniqueness, and checksums."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 << 20), b""):
            value.update(chunk)
    return value.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("data/processed"))
    parser.add_argument("--full-checksum", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    rows = pq.read_table(root / "metadata/catalog.parquet").to_pylist()
    shas = [row["apk_sha256"] for row in rows]
    errors: list[str] = []
    if len(rows) != 9_855 or len(shas) != len(set(shas)):
        errors.append("catalog row count or SHA uniqueness failed")
    for row in rows:
        for key in ("classical_path", "fcg_path"):
            path = root / row[key]
            if not path.is_file():
                errors.append(f"missing {key}: {path}")
    verified = 0
    if args.full_checksum:
        for line in (root / "metadata/checksums.sha256").read_text().splitlines():
            expected, relative = line.split("  ", 1)
            if digest(root / relative) != expected:
                errors.append(f"checksum mismatch: {relative}")
            verified += 1
    report = {
        "status": "ok" if not errors else "failed",
        "samples": len(rows),
        "split_counts": dict(Counter(row["split"] for row in rows)),
        "checksums_verified": verified,
        "errors": errors[:100],
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if not errors else 2)


if __name__ == "__main__":
    main()
