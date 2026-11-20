from __future__ import annotations

import os
import subprocess
from pathlib import Path


class BatchArchiveError(RuntimeError):
    pass


def archive_path_for(work_root: Path, batch: Path) -> Path:
    return work_root / "batch-archives" / f"{batch.name}.tar.zst"


def create_batch_archive(batch: Path, destination: Path) -> Path:
    """Create a deterministic, compressed batch archive with an atomic final rename."""
    batch = batch.resolve()
    destination = destination.resolve()
    if destination.is_file():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.unlink(missing_ok=True)

    tar_command = [
        "tar",
        "--sort=name",
        "--mtime=@0",
        "--owner=0",
        "--group=0",
        "--numeric-owner",
        "-C",
        str(batch.parent),
        "-cf",
        "-",
        batch.name,
    ]
    zstd_command = ["zstd", "-T0", "-3", "--no-progress", "-f", "-o", str(temporary)]
    tar_process = subprocess.Popen(tar_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert tar_process.stdout is not None
    zstd_process = subprocess.Popen(
        zstd_command,
        stdin=tar_process.stdout,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    tar_process.stdout.close()
    _, zstd_stderr = zstd_process.communicate()
    _, tar_stderr = tar_process.communicate()
    if tar_process.returncode or zstd_process.returncode:
        temporary.unlink(missing_ok=True)
        detail = (tar_stderr + zstd_stderr).decode(errors="replace").strip()
        raise BatchArchiveError(detail or "tar/zstd archive creation failed")
    os.replace(temporary, destination)
    return destination
