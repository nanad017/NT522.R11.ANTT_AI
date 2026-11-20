from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from .config import PipelineConfig, UploadTarget


class DriveError(RuntimeError):
    pass


def rclone_binary() -> str:
    """Prefer the project-pinned transport, with an explicit override for operators."""
    override = os.environ.get("ANDROID_PIPELINE_RCLONE")
    if override:
        return override
    project = Path(__file__).resolve().parents[2]
    pinned = project / "tools/rclone-v1.76.0-linux-amd64/rclone"
    if pinned.is_file():
        return str(pinned)
    found = shutil.which("rclone")
    if not found:
        raise DriveError("rclone is unavailable")
    return found


def _run(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    if args and args[0] == "rclone":
        args[0] = rclone_binary()
    proc = subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)
    if proc.returncode:
        raise DriveError(proc.stderr.strip() or proc.stdout.strip())
    return proc


def remote(cfg: PipelineConfig, suffix: str = "") -> str:
    path = cfg.drive.source_prefix
    if suffix:
        path += "/" + suffix.lstrip("/")
    return f"{cfg.drive.remote}:{path}"


def list_source(cfg: PipelineConfig) -> list[dict]:
    proc = _run(
        [
            "rclone",
            "lsjson",
            remote(cfg),
            "--recursive",
            "--files-only",
            "--hash",
            "--drive-root-folder-id",
            cfg.drive.root_folder_id,
        ]
    )
    return json.loads(proc.stdout)


def drive_about(cfg: PipelineConfig) -> dict:
    proc = _run(["rclone", "about", f"{cfg.drive.remote}:", "--json"])
    return json.loads(proc.stdout)


def copy_to_local(
    cfg: PipelineConfig, relative: str, destination: Path, timeout: int = 86_400
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "rclone",
            "copyto",
            remote(cfg, relative),
            str(destination),
            "--drive-root-folder-id",
            cfg.drive.root_folder_id,
            "--retries",
            str(cfg.execution.retries),
            "--low-level-retries",
            "10",
        ],
        timeout=timeout,
    )


def _local_listing(source: Path) -> list[dict]:
    proc = _run(["rclone", "lsjson", str(source), "--recursive", "--files-only", "--hash"])
    return json.loads(proc.stdout)


def _remote_output(
    cfg: PipelineConfig, relative_output: str, target: UploadTarget | None = None
) -> str:
    selected = target or cfg.upload_targets()[0]
    return f"{selected.remote}:{selected.output_prefix}/{relative_output.lstrip('/')}"


def _root_args(cfg: PipelineConfig, target: UploadTarget | None = None) -> list[str]:
    selected = target or cfg.upload_targets()[0]
    if not selected.root_folder_id:
        return []
    return ["--drive-root-folder-id", selected.root_folder_id]


def verify_upload(cfg: PipelineConfig, source: Path, relative_output: str) -> dict:
    """Verify every uploaded object by path, byte size and MD5 where available."""
    target = _remote_output(cfg, relative_output)
    remote_items = json.loads(
        _run(
            [
                "rclone",
                "lsjson",
                target,
                "--recursive",
                "--files-only",
                "--hash",
                "--drive-root-folder-id",
                cfg.drive.root_folder_id,
            ]
        ).stdout
    )
    local_items = _local_listing(source)
    local = {item["Path"]: item for item in local_items}
    remote = {item["Path"]: item for item in remote_items}
    errors: list[str] = []
    if set(local) != set(remote):
        errors.append(
            f"path set differs: local_only={sorted(set(local) - set(remote))} "
            f"remote_only={sorted(set(remote) - set(local))}"
        )
    for path in sorted(set(local) & set(remote)):
        lhs, rhs = local[path], remote[path]
        if lhs.get("Size") != rhs.get("Size"):
            errors.append(f"size mismatch: {path}")
        local_md5 = (lhs.get("Hashes") or {}).get("md5")
        remote_md5 = (rhs.get("Hashes") or {}).get("md5")
        if local_md5 and remote_md5 and local_md5.lower() != remote_md5.lower():
            errors.append(f"md5 mismatch: {path}")
    return {
        "status": "remote_verified" if not errors else "failed",
        "verified_at": datetime.now(UTC).isoformat(),
        "remote": target,
        "files": len(local),
        "bytes": sum(item.get("Size", 0) for item in local.values()),
        "errors": errors,
        "objects": [
            {
                "path": path,
                "size": item.get("Size"),
                "md5": (item.get("Hashes") or {}).get("md5"),
            }
            for path, item in sorted(local.items())
        ],
    }


def upload_dir(
    cfg: PipelineConfig, source: Path, relative_output: str, timeout: int = 86_400
) -> dict:
    target = _remote_output(cfg, relative_output)
    _run(
        [
            "rclone",
            "copy",
            str(source),
            target,
            "--drive-root-folder-id",
            cfg.drive.root_folder_id,
            "--transfers",
            str(cfg.execution.upload_transfers),
            "--retries",
            str(cfg.execution.retries),
            "--immutable",
        ],
        timeout=timeout,
    )
    receipt = verify_upload(cfg, source, relative_output)
    if receipt["status"] != "remote_verified":
        raise DriveError(f"remote verification failed: {receipt['errors']}")
    return receipt


def _file_hash(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_uploaded_file(
    cfg: PipelineConfig,
    source: Path,
    relative_output: str,
    target_config: UploadTarget | None = None,
) -> dict:
    """Verify one remote object by size and Drive MD5, retaining local SHA-256."""
    target = _remote_output(cfg, relative_output, target_config)
    remote_item = json.loads(
        _run(
            [
                "rclone",
                "lsjson",
                target,
                "--stat",
                "--hash",
                *_root_args(cfg, target_config),
            ]
        ).stdout
    )
    local_size = source.stat().st_size
    local_md5 = _file_hash(source, "md5")
    local_sha256 = _file_hash(source, "sha256")
    remote_md5 = (remote_item.get("Hashes") or {}).get("md5")
    errors: list[str] = []
    if remote_item.get("Size") != local_size:
        errors.append("size mismatch")
    if not remote_md5:
        errors.append("remote MD5 unavailable")
    elif remote_md5.lower() != local_md5:
        errors.append("MD5 mismatch")
    return {
        "status": "remote_verified" if not errors else "failed",
        "verified_at": datetime.now(UTC).isoformat(),
        "remote": target,
        "files": 1,
        "bytes": local_size,
        "local_sha256": local_sha256,
        "local_md5": local_md5,
        "remote_md5": remote_md5,
        "errors": errors,
        "objects": [{"path": source.name, "size": local_size, "md5": local_md5}],
    }


def upload_file(
    cfg: PipelineConfig,
    source: Path,
    relative_output: str,
    timeout: int = 86_400,
    target_config: UploadTarget | None = None,
) -> dict:
    target = _remote_output(cfg, relative_output, target_config)
    _run(
        [
            "rclone",
            "copyto",
            str(source),
            target,
            *_root_args(cfg, target_config),
            "--retries",
            str(cfg.execution.retries),
            "--immutable",
        ],
        timeout=timeout,
    )
    receipt = verify_uploaded_file(cfg, source, relative_output, target_config)
    if receipt["status"] != "remote_verified":
        raise DriveError(f"remote verification failed: {receipt['errors']}")
    return receipt
