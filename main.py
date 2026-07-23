import sys
import os

# 仅源码运行时需要把仓库目录加进搜索路径；冻结 app 里这行会把 Frameworks
# （含 cv2 源码包目录）顶到 sys.path[0]，遮蔽 cv2 引导器插入的二进制目录，
# 触发 "recursion is detected during loading of cv2" 导致自动识别静默失效。
if not getattr(sys, "frozen", False):
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


def _detect_selftest(image_path: str) -> None:
    """无头自测：RONGJING_DETECT_SELFTEST=<图片路径> 时只跑角点识别并落盘结果，不进 GUI。"""
    from core.screen_detector import detect_green_screen_points, detect_screen_points
    # 与 GUI「自动识别」按钮同款路由：绿幕优先，失败退经典
    result = detect_green_screen_points(image_path) or detect_screen_points(image_path)
    out_path = os.path.join(get_data_dir(), "detect_selftest.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"image: {image_path}\nresult: {result}\n")
    sys.exit(0 if result else 1)


def main():
    selftest = os.environ.get("RONGJING_DETECT_SELFTEST")
    if selftest:
        _detect_selftest(selftest)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    f = app.font(); f.setPointSize(13); app.setFont(f)

    templates_dir = os.path.join(get_data_dir(), "templates")
    backgrounds_dir = os.path.join(get_data_dir(), "backgrounds")
    collages_dir = os.path.join(get_data_dir(), "collages")
    os.makedirs(templates_dir, exist_ok=True)
    os.makedirs(backgrounds_dir, exist_ok=True)
    os.makedirs(collages_dir, exist_ok=True)
    os.makedirs(os.path.join(get_data_dir(), "ppt_export"), exist_ok=True)

    window = MainWindow(
        templates_dir=templates_dir,
        backgrounds_dir=backgrounds_dir,
        collages_dir=collages_dir,
        build=BUILD,
    )
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
