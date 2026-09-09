import os
import uuid
import hashlib
import json
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication
from PIL import Image

from core.document_exporter import DocumentExportResult, DocumentExportSummary
from ui.collage_tab import CollageTab


def new_test_root(label: str) -> Path:
    path = Path(__file__).parent / "qa" / "logs" / f"m4_ui_{label}_{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    return path


def test_file_policy_and_source_naming(app):
    root = new_test_root("source")
    collages_dir = root / "collages"
    collages_dir.mkdir()
    image_dir = root / "课件图片"
    image_dir.mkdir()
    Image.new("RGB", (20, 20), "red").save(image_dir / "1.png")
    Image.new("RGB", (20, 20), "blue").save(image_dir / "._0.jpg")
    Image.new("RGB", (20, 20), "blue").save(image_dir / ".hidden.png")
    Image.new("RGB", (20, 20), "blue").save(image_dir / "~$temp.png")
    tab = CollageTab(collages_dir=str(collages_dir))
    tab._set_input_dir(str(image_dir))
    assert [Path(path).name for path in tab._image_files] == ["1.png"]
    assert tab._source_name == "课件图片"

    with mock.patch(
        "ui.collage_tab.QFileDialog.getOpenFileNames",
        return_value=([str(image_dir / "1.png"), str(image_dir / "._0.jpg")], ""),
    ):
        tab._choose_files()
    assert [Path(path).name for path in tab._image_files] == ["1.png"]
    assert tab._source_name == "课件图片"


def test_ppt_import_uses_document_export_service(app):
    calls = []

    def fake_service(**kwargs):
        calls.append(kwargs)
        result_dir = Path(kwargs["output_dir"]) / "current-run"
        result_dir.mkdir(parents=True)
        Image.new("RGB", (20, 20), "green").save(result_dir / "1.png")
        Image.new("RGB", (20, 20), "black").save(result_dir / "._0.jpg")
        Image.new("RGB", (20, 20), "black").save(result_dir / ".hidden.png")
        (result_dir / "sidecar.txt").write_text("ignore", encoding="utf-8")
        result = DocumentExportResult(
            source_file=kwargs["input_path"],
            success=True,
            output_dir=str(result_dir),
            pages_exported=1,
            backend_used="ppt_mac",
        )
        return DocumentExportSummary(1, 0, kwargs["output_dir"], results=[result])

    root = new_test_root("ppt")
    collages_dir = root / "collages"
    collages_dir.mkdir()
    pptx = root / "语文课件.pptx"
    pptx.write_bytes(b"mock")
    hidden_pptx = Path(root) / "._foo.pptx"
    hidden_pptx.write_bytes(b"mock")
    tab = CollageTab(
        collages_dir=str(collages_dir), document_export_service=fake_service
    )
    tab._start_ppt_imports([str(pptx), str(hidden_pptx)])
    worker = tab._ppt_import_worker
    assert worker is not None
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    worker.finished.connect(loop.quit)
    timer.timeout.connect(loop.quit)
    timer.start(3000)
    loop.exec()

    assert len(calls) == 1
    assert calls[0]["document_type"] == "ppt"
    assert calls[0]["input_path"] == str(pptx)
    assert [Path(path).name for path in tab._image_files] == ["1.png"]
    assert tab._source_name == "语文课件"


def test_multi_source_output_paths_are_frozen_and_unique(app):
    root = new_test_root("freeze")
    collages_dir = root / "collages"
    output_dir = root / "output"
    collages_dir.mkdir()
    output_dir.mkdir()
    first = root / "first.png"
    second = root / "second.png"
    Image.new("RGB", (20, 20), "red").save(first)
    Image.new("RGB", (20, 20), "blue").save(second)
    tab = CollageTab(collages_dir=str(collages_dir))
    tab._output_dir = str(output_dir)
    tab._subfolder_items = [("课程【A】", [str(first)]), ("课程【B】", [str(second)])]
    with mock.patch.object(tab, "_run_next_in_queue"):
        tab._run_batch_multi_folder()
    assert [Path(item[-1]).name for item in tab._batch_queue] == ["课程", "课程_2"]
    assert all(Path(item[-1]).is_dir() for item in tab._batch_queue)


def main():
    app = QApplication.instance() or QApplication([])
    test_file_policy_and_source_naming(app)
    test_ppt_import_uses_document_export_service(app)
    test_multi_source_output_paths_are_frozen_and_unique(app)
    root = new_test_root("e2e")
    collages_dir = str(root / "collages")
    input_dir = str(root / "input")
    output_dir = str(root / "output")
    for path in (collages_dir, input_dir, output_dir):
        os.makedirs(path)
    if True:
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
        old_dir = Path(output_dir) / "input"
        old_dir.mkdir()
        old_file = old_dir / "拼图_1.png"
        old_file.write_bytes(b"historical output")
        (old_dir / ".rongjing_collage_manifest.json").write_text(
            json.dumps({"files": ["拼图_1.png"]}), encoding="utf-8"
        )
        old_hash = hashlib.sha256(old_file.read_bytes()).hexdigest()
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
        assert Path(tab._collage_runner.output_dir).name == "input_2"
        assert hashlib.sha256(old_file.read_bytes()).hexdigest() == old_hash
        outputs = [
            os.path.join(root, name)
            for root, _dirs, names in os.walk(tab._collage_runner.output_dir)
            for name in names
            if name.endswith(".png")
        ]
        assert len(outputs) == 3, f"hero 布局应按 5 页一组生成 3 张，实际 {outputs}"
    print("All collage tab end-to-end tests passed.")


if __name__ == "__main__":
    main()
