from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Self

import pyarrow as pa
import pyarrow.parquet as pq

from .schema import SCHEMAS


def write_tables(
    root: str | Path,
    tables: dict[str, list[dict]],
    table_names: list[str] | None = None,
) -> dict[str, dict]:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    inventory: dict[str, dict] = {}
    selected = list(SCHEMAS) if table_names is None else table_names
    unknown = set(selected) - set(SCHEMAS)
    if unknown:
        raise ValueError(f"unknown retained IR tables: {sorted(unknown)}")
    for name in selected:
        schema = SCHEMAS[name]
        rows = tables.get(name, [])
        table = pa.Table.from_pylist(rows, schema=schema)
        path = root / f"{name}.parquet"
        pq.write_table(table, path, compression="zstd", use_dictionary=True)
        digest = file_sha256(path)
        inventory[name] = {"rows": len(rows), "bytes": path.stat().st_size, "sha256": digest}
    return inventory


class BatchWriter:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.writers: dict[str, pq.ParquetWriter] = {}
        self.rows = {name: 0 for name in SCHEMAS}

    def append(self, tables: dict[str, list[dict]]) -> None:
        for name, rows in tables.items():
            if name not in SCHEMAS or not rows:
                continue
            table = pa.Table.from_pylist(rows, schema=SCHEMAS[name])
            if name not in self.writers:
                self.writers[name] = pq.ParquetWriter(
                    self.root / f"{name}.parquet",
                    SCHEMAS[name],
                    compression="zstd",
                    use_dictionary=True,
                )
            self.writers[name].write_table(table)
            self.rows[name] += len(rows)

    def close(self) -> dict[str, dict]:
        for writer in self.writers.values():
            writer.close()
        inventory = {}
        for name, schema in SCHEMAS.items():
            path = self.root / f"{name}.parquet"
            if not path.exists():
                pq.write_table(pa.Table.from_pylist([], schema=schema), path, compression="zstd")
            inventory[name] = {
                "rows": self.rows[name],
                "bytes": path.stat().st_size,
                "sha256": file_sha256(path),
            }
        return inventory

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        for writer in self.writers.values():
            writer.close()


def write_json_atomic(path: str | Path, value: dict | list) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    tmp.replace(path)


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_schema_contract(root: str | Path, schema_version: str) -> Path:
    root = Path(root)
    contract = {
        "schema_version": schema_version,
        "format": "Apache Parquet/Arrow",
        "null_semantics": "null means unavailable; status/diagnostic fields carry reason",
        "tables": {
            name: {
                "fields": [
                    {"name": field.name, "type": str(field.type), "nullable": field.nullable}
                    for field in schema
                ]
            }
            for name, schema in SCHEMAS.items()
        },
    }
    path = root / "schema.json"
    write_json_atomic(path, contract)
    return path


def write_checksums(root: str | Path) -> Path:
    """Write a deterministic SHA-256 manifest without hashing itself or temp files."""
    root = Path(root)
    target = root / "checksums.sha256"
    lines = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path == target or path.name.endswith(".tmp"):
            continue
        lines.append(f"{file_sha256(path)}  {path.relative_to(root).as_posix()}")
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.replace(target)
    return target


def finalize_artifacts(root: str | Path, schema_version: str) -> None:
    write_schema_contract(root, schema_version)
    write_checksums(root)
