from __future__ import annotations

import hashlib
import os
import tarfile
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

import zstandard


class UnsafeArchive(ValueError):
    pass


def safe_member_name(name: str) -> PurePosixPath:
    p = PurePosixPath(name)
    if not name or p.is_absolute() or ".." in p.parts or "\x00" in name:
        raise UnsafeArchive(f"unsafe member path: {name!r}")
    return p


def sha256_file(path: str | Path, chunk: int = 8 << 20) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while data := handle.read(chunk):
            h.update(data)
    return h.hexdigest()


def iter_apks(
    archive: str | Path,
    spool_root: str | Path,
    *,
    max_member_bytes: int = 21 * 1024 * 1024,
    selected_shas: set[str] | None = None,
) -> Iterator[tuple[str, Path]]:
    archive = Path(archive)
    spool_root = Path(spool_root).resolve()
    spool_root.mkdir(parents=True, exist_ok=True)
    with (
        archive.open("rb") as compressed,
        zstandard.ZstdDecompressor().stream_reader(compressed) as stream,
        tarfile.open(fileobj=stream, mode="r|") as tar,
    ):
        for member in tar:
            safe_member_name(member.name)
            if member.isdir():
                continue
            if member.issym() or member.islnk() or not member.isfile():
                raise UnsafeArchive(f"links/special members are forbidden: {member.name}")
            if member.size > max_member_bytes:
                raise UnsafeArchive(f"member exceeds APK limit: {member.name} ({member.size})")
            if not member.name.lower().endswith(".apk"):
                continue
            member_sha = PurePosixPath(member.name).stem.lower()
            if selected_shas is not None and member_sha not in selected_shas:
                continue
            source = tar.extractfile(member)
            if source is None:
                raise UnsafeArchive(f"cannot read member: {member.name}")
            name = PurePosixPath(member.name).name
            target = (spool_root / name).resolve()
            if spool_root not in target.parents:
                raise UnsafeArchive(f"resolved path escaped spool: {member.name}")
            tmp = target.with_suffix(target.suffix + ".partial")
            written = 0
            with tmp.open("xb") as out:
                while data := source.read(1 << 20):
                    written += len(data)
                    if written > max_member_bytes:
                        raise UnsafeArchive(f"stream exceeds APK limit: {member.name}")
                    out.write(data)
                out.flush()
                os.fsync(out.fileno())
            if written != member.size:
                raise UnsafeArchive(f"member size mismatch: {member.name}")
            tmp.replace(target)
            yield member.name, target
