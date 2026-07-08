"""On-disk thumbnail cache keyed by SHA256 hash.

Stored as ``{cache_dir}/{hash[:2]}/{hash}.jpg`` to avoid large flat dirs.
"""

from __future__ import annotations

import os
from pathlib import Path


class ThumbnailCache:
    def __init__(self, cache_dir: str = "./thumb_cache/"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, hash_: str) -> Path:
        sub = self.cache_dir / hash_[:2]
        sub.mkdir(parents=True, exist_ok=True)
        return sub / f"{hash_}.jpg"

    def get(self, hash_: str) -> bytes | None:
        path = self._path(hash_)
        if path.exists():
            try:
                return path.read_bytes()
            except OSError:
                return None
        return None

    def put(self, hash_: str, data: bytes) -> bool:
        path = self._path(hash_)
        try:
            path.write_bytes(data)
            return True
        except OSError:
            return False

    def has(self, hash_: str) -> bool:
        return self._path(hash_).exists()

    def clear(self) -> int:
        count = 0
        for root, _dirs, files in os.walk(self.cache_dir):
            for name in files:
                try:
                    (Path(root) / name).unlink()
                    count += 1
                except OSError:
                    pass
        return count
