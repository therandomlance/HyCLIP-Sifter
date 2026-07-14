"""Background workers built on :class:`QThread`.

All workers extend ``QThread`` directly and emit Qt signals. Cancellation uses
a ``_cancel`` flag protected by :class:`threading.Lock`.
"""

from __future__ import annotations

import struct
import threading

from PySide6.QtCore import QThread, Signal, QObject

from .database import Database, OP_DELETE, OP_ARCHIVE, OP_SKIP, OP_DEFER
from .clip_model import ClipModel
from .hydrus_service import HydrusService


def _pack(values: list[float]) -> bytes:
    return struct.pack(f"<{len(values)}f", *values)


class _Cancellable(QThread):
    """Base class providing a thread-safe cancel flag."""

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._cancel_lock = threading.Lock()
        self._cancel = False

    def cancel(self) -> None:
        with self._cancel_lock:
            self._cancel = True

    @property
    def cancelled(self) -> bool:
        with self._cancel_lock:
            return self._cancel


# ============================================================================
# Model loading
# ============================================================================
class ModelLoadWorker(_Cancellable):
    download_progress = Signal(int, int)  # done_bytes, total_bytes
    loaded = Signal(str, str)  # name, device
    failed = Signal(str)

    def __init__(self, clip: ClipModel, parent: QObject | None = None):
        super().__init__(parent)
        self._clip = clip

    def run(self) -> None:  # pragma: no cover - exercised at runtime
        try:
            def _cb(done: int, total: int, message: str = "") -> None:
                if self.cancelled:
                    return
                self.download_progress.emit(done, total)
            self._clip.load(progress_callback=_cb)
            if self.cancelled:
                return
            name = self._clip.loaded_name or self._clip.model_name
            device = self._clip.device or "cpu"
            self.loaded.emit(name, device)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


# ============================================================================
# Hydrus connection check
# ============================================================================
class HydrusCheckWorker(_Cancellable):
    ok = Signal(str)   # status message
    failed = Signal(str)

    def __init__(self, hydrus: HydrusService, tag_service_key: str = "",
                 parent: QObject | None = None):
        super().__init__(parent)
        self._hydrus = hydrus
        self._tag_service_key = tag_service_key

    def run(self) -> None:
        if self.cancelled:
            return
        try:
            version = self._hydrus.get_api_version()
            if self.cancelled:
                return
            perms = self._hydrus.verify_access_key()
            if self.cancelled:
                return
            api_v = version.get("version", "?")
            perm_names: list[str] = []
            basic = perms.get("basic_permissions") or []
            label_map = {0: "manage", 1: "search", 2: "edit", 3: "import",
                         4: "add tags", 5: "delete"}
            for code in basic:
                label_map.get(code, str(code))
                perm_names.append(label_map.get(code, str(code)))
            parts = [f"API v{api_v}", "perms: " + (", ".join(perm_names) or "none")]
            if self._tag_service_key:
                if self.cancelled:
                    return
                try:
                    self._hydrus.get_service(self._tag_service_key)
                    parts.append("tag service OK")
                except Exception as exc:
                    self.failed.emit(f"Tag service key invalid: {exc}")
                    return
            if self.cancelled:
                return
            self.ok.emit(" — ".join(parts))
        except Exception as exc:  # noqa: BLE001
            if not self.cancelled:
                self.failed.emit(str(exc))


# ============================================================================
# Ingest
# ============================================================================
class IngestWorker(_Cancellable):
    progress = Signal(int, int, str)  # done, total, message
    log = Signal(str)
    finished_ok = Signal(int)  # count embedded
    failed = Signal(str)

    def __init__(
        self,
        db: Database,
        clip: ClipModel,
        hydrus: HydrusService,
        bucket: str,
        hashes: list[str],
        batch_size: int = 8,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._db = db
        self._clip = clip
        self._hydrus = hydrus
        self._bucket = bucket
        self._hashes = list(hashes)
        self._batch_size = max(1, batch_size)

    def run(self) -> None:
        try:
            hashes = list(self._hashes)
            total = len(hashes)
            done = 0
            embedded = 0
            self.progress.emit(0, total, f"Starting ingest of {total} hashes")
            # Fetch metadata/extensions.
            try:
                exts = self._hydrus.extensions_for(hashes)
            except Exception as exc:
                self.log.emit(f"metadata fetch failed: {exc}; proceeding without ext filter")
                exts = {}
            queue = []
            for h in hashes:
                ext = exts.get(h.lower())
                if ext and not self._hydrus.is_supported_ext(ext):
                    self.log.emit(f"skipped {h[:12]} (unsupported ext: {ext})")
                    done += 1
                    continue
                if self._db.has_hash(self._bucket, h):
                    self.log.emit(f"skipped {h[:12]} (already in bucket)")
                    done += 1
                    continue
                queue.append(h)
            self.progress.emit(done, total, f"{len(queue)} files to embed")
            # Micro-batches.
            i = 0
            while i < len(queue):
                if self.cancelled:
                    self.log.emit("ingest cancelled")
                    break
                batch = queue[i:i + self._batch_size]
                i += self._batch_size
                # Download bytes.
                raw: list[tuple[str, bytes]] = []
                for h in batch:
                    if self.cancelled:
                        break
                    try:
                        data = self._hydrus.get_file(h)
                        raw.append((h, data))
                    except Exception as exc:
                        self.log.emit(f"download failed {h[:12]}: {exc}")
                        done += 1
                        self.progress.emit(done, total, f"failed {h[:12]}")
                if not raw or self.cancelled:
                    continue
                # Embed.
                try:
                    vectors = self._clip.embed_bytes_batch([d for _, d in raw])
                except Exception as exc:
                    self.log.emit(f"embed failed: {exc}")
                    done += len(raw)
                    self.progress.emit(done, total, "batch embed failed")
                    continue
                if self.cancelled:
                    self.log.emit("ingest cancelled")
                    break
                # Store.
                items = [(h, v) for (h, _), v in zip(raw, vectors)]
                self._db.add_embeddings_batch(self._bucket, items)
                embedded += len(items)
                done += len(raw)
                self.progress.emit(done, total, f"{done}/{total} embedded")
            self.progress.emit(done, total, "done")
            self.finished_ok.emit(embedded)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


# ============================================================================
# Search
# ============================================================================
class SearchWorker(_Cancellable):
    results = Signal(list)             # [(hash, distance)]
    incremental_results = Signal(list) # batch
    random_results = Signal(list)      # [hash]
    failed = Signal(str)

    def __init__(
        self,
        db: Database,
        clip: ClipModel | None,
        bucket: str,
        k: int,
        query_hash: str | None = None,
        query_bytes: bytes | None = None,
        query_embedding: list[float] | None = None,
        text: str = "",
        text_weight: float = 1.5,
        text_negative: bool = False,
        negative_hash: str | None = None,
        multi_hashes: list[str] | None = None,
        random: bool = False,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._db = db
        self._clip = clip
        self._bucket = bucket
        self._k = k
        self._query_hash = query_hash
        self._query_bytes = query_bytes
        self._query_embedding = query_embedding
        self._text = text
        self._text_weight = text_weight
        self._text_negative = text_negative
        self._negative_hash = negative_hash
        self._multi = multi_hashes
        self._random = random

    def run(self) -> None:
        try:
            if self._random:
                hashes = self._db.random_sample(self._bucket, self._k)
                self.random_results.emit(hashes)
                return
            # Build query vector.
            vec: list[float] | None = None
            if self._multi:
                acc = None
                for h in self._multi:
                    e = self._db.get_embedding(self._bucket, h)
                    if e is None:
                        continue
                    if acc is None:
                        acc = list(e)
                    else:
                        for i, x in enumerate(e):
                            acc[i] += x
                if acc is not None:
                    n = len(acc)
                    vec = [x / n for x in acc]
            elif self._query_hash is not None:
                e = self._db.get_embedding(self._bucket, self._query_hash)
                if e is None:
                    # Try the cached embedding first (survives triage removal).
                    if self._query_embedding is not None:
                        e = self._query_embedding
                    elif self._clip is not None and self._clip.is_loaded:
                        if self._query_bytes is not None:
                            e = self._clip.embed_bytes(self._query_bytes)
                        else:
                            self.failed.emit(
                                "Query image not in bucket, no cached embedding, "
                                "and no raw bytes provided"
                            )
                            return
                    else:
                        self.failed.emit("Query image not in bucket and CLIP model not loaded")
                        return
                vec = list(e)
            elif self._query_bytes is not None and self._clip is not None and self._clip.is_loaded:
                vec = self._clip.embed_bytes(self._query_bytes)

            # Text refinement.
            if self._text and vec is not None and self._clip is not None and self._clip.is_loaded:
                te = self._clip.embed_text(self._text)
                if len(te) != len(vec):
                    self.failed.emit("Text and image embedding dimensions differ")
                    return
                sign = -1.0 if self._text_negative else 1.0
                vec = [v + sign * self._text_weight * t for v, t in zip(vec, te)]
            elif self._text and vec is None and self._clip is not None and self._clip.is_loaded:
                vec = self._clip.embed_text(self._text)
                if self._text_negative:
                    vec = [-self._text_weight * v for v in vec]

            if vec is None:
                self.failed.emit("No query provided (image, multi-image, or text)")
                return

            # Negative image query.
            if self._negative_hash is not None:
                neg = self._db.get_embedding(self._bucket, self._negative_hash)
                if neg is not None and len(neg) == len(vec):
                    vec = [v - n for v, n in zip(vec, neg)]

            blob = _pack(vec)
            exclude = self._query_hash
            results: list[tuple[str, float]] = []
            batch: list[tuple[str, float]] = []
            for h, d in self._db.nearest_neighbors_stream(self._bucket, blob, self._k, exclude):
                if self.cancelled:
                    return
                results.append((h, d))
                batch.append((h, d))
                if len(batch) >= 20:
                    self.incremental_results.emit(list(batch))
                    batch.clear()
            if batch:
                self.incremental_results.emit(list(batch))
            self.results.emit(results)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


# ============================================================================
# Thumbnail loading
# ============================================================================
class ThumbnailLoader(_Cancellable):
    loaded = Signal(str, bytes)
    finished_all = Signal()

    def __init__(
        self,
        hydrus: HydrusService,
        cache,
        hashes: list[str],
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._hydrus = hydrus
        self._cache = cache
        self._hashes = list(hashes)

    def run(self) -> None:
        for h in self._hashes:
            if self.cancelled:
                break
            data = self._cache.get(h) if self._cache else None
            if data is None:
                try:
                    data = self._hydrus.get_thumbnail(h)
                    if self._cache:
                        self._cache.put(h, data)
                except Exception:
                    continue
            if data:
                self.loaded.emit(h, data)
        self.finished_all.emit()


# ============================================================================
# Hydrus triage operations
# ============================================================================
class HydrusOperationWorker(_Cancellable):
    done = Signal(int, list)   # operation code, hashes
    failed = Signal(int, list, str)

    def __init__(
        self,
        db: Database,
        hydrus: HydrusService,
        bucket: str,
        operation: int,
        hashes: list[str],
        tag_service_key: str = "",
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._db = db
        self._hydrus = hydrus
        self._bucket = bucket
        self._operation = operation
        self._hashes = list(hashes)
        self._tag_service_key = tag_service_key

    def run(self) -> None:
        try:
            if self._operation == OP_DELETE:
                self._hydrus.delete_files(self._hashes)
            elif self._operation == OP_ARCHIVE:
                self._hydrus.archive_files(self._hashes)
            elif self._operation == OP_DEFER:
                if not self._tag_service_key:
                    self.failed.emit(self._operation, self._hashes,
                                     "No tag service key configured for Defer")
                    return
                self._hydrus.add_tags(self._hashes, self._tag_service_key, ["hyclip:defer"])
            elif self._operation == OP_SKIP:
                pass
            else:
                self.failed.emit(self._operation, self._hashes, "Unknown operation")
                return
            for h in self._hashes:
                self._db.remove_from_bucket(self._bucket, h, self._operation)
            self.done.emit(self._operation, self._hashes)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(self._operation, self._hashes, str(exc))


# ============================================================================
# Deduplication
# ============================================================================
class DedupWorker(_Cancellable):
    results = Signal(list)  # [(ha, hb, distance)]
    failed = Signal(str)

    def __init__(self, db: Database, bucket: str, threshold: float,
                 parent: QObject | None = None):
        super().__init__(parent)
        self._db = db
        self._bucket = bucket
        self._threshold = threshold

    def run(self) -> None:
        try:
            pairs = self._db.find_duplicates(self._bucket, self._threshold)
            self.results.emit(pairs)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


# ============================================================================
# Undelete (Hydrus only, no DB write)
# ============================================================================
class UndeleteWorker(_Cancellable):
    done = Signal(list)   # hashes
    failed = Signal(str)

    def __init__(self, hydrus: HydrusService, hashes: list[str],
                 parent: QObject | None = None):
        super().__init__(parent)
        self._hydrus = hydrus
        self._hashes = list(hashes)

    def run(self) -> None:
        try:
            if self.cancelled:
                return
            self._hydrus.undelete_files(self._hashes)
            self.done.emit(self._hashes)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
