import json
from typing import Sequence

from PySide6.QtCore import QThread, Signal

from clip_model import ClipModel
from database import Database, SUPPORTED_EXTS
from hydrus_service import HydrusService

OP_DELETE = 0
OP_ARCHIVE = 1
OP_SKIP = 2
OP_DEFER = 3

DEFER_TAG = "hyclip:defer"


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

    def __init__(self, hydrus: HydrusService, tag_service_key: str = "", rating_service_key: str = "") -> None:
        super().__init__()
        self.hydrus = hydrus
        self.tag_service_key = tag_service_key
        self.rating_service_key = rating_service_key

    def run(self) -> None:
        try:
            info = self.hydrus.client.get_api_version()
            version = info.get("version", "?")
            messages = [f"Hydrus API reachable (API version {version})."]

            perm = self.hydrus.verify_access_key()
            basic_perm = perm.get("basic_permissions", [])
            if isinstance(basic_perm, list):
                perm_names = [str(p) for p in basic_perm]
                messages.append(f"Access key valid. Permissions: {', '.join(perm_names) if perm_names else 'none'}.")
            else:
                messages.append("Access key valid.")

            if self.tag_service_key:
                try:
                    svc = self.hydrus.get_service(self.tag_service_key)
                    name = svc.get("service", svc.get("name", "?"))
                    messages.append(f"Tag service '{name}' reachable.")
                except Exception as exc:
                    messages.append(f"Tag service key check failed: {exc}")

            if self.rating_service_key:
                try:
                    svc = self.hydrus.get_service(self.rating_service_key)
                    name = svc.get("service", svc.get("name", "?"))
                    messages.append(f"Rating service '{name}' reachable.")
                except Exception as exc:
                    messages.append(f"Rating service key check failed: {exc}")

            self.ok.emit("\n".join(messages))
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
        text: str = "",
        text_multiplier: float = 1.0,
        random: bool = False,
    ) -> None:
        super().__init__()
        self.db = db
        self.hydrus = hydrus
        self.clip = clip
        self.bucket = bucket
        self.query_hash = query_hash
        self.k = k
        self.text = text or ""
        self.text_multiplier = text_multiplier
        self.random = random
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            if self._cancel:
                return
            if self.random or (self.query_hash is None and not self.text):
                hashes = self.db.random_sample(self.bucket, self.k)
                if self._cancel:
                    return
                self.random_results.emit(hashes)
                return

            image_embedding: list[float] | None = None
            if self.query_hash is not None:
                emb = self.db.get_embedding(self.bucket, self.query_hash)
                if self._cancel:
                    return
                if emb is None:
                    if not self.clip.is_loaded:
                        self.failed.emit(
                            f"hash {self.query_hash[:12]}... is not in the bucket and the CLIP model is not loaded to re-embed it"
                        )
                        return
                    try:
                        file_bytes = self.hydrus.get_file_bytes(self.query_hash)
                        if self._cancel:
                            return
                        emb = self.clip.embed_bytes(file_bytes)
                    except Exception as exc:
                        self.failed.emit(f"failed to embed query image: {exc}")
                        return
                image_embedding = emb
            if self._cancel:
                return

            text_embedding: list[float] | None = None
            if self.text:
                if not self.clip.is_loaded:
                    self.failed.emit("text search requires the CLIP model to be loaded")
                    return
                try:
                    text_embedding = self.clip.embed_text(self.text)
                except Exception as exc:
                    self.failed.emit(f"failed to embed text query: {exc}")
                    return
                m = self.text_multiplier
                text_embedding = [v * m for v in text_embedding]
            if self._cancel:
                return

            if image_embedding is not None and text_embedding is not None:
                if len(image_embedding) != len(text_embedding):
                    self.failed.emit("image and text embedding dimensions differ")
                    return
                combined = [a + b for a, b in zip(image_embedding, text_embedding)]
                blob = json.dumps(combined)
            elif text_embedding is not None:
                blob = json.dumps(text_embedding)
            elif image_embedding is not None:
                blob = json.dumps(image_embedding)
            else:
                blob = None
            if self._cancel or blob is None:
                return
            neighbors = self.db.nearest_neighbors(self.bucket, blob, self.k, exclude_hash=self.query_hash)
            if self._cancel:
                return
            self.results.emit(neighbors)
        except Exception as exc:
            if not self._cancel:
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


class HydrusOperationWorker(QThread):
    done = Signal(int, list)
    failed = Signal(int, list, str)

    def __init__(
        self,
        hydrus: HydrusService,
        operation: int,
        hashes: list[str],
        tag_service_key: str = "",
    ) -> None:
        super().__init__()
        self.hydrus = hydrus
        self.operation = operation
        self.hashes = list(hashes)
        self.tag_service_key = tag_service_key
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            if self.operation == OP_ARCHIVE:
                self.hydrus.archive(self.hashes)
            elif self.operation == OP_DELETE:
                self.hydrus.delete(self.hashes)
            elif self.operation == OP_DEFER:
                if not self.tag_service_key:
                    raise ValueError("no tag service key configured")
                self.hydrus.add_tags(self.hashes, self.tag_service_key, [DEFER_TAG])
            elif self.operation == OP_SKIP:
                pass
            else:
                raise ValueError(f"unknown operation: {self.operation}")
            if not self._cancel:
                self.done.emit(self.operation, self.hashes)
        except Exception as exc:
            if not self._cancel:
                self.failed.emit(self.operation, self.hashes, str(exc))
