from __future__ import annotations

import csv
import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

SHA_RE = re.compile(r"^[0-9a-f]{64}$")
VALID_SPLITS = {"train", "validation", "test"}
VALID_LABELS = {"benign", "malware"}


@dataclass(frozen=True)
class ManifestIndex:
    rows: dict[str, dict[str, str]]
    sha256: str
    columns: tuple[str, ...]

    def summary(self) -> dict:
        values = list(self.rows.values())
        return {
            "manifest_sha256": self.sha256,
            "samples": len(values),
            "splits": dict(Counter(r["split"] for r in values)),
            "labels": dict(Counter(r["label"] for r in values)),
            "split_year_label": {
                "|".join(k): v
                for k, v in sorted(
                    Counter((r["split"], r["year"], r["label"]) for r in values).items()
                )
            },
        }


def load_manifest(path: str | Path, expected_samples: int | None = None) -> ManifestIndex:
    path = Path(path)
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    rows: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "sha256" not in reader.fieldnames:
            raise ValueError("manifest lacks sha256 column")
        for number, row in enumerate(reader, start=2):
            sha = (row.get("sha256") or "").lower()
            if not SHA_RE.fullmatch(sha):
                raise ValueError(f"invalid SHA-256 at row {number}")
            if sha in rows:
                raise ValueError(f"duplicate SHA-256 at row {number}: {sha}")
            if row.get("split") not in VALID_SPLITS:
                raise ValueError(f"invalid split at row {number}: {row.get('split')}")
            if row.get("label") not in VALID_LABELS:
                raise ValueError(f"invalid label at row {number}: {row.get('label')}")
            row["sha256"] = sha
            row["manifest_row_number"] = str(number)
            rows[sha] = row
    if expected_samples is not None and len(rows) != expected_samples:
        raise ValueError(f"expected {expected_samples} samples, found {len(rows)}")
    return ManifestIndex(rows, digest, tuple(reader.fieldnames))
