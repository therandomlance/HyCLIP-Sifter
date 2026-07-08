"""Hydrus API client wrapper with retry logic and helpers."""

from __future__ import annotations

import time
from typing import Iterable

import requests
from hydrus_api import Client, HydrusAPIException, ServerError, ConnectionError

SUPPORTED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "tiff", "tif"}


class HydrusError(Exception):
    """Raised when an API call fails after all retries."""


class HydrusService:
    """Wraps :class:`hydrus_api.Client` with configurable retries."""

    def __init__(
        self,
        api_url: str,
        api_key: str = "",
        retries: int = 3,
        retry_delay_ms: int = 1000,
        timeout: float = 30.0,
    ):
        # hydrus-api wants a URL ending in '/'.
        base = api_url.rstrip("/") + "/"
        self.api_url = base
        self.api_key = api_key
        self.retries = max(0, retries)
        self.retry_delay = max(0.0, retry_delay_ms) / 1000.0
        self.timeout = max(0.0, timeout)
        self._client: Client | None = None
        self._reconnect()

    def _reconnect(self) -> None:
        try:
            self._client = Client(
                access_key=self.api_key or None,
                api_url=self.api_url,
                timeout=self.timeout,
            )
        except TypeError:
            # Older hydrus-api versions don't accept a timeout kwarg.
            try:
                self._client = Client(access_key=self.api_key or None, api_url=self.api_url)
            except Exception as exc:  # noqa: BLE001
                raise HydrusError(f"Cannot create Hydrus client: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise HydrusError(f"Cannot create Hydrus client: {exc}") from exc

    @property
    def client(self) -> Client:
        if self._client is None:
            self._reconnect()
        return self._client  # type: ignore[return-value]

    def _reconnect_on_failure(self, exc: Exception) -> bool:
        """Re-create the client on connection-class failures. Returns True if reconnected."""
        if isinstance(exc, (ConnectionError, requests.ConnectionError, requests.Timeout)):
            try:
                self._reconnect()
                return True
            except HydrusError:
                return False
        return False

    # ------------------------------------------------------------- retry core
    def _call(self, fn, /, *args, **kwargs):
        """Invoke a Client method with retry on transient failures."""
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                return fn(*args, **kwargs)
            except (ServerError, ConnectionError, requests.ConnectionError,
                    requests.Timeout) as exc:
                last_exc = exc
                if attempt < self.retries:
                    self._reconnect_on_failure(exc)
                    time.sleep(self.retry_delay)
                    continue
                raise HydrusError(f"Hydrus API call failed after {attempt + 1} attempts: {exc}") from exc
            except HydrusAPIException as exc:
                # Auth/4xx — do not retry.
                raise HydrusError(str(exc)) from exc
        if last_exc is not None:
            raise HydrusError(str(last_exc))

    # ------------------------------------------------------------- API calls
    def get_file(self, hash_: str) -> bytes:
        resp = self._call(self.client.get_file, hash_=hash_)
        return resp.content

    def get_thumbnail(self, hash_: str) -> bytes:
        resp = self._call(self.client.get_thumbnail, hash_=hash_)
        return resp.content

    def get_file_path(self, hash_: str) -> str | None:
        try:
            data = self._call(self.client.get_file_path, hash_=hash_)
        except HydrusError:
            return None
        return data.get("path")

    def get_file_metadata(self, hashes: Iterable[str]) -> dict:
        hashes = list(hashes)
        if not hashes:
            return {}
        return self._call(self.client.get_file_metadata, hashes=hashes)

    def extensions_for(self, hashes: Iterable[str]) -> dict[str, str]:
        """Return ``{hash: extension}`` for the given hashes."""
        hashes = list(hashes)
        meta = self.get_file_metadata(hashes)
        out: dict[str, str] = {}
        by_hash = meta.get("metadata") or []
        for entry in by_hash:
            h = entry.get("hash")
            ext = entry.get("ext")
            if ext is None:
                ext = entry.get("filetype")
            if isinstance(ext, str) and ext.startswith("."):
                ext = ext[1:]
            if h and ext:
                out[h.lower()] = str(ext).lower()
        return out

    def archive_files(self, hashes: Iterable[str]) -> None:
        self._call(self.client.archive_files, hashes=list(hashes))

    def delete_files(self, hashes: Iterable[str]) -> None:
        self._call(self.client.delete_files, hashes=list(hashes))

    def undelete_files(self, hashes: Iterable[str]) -> None:
        self._call(self.client.undelete_files, hashes=list(hashes))

    def add_tags(self, hashes: Iterable[str], service_key: str, tags: Iterable[str]) -> None:
        hashes = list(hashes)
        if not service_key:
            raise HydrusError("No tag service key configured for Defer operation")
        self._call(
            self.client.add_tags,
            hashes=hashes,
            service_keys_to_tags={service_key: list(tags)},
        )

    def verify_access_key(self) -> dict:
        return self._call(self.client.verify_access_key)

    def get_service(self, service_key: str) -> dict:
        return self._call(self.client.get_service, service_key=service_key)

    def get_api_version(self) -> dict:
        return self._call(self.client.get_api_version)

    def search_files(self, tags: Iterable[str]) -> list[str]:
        result = self._call(
            self.client.search_files, tags=list(tags), return_hashes=True
        )
        if not isinstance(result, dict):
            return []
        return list(result.get("hashes") or [])

    @staticmethod
    def is_supported_ext(ext: str) -> bool:
        return (ext or "").lower().lstrip(".") in SUPPORTED_EXTENSIONS
