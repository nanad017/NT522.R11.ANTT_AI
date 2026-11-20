from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SPLITS = ("train", "validation", "test")
LABELS = {"benign": 0, "malware": 1}
FIDELITY = {
    "fcg": {"common_baseline", "canonical"},
    "ccg": {"canonical_class_collapse"},
    "gred": {"structural_approximation", "paper_faithful"},
    "sigdroid": {"structural_approximation", "paper_faithful"},
    "anakin": {"reimplementation", "paper_faithful"},
}


class ContractError(ValueError):
    """A data, split, fidelity, or artifact contract was violated."""


def normalize_sha(value: str) -> str:
    sha = value.strip().lower()
    if not SHA256_RE.fullmatch(sha):
        raise ContractError(f"invalid SHA-256: {value!r}")
    return sha


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path).resolve()
    raw = yaml.safe_load(config_path.read_text()) or {}
    if not isinstance(raw, dict):
        raise ContractError("model config root must be a mapping")
    raw["_config_path"] = str(config_path)
    raw["_config_hash"] = canonical_hash({k: v for k, v in raw.items() if not k.startswith("_")})
    return raw


def require_train_only(rows: list[dict[str, Any]], operation: str = "fit") -> None:
    invalid = sorted({row.get("split") for row in rows if row.get("split") != "train"})
    if invalid:
        raise ContractError(f"{operation} accepts train rows only; found splits={invalid}")


def enforce_fidelity(view: str, actual: str, requested: str) -> None:
    if requested == "paper_faithful" and actual != "paper_faithful":
        raise ContractError(
            f"{view} paper_faithful is blocked: input fidelity={actual}; no override is allowed"
        )
    allowed = FIDELITY.get(view)
    if allowed and actual not in allowed:
        raise ContractError(f"unsupported {view} fidelity: {actual}")


@dataclass(frozen=True)
class CatalogRow:
    apk_sha256: str
    label: int
    label_text: str
    split: str
    year: int
    analysis_status: str
    classical_path: str | None = None
    fcg_path: str | None = None
    ccg_path: str | None = None
    gred_path: str | None = None
    sigdroid_path: str | None = None
    anakin_path: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

