# Android static extraction quickstart

All commands below run from `/home/rl/Data`. The locked Python environment is
defined by `pyproject.toml` and `uv.lock`; credentials stay in the user's rclone
configuration and are not copied into this project.

## Bootstrap and checks

```bash
uv sync --all-extras
uv run pytest -q
uv run ruff check src tests
uv run android-pipeline preflight --config configs/full-p0-p1.yaml
```

`preflight` intentionally exits non-zero while any source archive is absent or
Drive free space is below `execution.remote_free_min_gib`. Its JSON output is
the authoritative explanation of the gate.

Lock the current 16-object input contract, including missing objects:

```bash
uv run android-pipeline plan-input \
  --config configs/full-p0-p1.yaml --no-dry-run
```

## Smoke and pilot

Extract one manifest-listed APK:

```bash
uv run android-pipeline extract-one \
  --config configs/full-p0-p1.yaml \
  --apk /path/to/SHA256.apk \
  --output work/extract-one/SHA256
```

The output contains canonical Parquet tables plus `views/fcg`, `views/ccg`,
`views/gred`, `views/sigdroid` and `views/anakin`. GRED/SIGDroid artifacts are
usable for provenance and shape checks but remain marked
`blocked_missing_asset` until the exact paper assets are provisioned.

Run the bounded isolated-worker path, including upload, remote MD5/size
verification, receipt, and cleanup:

```bash
uv run android-pipeline pilot-orchestrator \
  --config configs/pilot.yaml \
  --archive test/2025/test-2025-malware.tar.zst \
  --limit 2
```

Run consumer contract tests on one benign and one malware batch:

```bash
uv run android-pipeline smoke-consumers \
  --batch work/batches/BENIGN_BATCH \
  --batch work/batches/MALWARE_BATCH
```

Upload or re-verify an existing validated batch. The uploader deterministically
packs the directory as `work/batch-archives/BATCH.tar.zst`, uploads that single
object to `batches/BATCH.tar.zst`, and verifies remote size and MD5 before
cleanup:

```bash
uv run android-pipeline upload-batch --config configs/smoke.yaml --batch work/batches/BATCH
uv run android-pipeline verify-upload --config configs/smoke.yaml --batch work/batches/BATCH
```

## Full run and resume

Do not launch until `preflight` reports `status=ok`.

Once both gates are resolved:

```bash
scripts/launch-full.sh
scripts/status-full.sh
```

The launcher uses `nohup`, writes `state/run/android-static-v1.pid`, and logs to
`state/logs/android-static-v1.log`. The same durable operation can be resumed
explicitly with:

```bash
uv run android-pipeline resume \
  --config configs/full-p0-p1.yaml --confirm-full
```

Only samples with remote-verified receipts for the exact config hash are
skipped. Batch numbering resumes after the largest durable sequence for the
archive/config pair. Cleanup is restricted to `work/dataset-batches` and
`work/batch-archives`; source archives are never deleted by the scheduler.

For the full VM run, `runtime_tuning` is operational-only and therefore does
not change the artifact config hash. It uses up to 48 APKs/6 GiB raw input per
batch, overlaps extraction with uploads, and shards immutable batch archives
round-robin across `nad`, `nad2`, and `nad3`. Every receipt records the exact
remote destination, size, local SHA-256, and matching local/remote MD5.
