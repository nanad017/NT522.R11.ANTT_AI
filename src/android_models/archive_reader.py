from __future__ import annotations

import hashlib
import os
import tarfile
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

import zstandard

from .contracts import ContractError


def verify_checksum(path: str | Path, expected_sha256: str) -> None:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected_sha256.lower():
        raise ContractError(f"archive checksum mismatch: {path}")


def _safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts


@contextmanager
def stream_verified_archive(
    archive: str | Path,
    expected_sha256: str,
    cache_root: str | Path,
    max_member_bytes: int = 2 * 1024**3,
) -> Iterator[Iterator[tuple[str, Path]]]:
    """Yield one regular member at a time from a checksum-verified tar.zst.

    Members are atomically staged in a bounded temporary directory and removed
    before the next member. Symlinks, links, devices, and path traversal fail.
    """
    archive = Path(archive)
    verify_checksum(archive, expected_sha256)
    cache_root = Path(cache_root)
    cache_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="model-archive-", dir=cache_root))

    def members() -> Iterator[tuple[str, Path]]:
        with (
            archive.open("rb") as compressed,
            zstandard.ZstdDecompressor().stream_reader(compressed) as stream,
            tarfile.open(fileobj=stream, mode="r|") as tar,
        ):
            for member in tar:
                if member.isdir():
                    continue
                if not member.isfile() or not _safe_member(member.name):
                    raise ContractError(f"unsafe archive member: {member.name}")
                if member.size > max_member_bytes:
                    raise ContractError(f"archive member exceeds limit: {member.name}")
                source = tar.extractfile(member)
                if source is None:
                    raise ContractError(f"cannot read archive member: {member.name}")
                destination = temporary / "member.tmp"
                with destination.open("wb") as output:
                    copied = 0
                    while chunk := source.read(1024 * 1024):
                        copied += len(chunk)
                        if copied > max_member_bytes:
                            raise ContractError(f"expanded member exceeds limit: {member.name}")
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                yield member.name, destination
                destination.unlink(missing_ok=True)

    try:
        yield members()
    finally:
        for child in temporary.iterdir():
            child.unlink(missing_ok=True)
        temporary.rmdir()
