from __future__ import annotations

import json
from collections import Counter, defaultdict
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq
import torch
from gensim.models import Word2Vec
from sklearn.feature_extraction.text import TfidfVectorizer
from torch_geometric.data import Data

from ..contracts import ContractError, canonical_hash, require_train_only
from ..registry import write_json_atomic

CRITICAL_PREFIXES = (
    "Landroid/accounts",
    "Landroid/app",
    "Landroid/bluetooth",
    "Landroid/content",
    "Landroid/location",
    "Landroid/media",
    "Landroid/net",
    "Landroid/nfc",
    "Landroid/provider",
    "Landroid/telecom",
    "Landroid/telephony",
)


def is_critical_api(token: str) -> bool:
    return any(token.startswith(prefix) for prefix in CRITICAL_PREFIXES)


def _sequence_parquet(anakin_path: str | Path) -> Path | None:
    path = Path(anakin_path)
    for parent in path.parents:
        candidate = parent / "api_sequences.parquet"
        if candidate.exists():
            return candidate
    return None


def load_sequences(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[Path, set[str]] = defaultdict(set)
    fallback: list[dict[str, Any]] = []
    metadata = {row["apk_sha256"]: row for row in rows}
    for row in rows:
        if not row.get("anakin_path"):
            continue
        parquet = _sequence_parquet(row["anakin_path"])
        if parquet:
            grouped[parquet].add(row["apk_sha256"])
            continue
        artifact = json.loads(Path(row["anakin_path"]).read_text())
        for graph in artifact.get("graphs", []):
            if "tokens" in graph:
                fallback.append(
                    {
                        **row,
                        "sequence_id": graph["sequence_id"],
                        "entry_id": graph.get("entry_id"),
                        "component_type": graph.get("component_type"),
                        "tokens": graph["tokens"],
                        "token_provenance_json": json.dumps(graph.get("token_provenance", [])),
                        "truncated": graph.get("truncated", False),
                    }
                )
    result = fallback
    for path, wanted in grouped.items():
        for sequence in pq.read_table(path).to_pylist():
            sha = sequence["apk_sha256"]
            if sha in wanted:
                result.append({**metadata[sha], **sequence})
    return sorted(result, key=lambda row: (row["apk_sha256"], row["sequence_id"]))


def fit_anakin_features(
    train_rows: list[dict[str, Any]],
    output: str | Path,
    top_k: int = 140,
    embedding_dim: int = 100,
    window: int = 5,
    seed: int = 13,
    epochs: int = 5,
) -> dict[str, Any]:
    require_train_only(train_rows, "ANAKIN TF-IDF/Word2Vec fit")
    sequences = load_sequences(train_rows)
    documents = [[token for token in row["tokens"] if is_critical_api(token)] for row in sequences]
    documents = [tokens for tokens in documents if tokens]
    if not documents:
        raise ContractError("ANAKIN fit has no critical API sequences")
    vectorizer = TfidfVectorizer(
        analyzer=lambda value: value,
        lowercase=False,
        token_pattern=None,
        dtype=np.float32,
        norm="l2",
    )
    matrix = vectorizer.fit_transform(documents)
    means = np.asarray(matrix.mean(axis=0)).reshape(-1)
    names = np.asarray(vectorizer.get_feature_names_out())
    order = np.lexsort((names, -means))[: min(top_k, len(names))]
    selected = names[order].tolist()
    selected_set = set(selected)
    pruned = [[token for token in sequence if token in selected_set] for sequence in documents]
    pruned = [sequence for sequence in pruned if sequence]
    model = Word2Vec(
        sentences=pruned,
        vector_size=embedding_dim,
        window=window,
        min_count=1,
        workers=1,
        sg=0,
        seed=seed,
        epochs=epochs,
        sorted_vocab=1,
    )
    vectors = np.stack(
        [model.wv[token] if token in model.wv else np.zeros(embedding_dim) for token in selected]
    ).astype(np.float32)
    output_path = Path(output)
    output_path.mkdir(parents=True, exist_ok=True)
    selected_metadata = {
        "format": "anakin-selected-api-v1",
        "critical_prefixes": list(CRITICAL_PREFIXES),
        "selected_apis": selected,
        "average_tfidf": [float(means[index]) for index in order],
        "top_k": top_k,
        "fit_scope": "train_only",
        "fit_train_sha_hash": canonical_hash(sorted(row["apk_sha256"] for row in train_rows)),
        "corpus_hash": canonical_hash(pruned),
        "tfidf": {"norm": "l2", "lowercase": False, "unit": "sequence_document"},
    }
    write_json_atomic(output_path / "selected-apis.json", selected_metadata)
    np.savez_compressed(output_path / "embeddings.npz", tokens=np.asarray(selected), vectors=vectors)
    fit = {
        **selected_metadata,
        "embedding": {
            "algorithm": "Word2Vec-CBOW",
            "implementation": f"gensim-{model.__module__.split('.')[0]}",
            "dimension": embedding_dim,
            "window": window,
            "epochs": epochs,
            "seed": seed,
            "oov_policy": "zero_vector_and_report",
        },
    }
    write_json_atomic(output_path / "fit.json", fit)
    return fit


def load_anakin_features(directory: str | Path) -> tuple[list[str], dict[str, np.ndarray], dict[str, Any]]:
    directory = Path(directory)
    metadata = json.loads((directory / "fit.json").read_text())
    with np.load(directory / "embeddings.npz", allow_pickle=False) as raw:
        tokens = [str(value) for value in raw["tokens"].tolist()]
        vectors = np.asarray(raw["vectors"], dtype=np.float32)
    if tokens != metadata["selected_apis"]:
        raise ContractError("ANAKIN selected API/vector order mismatch")
    return tokens, dict(zip(tokens, vectors, strict=True)), metadata


def transform_anakin_apk(row: dict[str, Any], feature_dir: str | Path) -> list[Data]:
    selected, embeddings, metadata = load_anakin_features(feature_dir)
    selected_set = set(selected)
    sequences = load_sequences([row])
    graphs: list[Data] = []
    for sequence in sequences:
        tokens = [token for token in sequence["tokens"] if token in selected_set]
        if not tokens:
            continue
        node_names = list(dict.fromkeys(tokens))
        node_index = {token: index for index, token in enumerate(node_names)}
        transitions = Counter(pairwise(tokens))
        edge_pairs = list(transitions)
        edge_index = torch.tensor(
            [[node_index[src] for src, _ in edge_pairs], [node_index[dst] for _, dst in edge_pairs]],
            dtype=torch.long,
        ).reshape(2, -1)
        edge_weight = torch.tensor(
            [float(transitions[pair]) for pair in edge_pairs], dtype=torch.float32
        )
        x = torch.from_numpy(
            np.stack([embeddings.get(token, np.zeros(metadata["embedding"]["dimension"])) for token in node_names]).astype(np.float32)
        )
        provenance = json.loads(sequence.get("token_provenance_json") or "[]")
        graph = Data(
            x=x,
            edge_index=edge_index,
            edge_weight=edge_weight,
            y=torch.tensor([float(row["label"])], dtype=torch.float32),
        )
        graph.apk_sha256 = row["apk_sha256"]
        graph.sequence_id = sequence["sequence_id"]
        graph.node_names = node_names
        graph.transition_pairs = edge_pairs
        graph.token_provenance = provenance
        graph.truncated = bool(sequence.get("truncated", False))
        graphs.append(graph)
    return graphs


def apk_oov_report(rows: list[dict[str, Any]], feature_dir: str | Path) -> dict[str, Any]:
    selected, _, _ = load_anakin_features(feature_dir)
    known = set(selected)
    sequences = load_sequences(rows)
    total = critical = retained = 0
    for sequence in sequences:
        total += len(sequence["tokens"])
        critical_tokens = [token for token in sequence["tokens"] if is_critical_api(token)]
        critical += len(critical_tokens)
        retained += sum(token in known for token in critical_tokens)
    return {
        "tokens": total,
        "critical_tokens": critical,
        "retained_tokens": retained,
        "oov_rate": 1.0 - retained / critical if critical else 0.0,
    }
