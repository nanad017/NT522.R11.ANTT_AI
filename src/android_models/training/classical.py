from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import psutil
import pyarrow as pa
import pyarrow.parquet as pq
from sklearn.metrics import average_precision_score

from ..contracts import ContractError, canonical_hash
from ..evaluation import evaluate_predictions
from ..features.classical import load_matrix
from ..metrics import binary_metrics, choose_threshold
from ..models.classical import create_classical, load_classical, model_scores, save_classical
from ..registry import write_json_atomic
from ..reproducibility import seed_everything


def _labels(rows: list[dict[str, Any]]) -> np.ndarray:
    return np.asarray([row["label"] for row in rows], dtype=np.int64)


def train_classical(
    kind: str,
    matrix_dir: str | Path,
    output: str | Path,
    params: dict[str, Any],
    seed: int,
) -> dict[str, Any]:
    seed_metadata = seed_everything(seed)
    matrix_metadata = json.loads((Path(matrix_dir) / "matrix-metadata.json").read_text())
    x_train, rows_train = load_matrix(matrix_dir, "train")
    x_validation, rows_validation = load_matrix(matrix_dir, "validation")
    if not rows_train or not rows_validation:
        raise ContractError("classical training requires nonempty train and validation splits")
    y_train, y_validation = _labels(rows_train), _labels(rows_validation)
    if len(np.unique(y_train)) < 2 or len(np.unique(y_validation)) < 2:
        raise ContractError("classical training/selection requires both classes in train and validation")
    model = create_classical(kind, params, seed)
    memory_preflight = None
    if kind == "random_forest":
        matrix_bytes = int(x_train.data.nbytes + x_train.indices.nbytes + x_train.indptr.nbytes)
        estimated_tree_bytes = int(params.get("n_estimators", 200)) * len(rows_train) * 64
        estimated = matrix_bytes + estimated_tree_bytes
        budget = int(float(params.get("max_ram_gib", 8.0)) * 1024**3)
        available = int(psutil.virtual_memory().available)
        memory_preflight = {
            "matrix_bytes": matrix_bytes,
            "estimated_tree_bytes": estimated_tree_bytes,
            "estimated_total_bytes": estimated,
            "configured_budget_bytes": budget,
            "available_bytes": available,
            "dense_conversion": False,
        }
        if estimated > min(budget, available):
            raise ContractError("RandomForest RAM preflight exceeds configured/available budget")
    start = time.perf_counter()
    if kind == "xgboost":
        model.fit(x_train, y_train, eval_set=[(x_validation, y_validation)], verbose=False)
    else:
        model.fit(x_train, y_train)
    runtime = time.perf_counter() - start
    validation_scores, _ = model_scores(model, kind, x_validation)
    if not np.isfinite(validation_scores).all():
        raise ContractError("model produced non-finite validation scores")
    if kind == "linear_svm":
        classifier = model.named_steps["classifier"]
        if int(np.max(classifier.n_iter_)) >= classifier.max_iter:
            raise ContractError(
                "LinearSVC did not converge after train-only sparse MaxAbs scaling"
            )
    threshold = choose_threshold(y_validation, validation_scores)
    output_path = Path(output)
    output_path.mkdir(parents=True, exist_ok=True)
    checkpoint = save_classical(
        model,
        kind,
        output_path,
        {
            "seed": seed,
            "params": params,
            "preprocessing": (
                {
                    "type": "MaxAbsScaler",
                    "fit_split": "train",
                    "preserves_sparse": True,
                }
                if kind == "linear_svm"
                else None
            ),
            "train_sha_hash": canonical_hash([row["apk_sha256"] for row in rows_train]),
            "feature_count": int(x_train.shape[1]),
        },
    )
    write_json_atomic(output_path / "threshold.json", threshold)
    vectorizer_metadata = json.loads((Path(matrix_dir) / "vectorizer.json").read_text())
    feature_names = vectorizer_metadata["feature_names"]
    diagnostics: dict[str, Any] = {"importance_is_causal": False}
    if kind == "linear_svm":
        classifier = model.named_steps["classifier"]
        coefficients = np.asarray(classifier.coef_).reshape(-1)
        order = np.argsort(coefficients)
        diagnostics["top_negative_coefficients"] = [
            {"feature": feature_names[int(index)], "coefficient": float(coefficients[index])}
            for index in order[:50]
        ]
        diagnostics["top_positive_coefficients"] = [
            {"feature": feature_names[int(index)], "coefficient": float(coefficients[index])}
            for index in order[-50:][::-1]
        ]
    elif kind == "random_forest":
        order = np.argsort(model.feature_importances_)[-100:][::-1]
        diagnostics["impurity_importance_diagnostic"] = [
            {"feature": feature_names[int(index)], "importance": float(model.feature_importances_[index])}
            for index in order
        ]
    else:
        booster = model.get_booster()
        diagnostics["best_iteration"] = getattr(model, "best_iteration", None)
        diagnostics["booster_config"] = json.loads(booster.save_config())
        for importance_type in ("gain", "cover"):
            values = booster.get_score(importance_type=importance_type)
            diagnostics[f"feature_{importance_type}"] = [
                {
                    "feature": feature_names[int(name[1:])] if name.startswith("f") else name,
                    "value": float(value),
                }
                for name, value in sorted(values.items(), key=lambda item: item[1], reverse=True)
            ]
    write_json_atomic(output_path / "diagnostics.json", diagnostics)
    trials = {
        "status": "complete",
        "kind": kind,
        "seed": seed,
        "params": params,
        "runtime_seconds": runtime,
        "validation_pr_auc": float(average_precision_score(y_validation, validation_scores)),
        "best_iteration": getattr(model, "best_iteration", None),
        "seed_metadata": seed_metadata,
        "memory_preflight": memory_preflight,
    }
    write_json_atomic(output_path / "fit.json", trials)
    prediction_rows: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {}
    reloaded, _ = load_classical(output_path)
    for split in ("train", "validation", "test"):
        matrix, rows = load_matrix(matrix_dir, split)
        scores, probabilities = model_scores(reloaded, kind, matrix)
        original, _ = model_scores(model, kind, matrix)
        if not np.allclose(scores, original, rtol=1e-6, atol=1e-7):
            raise ContractError(f"checkpoint reload prediction mismatch on {split}")
        labels = _labels(rows)
        if rows:
            metrics[split] = binary_metrics(labels, scores, threshold["threshold"])
        for index, row in enumerate(rows):
            prediction_rows.append(
                {
                    "apk_sha256": row["apk_sha256"],
                    "split": split,
                    "year": int(row["year"]),
                    "label": int(row["label"]),
                    "score": float(scores[index]),
                    "probability": float(probabilities[index]) if probabilities is not None else None,
                    "prediction": int(scores[index] >= threshold["threshold"]),
                    "threshold": float(threshold["threshold"]),
                    "model_run_id": output_path.name,
                    "view_hash": matrix_metadata["feature_hash"],
                    "coverage_status": "included",
                }
            )
    pq.write_table(pa.Table.from_pylist(prediction_rows), output_path / "predictions.parquet")
    write_json_atomic(output_path / "metrics-all-splits.json", metrics)
    if any(row["split"] == "test" for row in prediction_rows):
        evaluate_predictions(output_path / "predictions.parquet", output_path, "test")
    else:
        write_json_atomic(output_path / "metrics.json", metrics)
    return {**trials, "checkpoint": str(checkpoint), "metrics": metrics}
