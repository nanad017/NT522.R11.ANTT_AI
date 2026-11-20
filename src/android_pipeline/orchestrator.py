from __future__ import annotations

import shutil
import subprocess
import sys
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass
from pathlib import Path

import psutil

from .archive_reader import iter_apks, sha256_file
from .batch_archive import archive_path_for, create_batch_archive
from .config import PipelineConfig, UploadTarget
from .drive import copy_to_local, upload_file
from .ir.io import finalize_artifacts, write_json_atomic
from .state import StateDB


@dataclass(frozen=True)
class WorkItem:
    sha256: str
    member: str
    apk_path: Path


@dataclass(frozen=True)
class PreparedBatch:
    batch_id: str
    root: Path
    batch_archive: Path
    remote_path: str
    target: UploadTarget
    succeeded: tuple[str, ...]
    failures: tuple[dict, ...]


def choose_workers(cfg: PipelineConfig, peak_worker_gib: float | None = None) -> int:
    if isinstance(cfg.execution.workers, int):
        return cfg.execution.workers
    peak_worker_gib = peak_worker_gib or cfg.runtime_tuning.worker_memory_gib
    available = psutil.virtual_memory().available
    memory_budget = max(0, available - 8 * 2**30)
    by_memory = max(1, int(memory_budget / (peak_worker_gib * 2**30)))
    return max(
        1,
        min(psutil.cpu_count() or 1, by_memory, cfg.effective_batch_max_apks()),
    )


def _run_worker(
    item: WorkItem,
    cfg: PipelineConfig,
    archive: str,
    output: Path,
    log_path: Path,
) -> dict:
    command = [
        sys.executable,
        "-m",
        "android_pipeline.worker",
        "--apk",
        str(item.apk_path),
        "--config",
        str(cfg.source_file),
        "--output",
        str(output),
        "--input-archive",
        archive,
        "--input-member",
        item.member,
    ]
    started = time.monotonic()
    try:
        process = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=cfg.execution.apk_timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        write_json_atomic(
            log_path,
            {"status": "timeout", "seconds": cfg.execution.apk_timeout_seconds},
        )
        return {"status": "failed", "reason": f"timeout: {exc}", "item": item}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(process.stdout + "\n--- stderr ---\n" + process.stderr)
    if process.returncode:
        return {
            "status": "failed",
            "reason": f"worker_exit_{process.returncode}",
            "item": item,
        }
    return {
        "status": "ok",
        "runtime_seconds": time.monotonic() - started,
        "item": item,
        "output": output,
    }


def _prepare_group(
    items: list[WorkItem],
    cfg: PipelineConfig,
    archive: str,
    archive_id: str,
    sequence: int,
    db: StateDB,
) -> PreparedBatch:
    batch_id = f"{Path(archive).stem}-{archive_id[:10]}-{cfg.digest()[:10]}-b{sequence:05d}"
    root = cfg.paths.work_root / "dataset-batches" / batch_id
    remote_path = f"batches/{batch_id}.tar.zst"
    targets = cfg.upload_targets()
    target = targets[(sequence - 1) % len(targets)]
    db.upsert_batch(
        {
            "batch_id": batch_id,
            "archive_id": archive_id,
            "config_hash": cfg.digest(),
            "sample_count": len(items),
            "local_path": str(root),
            "remote_path": remote_path,
        }
    )
    for item in items:
        db.upsert_sample(
            {
                "apk_sha256": item.sha256,
                "archive_id": archive_id,
                "member_path": item.member,
                "batch_id": batch_id,
            }
        )
    workers = choose_workers(cfg)
    results = []
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="apk-worker") as pool:
        futures = {
            pool.submit(
                _run_worker,
                item,
                cfg,
                archive,
                root / "samples" / item.sha256,
                cfg.paths.state_root / "logs/workers" / f"{item.sha256}.log",
            ): item
            for item in items
        }
        for future in as_completed(futures):
            results.append(future.result())

    failures = []
    succeeded = []
    for result in results:
        item = result["item"]
        if result["status"] == "ok":
            succeeded.append(item.sha256)
            db.transition("samples", "apk_sha256", item.sha256, "validated")
        else:
            failures.append({"apk_sha256": item.sha256, "reason": result["reason"]})
            db.mark_error("samples", "apk_sha256", item.sha256, "extract", result["reason"])
        item.apk_path.unlink(missing_ok=True)

    write_json_atomic(
        root / "inventory.json",
        {
            "batch_id": batch_id,
            "archive": archive,
            "archive_id": archive_id,
            "config_hash": cfg.digest(),
            "workers": workers,
            "expected": len(items),
            "succeeded": succeeded,
            "failures": failures,
        },
    )
    finalize_artifacts(root, cfg.ir_schema_version)
    db.transition("batches", "batch_id", batch_id, "validated")

    batch_archive = create_batch_archive(root, archive_path_for(cfg.paths.work_root, root))
    return PreparedBatch(
        batch_id=batch_id,
        root=root,
        batch_archive=batch_archive,
        remote_path=remote_path,
        target=target,
        succeeded=tuple(succeeded),
        failures=tuple(failures),
    )


def _upload_prepared(cfg: PipelineConfig, prepared: PreparedBatch) -> dict:
    return upload_file(
        cfg,
        prepared.batch_archive,
        prepared.remote_path,
        target_config=prepared.target,
    )


def _finalize_uploaded(
    cfg: PipelineConfig, prepared: PreparedBatch, receipt: dict, db: StateDB
) -> dict:
    receipt_path = cfg.paths.state_root / "receipts" / f"{prepared.batch_id}.json"
    write_json_atomic(receipt_path, receipt)
    db.record_batch_archive(prepared.batch_id, prepared.batch_archive, receipt)
    db.transition("batches", "batch_id", prepared.batch_id, "remote_verified", receipt)
    for sha in prepared.succeeded:
        db.transition("samples", "apk_sha256", sha, "remote_verified")

    retain_batch_root = cfg.runtime_tuning.retain_verified_batch_roots
    if cfg.execution.cleanup_verified_cache and not retain_batch_root:
        resolved = prepared.root.resolve()
        managed = (cfg.paths.work_root / "dataset-batches").resolve()
        if managed not in resolved.parents:
            raise RuntimeError(f"refusing cleanup outside managed root: {resolved}")
        shutil.rmtree(resolved)

    if cfg.execution.cleanup_verified_cache or retain_batch_root:
        archive_resolved = prepared.batch_archive.resolve()
        archive_root = (cfg.paths.work_root / "batch-archives").resolve()
        if archive_resolved.parent != archive_root:
            raise RuntimeError(f"refusing cleanup outside archive root: {archive_resolved}")
        archive_resolved.unlink()

    if cfg.execution.cleanup_verified_cache and not retain_batch_root:
        db.transition("batches", "batch_id", prepared.batch_id, "cache_cleaned")
    return {
        "batch_id": prepared.batch_id,
        "succeeded": len(prepared.succeeded),
        "failed": len(prepared.failures),
        "remote": receipt["remote"],
    }


def run_archive(
    record: dict,
    cfg: PipelineConfig,
    db: StateDB,
    max_samples: int | None = None,
    selected_shas: set[str] | None = None,
) -> list[dict]:
    archive = record["remote_path"]
    archive_id = record["archive_id"]
    db.upsert_archive(record)
    local = cfg.paths.work_root / "downloads" / Path(archive).name
    if not local.exists():
        copy_to_local(cfg, archive, local)
    db.transition("archives", "archive_id", archive_id, "downloaded")
    actual = sha256_file(local)
    if actual != record["expected_sha256"]:
        db.mark_error("archives", "archive_id", archive_id, "checksum", actual)
        raise RuntimeError(f"archive checksum mismatch: {archive}")
    db.transition("archives", "archive_id", archive_id, "checksum_verified")

    compatible_hashes = [cfg.digest(), *cfg.runtime_tuning.compatible_verified_config_hashes]
    verified = (
        db.remote_verified_samples_for_hashes(compatible_hashes)
        if cfg.runtime_tuning.skip_verified_samples and selected_shas is None
        else set()
    )
    spool = cfg.paths.work_root / "spool" / archive_id
    max_raw = int(cfg.effective_batch_max_raw_gib() * 2**30)
    max_apks = cfg.effective_batch_max_apks()
    group: list[WorkItem] = []
    raw_bytes = 0
    sequence = db.last_batch_sequence(archive_id, cfg.digest())
    scheduled = 0
    discovered: set[str] = set()
    batches = []
    pending_uploads: list[tuple[Future[dict], PreparedBatch]] = []
    upload_slots = min(cfg.runtime_tuning.max_parallel_uploads, len(cfg.upload_targets()))
    upload_pool = ThreadPoolExecutor(max_workers=upload_slots, thread_name_prefix="batch-upload")

    def finish_one() -> None:
        completed, _ = wait([future for future, _ in pending_uploads], return_when=FIRST_COMPLETED)
        future = next(iter(completed))
        index = next(i for i, (candidate, _) in enumerate(pending_uploads) if candidate is future)
        _, prepared = pending_uploads.pop(index)
        receipt = future.result()
        batches.append(_finalize_uploaded(cfg, prepared, receipt, db))

    def flush() -> None:
        nonlocal group, raw_bytes, sequence
        if not group:
            return
        sequence += 1
        prepared = _prepare_group(group, cfg, archive, archive_id, sequence, db)
        while len(pending_uploads) >= upload_slots:
            finish_one()
        future = upload_pool.submit(_upload_prepared, cfg, prepared)
        pending_uploads.append((future, prepared))
        group = []
        raw_bytes = 0

    try:
        for member, apk_path in iter_apks(local, spool, selected_shas=selected_shas):
            sha = apk_path.stem.lower()
            discovered.add(sha)
            if sha in verified:
                apk_path.unlink(missing_ok=True)
                continue
            size = apk_path.stat().st_size
            if group and (len(group) >= max_apks or raw_bytes + size > max_raw):
                flush()
            item = WorkItem(sha256=sha, member=member, apk_path=apk_path)
            db.upsert_sample(
                {
                    "apk_sha256": sha,
                    "archive_id": archive_id,
                    "member_path": member,
                    "batch_id": None,
                }
            )
            group.append(item)
            raw_bytes += size
            scheduled += 1
            if max_samples is not None and scheduled >= max_samples:
                break
        flush()
        while pending_uploads:
            finish_one()
        missing = (selected_shas or set()) - discovered
        for sha in sorted(missing):
            db.mark_error(
                "samples",
                "apk_sha256",
                sha,
                "source_archive",
                "selected APK member absent from verified source archive",
            )
    finally:
        upload_pool.shutdown(wait=True, cancel_futures=False)
    final_state = "pilot_completed" if max_samples is not None else "remote_verified"
    db.transition("archives", "archive_id", archive_id, final_state)
    if max_samples is None and cfg.runtime_tuning.cleanup_source_archive_after_run:
        resolved_local = local.resolve()
        download_root = (cfg.paths.work_root / "downloads").resolve()
        if resolved_local.parent != download_root:
            raise RuntimeError(f"refusing source cleanup outside download root: {resolved_local}")
        resolved_local.unlink(missing_ok=True)
    return batches
