import json
from typing import Sequence

from PySide6.QtCore import QThread, Signal

from clip_model import ClipModel
from database import Database, SUPPORTED_EXTS
from hydrus_service import HydrusService


class IngestWorker(QThread):
    progress = Signal(int, int, str)
    log = Signal(str)
    finished_ok = Signal(int)
    failed = Signal(str)

    def __init__(
        self,
        db: Database,
        hydrus: HydrusService,
        clip: ClipModel,
        bucket: str,
        hashes: list[str],
    ) -> None:
        super().__init__()
        self.db = db
        self.hydrus = hydrus
        self.clip = clip
        self.bucket = bucket
        self.hashes = hashes
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            if not self.clip.is_loaded:
                self.failed.emit("CLIP model is not loaded")
                return
            hashes = [h.strip() for h in self.hashes if h.strip()]
            total = len(hashes)
            if total == 0:
                self.failed.emit("no hashes provided")
                return
            self.progress.emit(0, total, "Fetching file metadata...")
            try:
                ext_map = self.hydrus.get_extensions(hashes)
            except Exception as exc:
                self.failed.emit(f"metadata fetch failed: {exc}")
                return

            processed = 0
            skipped = 0
            for idx, h in enumerate(hashes, start=1):
                if self._cancel:
                    self.log.emit("Ingest cancelled")
                    break
                ext = ext_map.get(h, "")
                if ext not in SUPPORTED_EXTS:
                    skipped += 1
                    self.progress.emit(idx, total, f"skipped {h[:12]}... (unsupported {ext or '?'})")
                    continue
                if self.db.has_hash(self.bucket, h):
                    skipped += 1
                    self.progress.emit(idx, total, f"skipped {h[:12]}... (already in bucket)")
                    continue
                try:
                    file_bytes = self.hydrus.get_file_bytes(h)
                except Exception as exc:
                    self.log.emit(f"failed to download {h[:12]}...: {exc}")
                    self.progress.emit(idx, total, f"download failed {h[:12]}...")
                    continue
                try:
                    embedding = self.clip.embed_bytes(file_bytes)
                except Exception as exc:
                    self.log.emit(f"failed to embed {h[:12]}...: {exc}")
                    self.progress.emit(idx, total, f"embed failed {h[:12]}...")
                    continue
                self.db.add_embedding(self.bucket, h, embedding)
                processed += 1
                self.progress.emit(idx, total, f"embedded {h[:12]}...")

            self.log.emit(
                f"Ingest complete: {processed} added, {skipped} skipped, {total} total"
            )
            self.finished_ok.emit(processed)
        except Exception as exc:
            self.failed.emit(str(exc))


class HydrusCheckWorker(QThread):
    ok = Signal(str)
    failed = Signal(str)

    def __init__(self, hydrus: HydrusService) -> None:
        super().__init__()
        self.hydrus = hydrus

    def run(self) -> None:
        try:
            info = self.hydrus.client.get_api_version()
            version = info.get("version", "?")
            self.ok.emit(f"Hydrus API reachable (API version {version}).")
        except Exception as exc:
            self.failed.emit(str(exc))


class SearchWorker(QThread):
    results = Signal(list)
    random_results = Signal(list)
    failed = Signal(str)

    def __init__(
        self,
        db: Database,
        hydrus: HydrusService,
        clip: ClipModel,
        bucket: str,
        query_hash: str | None,
        k: int,
    ) -> None:
        super().__init__()
        self.db = db
        self.hydrus = hydrus
        self.clip = clip
        self.bucket = bucket
        self.query_hash = query_hash
        self.k = k

    def run(self) -> None:
        try:
            if self.query_hash is None:
                hashes = self.db.random_sample(self.bucket, self.k)
                self.random_results.emit(hashes)
                return
            blob = self.db.get_embedding_blob(self.bucket, self.query_hash)
            if blob is None:
                if not self.clip.is_loaded:
                    self.failed.emit(
                        f"hash {self.query_hash[:12]}... is not in the bucket and the CLIP model is not loaded to re-embed it"
                    )
                    return
                try:
                    file_bytes = self.hydrus.get_file_bytes(self.query_hash)
                    embedding = self.clip.embed_bytes(file_bytes)
                except Exception as exc:
                    self.failed.emit(f"failed to embed query image: {exc}")
                    return
                blob = json.dumps(embedding)
            neighbors = self.db.nearest_neighbors(self.bucket, blob, self.k, exclude_hash=self.query_hash)
            self.results.emit(neighbors)
        except Exception as exc:
            self.failed.emit(str(exc))


class ModelLoadWorker(QThread):
    loaded = Signal(str, str)
    failed = Signal(str)

    def __init__(self, clip: ClipModel, model_name: str) -> None:
        super().__init__()
        self.clip = clip
        self.model_name = model_name

    def run(self) -> None:
        try:
            self.clip.load(self.model_name)
            self.loaded.emit(self.model_name, self.clip.device)
        except Exception as exc:
            self.failed.emit(str(exc))


class ThumbnailLoader(QThread):
    loaded = Signal(str, bytes)
    finished_all = Signal()

    def __init__(self, hydrus: HydrusService, hashes: Sequence[str]) -> None:
        super().__init__()
        self.hydrus = hydrus
        self.hashes = list(hashes)
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        for h in self.hashes:
            if self._cancel:
                break
            try:
                data = self.hydrus.get_thumbnail_bytes(h)
                self.loaded.emit(h, data)
            except Exception:
                pass
        self.finished_all.emit()
