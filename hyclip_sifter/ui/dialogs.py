"""Modal dialogs: first-run setup, preferences, prompts."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QLabel, QComboBox, QSpinBox, QCheckBox,
)


class FirstRunDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("HyCLIP Sifter — First Run Setup")
        self.setModal(True)
        form = QFormLayout(self)
        self.api_url = QLineEdit("http://127.0.0.1:45869")
        self.api_key = QLineEdit()
        self.api_key.setPlaceholderText("Hydrus Client API access key (optional)")
        self.tag_service_key = QLineEdit()
        self.tag_service_key.setPlaceholderText("Tag service key for Defer (optional)")
        form.addRow("Hydrus API URL:", self.api_url)
        form.addRow("Hydrus API access key:", self.api_key)
        form.addRow("Hydrus tag service key:", self.tag_service_key)
        note = QLabel(
            "Only the URL is required. You can leave the keys blank and "
            "configure them later via Preferences."
        )
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> dict:
        return {
            "api_url": self.api_url.text().strip(),
            "api_key": self.api_key.text().strip(),
            "tag_service_key": self.tag_service_key.text().strip(),
        }


class PreferencesDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self._config = config
        form = QFormLayout(self)
        self.api_url = QLineEdit(config.hydrus_api_url)
        self.api_key = QLineEdit(config.hydrus_api_key)
        self.tag_service_key = QLineEdit(config.hydrus_tag_service_key)
        self.rating_service_key = QLineEdit(config.get("hydrus", "rating_service_key"))
        self.model = QLineEdit(config.clip_model)
        self.load_on_startup = QCheckBox()
        self.load_on_startup.setChecked(config.load_on_startup)
        self.thumbnail_size = QSpinBox()
        self.thumbnail_size.setRange(48, 1000)
        self.thumbnail_size.setValue(config.thumbnail_size)
        self.search_size = QSpinBox()
        self.search_size.setRange(1, 2000)
        self.search_size.setValue(config.search_size)
        self.confirm_triage = QCheckBox()
        self.confirm_triage.setChecked(config.confirm_triage)
        self.retries = QSpinBox()
        self.retries.setRange(0, 20)
        self.retries.setValue(config.hydrus_retries)
        self.retry_delay = QSpinBox()
        self.retry_delay.setRange(0, 60000)
        self.retry_delay.setSuffix(" ms")
        self.retry_delay.setValue(config.hydrus_retry_delay_ms)
        self.theme = QComboBox()
        self.theme.addItems(["system", "dark", "light"])
        self.theme.setCurrentText(config.theme)
        self.stylesheet = QLineEdit(config.stylesheet)
        self.stylesheet.setPlaceholderText("Optional .qss file path")
        self.batch_size = QSpinBox()
        self.batch_size.setRange(0, 128)
        self.batch_size.setSpecialValueText("auto")
        self.batch_size.setValue(config.ingest_batch_size)
        self.cache_dir = QLineEdit(config.thumbnail_cache_dir)
        form.addRow("Hydrus API URL:", self.api_url)
        form.addRow("Hydrus API key:", self.api_key)
        form.addRow("Tag service key:", self.tag_service_key)
        form.addRow("Rating service key:", self.rating_service_key)
        form.addRow("CLIP model:", self.model)
        form.addRow("Load on startup:", self.load_on_startup)
        form.addRow("Thumbnail size:", self.thumbnail_size)
        form.addRow("Search result count:", self.search_size)
        form.addRow("Confirm triage:", self.confirm_triage)
        form.addRow("Hydrus retries:", self.retries)
        form.addRow("Hydrus retry delay:", self.retry_delay)
        form.addRow("Ingest batch size:", self.batch_size)
        form.addRow("Theme:", self.theme)
        form.addRow("Stylesheet:", self.stylesheet)
        form.addRow("Thumbnail cache dir:", self.cache_dir)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        # Widen the dialog by 50% over its natural sizeHint.
        hint = self.sizeHint()
        self.resize(int(hint.width() * 1.5), hint.height())

    def _apply(self) -> None:
        c = self._config
        c.set("hydrus", "api_url", self.api_url.text().strip())
        c.set("hydrus", "api_key", self.api_key.text().strip())
        c.set("hydrus", "tag_service_key", self.tag_service_key.text().strip())
        c.set("hydrus", "rating_service_key", self.rating_service_key.text().strip())
        c.set("clip", "model", self.model.text().strip())
        c.set("clip", "load_on_startup", self.load_on_startup.isChecked())
        c.set("ui", "thumbnail_size", self.thumbnail_size.value())
        c.set("ui", "search_size", self.search_size.value())
        c.set("ui", "confirm_triage", self.confirm_triage.isChecked())
        c.set("hydrus", "retries", self.retries.value())
        c.set("hydrus", "retry_delay_ms", self.retry_delay.value())
        c.set("ui", "theme", self.theme.currentText())
        c.set("ui", "stylesheet", self.stylesheet.text().strip())
        c.set("ui", "ingest_batch_size", self.batch_size.value())
        c.set("ui", "thumbnail_cache_dir", self.cache_dir.text().strip())
        c.save()
        self.accept()
