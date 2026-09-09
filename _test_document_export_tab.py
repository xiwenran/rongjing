import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication

from core.document_exporter import DocumentExportResult, DocumentExportSummary
from ui.document_export_tab import DocumentExportTab
from ui.main_window import MainWindow


class DocumentExportTabTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_mode_routing_and_service_summary(self):
        calls = []

        def fake_service(**kwargs):
            calls.append(kwargs)
            result = DocumentExportResult(
                source_file=kwargs["input_path"],
                success=True,
                output_dir=str(Path(kwargs["output_dir"]) / "lesson"),
                pages_exported=3,
                backend_used="ppt_mac",
            )
            return DocumentExportSummary(
                success_count=1,
                failed_count=0,
                output_dir=kwargs["output_dir"],
                results=[result],
            )

        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "lesson.pptx"
            source.write_bytes(b"mock")
            output = Path(root) / "output"
            output.mkdir()
            tab = DocumentExportTab(
                export_service=fake_service,
                backend_detector=lambda mode: ["ppt_mac"] if mode == "ppt" else ["word_mac"],
            )
            self.assertEqual(tab.document_type, "ppt")
            self.assertEqual(tab._backend_combo.itemData(1), "ppt_mac")
            tab._set_document_type("word")
            self.assertEqual(tab.document_type, "word")
            self.assertEqual(tab._backend_combo.itemData(1), "word_mac")
            tab._set_document_type("ppt")
            tab._input_edit.setText(str(source))
            tab._output_edit.setText(str(output))
            tab._max_pages_spin.setValue(9)
            tab._backend_combo.setCurrentIndex(1)

            loop = QEventLoop()
            timer = QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(loop.quit)
            tab._start()
            tab._worker.finished.connect(loop.quit)
            timer.start(3000)
            loop.exec()

            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["document_type"], "ppt")
            self.assertEqual(calls[0]["max_pages"], 9)
            self.assertEqual(calls[0]["backend"], "ppt_mac")
            self.assertIn("共 3 页", tab._status_label.text())
            self.assertTrue(tab._start_btn.isEnabled())

    def test_directory_input_filters_hidden_and_temporary_documents(self):
        seen = []

        def fake_service(**kwargs):
            seen.append(Path(kwargs["input_path"]).name)
            result = DocumentExportResult(
                source_file=kwargs["input_path"],
                success=True,
                output_dir=kwargs["output_dir"],
                pages_exported=1,
            )
            return DocumentExportSummary(1, 0, kwargs["output_dir"], results=[result])

        with tempfile.TemporaryDirectory() as root:
            source_dir = Path(root) / "inputs"
            output_dir = Path(root) / "output"
            source_dir.mkdir()
            output_dir.mkdir()
            for name in ("lesson.pptx", "._0.pptx", ".hidden.pptx", "~$lesson.pptx"):
                (source_dir / name).write_bytes(b"mock")
            tab = DocumentExportTab(
                export_service=fake_service, backend_detector=lambda _mode: ["ppt_mac"]
            )
            tab._input_edit.setText(str(source_dir))
            tab._output_edit.setText(str(output_dir))

            loop = QEventLoop()
            timer = QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(loop.quit)
            tab._start()
            tab._worker.finished.connect(loop.quit)
            timer.start(3000)
            loop.exec()

            self.assertEqual(seen, ["lesson.pptx"])

    def test_main_window_registers_document_export_without_reordering_existing_pages(self):
        with tempfile.TemporaryDirectory() as root:
            templates = Path(root) / "templates"
            collages = Path(root) / "collages"
            backgrounds = Path(root) / "backgrounds"
            for directory in (templates, collages, backgrounds):
                directory.mkdir()
            window = MainWindow(
                str(templates),
                backgrounds_dir=str(backgrounds),
                collages_dir=str(collages),
            )
            self.assertEqual(
                [
                    window._page_indices[name]
                    for name in (
                        "editor",
                        "batch",
                        "document_export",
                        "collage",
                        "ai_generate",
                        "dewatermark",
                        "settings",
                    )
                ],
                list(range(7)),
            )
            self.assertIs(
                window.stack.widget(window._page_indices["document_export"]),
                window._document_export_tab,
            )


if __name__ == "__main__":
    unittest.main()
