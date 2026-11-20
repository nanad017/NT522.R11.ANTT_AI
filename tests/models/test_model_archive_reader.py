from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

import zstandard

from android_models.archive_reader import stream_verified_archive


def test_verified_archive_stream_is_bounded(tmp_path: Path) -> None:
    raw_tar = io.BytesIO()
    with tarfile.open(fileobj=raw_tar, mode="w") as archive:
        payload = b"model artifact"
        info = tarfile.TarInfo("batch/data.json")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    compressed = tmp_path / "batch.tar.zst"
    compressed.write_bytes(zstandard.ZstdCompressor().compress(raw_tar.getvalue()))
    checksum = hashlib.sha256(compressed.read_bytes()).hexdigest()
    with stream_verified_archive(compressed, checksum, tmp_path / "cache") as members:
        name, path = next(members)
        assert name == "batch/data.json"
        assert path.read_bytes() == b"model artifact"
    assert not list((tmp_path / "cache").iterdir())
