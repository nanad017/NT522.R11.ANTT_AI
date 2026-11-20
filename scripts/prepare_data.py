#!/usr/bin/env python3
"""Build and audit a portable model catalog."""

from __future__ import annotations

import subprocess
import sys

if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "preflight"
    arguments = sys.argv[2:] if len(sys.argv) > 1 else []
    raise SystemExit(
        subprocess.call([sys.executable, "-m", "android_models.cli", command, *arguments])
    )
