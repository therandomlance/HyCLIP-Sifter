import sys

from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("HyCLIP Sifter")
    try:
        window = MainWindow()
    except Exception:
        app.quit()
        return
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
