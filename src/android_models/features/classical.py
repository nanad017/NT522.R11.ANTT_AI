from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from scipy import sparse
from sklearn.feature_extraction import DictVectorizer

from ..contracts import ContractError, canonical_hash, require_train_only
from ..leakage import validate_feature_names
from ..registry import write_json_atomic


def iter_named_records(catalog_rows: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    cached_path: Path | None = None
    cached_records: dict[str, dict[str, Any]] = {}
    for row in sorted(catalog_rows, key=lambda item: item["apk_sha256"]):
        raw_path = row.get("classical_path")
        if not raw_path:
            continue
        path = Path(raw_path)
        if path != cached_path:
            payload = json.loads(path.read_text())
            cached_records = {item["apk_sha256"].lower(): item for item in payload}
            cached_path = path
        sha = row["apk_sha256"]
        if sha not in cached_records:
            raise ContractError(f"catalog points to classical file without SHA {sha}: {path}")
        item = cached_records[sha]
        if int(item["label"]) != int(row["label"]):
            raise ContractError(f"classical label mismatch for {sha}")
        features = item.get("features")
        if not isinstance(features, dict):
            raise ContractError(f"classical features must be a mapping for {sha}")
        yield {**row, "features": features}


def load_named_records(catalog_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Materialize named records for tests and small pilots.

    Full-cohort commands use ``iter_named_records`` to avoid retaining several
    gigabytes of JSON as Python dictionaries.
    """
    return sorted(iter_named_records(catalog_rows), key=lambda row: row["apk_sha256"])


def fit_vectorizer(
    train_records: Iterable[dict[str, Any]], output: str | Path
) -> dict[str, Any]:
    vectorizer = DictVectorizer(sparse=True, dtype=np.float32, sort=True)
    train_shas: list[str] = []

    def feature_stream() -> Iterator[dict[str, float]]:
        for row in train_records:
            require_train_only([row], "classical vectorizer fit")
            train_shas.append(row["apk_sha256"])
            yield row["features"]

    vectorizer.fit(feature_stream())
    if not train_shas:
        raise ContractError("cannot fit vectorizer on empty train records")
    names = list(vectorizer.get_feature_names_out())
    validate_feature_names(names)
    blocks: dict[str, list[int]] = defaultdict(list)
    for index, name in enumerate(names):
        block = name.split("::", 1)[0].split(".", 1)[0]
        blocks[block].append(index)
    output_path = Path(output)
    output_path.mkdir(parents=True, exist_ok=True)
    metadata = {
        "format": "dict-vectorizer-json-v1",
        "feature_names": names,
        "vocabulary": {name: int(index) for name, index in vectorizer.vocabulary_.items()},
        "feature_hash": canonical_hash(names),
        "block_offsets": {
            block: {"start": indices[0], "end_exclusive": indices[-1] + 1, "count": len(indices)}
            for block, indices in sorted(blocks.items())
        },
        "fit_train_sha_hash": canonical_hash(train_shas),
        "fit_rows": len(train_shas),
        "dtype": "float32",
        "sparse": "csr",
    }
    write_json_atomic(output_path / "vectorizer.json", metadata)
    return metadata


def load_vectorizer(path: str | Path) -> tuple[DictVectorizer, dict[str, Any]]:
    metadata = json.loads(Path(path).read_text())
    names = metadata["feature_names"]
    if canonical_hash(names) != metadata["feature_hash"]:
        raise ContractError("vectorizer feature hash mismatch")
    vectorizer = DictVectorizer(sparse=True, dtype=np.float32, sort=True)
    vectorizer.vocabulary_ = {str(k): int(v) for k, v in metadata["vocabulary"].items()}
    vectorizer.feature_names_ = names
    return vectorizer, metadata


def transform_records(
    records: Iterable[dict[str, Any]],
    vectorizer_path: str | Path,
    output: str | Path,
    chunk_size: int = 64,
) -> dict[str, Any]:
    vectorizer, metadata = load_vectorizer(vectorizer_path)
    output_path = Path(output)
    output_path.mkdir(parents=True, exist_ok=True)
    known = set(metadata["vocabulary"])
    if chunk_size < 1:
        raise ContractError("classical transform chunk_size must be positive")
    split_order = ("train", "validation", "test")
    chunks: dict[str, list[sparse.csr_matrix]] = {split: [] for split in split_order}
    row_metadata: dict[str, list[dict[str, Any]]] = {split: [] for split in split_order}
    unknown_counts = dict.fromkeys(split_order, 0)
    feature_counts = dict.fromkeys(split_order, 0)
    buffers: dict[str, list[dict[str, Any]]] = {split: [] for split in split_order}

    def flush(split: str) -> None:
        buffered = buffers[split]
        if not buffered:
            return
        matrix = vectorizer.transform([row["features"] for row in buffered]).tocsr()
        _validate_csr(matrix)
        chunks[split].append(matrix)
        for row in buffered:
            feature_keys = set(row["features"])
            unknown_counts[split] += len(feature_keys - known)
            feature_counts[split] += len(feature_keys)
            row_metadata[split].append(
                {
                    "apk_sha256": row["apk_sha256"],
                    "split": split,
                    "year": int(row["year"]),
                    "label": int(row["label"]),
                }
            )
        buffered.clear()

    previous_sha: dict[str, str] = {}
    for row in records:
        split = row["split"]
        if split not in buffers:
            raise ContractError(f"unsupported split in classical records: {split}")
        sha = row["apk_sha256"]
        if sha <= previous_sha.get(split, ""):
            raise ContractError(f"classical records are not strictly SHA-sorted for {split}")
        previous_sha[split] = sha
        buffers[split].append(row)
        if len(buffers[split]) >= chunk_size:
            flush(split)
    for split in split_order:
        flush(split)

    result: dict[str, Any] = {"feature_hash": metadata["feature_hash"], "splits": {}}
    for split in split_order:
        split_rows = row_metadata[split]
        matrix = (
            sparse.vstack(chunks[split], format="csr", dtype=np.float32)
            if chunks[split]
            else sparse.csr_matrix((0, len(metadata["feature_names"])), dtype=np.float32)
        )
        _validate_csr(matrix)
        sparse.save_npz(output_path / f"x-{split}.npz", matrix, compressed=True)
        row_table = pa.Table.from_pylist(
            [
                {
                    "row_index": index,
                    **row,
                }
                for index, row in enumerate(split_rows)
            ],
            schema=pa.schema(
                [
                    ("row_index", pa.int64()),
                    ("apk_sha256", pa.string()),
                    ("split", pa.string()),
                    ("year", pa.int64()),
                    ("label", pa.int64()),
                ]
            ),
        )
        pq.write_table(row_table, output_path / f"rows-{split}.parquet", compression="zstd")
        result["splits"][split] = {
            "rows": len(split_rows),
            "shape": list(matrix.shape),
            "nnz": int(matrix.nnz),
            "unknown_feature_count": unknown_counts[split],
            "unknown_feature_rate": (
                float(unknown_counts[split] / feature_counts[split])
                if feature_counts[split]
                else 0.0
            ),
            "row_sha_hash": canonical_hash([row["apk_sha256"] for row in split_rows]),
        }
    write_json_atomic(output_path / "matrix-metadata.json", result)
    return result


def _validate_csr(matrix: sparse.csr_matrix) -> None:
    if not sparse.isspmatrix_csr(matrix):
        raise ContractError("feature matrix must remain CSR")
    if matrix.dtype != np.float32:
        matrix.data = matrix.data.astype(np.float32)
    if not np.isfinite(matrix.data).all():
        raise ContractError("classical matrix contains NaN/Inf")
    if matrix.nnz > np.iinfo(np.int32).max or matrix.shape[1] > np.iinfo(np.int32).max:
        raise ContractError("CSR matrix exceeds safe int32 index range")
    matrix.indices = matrix.indices.astype(np.int32, copy=False)
    matrix.indptr = matrix.indptr.astype(np.int32, copy=False)


def load_matrix(directory: str | Path, split: str) -> tuple[sparse.csr_matrix, list[dict[str, Any]]]:
    directory = Path(directory)
    matrix = sparse.load_npz(directory / f"x-{split}.npz").tocsr().astype(np.float32)
    rows = pq.read_table(directory / f"rows-{split}.parquet").to_pylist()
    if matrix.shape[0] != len(rows):
        raise ContractError(f"matrix/row mismatch for {split}")
    if [row["row_index"] for row in rows] != list(range(len(rows))):
        raise ContractError(f"row order is not canonical for {split}")
    _validate_csr(matrix)
    return matrix, rows
