import configparser
import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from config import DEFAULT_HYDRUS_URL, DEFAULTS
from ui.main_window import MainWindow

CONFIG_PATH = "hyclip_sifter.ini"
DB_PATH = "hyclip_sifter.db"


class FirstRunDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("HyCLIP Sifter — First-time setup")
        self.setMinimumWidth(420)

        outer = QVBoxLayout(self)
        outer.addWidget(QLabel(
            "No configuration file was found. Enter your Hydrus API "
            "settings to continue."
        ))

        form = QFormLayout()
        self.url_edit = QLineEdit(DEFAULT_HYDRUS_URL)
        self.key_edit = QLineEdit()
        self.key_edit.setPlaceholderText("Hydrus access key")
        self.tag_edit = QLineEdit()
        self.tag_edit.setPlaceholderText("Hydrus tag service key")
        form.addRow("API URL:", self.url_edit)
        form.addRow("API key:", self.key_edit)
        form.addRow("Tag service key:", self.tag_edit)
        outer.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _on_accept(self) -> None:
        if not self.url_edit.text().strip():
            QMessageBox.warning(self, "Missing value", "API URL is required.")
            return
        self.accept()

    def values(self) -> dict[str, str]:
        return {
            "api_url": self.url_edit.text().strip(),
            "api_key": self.key_edit.text().strip(),
            "tag_service_key": self.tag_edit.text().strip(),
        }

    @classmethod
    def prompt(cls, parent=None) -> dict[str, str] | None:
        dlg = cls(parent)
        if dlg.exec() == QDialog.Accepted:
            return dlg.values()
        return None


def write_initial_ini(path: str, values: dict[str, str]) -> None:
    parser = configparser.ConfigParser()
    for section, options in DEFAULTS.items():
        parser[section] = dict(options)
    parser.set("hydrus", "api_url", values.get("api_url", DEFAULT_HYDRUS_URL))
    parser.set("hydrus", "api_key", values.get("api_key", ""))
    parser.set("hydrus", "tag_service_key", values.get("tag_service_key", ""))
    Path(path).write_text("")
    with Path(path).open("w") as fh:
        parser.write(fh)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("HyCLIP Sifter")
    if not Path(CONFIG_PATH).exists():
        values = FirstRunDialog.prompt()
        if values is None:
            QMessageBox.information(
                None,
                "HyCLIP Sifter",
                "Hydrus configuration is required to use this app.",
            )
            return
        write_initial_ini(CONFIG_PATH, values)
    try:
        window = MainWindow(config_path=CONFIG_PATH, db_path=DB_PATH)
    except Exception:
        app.quit()
        return
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
