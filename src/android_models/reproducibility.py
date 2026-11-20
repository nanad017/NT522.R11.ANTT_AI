from __future__ import annotations

import os
import platform
import random
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np

from .contracts import sha256_file


def seed_everything(seed: int, deterministic: bool = True) -> dict[str, Any]:
    random.seed(seed)
    np.random.seed(seed)
    os.environ.setdefault("PYTHONHASHSEED", str(seed))
    result: dict[str, Any] = {"seed": seed, "deterministic": deterministic}
    try:
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        torch.use_deterministic_algorithms(deterministic, warn_only=True)
        result.update(
            torch_version=torch.__version__,
            cuda_available=torch.cuda.is_available(),
            cuda_version=torch.version.cuda,
            cudnn_version=torch.backends.cudnn.version(),
        )
    except ImportError:
        result["torch"] = "not_installed"
    return result


def environment_metadata(project_root: Path) -> dict[str, Any]:
    def git(*args: str) -> str | None:
        try:
            return subprocess.check_output(
                ["git", *args], cwd=project_root, text=True, stderr=subprocess.DEVNULL
            ).strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None

    lock = project_root / "uv.lock"
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "git_commit": git("rev-parse", "HEAD"),
        "git_dirty": bool(git("status", "--porcelain")),
        "lock_file": str(lock) if lock.exists() else None,
        "lock_sha256": sha256_file(lock) if lock.exists() else None,
    }

