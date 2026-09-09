"""PPT / Word 资料导出页面。"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QSettings, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.document_exporter import (
    DocumentExportSummary,
    backend_display_name,
    collect_document_files,
    detect_backends,
    export_material,
)


_GREEN = "#07C160"
_CARD = "#FFFFFF"
_INPUT = "#F0F0F0"
_TEXT = "#191919"
_TEXT2 = "#888888"


class _DocumentExportWorker(QThread):
    progress = pyqtSignal(int, int, str)
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)
    canceled = pyqtSignal()

    def __init__(
        self,
        *,
        document_type: str,
        input_path: str,
        output_dir: str,
        max_pages: int,
        backend: str | None,
        service: Callable = export_material,
        parent=None,
    ):
        super().__init__(parent)
        self._document_type = document_type
        self._input_path = input_path
        self._output_dir = output_dir
        self._max_pages = max_pages
        self._backend = backend
        self._service = service

    def run(self):
        try:
            sources = collect_document_files(self._input_path, self._document_type)
            results = []
            failed_files = []
            total = len(sources)
            for index, source in enumerate(sources, 1):
                if self.isInterruptionRequested():
                    self.canceled.emit()
                    return
                self.progress.emit(index - 1, total, f"正在导出：{source.name}")
                summary = self._service(
                    document_type=self._document_type,
                    input_path=str(source),
                    output_dir=self._output_dir,
                    max_pages=self._max_pages,
                    backend=self._backend,
                )
                results.extend(summary.results)
                failed_files.extend(summary.failed_files)
                self.progress.emit(index, total, f"已处理：{source.name}")
                if self.isInterruptionRequested():
                    self.canceled.emit()
                    return
            summary = DocumentExportSummary(
                success_count=sum(result.success for result in results),
                failed_count=len(failed_files),
                skipped_count=0,
                output_dir=self._output_dir,
                failed_files=failed_files,
                results=results,
            )
            self.completed.emit(summary)
        except Exception as exc:
            self.failed.emit(str(exc))


class DocumentExportTab(QWidget):
    """独立资料导出页；转换逻辑全部交给 core.document_exporter。"""

    def __init__(
        self,
        parent=None,
        *,
        export_service: Callable = export_material,
        backend_detector: Callable = detect_backends,
    ):
        super().__init__(parent)
        self._export_service = export_service
        self._backend_detector = backend_detector
        self._worker: _DocumentExportWorker | None = None
        self._settings = QSettings("融景", "RongJing")
        self.setObjectName("DocumentExportTab")
        self._build_ui()
        self._set_document_type("ppt")

    @property
    def document_type(self) -> str:
        return "ppt" if self._ppt_btn.isChecked() else "word"

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(36, 28, 36, 28)
        root.setSpacing(16)

        title = QLabel("资料导出")
        title.setObjectName("title")
        hint = QLabel("将 PPT 或 Word 资料按页导出为 PNG 图片。")
        hint.setObjectName("hint")
        root.addWidget(title)
        root.addWidget(hint)

        card = QWidget()
        card.setObjectName("card")
        form = QFormLayout(card)
        form.setContentsMargins(24, 22, 24, 22)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(14)

        mode_row = QHBoxLayout()
        self._ppt_btn = QPushButton("PPT")
        self._word_btn = QPushButton("Word")
        self._mode_group = QButtonGroup(self)
        for button, mode in ((self._ppt_btn, "ppt"), (self._word_btn, "word")):
            button.setCheckable(True)
            button.setObjectName("modeBtn")
            button.clicked.connect(lambda _checked, value=mode: self._set_document_type(value))
            self._mode_group.addButton(button)
            mode_row.addWidget(button)
        mode_row.addStretch(1)
        form.addRow("资料类型", mode_row)

        input_row = QHBoxLayout()
        self._input_edit = QLineEdit()
        self._input_edit.setPlaceholderText("选择单个文件或包含资料的文件夹")
        self._file_btn = QPushButton("选择文件…")
        self._folder_btn = QPushButton("选择文件夹…")
        self._file_btn.clicked.connect(self._choose_file)
        self._folder_btn.clicked.connect(self._choose_folder)
        input_row.addWidget(self._input_edit, 1)
        input_row.addWidget(self._file_btn)
        input_row.addWidget(self._folder_btn)
        form.addRow("输入", input_row)

        output_row = QHBoxLayout()
        self._output_edit = QLineEdit()
        self._output_edit.setPlaceholderText("选择图片输出目录")
        self._output_btn = QPushButton("选择目录…")
        self._output_btn.clicked.connect(self._choose_output)
        output_row.addWidget(self._output_edit, 1)
        output_row.addWidget(self._output_btn)
        form.addRow("输出", output_row)

        self._max_pages_spin = QSpinBox()
        self._max_pages_spin.setRange(1, 9999)
        self._max_pages_spin.setValue(17)
        self._max_pages_spin.setSuffix(" 页/文件")
        form.addRow("最多导出", self._max_pages_spin)

        self._backend_combo = QComboBox()
        form.addRow("转换后端", self._backend_combo)
        root.addWidget(card)

        self._progress = QProgressBar()
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._status_label = QLabel("尚未开始")
        self._status_label.setObjectName("hint")
        root.addWidget(self._progress)
        root.addWidget(self._status_label)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._cancel)
        self._start_btn = QPushButton("开始导出")
        self._start_btn.setObjectName("primary")
        self._start_btn.clicked.connect(self._start)
        action_row.addWidget(self._cancel_btn)
        action_row.addWidget(self._start_btn)
        root.addLayout(action_row)
        root.addStretch(1)

        self.setStyleSheet(f"""
            QWidget#DocumentExportTab {{ background: #F7F7F7; color: {_TEXT}; }}
            QWidget#DocumentExportTab QLabel#title {{ font-size: 24px; font-weight: 700; }}
            QWidget#DocumentExportTab QLabel#hint {{ color: {_TEXT2}; }}
            QWidget#DocumentExportTab QWidget#card {{ background: {_CARD}; border-radius: 12px; }}
            QWidget#DocumentExportTab QLineEdit,
            QWidget#DocumentExportTab QComboBox,
            QWidget#DocumentExportTab QSpinBox {{ background: {_INPUT}; border: none; border-radius: 8px; padding: 8px 10px; }}
            QWidget#DocumentExportTab QPushButton {{ border: none; border-radius: 8px; padding: 9px 14px; background: {_INPUT}; }}
            QWidget#DocumentExportTab QPushButton#primary {{ background: {_GREEN}; color: white; font-weight: 600; }}
            QWidget#DocumentExportTab QPushButton#modeBtn:checked {{ background: {_GREEN}; color: white; font-weight: 600; }}
            QWidget#DocumentExportTab QProgressBar {{ border: none; border-radius: 5px; background: {_INPUT}; text-align: center; }}
            QWidget#DocumentExportTab QProgressBar::chunk {{ background: {_GREEN}; border-radius: 5px; }}
        """)

    def _set_document_type(self, document_type: str):
        self._ppt_btn.setChecked(document_type == "ppt")
        self._word_btn.setChecked(document_type == "word")
        self._refresh_backends()

    def _refresh_backends(self):
        self._backend_combo.clear()
        self._backend_combo.addItem("自动选择", "auto")
        try:
            available = self._backend_detector(self.document_type)
        except Exception:
            available = []
        for backend in available:
            self._backend_combo.addItem(backend_display_name(backend), backend)

    def _choose_file(self):
        mode = self.document_type
        file_filter = "PPT (*.ppt *.pptx)" if mode == "ppt" else "Word (*.doc *.docx)"
        start = self._settings.value("document_export/last_input", "", type=str)
        path, _ = QFileDialog.getOpenFileName(self, "选择资料文件", start, file_filter)
        if path:
            self._input_edit.setText(path)
            self._settings.setValue("document_export/last_input", str(Path(path).parent))

    def _choose_folder(self):
        start = self._settings.value("document_export/last_input", "", type=str)
        path = QFileDialog.getExistingDirectory(self, "选择资料文件夹", start)
        if path:
            self._input_edit.setText(path)
            self._settings.setValue("document_export/last_input", path)

    def _choose_output(self):
        start = self._output_edit.text().strip() or self._settings.value(
            "document_export/last_output", "", type=str
        )
        path = QFileDialog.getExistingDirectory(self, "选择输出文件夹", start)
        if path:
            self._output_edit.setText(path)
            self._settings.setValue("document_export/last_output", path)

    def _start(self):
        input_path = self._input_edit.text().strip()
        output_dir = self._output_edit.text().strip()
        if not input_path or not output_dir:
            QMessageBox.warning(self, "提示", "请选择输入文件/文件夹和输出目录。")
            return
        try:
            sources = collect_document_files(input_path, self.document_type)
        except Exception as exc:
            QMessageBox.warning(self, "输入不可用", str(exc))
            return
        if not sources:
            QMessageBox.warning(self, "输入不可用", "没有找到可处理的资料文件。")
            return

        backend = self._backend_combo.currentData()
        self._worker = _DocumentExportWorker(
            document_type=self.document_type,
            input_path=input_path,
            output_dir=output_dir,
            max_pages=self._max_pages_spin.value(),
            backend=None if backend == "auto" else backend,
            service=self._export_service,
            parent=self,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.completed.connect(self._on_completed)
        self._worker.failed.connect(self._on_failed)
        self._worker.canceled.connect(self._on_canceled)
        self._worker.finished.connect(self._worker.deleteLater)
        self._set_running(True)
        self._progress.setRange(0, len(sources))
        self._progress.setValue(0)
        self._status_label.setText(f"准备导出 {len(sources)} 个文件…")
        self._worker.start()

    def _cancel(self):
        if self._worker and self._worker.isRunning():
            self._worker.requestInterruption()
            self._cancel_btn.setEnabled(False)
            self._status_label.setText("已请求取消，当前文件完成后停止。")

    def _on_progress(self, done: int, total: int, message: str):
        self._progress.setRange(0, max(1, total))
        self._progress.setValue(done)
        self._status_label.setText(message)

    def _on_completed(self, summary: DocumentExportSummary):
        pages = sum(result.pages_exported for result in summary.results if result.success)
        self._status_label.setText(
            f"导出完成：成功 {summary.success_count} 个，失败 {summary.failed_count} 个，共 {pages} 页。"
        )
        self._set_running(False)
        self._worker = None

    def _on_failed(self, error: str):
        self._status_label.setText(f"导出失败：{error}")
        self._set_running(False)
        self._worker = None

    def _on_canceled(self):
        self._status_label.setText("已取消。")
        self._set_running(False)
        self._worker = None

    def _set_running(self, running: bool):
        self._start_btn.setEnabled(not running)
        self._cancel_btn.setEnabled(running)
        for widget in (
            self._ppt_btn,
            self._word_btn,
            self._input_edit,
            self._file_btn,
            self._folder_btn,
            self._output_edit,
            self._output_btn,
            self._max_pages_spin,
            self._backend_combo,
        ):
            widget.setEnabled(not running)
