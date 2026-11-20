from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import MaxAbsScaler
from sklearn.svm import LinearSVC
from xgboost import XGBClassifier

from ..contracts import ContractError
from ..registry import write_json_atomic


def create_classical(kind: str, params: dict[str, Any], seed: int):
    if kind == "linear_svm":
        scaling = params.get("scaling", "max_abs")
        if scaling != "max_abs":
            raise ContractError(f"unsupported LinearSVC scaling: {scaling}")
        return Pipeline(
            [
                ("scale", MaxAbsScaler(copy=True)),
                (
                    "classifier",
                    LinearSVC(
                        C=float(params.get("C", 0.1)),
                        class_weight=params.get("class_weight"),
                        dual=params.get("dual", "auto"),
                        tol=float(params.get("tol", 1e-4)),
                        max_iter=int(params.get("max_iter", 10_000)),
                        random_state=seed,
                    ),
                ),
            ]
        )
    if kind == "random_forest":
        return RandomForestClassifier(
            n_estimators=int(params.get("n_estimators", 200)),
            max_depth=params.get("max_depth"),
            min_samples_leaf=int(params.get("min_samples_leaf", 1)),
            max_features=params.get("max_features", "sqrt"),
            n_jobs=int(params.get("n_jobs", 1)),
            random_state=seed,
        )
    if kind == "xgboost":
        early_stopping = params.get("early_stopping_rounds", 20)
        return XGBClassifier(
            objective="binary:logistic",
            eval_metric="aucpr",
            tree_method=params.get("tree_method", "hist"),
            device=params.get("device", "cpu"),
            n_estimators=int(params.get("n_estimators", 300)),
            max_depth=int(params.get("max_depth", 6)),
            learning_rate=float(params.get("learning_rate", 0.1)),
            min_child_weight=float(params.get("min_child_weight", 1.0)),
            subsample=float(params.get("subsample", 1.0)),
            colsample_bytree=float(params.get("colsample_bytree", 1.0)),
            reg_alpha=float(params.get("reg_alpha", 0.0)),
            reg_lambda=float(params.get("reg_lambda", 1.0)),
            early_stopping_rounds=(int(early_stopping) if early_stopping is not None else None),
            n_jobs=int(params.get("n_jobs", 1)),
            random_state=seed,
        )
    raise ContractError(f"unsupported classical model: {kind}")


def model_scores(model: Any, kind: str, matrix) -> tuple[np.ndarray, np.ndarray | None]:
    if kind == "linear_svm":
        return np.asarray(model.decision_function(matrix), dtype=np.float64), None
    probabilities = np.asarray(model.predict_proba(matrix)[:, 1], dtype=np.float64)
    return probabilities, probabilities


def save_classical(model: Any, kind: str, output: str | Path, metadata: dict[str, Any]) -> Path:
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if kind == "xgboost":
        checkpoint = output / "checkpoint.ubj"
        model.save_model(checkpoint)
        checkpoint_format = "xgboost-ubjson"
    else:
        try:
            import skops.io as sio
        except ImportError as exc:
            raise ContractError("safe sklearn checkpointing requires the pinned skops dependency") from exc
        checkpoint = output / "checkpoint.skops"
        sio.dump(model, checkpoint)
        checkpoint_format = "skops"
    write_json_atomic(
        output / "checkpoint.json",
        {"model_kind": kind, "format": checkpoint_format, "file": checkpoint.name, **metadata},
    )
    return checkpoint


def load_classical(directory: str | Path):
    directory = Path(directory)
    metadata = json.loads((directory / "checkpoint.json").read_text())
    path = directory / metadata["file"]
    if metadata["format"] == "xgboost-ubjson":
        model = XGBClassifier()
        model.load_model(path)
        return model, metadata
    if metadata["format"] != "skops":
        raise ContractError(f"unsafe/unsupported checkpoint format: {metadata['format']}")
    import skops.io as sio

    unknown = sio.get_untrusted_types(file=path)
    allowed_prefixes = ("sklearn.", "numpy.")
    refused = [name for name in unknown if not name.startswith(allowed_prefixes)]
    if refused:
        raise ContractError(f"checkpoint contains untrusted types: {refused}")
    return sio.load(path, trusted=unknown), metadata
