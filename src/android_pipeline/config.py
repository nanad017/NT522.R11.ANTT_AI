from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Paths(BaseModel):
    work_root: Path
    state_root: Path
    knowledge_root: Path


class Drive(BaseModel):
    remote: str = "nad"
    root_folder_id: str
    source_prefix: str
    output_prefix: str


class UploadTarget(BaseModel):
    remote: str
    root_folder_id: str | None = None
    output_prefix: str = "processed/android-static-v1"


class RuntimeTuning(BaseModel):
    """Operational knobs excluded from extraction artifact identity."""

    batch_max_apks: int | None = None
    batch_max_raw_gib: float | None = None
    worker_memory_gib: float = 3.0
    max_parallel_uploads: int = 1
    upload_targets: list[UploadTarget] = Field(default_factory=list)
    compatible_verified_config_hashes: list[str] = Field(default_factory=list)
    cleanup_source_archive_after_run: bool = False
    skip_verified_samples: bool = True
    retain_verified_batch_roots: bool = False

    @model_validator(mode="after")
    def safe_limits(self) -> RuntimeTuning:
        if self.batch_max_apks is not None and self.batch_max_apks < 1:
            raise ValueError("runtime batch_max_apks must be positive")
        if self.batch_max_raw_gib is not None and self.batch_max_raw_gib <= 0:
            raise ValueError("runtime batch_max_raw_gib must be positive")
        if self.worker_memory_gib <= 0 or self.max_parallel_uploads < 1:
            raise ValueError("runtime worker/upload limits must be positive")
        if any(len(value) != 64 for value in self.compatible_verified_config_hashes):
            raise ValueError("compatible config hashes must be full SHA-256 strings")
        return self


class Dataset(BaseModel):
    manifest_file: str = "final-manifest.csv"
    expected_samples: int = 10_000
    label_encoding: dict[str, int] = {"benign": 0, "malware": 1}


class Execution(BaseModel):
    mode: Literal["smoke", "pilot", "full"] = "smoke"
    workers: int | Literal["auto"] = 1
    max_inflight_archives: int = 1
    batch_max_apks: int = 6
    batch_max_raw_gib: float = 0.5
    apk_timeout_seconds: int = 1800
    disk_reserve_gib: float = 12
    remote_free_min_gib: float = 2048
    upload_transfers: int = 2
    retries: int = 5
    cleanup_verified_cache: bool = True
    resume: bool = True

    @model_validator(mode="after")
    def safe_limits(self) -> Execution:
        if self.max_inflight_archives != 1:
            raise ValueError(".tar.zst input requires max_inflight_archives=1")
        if self.batch_max_apks < 1 or self.batch_max_raw_gib <= 0:
            raise ValueError("batch limits must be positive")
        return self


class Extraction(BaseModel):
    all_dex: bool = True
    instructions: bool = True
    raw_invokes: bool = True
    cfg: bool = True
    entrypoints: bool = True
    strings: bool = True
    signing_verify: bool = False
    jadx_source: bool = False


class Graphs(BaseModel):
    directed: bool = True
    collapse_parallel_calls: bool = True
    add_reverse_edges: bool = False
    synthetic_self_loops_in_canonical_ir: bool = False
    views: list[str] = Field(default_factory=lambda: ["fcg"])


class Artifacts(BaseModel):
    """Persistence policy applied after model inputs are derived in memory."""

    profile: Literal["full", "ml_fcg_compact"] = "full"
    retain_tables: list[str] | None = None
    fcg_mapping: Literal["full", "model_only"] = "full"

    @model_validator(mode="after")
    def compact_contract(self) -> Artifacts:
        if self.retain_tables is not None and "samples" not in self.retain_tables:
            raise ValueError("retained table set must include samples")
        if self.profile == "ml_fcg_compact" and self.fcg_mapping != "model_only":
            raise ValueError("ml_fcg_compact requires model_only FCG mapping")
        return self


class PipelineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pipeline_version: str
    ir_schema_version: str = "ir-v1"
    paths: Paths
    drive: Drive
    dataset: Dataset
    execution: Execution
    extraction: Extraction
    graphs: Graphs
    artifacts: Artifacts = Field(default_factory=Artifacts)
    paper_adapters: dict[str, dict] = Field(default_factory=dict)
    runtime_tuning: RuntimeTuning = Field(default_factory=RuntimeTuning, exclude=True)
    source_file: Path | None = Field(default=None, exclude=True)

    def digest(self) -> str:
        data = self.model_dump(mode="json", exclude={"source_file"})
        # Preserve the established android-static-v1 identity for legacy full
        # configs created before the artifact policy field existed. Compact
        # profiles retain this field and therefore receive a distinct hash.
        if self.artifacts == Artifacts():
            data.pop("artifacts", None)
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def effective_batch_max_apks(self) -> int:
        return self.runtime_tuning.batch_max_apks or self.execution.batch_max_apks

    def effective_batch_max_raw_gib(self) -> float:
        return self.runtime_tuning.batch_max_raw_gib or self.execution.batch_max_raw_gib

    def upload_targets(self) -> list[UploadTarget]:
        return self.runtime_tuning.upload_targets or [
            UploadTarget(
                remote=self.drive.remote,
                root_folder_id=self.drive.root_folder_id,
                output_prefix=self.drive.output_prefix,
            )
        ]


def _merge(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for key, value in overlay.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: str | Path) -> PipelineConfig:
    path = Path(path).resolve()
    raw = yaml.safe_load(path.read_text()) or {}
    parent = raw.pop("extends", None)
    if parent:
        parent_raw = yaml.safe_load((path.parent / parent).read_text()) or {}
        raw = _merge(parent_raw, raw)
    for key in ("work_root", "state_root", "knowledge_root"):
        raw["paths"][key] = str((path.parent / raw["paths"][key]).resolve())
    cfg = PipelineConfig.model_validate(raw)
    cfg.source_file = path
    return cfg
