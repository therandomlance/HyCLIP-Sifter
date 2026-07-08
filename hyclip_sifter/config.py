"""Configuration system backed by an INI file.

The config automatically backfills missing keys/sections with defaults so the
file can be hand-edited without breaking the application.
"""

from __future__ import annotations

import configparser
from pathlib import Path
from typing import Any

# (section, key, default-value-as-string)
_DEFAULTS: list[tuple[str, str, str]] = [
    ("hydrus", "api_url", "http://127.0.0.1:45869"),
    ("hydrus", "api_key", ""),
    ("hydrus", "tag_service_key", ""),
    ("hydrus", "rating_service_key", ""),
    ("hydrus", "retries", "3"),
    ("hydrus", "retry_delay_ms", "1000"),
    ("clip", "model", "ViT-B-16-SigLIP2"),
    ("clip", "load_on_startup", "false"),
    ("ui", "thumbnail_size", "400"),
    ("ui", "search_size", "50"),
    ("ui", "confirm_triage", "true"),
    ("ui", "theme", "system"),
    ("ui", "stylesheet", ""),
    ("ui", "thumbnail_cache_dir", "./thumb_cache/"),
    ("ui", "ingest_batch_size", "0"),
]

def _to_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    """Wrapper around :class:`configparser.ConfigParser` with typed accessors."""

    def __init__(self, path: str | Path = "hyclip_sifter.ini"):
        self.path = Path(path)
        self._parser = configparser.ConfigParser()
        # Backfill defaults so missing sections/keys never raise.
        for section, key, default in _DEFAULTS:
            if not self._parser.has_section(section):
                self._parser.add_section(section)
            if not self._parser.has_option(section, key):
                self._parser.set(section, key, default)
        if self.path.exists():
            self._parser.read(self.path, encoding="utf-8")
        # Re-backfill in case the on-disk file was missing keys.
        for section, key, default in _DEFAULTS:
            if not self._parser.has_section(section):
                self._parser.add_section(section)
            if not self._parser.has_option(section, key):
                self._parser.set(section, key, default)
        # Migrate old [ui] hydrus keys to [hydrus] section.
        self._migrate_old_keys()

    # --- typed accessors -------------------------------------------------
    def _migrate_old_keys(self) -> None:
        """Move hydrus retry settings from [ui] to [hydrus] section (one-time)."""
        moved = False
        for old_key, new_key in [("hydrus_retries", "retries"),
                                 ("hydrus_retry_delay_ms", "retry_delay_ms")]:
            if self._parser.has_option("ui", old_key):
                val = self._parser.get("ui", old_key)
                self._parser.set("hydrus", new_key, val)
                self._parser.remove_option("ui", old_key)
                moved = True
        if moved:
            self.save()

    def get(self, section: str, key: str, default: str | None = None) -> str:
        if self._parser.has_option(section, key):
            return self._parser.get(section, key)
        if default is not None:
            return default
        raise KeyError(f"{section}/{key}")

    def get_bool(self, section: str, key: str, default: bool = False) -> bool:
        try:
            return _to_bool(self.get(section, key))
        except KeyError:
            return default

    def get_int(self, section: str, key: str, default: int = 0) -> int:
        try:
            return int(self.get(section, key))
        except (KeyError, ValueError):
            return default

    # --- setters ---
    def set(self, section: str, key: str, value: Any) -> None:
        if not self._parser.has_section(section):
            self._parser.add_section(section)
        self._parser.set(section, key, str(value))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fh:
            self._parser.write(fh)

    @property
    def exists(self) -> bool:
        return self.path.exists()

    # --- convenience helpers --------------------------------------------
    @property
    def hydrus_api_url(self) -> str:
        return self.get("hydrus", "api_url")

    @property
    def hydrus_api_key(self) -> str:
        return self.get("hydrus", "api_key")

    @property
    def hydrus_tag_service_key(self) -> str:
        return self.get("hydrus", "tag_service_key")

    @property
    def clip_model(self) -> str:
        return self.get("clip", "model")

    @property
    def load_on_startup(self) -> bool:
        return self.get_bool("clip", "load_on_startup")

    @property
    def thumbnail_size(self) -> int:
        return self.get_int("ui", "thumbnail_size")

    @property
    def search_size(self) -> int:
        return self.get_int("ui", "search_size")

    @property
    def confirm_triage(self) -> bool:
        return self.get_bool("ui", "confirm_triage")

    @property
    def hydrus_retries(self) -> int:
        return self.get_int("hydrus", "retries")

    @property
    def hydrus_retry_delay_ms(self) -> int:
        return self.get_int("hydrus", "retry_delay_ms")

    @property
    def theme(self) -> str:
        return self.get("ui", "theme")

    @property
    def stylesheet(self) -> str:
        return self.get("ui", "stylesheet")

    @property
    def thumbnail_cache_dir(self) -> str:
        return self.get("ui", "thumbnail_cache_dir")

    @property
    def ingest_batch_size(self) -> int:
        return self.get_int("ui", "ingest_batch_size")
