import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication
from PIL import Image

from ui.collage_tab import CollageTab


def main():
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as collages_dir, \
         tempfile.TemporaryDirectory() as input_dir, \
         tempfile.TemporaryDirectory() as output_dir:
        for i in range(1, 13):
            img = Image.new("RGB", (200, 150), color=((i * 20) % 256, 100, 200))
            img.save(os.path.join(input_dir, f"{i:02d}.png"))
        tab = CollageTab(collages_dir=collages_dir)
        cfg = tab.get_current_config()
        assert cfg.rows == 3 and cfg.cols == 4
        tab._layout_combo.setCurrentIndex(tab._layout_combo.findData("hero"))
        cfg = tab.get_current_config()
        assert cfg.layout == "hero" and cfg.rows == 2 and cfg.cols == 2 and cfg.total_cells == 5
        tab._set_input_dir(input_dir)
        assert len(tab._image_files) == 12
        tab._toggle_excluded(2)
        assert 2 in tab._excluded_indices
        tab._output_dir = output_dir
        tab._output_path_label.setText(output_dir)
        tab._format_combo.setCurrentText("PNG")
        finished_args = []

        def on_finished(success, msg):
            finished_args.append((success, msg))
            QApplication.instance().quit()

        tab._run_collage_batch(callback=on_finished)
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(QApplication.instance().quit)
        timer.start(30000)
        QApplication.instance().exec()
        assert len(finished_args) == 1, f"finished 应触发 1 次，实际 {len(finished_args)}"
        assert finished_args[0][0] is True, f"finished 应为 success=True，实际 {finished_args[0]}"
        outputs = [f for f in os.listdir(output_dir) if f.endswith(".png")]
        assert len(outputs) == 3, f"hero 布局应按 5 页一组生成 3 张，实际 {outputs}"
    print("All collage tab end-to-end tests passed.")


if __name__ == "__main__":
    main()
