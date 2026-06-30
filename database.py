import json
import re
import sqlite3
import struct
import threading
from pathlib import Path

import sqlite_vector

_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
SUPPORTED_EXTS = {"png", "jpg", "jpeg", "webp", "tiff", "tif"}

_EXTENSION_PATH = str(Path(sqlite_vector.__file__).parent / "binaries" / "vector")


def valid_bucket_name(name: str) -> bool:
    return bool(name) and " " not in name and bool(_NAME_RE.match(name))


def table_name(bucket: str) -> str:
    return f"bucket_{bucket}"


class Database:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.enable_load_extension(True)
        except Exception:
            pass
        try:
            self.conn.load_extension(_EXTENSION_PATH)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load the sqlite-vector extension "
                f"({_EXTENSION_PATH}): {exc}. Ensure the native library "
                "is present and compatible with this system."
            ) from exc
        self._init_base_tables()
        self._quantized: set[str] = set()
        self._dirty: set[str] = set()
        self._init_all_vectors()

    def _init_base_tables(self) -> None:
        with self._lock:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS buckets (
                    name TEXT PRIMARY KEY,
                    dimension INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS history (
                    hash TEXT NOT NULL,
                    bucket TEXT NOT NULL,
                    operation INTEGER NOT NULL
                );
                """
            )
            self.conn.commit()

    def _init_all_vectors(self) -> None:
        with self._lock:
            rows = self.conn.execute("SELECT name, dimension FROM buckets").fetchall()
            for row in rows:
                self._vector_init_locked(row["name"], row["dimension"])

    def _vector_init_locked(self, bucket: str, dimension: int) -> None:
        table = table_name(bucket)
        opts = f"dimension={dimension},type=FLOAT32,distance=cosine"
        self.conn.execute("SELECT vector_init(?, 'embedding', ?)", (table, opts))

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def create_bucket(self, name: str, dimension: int) -> None:
        if not valid_bucket_name(name):
            raise ValueError("bucket name must be non-empty, contain no spaces, and use only letters, digits, _ or -")
        with self._lock:
            table = table_name(name)
            self.conn.execute(
                f"CREATE TABLE IF NOT EXISTS {table} (hash TEXT PRIMARY KEY, embedding BLOB)"
            )
            self.conn.execute(
                "INSERT OR REPLACE INTO buckets(name, dimension) VALUES (?, ?)", (name, dimension)
            )
            self._vector_init_locked(name, dimension)
            self.conn.commit()

    def delete_bucket(self, name: str) -> None:
        if not valid_bucket_name(name):
            return
        with self._lock:
            table = table_name(name)
            try:
                self.conn.execute("SELECT vector_quantize_cleanup(?, 'embedding')", (table,))
            except Exception:
                pass
            self.conn.execute(f"DROP TABLE IF EXISTS {table}")
            self.conn.execute("DELETE FROM buckets WHERE name = ?", (name,))
            self._quantized.discard(name)
            self._dirty.discard(name)
            self.conn.commit()

    def list_buckets(self) -> list[str]:
        with self._lock:
            rows = self.conn.execute("SELECT name FROM buckets ORDER BY name").fetchall()
            return [r["name"] for r in rows]

    def bucket_dimension(self, name: str) -> int | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT dimension FROM buckets WHERE name = ?", (name,)
            ).fetchone()
            return row["dimension"] if row else None

    def bucket_count(self, name: str) -> int:
        if not valid_bucket_name(name):
            return 0
        with self._lock:
            row = self.conn.execute(f"SELECT COUNT(*) AS c FROM {table_name(name)}").fetchone()
            return row["c"]

    def add_embedding(self, bucket: str, hash_: str, embedding: list[float]) -> None:
        with self._lock:
            table = table_name(bucket)
            json_vec = json.dumps(embedding)
            self.conn.execute(
                f"INSERT OR REPLACE INTO {table}(hash, embedding) VALUES (?, vector_as_f32(?))",
                (hash_, json_vec),
            )
            self.conn.commit()
            self._dirty.add(bucket)

    def has_hash(self, bucket: str, hash_: str) -> bool:
        with self._lock:
            table = table_name(bucket)
            row = self.conn.execute(
                f"SELECT 1 FROM {table} WHERE hash = ?", (hash_,)
            ).fetchone()
            return row is not None

    def get_embedding_blob(self, bucket: str, hash_: str) -> bytes | None:
        with self._lock:
            table = table_name(bucket)
            row = self.conn.execute(
                f"SELECT embedding FROM {table} WHERE hash = ?", (hash_,)
            ).fetchone()
            return bytes(row["embedding"]) if row else None

    def get_embedding(self, bucket: str, hash_: str) -> list[float] | None:
        blob = self.get_embedding_blob(bucket, hash_)
        if blob is None:
            return None
        dim = self.bucket_dimension(bucket)
        if not dim:
            return None
        return list(struct.unpack(f"<{dim}f", blob))

    def _prepare_search_locked(self, bucket: str) -> None:
        if bucket in self._dirty or bucket not in self._quantized:
            table = table_name(bucket)
            self.conn.execute("SELECT vector_quantize(?, 'embedding')", (table,))
            try:
                self.conn.execute("SELECT vector_quantize_preload(?, 'embedding')", (table,))
            except Exception:
                pass
            self._quantized.add(bucket)
            self._dirty.discard(bucket)

    def nearest_neighbors(
        self,
        bucket: str,
        query_blob: bytes,
        k: int,
        exclude_hash: str | None = None,
    ) -> list[tuple[str, float]]:
        with self._lock:
            self._prepare_search_locked(bucket)
            table = table_name(bucket)
            rows = self.conn.execute(
                f"SELECT hash, distance "
                f"FROM vector_quantize_scan(?, 'embedding', ?, ?) AS v "
                f"JOIN {table} ON {table}.rowid = v.rowid "
                f"ORDER BY v.distance",
                (table, query_blob, k + (1 if exclude_hash else 0)),
            ).fetchall()
            results = [(r["hash"], r["distance"]) for r in rows]
            if exclude_hash:
                results = [(h, d) for h, d in results if h != exclude_hash]
            return results[:k]

    def random_sample(self, bucket: str, k: int) -> list[str]:
        with self._lock:
            table = table_name(bucket)
            rows = self.conn.execute(
                f"SELECT hash FROM {table} ORDER BY RANDOM() LIMIT ?", (k,)
            ).fetchall()
            return [r["hash"] for r in rows]

    def remove_from_bucket(self, bucket: str, hash_: str, operation: int) -> None:
        with self._lock:
            table = table_name(bucket)
            self.conn.execute(f"DELETE FROM {table} WHERE hash = ?", (hash_,))
            self.conn.execute(
                "INSERT INTO history(hash, bucket, operation) VALUES (?, ?, ?)",
                (hash_, bucket, operation),
            )
            self.conn.commit()
            self._dirty.add(bucket)

    def history_query(self, bucket: str, operation: int, limit: int) -> list[str]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT hash FROM history WHERE bucket = ? AND operation = ? "
                "ORDER BY rowid DESC LIMIT ?",
                (bucket, operation, limit),
            ).fetchall()
            return [r["hash"] for r in rows]

    def history_buckets(self) -> list[tuple[str, int]]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT bucket, COUNT(*) AS c FROM history GROUP BY bucket ORDER BY bucket"
            ).fetchall()
            return [(r["bucket"], r["c"]) for r in rows]

    def history_counts(self, bucket: str) -> dict[int, int]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT operation, COUNT(*) AS c FROM history WHERE bucket = ? GROUP BY operation",
                (bucket,),
            ).fetchall()
            return {r["operation"]: r["c"] for r in rows}
