from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.metrics import (
    average_precision_score,
    balanced_accuracy_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    matthews_corrcoef,
    precision_score,
    recall_score,
    roc_auc_score,
)

from .contracts import ContractError


def choose_threshold(y_true: np.ndarray, scores: np.ndarray, rule: str = "max_f1") -> dict[str, float]:
    if len(np.unique(y_true)) < 2:
        raise ContractError("threshold selection requires both classes on validation")
    if rule != "max_f1":
        raise ContractError(f"unsupported threshold rule: {rule}")
    candidates = np.unique(np.concatenate(([0.0], scores, [1.0])))
    values = [(f1_score(y_true, scores >= threshold, zero_division=0), threshold) for threshold in candidates]
    best_f1, threshold = max(values, key=lambda item: (item[0], -item[1]))
    return {"rule": rule, "threshold": float(threshold), "validation_f1": float(best_f1)}


def binary_metrics(y_true: np.ndarray, scores: np.ndarray, threshold: float) -> dict[str, Any]:
    y_true = np.asarray(y_true, dtype=np.int64)
    scores = np.asarray(scores, dtype=np.float64)
    if not np.isfinite(scores).all():
        raise ContractError("non-finite prediction score")
    predicted = (scores >= threshold).astype(np.int64)
    tn, fp, fn, tp = confusion_matrix(y_true, predicted, labels=[0, 1]).ravel()
    both = len(np.unique(y_true)) == 2
    result: dict[str, Any] = {
        "n": len(y_true),
        "threshold": float(threshold),
        "pr_auc": float(average_precision_score(y_true, scores)) if both else None,
        "roc_auc": float(roc_auc_score(y_true, scores)) if both else None,
        "f1": float(f1_score(y_true, predicted, zero_division=0)),
        "precision": float(precision_score(y_true, predicted, zero_division=0)),
        "recall_tpr": float(recall_score(y_true, predicted, zero_division=0)),
        "specificity_tnr": float(tn / (tn + fp)) if tn + fp else None,
        "fpr": float(fp / (fp + tn)) if fp + tn else None,
        "fnr": float(fn / (fn + tp)) if fn + tp else None,
        "balanced_accuracy": float(balanced_accuracy_score(y_true, predicted)),
        "mcc": float(matthews_corrcoef(y_true, predicted)),
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
    }
    if np.all((scores >= 0) & (scores <= 1)):
        result["brier"] = float(brier_score_loss(y_true, scores))
    return result
