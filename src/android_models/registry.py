from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contracts import ContractError, canonical_hash, sha256_file
from .reproducibility import environment_metadata


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class RunRegistry:
    def __init__(
        self,
        output: str | Path,
        command: str,
        config: dict[str, Any],
        project_root: str | Path,
        resume: bool = False,
    ) -> None:
        root = Path(output).resolve()
        clean_config = {k: v for k, v in config.items() if not k.startswith("_")}
        self.config_hash = canonical_hash(clean_config)
        run_id = f"{command.replace('_', '-')}-{self.config_hash[:12]}"
        self.path = root / run_id
        if self.path.exists() and not resume:
            raise ContractError(f"run already exists: {self.path}; pass --resume")
        self.path.mkdir(parents=True, exist_ok=True)
        existing = self.path / "run.json"
        if existing.exists():
            old = json.loads(existing.read_text())
            if old.get("config_hash") != self.config_hash:
                raise ContractError("resume config hash mismatch")
        else:
            write_json_atomic(
                existing,
                {
                    "run_id": run_id,
                    "created_at": datetime.now(UTC).isoformat(),
                    "command": command,
                    "config": clean_config,
                    "config_hash": self.config_hash,
                    "environment": environment_metadata(Path(project_root)),
                    "status": "running",
                },
            )
        self.log("run_started", command=command)

    @property
    def run_id(self) -> str:
        return self.path.name

    def log(self, event: str, **fields: Any) -> None:
        record = {"timestamp": datetime.now(UTC).isoformat(), "event": event, **fields}
        with (self.path / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")

    def write(self, name: str, value: Any) -> Path:
        path = self.path / name
        write_json_atomic(path, value)
        return path

    def complete(self, artifacts: list[Path] | None = None) -> None:
        run_file = self.path / "run.json"
        run = json.loads(run_file.read_text())
        run["status"] = "complete"
        run["completed_at"] = datetime.now(UTC).isoformat()
        if artifacts:
            run["artifacts"] = {
                str(path.relative_to(self.path)): sha256_file(path) for path in artifacts
            }
        write_json_atomic(run_file, run)
        self.log("run_complete")

    def fail(self, reason: str) -> None:
        run_file = self.path / "run.json"
        run = json.loads(run_file.read_text())
        run["status"] = "failed"
        run["failed_at"] = datetime.now(UTC).isoformat()
        run["failure_reason"] = reason
        write_json_atomic(run_file, run)
        self.log("run_failed", reason=reason)
