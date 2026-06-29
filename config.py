import configparser
from pathlib import Path

DEFAULT_HYDRUS_URL = "http://127.0.0.1:45869"
DEFAULT_CLIP_MODEL = "ViT-B-16-SigLIP2"

DEFAULTS = {
    "hydrus": {
        "api_url": DEFAULT_HYDRUS_URL,
        "api_key": "",
        "tag_service_key": "",
        "rating_service_key": "",
    },
    "clip": {
        "model": DEFAULT_CLIP_MODEL,
    },
}


class Config:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.parser = configparser.ConfigParser()
        self._ensure_file()
        self.reload()

    def _ensure_file(self) -> None:
        if not self.path.exists():
            parser = configparser.ConfigParser()
            for section, options in DEFAULTS.items():
                parser[section] = options
            self.path.write_text("")
            with self.path.open("w") as fh:
                parser.write(fh)
            return
        # backfill missing keys
        parser = configparser.ConfigParser()
        parser.read(self.path)
        changed = False
        for section, options in DEFAULTS.items():
            if not parser.has_section(section):
                parser.add_section(section)
                changed = True
            for key, value in options.items():
                if not parser.has_option(section, key):
                    parser.set(section, key, value)
                    changed = True
        if changed:
            with self.path.open("w") as fh:
                parser.write(fh)

    def reload(self) -> None:
        self.parser = configparser.ConfigParser()
        self.parser.read(self.path)

    def get(self, section: str, key: str, fallback: str | None = None) -> str:
        return self.parser.get(section, key, fallback=fallback)

    def set(self, section: str, key: str, value: str) -> None:
        if not self.parser.has_section(section):
            self.parser.add_section(section)
        self.parser.set(section, key, value)
        with self.path.open("w") as fh:
            self.parser.write(fh)

    @property
    def hydrus_url(self) -> str:
        return self.get("hydrus", "api_url", DEFAULT_HYDRUS_URL).rstrip("/")

    @property
    def hydrus_key(self) -> str:
        return self.get("hydrus", "api_key", "")

    @property
    def tag_service_key(self) -> str:
        return self.get("hydrus", "tag_service_key", "")

    @property
    def rating_service_key(self) -> str:
        return self.get("hydrus", "rating_service_key", "")

    @property
    def clip_model(self) -> str:
        return self.get("clip", "model", DEFAULT_CLIP_MODEL)
