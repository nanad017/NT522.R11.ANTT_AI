from __future__ import annotations

import csv
from pathlib import Path

import pytest

from android_models.catalog import read_manifest
from android_models.contracts import (
    ContractError,
    enforce_fidelity,
    normalize_sha,
    require_train_only,
)
from android_models.leakage import validate_feature_names


def test_sha_split_and_fidelity_guards() -> None:
    assert normalize_sha("A" * 64) == "a" * 64
    with pytest.raises(ContractError):
        normalize_sha("bad")
    require_train_only([{"split": "train"}])
    with pytest.raises(ContractError):
        require_train_only([{"split": "validation"}])
    with pytest.raises(ContractError):
        enforce_fidelity("gred", "structural_approximation", "paper_faithful")
    with pytest.raises(ContractError):
        validate_feature_names(["year::2023"])


def test_manifest_duplicate_conflict_fails(tmp_path: Path) -> None:
    path = tmp_path / "manifest.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sha256", "label", "split", "year"])
        writer.writeheader()
        writer.writerow({"sha256": "1" * 64, "label": "benign", "split": "train", "year": 2019})
        writer.writerow({"sha256": "1" * 64, "label": "malware", "split": "test", "year": 2023})
    with pytest.raises(ContractError):
        read_manifest(path)

