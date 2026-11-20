from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from android_models.contracts import ContractError
from android_models.metrics import binary_metrics, choose_threshold
from android_models.registry import RunRegistry


def test_known_metrics_and_validation_threshold() -> None:
    labels = np.asarray([0, 0, 1, 1])
    scores = np.asarray([0.1, 0.4, 0.6, 0.9])
    threshold = choose_threshold(labels, scores)
    metrics = binary_metrics(labels, scores, threshold["threshold"])
    assert metrics["pr_auc"] == 1.0
    assert metrics["f1"] == 1.0


def test_registry_resume_config_guard(tmp_path: Path) -> None:
    first = RunRegistry(tmp_path, "test", {"value": 1}, tmp_path)
    first.complete()
    resumed = RunRegistry(tmp_path, "test", {"value": 1}, tmp_path, resume=True)
    assert resumed.run_id == first.run_id
    run_path = first.path / "run.json"
    payload = json.loads(run_path.read_text())
    payload["config_hash"] = "tampered"
    run_path.write_text(json.dumps(payload))
    with pytest.raises(ContractError):
        RunRegistry(tmp_path, "test", {"value": 1}, tmp_path, resume=True)
