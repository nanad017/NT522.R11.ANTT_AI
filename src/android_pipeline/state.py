from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS archives(
  archive_id TEXT PRIMARY KEY, remote_path TEXT NOT NULL UNIQUE, remote_object_id TEXT,
  expected_sha256 TEXT, expected_md5 TEXT, remote_size INTEGER,
  state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
  error_stage TEXT, error_reason TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS samples(
  apk_sha256 TEXT PRIMARY KEY, archive_id TEXT, member_path TEXT,
  state TEXT NOT NULL DEFAULT 'pending', batch_id TEXT, attempts INTEGER NOT NULL DEFAULT 0,
  error_stage TEXT, error_reason TEXT, updated_at TEXT NOT NULL,
  FOREIGN KEY(archive_id) REFERENCES archives(archive_id),
  UNIQUE(archive_id, member_path)
);
CREATE TABLE IF NOT EXISTS batches(
  batch_id TEXT PRIMARY KEY, archive_id TEXT NOT NULL, state TEXT NOT NULL,
  config_hash TEXT NOT NULL, sample_count INTEGER NOT NULL DEFAULT 0,
  local_path TEXT, remote_path TEXT, remote_object_id TEXT,
  local_sha256 TEXT, local_md5 TEXT, remote_md5 TEXT, bytes INTEGER,
  attempts INTEGER NOT NULL DEFAULT 0,
  error_stage TEXT, error_reason TEXT, updated_at TEXT NOT NULL,
  FOREIGN KEY(archive_id) REFERENCES archives(archive_id)
);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
  old_state TEXT, new_state TEXT NOT NULL, detail_json TEXT, created_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(UTC).isoformat()


class StateDB:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        batch_columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(batches)")}
        if "attempts" not in batch_columns:
            self.conn.execute("ALTER TABLE batches ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
        self.conn.execute("INSERT OR IGNORE INTO meta VALUES('schema_version','state-v1')")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.conn:
            yield self.conn

    def upsert_archive(self, record: dict) -> None:
        fields = {
            "archive_id": record["archive_id"],
            "remote_path": record["remote_path"],
            "remote_object_id": record.get("remote_object_id"),
            "expected_sha256": record.get("expected_sha256"),
            "expected_md5": record.get("expected_md5"),
            "remote_size": record.get("remote_size"),
            "updated_at": now(),
        }
        with self.conn:
            self.conn.execute(
                """INSERT INTO archives(archive_id,remote_path,remote_object_id,expected_sha256,
                expected_md5,remote_size,updated_at) VALUES(:archive_id,:remote_path,
                :remote_object_id,:expected_sha256,:expected_md5,:remote_size,:updated_at)
                ON CONFLICT(archive_id) DO UPDATE SET remote_path=excluded.remote_path,
                remote_object_id=excluded.remote_object_id, expected_sha256=excluded.expected_sha256,
                expected_md5=excluded.expected_md5, remote_size=excluded.remote_size,
                updated_at=excluded.updated_at""",
                fields,
            )

    def upsert_sample(self, record: dict) -> None:
        fields = {
            "apk_sha256": record["apk_sha256"],
            "archive_id": record.get("archive_id"),
            "member_path": record.get("member_path"),
            "batch_id": record.get("batch_id"),
            "updated_at": now(),
        }
        with self.conn:
            self.conn.execute(
                """INSERT INTO samples(apk_sha256,archive_id,member_path,batch_id,updated_at)
                VALUES(:apk_sha256,:archive_id,:member_path,:batch_id,:updated_at)
                ON CONFLICT(apk_sha256) DO UPDATE SET
                archive_id=excluded.archive_id, member_path=excluded.member_path,
                batch_id=excluded.batch_id, updated_at=excluded.updated_at""",
                fields,
            )

    def upsert_batch(self, record: dict) -> None:
        fields = {
            "batch_id": record["batch_id"],
            "archive_id": record["archive_id"],
            "state": record.get("state", "pending"),
            "config_hash": record["config_hash"],
            "sample_count": record.get("sample_count", 0),
            "local_path": record.get("local_path"),
            "remote_path": record.get("remote_path"),
            "bytes": record.get("bytes"),
            "updated_at": now(),
        }
        with self.conn:
            self.conn.execute(
                """INSERT INTO batches(batch_id,archive_id,state,config_hash,sample_count,
                local_path,remote_path,bytes,updated_at)
                VALUES(:batch_id,:archive_id,:state,:config_hash,:sample_count,:local_path,
                :remote_path,:bytes,:updated_at)
                ON CONFLICT(batch_id) DO UPDATE SET state=excluded.state,
                sample_count=excluded.sample_count,local_path=excluded.local_path,
                remote_path=excluded.remote_path,bytes=excluded.bytes,updated_at=excluded.updated_at""",
                fields,
            )

    def mark_error(
        self, table: str, id_column: str, entity_id: str, stage: str, reason: str
    ) -> None:
        if table not in {"archives", "samples", "batches"}:
            raise ValueError("invalid state table")
        with self.conn:
            row = self.conn.execute(
                f"SELECT state FROM {table} WHERE {id_column}=?", (entity_id,)
            ).fetchone()
            if row is None:
                raise KeyError(entity_id)
            self.conn.execute(
                f"""UPDATE {table} SET state='failed',attempts=attempts+1,
                error_stage=?,error_reason=?,updated_at=? WHERE {id_column}=?""",
                (stage, reason, now(), entity_id),
            )
            self.conn.execute(
                """INSERT INTO events(entity_type,entity_id,old_state,new_state,detail_json,
                created_at) VALUES(?,?,?,?,?,?)""",
                (
                    table,
                    entity_id,
                    row["state"],
                    "failed",
                    json.dumps({"stage": stage, "reason": reason}, sort_keys=True),
                    now(),
                ),
            )

    def transition(
        self, table: str, id_column: str, entity_id: str, new_state: str, detail: dict | None = None
    ) -> None:
        if table not in {"archives", "samples", "batches"}:
            raise ValueError("invalid state table")
        with self.conn:
            row = self.conn.execute(
                f"SELECT state FROM {table} WHERE {id_column}=?", (entity_id,)
            ).fetchone()
            if row is None:
                raise KeyError(entity_id)
            self.conn.execute(
                f"UPDATE {table} SET state=?,updated_at=? WHERE {id_column}=?",
                (new_state, now(), entity_id),
            )
            self.conn.execute(
                "INSERT INTO events(entity_type,entity_id,old_state,new_state,detail_json,created_at) VALUES(?,?,?,?,?,?)",
                (
                    table,
                    entity_id,
                    row["state"],
                    new_state,
                    json.dumps(detail or {}, sort_keys=True),
                    now(),
                ),
            )

    def record_batch_archive(self, batch_id: str, local_path: Path, receipt: dict) -> None:
        with self.conn:
            self.conn.execute(
                """UPDATE batches SET local_path=?,remote_path=?,local_sha256=?,local_md5=?,
                remote_md5=?,bytes=?,updated_at=? WHERE batch_id=?""",
                (
                    str(local_path),
                    receipt["remote"],
                    receipt["local_sha256"],
                    receipt["local_md5"],
                    receipt["remote_md5"],
                    receipt["bytes"],
                    now(),
                    batch_id,
                ),
            )

    def summary(self) -> dict:
        out = {}
        for table in ("archives", "samples", "batches"):
            out[table] = {
                row["state"]: row["n"]
                for row in self.conn.execute(f"SELECT state,count(*) n FROM {table} GROUP BY state")
            }
        return out

    def remote_verified_samples(self, config_hash: str) -> set[str]:
        """Return samples verified under the exact extraction config.

        A sample produced by a pilot or an older view/config must be rebuilt;
        SHA identity alone is not a valid artifact cache key.
        """
        return {
            row["apk_sha256"]
            for row in self.conn.execute(
                """SELECT s.apk_sha256
                FROM samples AS s
                JOIN batches AS b ON b.batch_id = s.batch_id
                WHERE s.state='remote_verified' AND b.config_hash=?""",
                (config_hash,),
            )
        }

    def remote_verified_samples_for_hashes(self, config_hashes: list[str]) -> set[str]:
        """Return verified samples from explicitly compatible artifact profiles."""
        hashes = sorted(set(config_hashes))
        if not hashes:
            return set()
        placeholders = ",".join("?" for _ in hashes)
        return {
            row["apk_sha256"]
            for row in self.conn.execute(
                f"""SELECT s.apk_sha256
                FROM samples AS s
                JOIN batches AS b ON b.batch_id = s.batch_id
                WHERE s.state='remote_verified' AND b.config_hash IN ({placeholders})""",
                hashes,
            )
        }

    def failed_samples_by_archive(self) -> dict[str, set[str]]:
        """Snapshot failed sample identities grouped by their source archive."""
        grouped: dict[str, set[str]] = {}
        for row in self.conn.execute(
            "SELECT archive_id,apk_sha256 FROM samples WHERE state='failed' ORDER BY archive_id"
        ):
            if row["archive_id"]:
                grouped.setdefault(row["archive_id"], set()).add(row["apk_sha256"])
        return grouped

    def last_batch_sequence(self, archive_id: str, config_hash: str) -> int:
        """Return the largest durable batch suffix for safe restart numbering."""
        sequences = []
        for row in self.conn.execute(
            "SELECT batch_id FROM batches WHERE archive_id=? AND config_hash=?",
            (archive_id, config_hash),
        ):
            match = re.search(r"-b(\d+)$", row["batch_id"])
            if match:
                sequences.append(int(match.group(1)))
        return max(sequences, default=0)
