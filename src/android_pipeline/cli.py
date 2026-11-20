from __future__ import annotations

import hashlib
import json
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import psutil
import typer

from .adapters.fcg import classical_record
from .adapters.views import build_views
from .archive_reader import iter_apks, sha256_file
from .batch_archive import archive_path_for, create_batch_archive
from .config import PipelineConfig, load_config
from .consumers.smoke import smoke_all
from .drive import (
    copy_to_local,
    drive_about,
    list_source,
    rclone_binary,
    upload_file,
    verify_uploaded_file,
)
from .extractor.apk import extract_apk
from .ir.io import BatchWriter, finalize_artifacts, write_json_atomic
from .manifest import load_manifest
from .orchestrator import choose_workers, run_archive
from .quality import validate_batch
from .state import StateDB
from .worker import extract_to_directory

app = typer.Typer(no_args_is_help=True, pretty_exceptions_show_locals=False)
C = Annotated[Path, typer.Option("--config", exists=True, dir_okay=False)]


def _cfg(path: Path) -> PipelineConfig:
    return load_config(path)


def _manifest(cfg: PipelineConfig):
    path = cfg.paths.state_root / "input" / cfg.dataset.manifest_file
    return load_manifest(path, cfg.dataset.expected_samples)


def _expected_archives() -> list[str]:
    groups = (
        [("train", y, l) for y in (2019, 2020, 2021) for l in ("benign", "malware")]
        + [("validation", 2022, l) for l in ("benign", "malware")]
        + [("test", y, l) for y in (2023, 2024, 2025, 2026) for l in ("benign", "malware")]
    )
    return [f"{s}/{y}/{s}-{y}-{l}.tar.zst" for s, y, l in groups]


def _read_sidecar(path: Path) -> str:
    return path.read_text().strip().split()[0].lower()


def _taxonomy(cfg: PipelineConfig) -> dict:
    return json.loads(
        (cfg.source_file.parent.parent / "assets/security-taxonomy-v1.json").read_text()
    )


@app.command()
def preflight(config: C = Path("configs/smoke.yaml")) -> None:
    """Check local resources, tools, manifest and Drive inventory without downloading archives."""
    cfg = _cfg(config)
    disk = shutil.disk_usage(cfg.paths.work_root.parent)
    tools = {}
    for tool in ("rclone", "tar", "zstd", "java", "apksigner", "apkanalyzer", "jadx"):
        path = shutil.which(tool)
        tools[tool] = {"status": "available" if path else "missing", "path": path}
    project = cfg.source_file.parent.parent
    local_tools = {
        "java": project / "tools/android-verify-root/usr/lib/jvm/java-21-openjdk-amd64/bin/java",
        "apksigner": project / "scripts/apksigner-local",
    }
    for name, path in local_tools.items():
        if path.is_file():
            tools[name] = {"status": "available", "path": str(path), "scope": "project-local"}
    source = list_source(cfg)
    quota = drive_about(cfg)
    paths = {x["Path"]: x for x in source}
    expected = _expected_archives()
    manifest = _manifest(cfg)
    blockers = []
    if not all(p in paths for p in expected):
        blockers.append("missing_archive")
    if quota.get("free", 0) < int(cfg.execution.remote_free_min_gib * 2**30):
        blockers.append("insufficient_remote_quota")
    report = {
        "status": "ok" if not blockers else "blocked_" + "+".join(blockers),
        "blockers": blockers,
        "config_hash": cfg.digest(),
        "manifest": manifest.summary(),
        "tools": tools,
        "resources": {
            "cpu_count": psutil.cpu_count(),
            "ram_total": psutil.virtual_memory().total,
            "ram_available": psutil.virtual_memory().available,
            "disk_free": disk.free,
            "disk_reserve": int(cfg.execution.disk_reserve_gib * 2**30),
            "selected_workers": choose_workers(cfg),
        },
        "drive": {
            "files": len(source),
            "archives_found": sum(p.endswith(".tar.zst") for p in paths),
            "missing_archives": [p for p in expected if p not in paths],
            "unexpected_archives": sorted(
                p for p in paths if p.endswith(".tar.zst") and p not in expected
            ),
            "quota": quota,
        },
    }
    report["tools"]["rclone"] = {"status": "available", "path": rclone_binary()}
    typer.echo(json.dumps(report, indent=2, sort_keys=True))
    if report["status"] != "ok":
        raise typer.Exit(2)


@app.command("plan-input")
def plan_input(config: C = Path("configs/smoke.yaml"), dry_run: bool = True) -> None:
    """Lock the remote object list and sidecar checksums into SQLite/JSON state."""
    cfg = _cfg(config)
    source = list_source(cfg)
    by_path = {x["Path"]: x for x in source}
    expected = _expected_archives()
    records = []
    db = StateDB(cfg.paths.state_root / "pipeline.sqlite3")
    try:
        for path in expected:
            item = by_path.get(path)
            side = cfg.paths.state_root / "input" / f"{path}.sha256"
            md5 = cfg.paths.state_root / "input" / f"{path}.md5"
            record = {
                "archive_id": hashlib.sha256(path.encode()).hexdigest(),
                "remote_path": path,
                "remote_object_id": item.get("ID") if item else None,
                "remote_size": item.get("Size") if item else None,
                "expected_sha256": _read_sidecar(side) if side.exists() else None,
                "expected_md5": _read_sidecar(md5) if md5.exists() else None,
                "present": item is not None,
            }
            records.append(record)
            if not dry_run:
                db.upsert_archive(record)
    finally:
        db.close()
    plan = {
        "created_at": datetime.now(UTC).isoformat(),
        "dry_run": dry_run,
        "config_hash": cfg.digest(),
        "archives": records,
        "complete": all(r["present"] for r in records),
    }
    if not dry_run:
        write_json_atomic(cfg.paths.state_root / "input-plan.json", plan)
    typer.echo(json.dumps(plan, indent=2, sort_keys=True))
    if not plan["complete"]:
        raise typer.Exit(2)


@app.command("extract-one")
def extract_one(
    apk: Annotated[Path, typer.Option("--apk", exists=True, dir_okay=False)],
    config: C = Path("configs/smoke.yaml"),
    output: Annotated[Path | None, typer.Option("--output")] = None,
) -> None:
    """Extract one APK with a matching row in the locked manifest."""
    cfg = _cfg(config)
    sha = sha256_file(apk)
    output = output or cfg.paths.work_root / "extract-one" / sha
    result = extract_to_directory(apk, config, output)
    typer.echo(json.dumps(result, indent=2))


@app.command("run-batch")
def run_batch(
    archive: Annotated[str, typer.Option("--archive")],
    config: C = Path("configs/smoke.yaml"),
    limit: Annotated[int | None, typer.Option("--limit")] = None,
    upload: Annotated[bool, typer.Option("--upload/--no-upload")] = False,
) -> None:
    """Download, verify and process a bounded prefix of one source archive."""
    cfg = _cfg(config)
    if archive not in _expected_archives():
        raise typer.BadParameter("archive is not in the 16-file source contract")
    side = cfg.paths.state_root / "input" / f"{archive}.sha256"
    if not side.exists():
        raise typer.BadParameter(f"missing sidecar: {side}")
    expected_archive_sha = _read_sidecar(side)
    local_archive = cfg.paths.work_root / "downloads" / Path(archive).name
    if not local_archive.exists():
        copy_to_local(cfg, archive, local_archive)
    actual = sha256_file(local_archive)
    if actual != expected_archive_sha:
        raise RuntimeError(f"archive SHA mismatch expected={expected_archive_sha} actual={actual}")
    manifest = _manifest(cfg)
    batch_id = f"{Path(archive).stem}-{actual[:12]}-{int(time.time())}"
    root = cfg.paths.work_root / "batches" / batch_id
    spool = cfg.paths.work_root / "spool" / batch_id
    failures, diagnostics, classical = [], [], []
    processed = 0
    writer = BatchWriter(root)
    try:
        for member, apk_path in iter_apks(local_archive, spool):
            sha = apk_path.stem.lower()
            row = manifest.rows.get(sha)
            if row is None:
                failures.append({"member": member, "reason": "not_in_manifest"})
                apk_path.unlink(missing_ok=True)
                continue
            expected_parts = (row["split"], row["year"], row["label"])
            if not all(part in PurePath(member).parts for part in expected_parts):
                failures.append({"member": member, "reason": "manifest_path_mismatch"})
                apk_path.unlink(missing_ok=True)
                continue
            try:
                tables, diag = extract_apk(
                    apk_path, row, cfg, input_archive=archive, input_member=member
                )
                writer.append(tables)
                diag["views"] = build_views(tables, root / "views", cfg, _taxonomy(cfg))
                diagnostics.append(diag)
                classical.append(classical_record(tables, _taxonomy(cfg)))
                processed += 1
            except Exception as exc:  # noqa: BLE001 - per-APK failures must not abort the archive
                failures.append(
                    {"member": member, "apk_sha256": sha, "reason": f"{type(exc).__name__}: {exc}"}
                )
            finally:
                apk_path.unlink(missing_ok=True)
            if limit is not None and processed >= limit:
                break
    finally:
        inventory = writer.close()
    write_json_atomic(root / "quality-report.json", {"samples": diagnostics, "failures": failures})
    write_json_atomic(root / "classical-records.json", classical)
    write_json_atomic(
        root / "inventory.json",
        {
            "batch_id": batch_id,
            "archive": archive,
            "archive_sha256": actual,
            "config_hash": cfg.digest(),
            "processed": processed,
            "failed": len(failures),
            "tables": inventory,
        },
    )
    quality = validate_batch(root)
    write_json_atomic(root / "validation.json", quality)
    if quality["status"] != "ok":
        raise RuntimeError(f"batch validation failed: {quality['errors']}")
    finalize_artifacts(root, cfg.ir_schema_version)
    if upload:
        archive_path = create_batch_archive(root, archive_path_for(cfg.paths.work_root, root))
        receipt = upload_file(cfg, archive_path, f"batches/{batch_id}.tar.zst")
        write_json_atomic(cfg.paths.state_root / "receipts" / f"{batch_id}.json", receipt)
    typer.echo(
        json.dumps(
            {
                "status": "ok",
                "batch_id": batch_id,
                "processed": processed,
                "failures": failures,
                "uploaded": upload,
            },
            indent=2,
        )
    )


class PurePath:
    """Small POSIX member path adapter, kept local to avoid accepting host separators."""

    def __init__(self, value: str):
        self.parts = tuple(value.split("/"))


@app.command()
def validate(batch: Annotated[Path, typer.Option("--batch", exists=True, file_okay=False)]) -> None:
    report = validate_batch(batch)
    typer.echo(json.dumps(report, indent=2, sort_keys=True))
    if report["status"] != "ok":
        raise typer.Exit(2)


@app.command("upload-batch")
def upload_batch(
    batch: Annotated[Path, typer.Option("--batch", exists=True, file_okay=False)],
    config: C = Path("configs/smoke.yaml"),
) -> None:
    """Upload one validated batch, verify all objects, and persist a local receipt."""
    cfg = _cfg(config)
    finalize_artifacts(batch, cfg.ir_schema_version)
    quality = validate_batch(batch)
    if quality["status"] != "ok":
        raise RuntimeError(f"batch validation failed: {quality['errors']}")
    relative = f"batches/{batch.name}"
    archive_path = create_batch_archive(batch, archive_path_for(cfg.paths.work_root, batch))
    receipt = upload_file(cfg, archive_path, relative + ".tar.zst")
    receipt_path = cfg.paths.state_root / "receipts" / f"{batch.name}.json"
    write_json_atomic(receipt_path, receipt)
    typer.echo(json.dumps({"receipt": str(receipt_path), **receipt}, indent=2))


@app.command("verify-upload")
def verify_uploaded_batch(
    batch: Annotated[Path, typer.Option("--batch", exists=True, file_okay=False)],
    config: C = Path("configs/smoke.yaml"),
) -> None:
    """Re-verify a previously uploaded batch without mutating local or remote data."""
    cfg = _cfg(config)
    archive_path = archive_path_for(cfg.paths.work_root, batch)
    if not archive_path.is_file():
        raise RuntimeError(f"local batch archive is missing: {archive_path}")
    receipt = verify_uploaded_file(cfg, archive_path, f"batches/{batch.name}.tar.zst")
    typer.echo(json.dumps(receipt, indent=2))
    if receipt["status"] != "remote_verified":
        raise typer.Exit(2)


@app.command("resume-batch-upload")
def resume_batch_upload(
    batch: Annotated[Path, typer.Option("--batch", exists=True, file_okay=False)],
    config: C = Path("configs/pilot.yaml"),
    target_remote: Annotated[str | None, typer.Option("--target-remote")] = None,
) -> None:
    """Resume upload/verification for an extracted dataset batch without re-extraction."""
    cfg = _cfg(config)
    inventory = json.loads((batch / "inventory.json").read_text())
    for sha in inventory["succeeded"]:
        report = validate_batch(batch / "samples" / sha)
        if report["status"] != "ok":
            raise RuntimeError(f"sample {sha} failed validation: {report['errors']}")
    archive_path = create_batch_archive(batch, archive_path_for(cfg.paths.work_root, batch))
    targets = cfg.upload_targets()
    target = next((item for item in targets if item.remote == target_remote), None)
    if target_remote and target is None:
        raise RuntimeError(f"unknown upload target: {target_remote}")
    receipt = upload_file(
        cfg,
        archive_path,
        f"batches/{batch.name}.tar.zst",
        target_config=target,
    )
    receipt_path = cfg.paths.state_root / "receipts" / f"{batch.name}.json"
    write_json_atomic(receipt_path, receipt)
    db = StateDB(cfg.paths.state_root / "pipeline.sqlite3")
    try:
        db.transition("batches", "batch_id", batch.name, "remote_verified", receipt)
        db.record_batch_archive(batch.name, archive_path, receipt)
        for sha in inventory["succeeded"]:
            db.transition("samples", "apk_sha256", sha, "remote_verified")
        if cfg.execution.cleanup_verified_cache:
            resolved = batch.resolve()
            managed = (cfg.paths.work_root / "dataset-batches").resolve()
            if managed not in resolved.parents:
                raise RuntimeError(f"refusing cleanup outside managed root: {resolved}")
            shutil.rmtree(resolved)
            archive_resolved = archive_path.resolve()
            archive_root = (cfg.paths.work_root / "batch-archives").resolve()
            if archive_resolved.parent != archive_root:
                raise RuntimeError(f"refusing cleanup outside archive root: {archive_resolved}")
            archive_resolved.unlink()
            db.transition("batches", "batch_id", batch.name, "cache_cleaned")
    finally:
        db.close()
    typer.echo(json.dumps({"receipt": str(receipt_path), **receipt}, indent=2))


@app.command()
def status(config: C = Path("configs/smoke.yaml")) -> None:
    cfg = _cfg(config)
    db = StateDB(cfg.paths.state_root / "pipeline.sqlite3")
    try:
        typer.echo(json.dumps(db.summary(), indent=2, sort_keys=True))
    finally:
        db.close()


@app.command("smoke-consumers")
def smoke_consumers(
    batches: Annotated[list[Path], typer.Option("--batch", exists=True, file_okay=False)],
) -> None:
    """Load real artifacts and run classical/GNN contract checks."""
    report = smoke_all(batches)
    typer.echo(json.dumps(report, indent=2, sort_keys=True))
    for section in ("classical", "common_gnn"):
        if report[section]["status"] != "ok":
            raise typer.Exit(2)


@app.command("run-dataset")
def run_dataset(
    config: C = Path("configs/full-p0-p1.yaml"),
    confirm_full: Annotated[bool, typer.Option("--confirm-full")] = False,
) -> None:
    """Run the immutable 16-archive plan with bounded parallel APK workers."""
    cfg = _cfg(config)
    if cfg.execution.mode != "full" or not confirm_full:
        raise RuntimeError("full run requires mode=full and --confirm-full")
    pause_full = cfg.paths.state_root / "PAUSE_FULL_EXTRACTION"
    if cfg.artifacts.profile == "full" and pause_full.exists():
        raise RuntimeError(
            f"full extraction is paused by {pause_full}; use the compact profile or "
            "remove the marker only after explicitly restoring full extraction"
        )
    plan_path = cfg.paths.state_root / "input-plan.json"
    if not plan_path.exists():
        raise RuntimeError("input plan absent; run plan-input without --dry-run")
    plan = json.loads(plan_path.read_text())
    if not plan.get("complete") or len(plan.get("archives", [])) != 16:
        raise RuntimeError("full run blocked: immutable input plan is incomplete")
    quota = drive_about(cfg)
    required = int(cfg.execution.remote_free_min_gib * 2**30)
    if quota.get("free", 0) < required:
        raise RuntimeError(
            f"full run blocked: Drive free={quota.get('free', 0)} bytes, required={required} bytes"
        )
    db = StateDB(cfg.paths.state_root / "pipeline.sqlite3")
    results = []
    try:
        for record in plan["archives"]:
            results.extend(run_archive(record, cfg, db))
    finally:
        summary = db.summary()
        db.close()
    typer.echo(json.dumps({"status": "finished", "batches": results, "state": summary}, indent=2))


@app.command("pilot-orchestrator")
def pilot_orchestrator(
    archive: Annotated[str, typer.Option("--archive")],
    limit: Annotated[int, typer.Option("--limit", min=1, max=100)] = 2,
    config: C = Path("configs/pilot.yaml"),
) -> None:
    """Exercise bounded subprocess workers and upload/verify/cleanup on at most 100 APKs."""
    cfg = _cfg(config)
    if cfg.execution.mode != "pilot":
        raise RuntimeError("pilot-orchestrator requires execution.mode=pilot")
    plan_path = cfg.paths.state_root / "input-plan.json"
    if not plan_path.exists():
        raise RuntimeError("input plan absent; run plan-input without --dry-run")
    plan = json.loads(plan_path.read_text())
    record = next((row for row in plan["archives"] if row["remote_path"] == archive), None)
    if record is None or not record.get("present"):
        raise RuntimeError(f"archive is unavailable in the locked input plan: {archive}")
    db = StateDB(cfg.paths.state_root / "pipeline.sqlite3")
    try:
        batches = run_archive(record, cfg, db, max_samples=limit)
        summary = db.summary()
    finally:
        db.close()
    typer.echo(
        json.dumps({"status": "pilot_completed", "batches": batches, "state": summary}, indent=2)
    )


@app.command()
def resume(
    config: C = Path("configs/full-p0-p1.yaml"),
    confirm_full: Annotated[bool, typer.Option("--confirm-full")] = False,
) -> None:
    """Resume the durable full-run plan; verified samples are skipped."""
    run_dataset(config=config, confirm_full=confirm_full)


@app.command("retry-failed")
def retry_failed(
    config: C = Path("configs/full-ml-fcg-compact.yaml"),
    confirm_full: Annotated[bool, typer.Option("--confirm-full")] = False,
) -> None:
    """Retry only failed manifest APKs, one affected source archive at a time."""
    cfg = _cfg(config)
    if cfg.execution.mode != "full" or not confirm_full:
        raise RuntimeError("failed-sample recovery requires mode=full and --confirm-full")
    plan_path = cfg.paths.state_root / "input-plan.json"
    if not plan_path.exists():
        raise RuntimeError("input plan absent; run plan-input without --dry-run")
    plan = json.loads(plan_path.read_text())
    records = {row["archive_id"]: row for row in plan.get("archives", [])}
    db = StateDB(cfg.paths.state_root / "pipeline.sqlite3")
    results = []
    try:
        failed = db.failed_samples_by_archive()
        for archive_id, shas in failed.items():
            record = records.get(archive_id)
            if record is None or not record.get("present"):
                raise RuntimeError(f"failed sample source archive unavailable: {archive_id}")
            results.extend(run_archive(record, cfg, db, selected_shas=shas))
        remaining = sum(len(shas) for shas in db.failed_samples_by_archive().values())
        summary = db.summary()
    finally:
        db.close()
    typer.echo(
        json.dumps(
            {
                "status": "recovered" if remaining == 0 else "completed_with_failures",
                "initial_failed": sum(len(shas) for shas in failed.values()),
                "remaining_failed": remaining,
                "batches": results,
                "state": summary,
            },
            indent=2,
        )
    )
    if remaining:
        raise typer.Exit(2)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
