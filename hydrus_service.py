from typing import Iterable

import hydrus_api


class HydrusError(Exception):
    pass


class HydrusService:
    def __init__(self, api_url: str, api_key: str):
        key = api_key.strip() or None
        self.client = hydrus_api.Client(key, api_url)
        self.api_url = api_url
        self.api_key = api_key

    def configure(self, api_url: str, api_key: str) -> None:
        key = api_key.strip() or None
        self.client = hydrus_api.Client(key, api_url)
        self.api_url = api_url
        self.api_key = api_key

    def get_file_bytes(self, hash_: str) -> bytes:
        return self.client.get_file(hash_=hash_).content

    def get_thumbnail_bytes(self, hash_: str) -> bytes:
        return self.client.get_thumbnail(hash_=hash_).content

    def get_local_path(self, hash_: str) -> str | None:
        try:
            result = self.client.get_file_path(hash_=hash_)
        except Exception:
            return None
        return result.get("path") if isinstance(result, dict) else None

    def get_extensions(self, hashes: list[str]) -> dict[str, str]:
        if not hashes:
            return {}
        meta = self.client.get_file_metadata(hashes=hashes, only_return_basic_information=True)
        out: dict[str, str] = {}
        for entry in meta.get("metadata", []):
            h = entry.get("hash")
            ext = entry.get("ext", "")
            if h and ext:
                out[h] = ext.lstrip(".").lower()
        return out

    def archive(self, hashes: Iterable[str]) -> None:
        self.client.archive_files(hashes=list(hashes))

    def delete(self, hashes: Iterable[str]) -> None:
        self.client.delete_files(hashes=list(hashes))

    def add_tags(self, hashes: Iterable[str], service_key: str, tags: Iterable[str]) -> None:
        self.client.add_tags(
            hashes=list(hashes),
            service_keys_to_tags={service_key: list(tags)},
        )

    def set_rating(self, hashes: Iterable[str], rating_service_key: str, rating) -> None:
        self.client.set_rating(
            rating_service_key=rating_service_key,
            rating=rating,
            hashes=list(hashes),
        )

    def verify_access_key(self) -> dict[str, object]:
        return self.client.verify_access_key()

    def get_service(self, service_key: str) -> dict[str, object]:
        return self.client.get_service(service_key=service_key)
