from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .adapters.fcg import classical_record
from .adapters.views import build_views
from .archive_reader import sha256_file
from .config import load_config
from .extractor.apk import extract_apk
from .ir.io import finalize_artifacts, write_json_atomic, write_tables
from .manifest import load_manifest
from .quality import validate_batch


def extract_to_directory(
    apk: Path,
    config: Path,
    output: Path,
    *,
    input_archive: str | None = None,
    input_member: str | None = None,
) -> dict:
    cfg = load_config(config)
    sha = sha256_file(apk)
    manifest_path = cfg.paths.state_root / "input" / cfg.dataset.manifest_file
    manifest = load_manifest(manifest_path, cfg.dataset.expected_samples)
    row = manifest.rows.get(sha)
    if row is None:
        raise ValueError(f"APK {sha} is absent from locked manifest")
    if input_member:
        parts = tuple(input_member.split("/"))
        expected_parts = (row["split"], row["year"], row["label"])
        if not all(part in parts for part in expected_parts):
            raise ValueError(
                f"archive member disagrees with manifest: {input_member} vs {expected_parts}"
            )
    taxonomy = json.loads(
        (cfg.source_file.parent.parent / "assets/security-taxonomy-v1.json").read_text()
    )
    started = time.monotonic()
    tables, diagnostics = extract_apk(
        apk,
        row,
        cfg,
        input_archive=input_archive,
        input_member=input_member,
    )
    table_inventory = write_tables(output, tables, cfg.artifacts.retain_tables)
    views = build_views(tables, output / "views", cfg, taxonomy)
    write_json_atomic(output / "classical-records.json", [classical_record(tables, taxonomy)])
    write_json_atomic(
        output / "quality-report.json",
        {"samples": [diagnostics], "failures": [], "views": views},
    )
    inventory = {
        "pipeline_version": cfg.pipeline_version,
        "schema_version": cfg.ir_schema_version,
        "config_hash": cfg.digest(),
        "apk_sha256": sha,
        "input_archive": input_archive,
        "input_member": input_member,
        "tables": table_inventory,
        "views": views,
        "artifact_profile": cfg.artifacts.profile,
        "runtime_seconds": time.monotonic() - started,
    }
    write_json_atomic(output / "inventory.json", inventory)
    # A retry may reuse a managed output directory; never validate against a
    # checksum manifest from an earlier attempt.
    (output / "checksums.sha256").unlink(missing_ok=True)
    validation = validate_batch(output)
    write_json_atomic(output / "validation.json", validation)
    if validation["status"] != "ok":
        raise RuntimeError(f"artifact validation failed: {validation['errors']}")
    finalize_artifacts(output, cfg.ir_schema_version)
    return {"status": "ok", "apk_sha256": sha, "output": str(output), **inventory}


def main() -> None:
    parser = argparse.ArgumentParser(description="Isolated APK extraction worker")
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--input-archive")
    parser.add_argument("--input-member")
    args = parser.parse_args()
    result = extract_to_directory(
        args.apk,
        args.config,
        args.output,
        input_archive=args.input_archive,
        input_member=args.input_member,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
