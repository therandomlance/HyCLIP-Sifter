"""Application entry point: startup sequence + first-run dialog."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from .config import Config
from .database import Database
from .clip_model import ClipModel
from .hydrus_service import HydrusService
from .thumbnail_cache import ThumbnailCache
from .ui.main_window import MainWindow
from .ui.dialogs import FirstRunDialog
from .ui.theme import apply_theme


CONFIG_PATH = Path("hyclip_sifter.ini")
DB_PATH = Path("hyclip_sifter.db")


def _ensure_config() -> Config | None:
    """Return a :class:`Config`, running the first-run dialog if needed."""
    if not CONFIG_PATH.exists():
        # We need a QApplication for the dialog.
        QApplication.instance() or QApplication(sys.argv)
        dlg = FirstRunDialog()
        if dlg.exec() != FirstRunDialog.DialogCode.Accepted:
            return None
        values = dlg.values()
        cfg = Config(CONFIG_PATH)
        cfg.set("hydrus", "api_url", values["api_url"])
        cfg.set("hydrus", "api_key", values["api_key"])
        cfg.set("hydrus", "tag_service_key", values["tag_service_key"])
        cfg.save()
        return cfg
    return Config(CONFIG_PATH)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("HyCLIP Sifter")
    config = _ensure_config()
    if config is None:
        return 0
    try:
        db = Database(DB_PATH)
    except Exception as exc:
        QMessageBox.critical(None, "Database error", str(exc))
        return 1
    clip = ClipModel(config.clip_model)
    try:
        hydrus = HydrusService(
            config.hydrus_api_url,
            config.hydrus_api_key,
            retries=config.hydrus_retries,
            retry_delay_ms=config.hydrus_retry_delay_ms,
        )
    except Exception as exc:
        QMessageBox.critical(None, "Hydrus error", str(exc))
        return 1
    cache = ThumbnailCache(config.thumbnail_cache_dir)
    apply_theme(app, config.theme, config.stylesheet)
    win = MainWindow(config, db, clip, hydrus, cache)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
