#!/usr/bin/env python3
"""Evaluate frozen predictions on the temporal test split."""

from __future__ import annotations

import subprocess
import sys

if __name__ == "__main__":
    raise SystemExit(
        subprocess.call([sys.executable, "-m", "android_models.cli", "evaluate", *sys.argv[1:]])
    )
