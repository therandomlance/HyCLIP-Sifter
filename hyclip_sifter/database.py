"""SQLite + sqlite-vector database layer.

All operations are guarded by a single :class:`threading.RLock` to allow safe
access from worker threads. The connection is opened with WAL journal mode and
``check_same_thread=False``.
"""

from __future__ import annotations

import csv
import os
import re
import sqlite3
import struct
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import sqlite_vector

# Operation codes (see SPEC §8).
OP_DELETE = 0
OP_ARCHIVE = 1
OP_SKIP = 2
OP_DEFER = 3

OP_NAMES = {OP_DELETE: "delete", OP_ARCHIVE: "archive", OP_SKIP: "skip", OP_DEFER: "defer"}

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _bucket_table(name: str) -> str:
    return f"bucket_{name}"


def _vector_ext_path() -> str:
    suffix = ".dll" if os.name == "nt" else ".so"
    return os.path.join(os.path.dirname(sqlite_vector.__file__), "binaries", f"vector{suffix}")


def _pack_embedding(values: list[float]) -> bytes:
    return struct.pack(f"<{len(values)}f", *values)


def _unpack_embedding(blob: bytes, dim: int) -> list[float]:
    return list(struct.unpack(f"<{dim}f", blob))


class Database:
    """Thread-safe wrapper around a sqlite3 connection using sqlite-vector."""

    def __init__(self, path: str | Path = "hyclip_sifter.db"):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._dirty: set[str] = set()  # bucket tables needing re-quantization
        self._ext_path = _vector_ext_path()
        try:
            self._conn.enable_load_extension(True)
            self._conn.load_extension(self._ext_path)
        except sqlite3.OperationalError as exc:
            raise RuntimeError(
                f"Failed to load sqlite-vector extension at {self._ext_path}: {exc}"
            ) from exc
        # WAL mode for less lock contention.
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._create_base_tables()
            self._init_existing_indices()

    # ------------------------------------------------------------------ utils
    def _execute(self, sql: str, params: Iterable[Any] | dict[str, Any] = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql, params)

    def _create_base_tables(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS buckets (
                name TEXT PRIMARY KEY,
                dimension INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS history (
                hash TEXT NOT NULL,
                bucket TEXT NOT NULL,
                operation INTEGER NOT NULL,
                timestamp TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS history_bucket_op_idx
                ON history (bucket, operation);
            """
        )

    def _init_existing_indices(self) -> None:
        for (name, dim) in self._conn.execute("SELECT name, dimension FROM buckets"):
            table = _bucket_table(name)
            try:
                self._conn.execute(
                    f"SELECT vector_init('{table}','embedding',"
                    f"'dimension={dim},type=FLOAT32,distance=cosine')"
                )
            except sqlite3.OperationalError:
                pass

    @staticmethod
    def _validate_name(name: str) -> None:
        if not name or " " in name or not _NAME_RE.match(name):
            raise ValueError(
                "Bucket name must be non-empty, contain no spaces, "
                "and use only [A-Za-z0-9_-]."
            )

    # -------------------------------------------------------------- buckets
    def create_bucket(self, name: str, dim: int) -> None:
        self._validate_name(name)
        table = _bucket_table(name)
        with self._lock:
            self._conn.execute(
                f"CREATE TABLE IF NOT EXISTS {table} (hash TEXT PRIMARY KEY, embedding BLOB)"
            )
            self._conn.execute(
                f"SELECT vector_init('{table}','embedding',"
                f"'dimension={dim},type=FLOAT32,distance=cosine')"
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO buckets(name, dimension) VALUES(?, ?)", (name, dim)
            )
            self._conn.commit()

    def delete_bucket(self, name: str) -> None:
        self._validate_name(name)
        table = _bucket_table(name)
        with self._lock:
            try:
                self._conn.execute(f"SELECT vector_quantize_cleanup('{table}','embedding')")
            except sqlite3.OperationalError:
                pass
            self._conn.execute(f"DROP TABLE IF EXISTS {table}")
            self._conn.execute("DELETE FROM buckets WHERE name=?", (name,))
            self._dirty.discard(name)
            self._conn.commit()

    def rename_bucket(self, old: str, new: str) -> None:
        self._validate_name(old)
        self._validate_name(new)
        with self._lock:
            if not self._conn.execute("SELECT 1 FROM buckets WHERE name=?", (old,)).fetchone():
                raise ValueError(f"Bucket {old!r} does not exist")
            if self._conn.execute("SELECT 1 FROM buckets WHERE name=?", (new,)).fetchone():
                raise ValueError(f"Bucket {new!r} already exists")
            old_table = _bucket_table(old)
            new_table = _bucket_table(new)
            try:
                self._conn.execute(f"SELECT vector_quantize_cleanup('{old_table}','embedding')")
            except sqlite3.OperationalError:
                pass
            self._conn.execute(f"ALTER TABLE {old_table} RENAME TO {new_table}")
            dim = self._conn.execute(
                "UPDATE buckets SET name=? WHERE name=? RETURNING dimension", (new, old)
            ).fetchone()[0]
            self._conn.execute(
                f"SELECT vector_init('{new_table}','embedding',"
                f"'dimension={dim},type=FLOAT32,distance=cosine')"
            )
            self._dirty.discard(old)
            self._conn.commit()

    def list_buckets(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute("SELECT name FROM buckets ORDER BY name").fetchall()
            return [r[0] for r in rows]

    def bucket_dimension(self, name: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT dimension FROM buckets WHERE name=?", (name,)
            ).fetchone()
            if not row:
                raise KeyError(f"Bucket {name!r} not found")
            return int(row[0])

    def bucket_count(self, name: str) -> int:
        table = _bucket_table(name)
        with self._lock:
            try:
                row = self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                return int(row[0]) if row else 0
            except sqlite3.OperationalError:
                return 0

    # ----------------------------------------------------------- embeddings
    def add_embedding(self, bucket: str, hash_: str, embedding: list[float]) -> None:
        table = _bucket_table(bucket)
        blob = _pack_embedding(embedding)
        with self._lock:
            self._conn.execute(
                f"INSERT OR REPLACE INTO {table}(hash, embedding) VALUES(?, ?)",
                (hash_, blob),
            )
            self._dirty.add(bucket)
            self._conn.commit()

    def add_embeddings_batch(self, bucket: str, items: Iterable[tuple[str, list[float]]]) -> None:
        table = _bucket_table(bucket)
        rows = [(h, _pack_embedding(v)) for h, v in items]
        if not rows:
            return
        with self._lock:
            self._conn.executemany(
                f"INSERT OR REPLACE INTO {table}(hash, embedding) VALUES(?, ?)", rows
            )
            self._dirty.add(bucket)
            self._conn.commit()

    def has_hash(self, bucket: str, hash_: str) -> bool:
        table = _bucket_table(bucket)
        with self._lock:
            row = self._conn.execute(
                f"SELECT 1 FROM {table} WHERE hash=?", (hash_,)
            ).fetchone()
            return row is not None

    def get_embedding(self, bucket: str, hash_: str) -> list[float] | None:
        table = _bucket_table(bucket)
        dim = self.bucket_dimension(bucket)
        with self._lock:
            row = self._conn.execute(
                f"SELECT embedding FROM {table} WHERE hash=?", (hash_,)
            ).fetchone()
            if row is None:
                return None
            return _unpack_embedding(row[0], dim)

    def random_sample(self, bucket: str, k: int) -> list[str]:
        table = _bucket_table(bucket)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT hash FROM {table} ORDER BY RANDOM() LIMIT ?", (k,)
            ).fetchall()
            return [r[0] for r in rows]

    def remove_from_bucket(self, bucket: str, hash_: str, operation: int) -> None:
        table = _bucket_table(bucket)
        ts = datetime.now().isoformat()
        with self._lock:
            self._conn.execute(f"DELETE FROM {table} WHERE hash=?", (hash_,))
            self._conn.execute(
                "INSERT INTO history(hash, bucket, operation, timestamp) VALUES(?, ?, ?, ?)",
                (hash_, bucket, operation, ts),
            )
            self._dirty.add(bucket)
            self._conn.commit()

    def remove_from_bucket_silent(self, bucket: str, hash_: str) -> None:
        """Remove a hash from a bucket without recording a triage history entry.

        Used by Copy/Move between buckets, where the hash is being relocated
        rather than triaged.
        """
        table = _bucket_table(bucket)
        with self._lock:
            self._conn.execute(f"DELETE FROM {table} WHERE hash=?", (hash_,))
            self._dirty.add(bucket)
            self._conn.commit()

    def restore_to_bucket(self, bucket: str, hash_: str, embedding: list[float]) -> None:
        """Re-insert a hash into a bucket (requires a fresh embedding)."""
        table = _bucket_table(bucket)
        with self._lock:
            self._conn.execute(
                f"INSERT OR REPLACE INTO {table}(hash, embedding) VALUES(?, ?)",
                (hash_, _pack_embedding(embedding)),
            )
            self._dirty.add(bucket)
            self._conn.commit()

    # ------------------------------------------------------------- searching
    def _ensure_quantized(self, bucket: str) -> None:
        table = _bucket_table(bucket)
        if bucket in self._dirty:
            self._conn.execute(f"SELECT vector_quantize('{table}','embedding')")
            self._dirty.discard(bucket)
        else:
            # Quantization tables don't persist across restarts — ensure the
            # quantization exists even if the bucket isn't marked dirty.
            # vector_quantize is idempotent: it creates if missing, rebuilds if stale.
            try:
                self._conn.execute(f"SELECT vector_quantize('{table}','embedding')")
            except sqlite3.OperationalError:
                pass

    def nearest_neighbors(
        self,
        bucket: str,
        query_blob: bytes,
        k: int,
        exclude_hash: str | None = None,
    ) -> list[tuple[str, float]]:
        table = _bucket_table(bucket)
        with self._lock:
            self._ensure_quantized(bucket)
            try:
                self._conn.execute(f"SELECT vector_quantize_preload('{table}','embedding')")
            except sqlite3.OperationalError:
                pass
            if exclude_hash is not None:
                sql = (
                    f"SELECT t.hash, v.distance FROM "
                    f"vector_quantize_scan('{table}','embedding',?,{k}) AS v "
                    f"JOIN {table} AS t ON t.rowid = v.rowid "
                    f"WHERE t.hash != ? ORDER BY v.distance LIMIT ?"
                )
                rows = self._conn.execute(sql, (query_blob, exclude_hash, k)).fetchall()
            else:
                sql = (
                    f"SELECT t.hash, v.distance FROM "
                    f"vector_quantize_scan('{table}','embedding',?,{k}) AS v "
                    f"JOIN {table} AS t ON t.rowid = v.rowid "
                    f"ORDER BY v.distance LIMIT ?"
                )
                rows = self._conn.execute(sql, (query_blob, k)).fetchall()
            return [(r[0], float(r[1])) for r in rows]

    def nearest_neighbors_stream(
        self,
        bucket: str,
        query_blob: bytes,
        k: int,
        exclude_hash: str | None = None,
    ):
        """Yield (hash, distance) progressively; used for incremental grid UI.

        Rows are materialized under the DB lock so the cursor never escapes
        it; ``k`` is bounded by the UI results spin so this is memory-safe.
        """
        table = _bucket_table(bucket)
        with self._lock:
            self._ensure_quantized(bucket)
            try:
                self._conn.execute(f"SELECT vector_quantize_preload('{table}','embedding')")
            except sqlite3.OperationalError:
                pass
            sql = (
                f"SELECT t.hash, v.distance FROM "
                f"vector_quantize_scan('{table}','embedding',?) AS v "
                f"JOIN {table} AS t ON t.rowid = v.rowid "
                + ("WHERE t.hash != ? " if exclude_hash is not None else "")
                + "ORDER BY v.distance LIMIT ?"
            )
            params: tuple[Any, ...]
            if exclude_hash is not None:
                params = (query_blob, exclude_hash, k)
            else:
                params = (query_blob, k)
            rows = self._conn.execute(sql, params).fetchall()
        for r in rows:
            yield r[0], float(r[1])

    def quantize_now(self, bucket: str) -> None:
        """Force quantization (e.g. from a background thread)."""
        with self._lock:
            self._ensure_quantized(bucket)

    def find_duplicates(self, bucket: str, threshold: float) -> list[tuple[str, str, float]]:
        """Return pairs of hashes whose cosine distance is below ``threshold``."""
        table = _bucket_table(bucket)
        with self._lock:
            self._ensure_quantized(bucket)
            dim = self.bucket_dimension(bucket)
            # Self-join via full scan of each row against the bucket.
            sql = (
                f"SELECT a.hash AS ha, b.hash AS hb, v.distance AS d FROM "
                f"{table} AS a JOIN vector_full_scan('{table}','embedding',a.embedding) AS v "
                f"JOIN {table} AS b ON b.rowid = v.rowid "
                f"WHERE a.hash < b.hash AND v.distance < ? ORDER BY v.distance"
            )
            try:
                rows = self._conn.execute(sql, (threshold,)).fetchall()
            except sqlite3.OperationalError:
                # Fallback: brute force in Python (only for small buckets).
                rows_all = self._conn.execute(
                    f"SELECT hash, embedding FROM {table}"
                ).fetchall()
                pairs: list[tuple[str, str, float]] = []
                for i in range(len(rows_all)):
                    ha, ba = rows_all[i][0], rows_all[i][1]
                    va = _unpack_embedding(ba, dim)
                    for j in range(i + 1, len(rows_all)):
                        hb, bb = rows_all[j][0], rows_all[j][1]
                        vb = _unpack_embedding(bb, dim)
                        d = _cosine(va, vb)
                        if d < threshold:
                            pairs.append((ha, hb, d))
                pairs.sort(key=lambda x: x[2])
                return pairs
            return [(r[0], r[1], float(r[2])) for r in rows]

    # ---------------------------------------------------------------- history
    def history_query(
        self,
        bucket: str | None = None,
        operation: int | None = None,
        limit: int = 100,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[tuple[str, str, int, str]]:
        clauses: list[str] = []
        params: list[Any] = []
        if bucket:
            clauses.append("bucket = ?")
            params.append(bucket)
        if operation is not None:
            clauses.append("operation = ?")
            params.append(operation)
        if date_from:
            clauses.append("timestamp >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("timestamp <= ?")
            params.append(date_to)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT hash, bucket, operation, timestamp FROM history" + where
            + " ORDER BY timestamp DESC, rowid DESC LIMIT ?"
        )
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
            return [(r[0], r[1], int(r[2]), r[3]) for r in rows]

    def history_query_filtered(
        self,
        bucket: str | None = None,
        operation: int | None = None,
        limit: int = 100,
        search: str = "",
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[tuple[str, str, int, str]]:
        clauses: list[str] = []
        params: list[Any] = []
        if bucket:
            clauses.append("bucket = ?")
            params.append(bucket)
        if operation is not None:
            clauses.append("operation = ?")
            params.append(operation)
        if search:
            clauses.append("hash LIKE ?")
            params.append(search.replace("%", r"\%") + "%")
        if date_from:
            clauses.append("timestamp >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("timestamp <= ?")
            params.append(date_to)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT hash, bucket, operation, timestamp FROM history" + where
            + " ORDER BY timestamp DESC, rowid DESC LIMIT ?"
        )
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
            return [(r[0], r[1], int(r[2]), r[3]) for r in rows]

    def history_count(
        self,
        bucket: str | None = None,
        operation: int | None = None,
        search: str = "",
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> int:
        clauses: list[str] = []
        params: list[Any] = []
        if bucket:
            clauses.append("bucket = ?")
            params.append(bucket)
        if operation is not None:
            clauses.append("operation = ?")
            params.append(operation)
        if search:
            clauses.append("hash LIKE ?")
            params.append(search.replace("%", r"\%") + "%")
        if date_from:
            clauses.append("timestamp >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("timestamp <= ?")
            params.append(date_to)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._lock:
            row = self._conn.execute(f"SELECT COUNT(*) FROM history{where}", params).fetchone()
            return int(row[0]) if row else 0

    def history_buckets(self) -> list[tuple[str, int]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT bucket, COUNT(*) AS c FROM history GROUP BY bucket ORDER BY bucket"
            ).fetchall()
            return [(r[0], int(r[1])) for r in rows]

    def history_counts(self, bucket: str) -> dict[int, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT operation, COUNT(*) FROM history WHERE bucket=? GROUP BY operation",
                (bucket,),
            ).fetchall()
            return {int(r[0]): int(r[1]) for r in rows}

    def history_export_csv(
        self, path: str | Path, bucket: str | None = None, operation: int | None = None
    ) -> int:
        rows = self.history_query(bucket, operation, limit=10_000_000)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["hash", "bucket", "operation", "timestamp"])
            for h, b, op, ts in rows:
                writer.writerow([h, b, OP_NAMES.get(op, op), ts])
        return len(rows)

    # ----------------------------------------------------------- integrity
    def verify_integrity(self) -> list[str]:
        """Check for orphaned vector indices or history referencing deleted buckets."""
        problems: list[str] = []
        with self._lock:
            known = {r[0] for r in self._conn.execute("SELECT name FROM buckets")}
            # History references
            for r in self._conn.execute("SELECT DISTINCT bucket FROM history"):
                if r[0] not in known:
                    problems.append(f"history references missing bucket {r[0]!r}")
            # Orphaned bucket_* tables (escape _ so it isn't a wildcard).
            for r in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name LIKE 'bucket\\_%' ESCAPE '\\'"
            ):
                tname = r[0]
                bname = tname[len("bucket_") :]
                if bname not in known:
                    problems.append(f"orphaned table {tname}")
        return problems

    # ----------------------------------------------------------------- close
    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass


def _cosine(a: list[float], b: list[float]) -> float:
    import math

    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 1.0
    return 1.0 - dot / (na * nb)
