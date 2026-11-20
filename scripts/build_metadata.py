#!/usr/bin/env python3
"""Build portable split manifests and raw-source metadata for the report repository."""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

REPO = Path(__file__).resolve().parents[1]
PROCESSED = REPO / "data/processed"


def portable_catalog() -> list[dict]:
    path = PROCESSED / "metadata/catalog.parquet"
    rows = pq.read_table(path).to_pylist()
    old_root = Path("/home/rl/Data/work/ml-fcg-by-split")
    for row in rows:
        for key in ("classical_path", "fcg_path"):
            absolute = Path(row[key])
            try:
                row[key] = absolute.relative_to(old_root).as_posix()
            except ValueError:
                if absolute.is_absolute():
                    raise RuntimeError(f"unexpected absolute catalog path: {absolute}") from None
    temporary = path.with_suffix(".parquet.tmp")
    pq.write_table(pa.Table.from_pylist(rows), temporary)
    temporary.replace(path)
    return rows


def write_split_csv(rows: list[dict]) -> None:
    fields = ["apk_sha256", "label", "label_text", "year", "classical_path", "fcg_path"]
    for split in ("train", "validation", "test"):
        selected = sorted(
            (row for row in rows if row["split"] == split),
            key=lambda row: (int(row["year"]), row["label_text"], row["apk_sha256"]),
        )
        with (PROCESSED / "metadata" / f"{split}.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows({field: row[field] for field in fields} for row in selected)


def write_archive_index() -> None:
    source_db = Path("/home/rl/Data/state/pipeline.sqlite3")
    connection = sqlite3.connect(source_db)
    connection.row_factory = sqlite3.Row
    fields = [
        "remote_path",
        "remote_object_id",
        "expected_sha256",
        "expected_md5",
        "remote_size",
    ]
    rows = connection.execute(
        "SELECT remote_path, remote_object_id, expected_sha256, expected_md5, remote_size "
        "FROM archives ORDER BY remote_path"
    ).fetchall()
    connection.close()
    with (REPO / "data/raw/source-archives.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(dict(row) for row in rows)
    (REPO / "data/raw/source-checksums.sha256").write_text(
        "".join(f"{row['expected_sha256']}  {row['remote_path']}\n" for row in rows)
    )


def main() -> None:
    rows = portable_catalog()
    write_split_csv(rows)
    write_archive_index()
    counts = {split: sum(row["split"] == split for row in rows) for split in ("train", "validation", "test")}
    print(json.dumps({"catalog_rows": len(rows), "splits": counts}, indent=2))


if __name__ == "__main__":
    main()
