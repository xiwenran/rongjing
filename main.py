import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont
from ui.main_window import MainWindow

APP_NAME = "融景"

try:
    from _build_info import BUILD
except ImportError:
    BUILD = "dev"


def get_data_dir() -> str:
    """返回跨版本持久化的用户数据目录（更新 app 不会丢失数据）。"""
    if sys.platform == "darwin":
        return os.path.expanduser(f"~/Library/Application Support/{APP_NAME}")
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
        return os.path.join(appdata, APP_NAME)
    else:
        return os.path.expanduser(f"~/.{APP_NAME}")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    f = app.font(); f.setPointSize(13); app.setFont(f)

    templates_dir = os.path.join(get_data_dir(), "templates")
    os.makedirs(templates_dir, exist_ok=True)

    window = MainWindow(templates_dir=templates_dir, build=BUILD)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
