#!/usr/bin/env python3
"""Delegate reproducible training commands to the versioned modeling CLI."""

from __future__ import annotations

import subprocess
import sys


def main() -> int:
    if not sys.argv[1:]:
        print("usage: train.py <train-classical|train-common-gnn|train-anakin|train-gred> [args]")
        return 2
    return subprocess.call([sys.executable, "-m", "android_models.cli", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
