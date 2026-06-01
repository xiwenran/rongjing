from __future__ import annotations

import os
from pathlib import Path

from PIL import Image
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QImage, QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.dewatermark import DewatermarkRunner, dewatermark_image


_WIN = "#F7F7F7"
_SIDE = "#EFEFEF"
_CARD = "#FFFFFF"
_INPUT = "#F0F0F0"
_SEP = "#E5E5E5"
_TEXT = "#191919"
_TEXT2 = "#888888"
_GREEN = "#07C160"
_RED = "#FA5151"
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}


class DewatermarkTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._files: list[str] = []
        self._output_dir = ""
        self._runner: DewatermarkRunner | None = None
        self._preview_mode = "processed"
        self._preview_index = 0
        self._preview_source: Image.Image | None = None
        self._preview_processed: Image.Image | None = None
        self.setAcceptDrops(True)
        self.setObjectName("DewatermarkTab")
        self.setStyleSheet(self._stylesheet())
        self._build_ui()
        self._refresh_state()

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        paths = [url.toLocalFile() for url in event.mimeData().urls()]
        files: list[str] = []
        for path in paths:
            if os.path.isdir(path):
                files.extend(_scan_images(path))
            elif _is_image(path):
                files.append(path)
        if files:
            self._set_files(files)

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar(), 0)
        root.addWidget(self._build_right_panel(), 1)

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("dewatermark_sidebar")
        sidebar.setFixedWidth(392)

        outer = QVBoxLayout(sidebar)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        body = QWidget()
        body.setObjectName("dewatermark_scroll_body")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(18, 20, 18, 20)
        layout.setSpacing(16)

        layout.addWidget(self._build_input_section())
        layout.addWidget(_sep())
        layout.addWidget(self._build_strength_section())
        layout.addWidget(_sep())
        layout.addWidget(self._build_file_section(), 1)
        layout.addStretch(1)

        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        return sidebar

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)
        layout.addWidget(self._build_preview_area(), 1)
        layout.addWidget(self._build_export_bar(), 0)
        return panel

    def _build_input_section(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        layout.addWidget(_label("输入来源", "h2"))

        source_row = QHBoxLayout()
        source_row.setSpacing(8)
        self._source_files_btn = QPushButton("单张 / 多张图")
        self._source_folder_btn = QPushButton("批量文件夹")
        for btn in (self._source_files_btn, self._source_folder_btn):
            btn.setObjectName("modeBtn")
            btn.setCheckable(True)
            btn.setMinimumHeight(34)
        self._source_files_btn.setChecked(True)
        self._source_files_btn.clicked.connect(self._choose_files)
        self._source_folder_btn.clicked.connect(self._choose_dir)
        source_row.addWidget(self._source_files_btn)
        source_row.addWidget(self._source_folder_btn)
        layout.addLayout(source_row)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self._choose_files_btn = QPushButton("选择图片")
        self._choose_folder_btn = QPushButton("选择文件夹")
        self._choose_files_btn.clicked.connect(self._choose_files)
        self._choose_folder_btn.clicked.connect(self._choose_dir)
        action_row.addWidget(self._choose_files_btn)
        action_row.addWidget(self._choose_folder_btn)
        layout.addLayout(action_row)

        self._input_hint = _label("未选择图片，可拖入文件夹或多张图片", "hint")
        self._input_hint.setWordWrap(True)
        layout.addWidget(self._input_hint)

        self._count_label = _label("已选 0 张", "badge_ok")
        layout.addWidget(self._count_label, 0, Qt.AlignmentFlag.AlignLeft)
        return box

    def _build_strength_section(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        layout.addWidget(_label("去水印强度", "h2"))
        layout.addWidget(_label("保留原图观感的前提下，逐步增强处理力度", "cap"))

        self._strength_group = QButtonGroup(self)
        self._strength_group.setExclusive(True)

        strength_row = QHBoxLayout()
        strength_row.setSpacing(8)
        options = [("轻", "low"), ("中", "medium"), ("强", "high")]
        for title, value in options:
            btn = QPushButton(title)
            btn.setObjectName("strengthCard")
            btn.setCheckable(True)
            btn.setProperty("strength", value)
            btn.setMinimumHeight(38)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(self._update_preview)
            self._strength_group.addButton(btn)
            strength_row.addWidget(btn)
            if value == "medium":
                btn.setChecked(True)
        layout.addLayout(strength_row)
        return box

    def _build_file_section(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        layout.addWidget(_label("预览图片", "h2"))
        self._summary_label = _label("暂无待处理图片", "cap")
        self._summary_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._summary_label)

        self._file_list = QListWidget()
        self._file_list.setObjectName("fileList")
        self._file_list.currentRowChanged.connect(self._on_preview_file_changed)
        layout.addWidget(self._file_list, 1)
        return box

    def _build_preview_area(self) -> QWidget:
        frame = QWidget()
        frame.setObjectName("preview_frame")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(_label("处理预览", "h2"))
        header.addStretch(1)
        self._original_btn = QPushButton("原图")
        self._processed_btn = QPushButton("处理后")
        for btn, mode in ((self._original_btn, "original"), (self._processed_btn, "processed")):
            btn.setObjectName("toggleBtn")
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked=False, value=mode: self._set_preview_mode(value))
            header.addWidget(btn)
        self._processed_btn.setChecked(True)
        layout.addLayout(header)

        self._preview_name = _label("未选择图片", "cap")
        layout.addWidget(self._preview_name)

        self._preview_label = QLabel("选择图片后，这里会显示处理预览")
        self._preview_label.setObjectName("preview_canvas")
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._preview_label.setMinimumHeight(560)
        self._preview_label.installEventFilter(self)
        layout.addWidget(self._preview_label, 1)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        self._preview_meta = _label("处理中强度：中", "hint")
        self._preview_tip = _label("右侧默认显示处理后图片", "hint")
        self._preview_tip.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        footer.addWidget(self._preview_meta)
        footer.addWidget(self._preview_tip, 1)
        layout.addLayout(footer)
        return frame

    def _build_export_bar(self) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        bar = QWidget()
        bar.setObjectName("export_bar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(16, 14, 16, 14)
        row.setSpacing(12)

        self._choose_output_btn = QPushButton("输出文件夹")
        self._choose_output_btn.clicked.connect(self._choose_output_dir)
        self._output_label = _label("未选择输出目录", "hint")
        self._output_label.setMinimumWidth(90)
        self._format_combo = QComboBox()
        self._format_combo.addItems(["JPEG", "PNG", "保持原格式"])
        self._format_combo.currentTextChanged.connect(self._update_preview)
        self._start_btn = QPushButton("开始处理")
        self._start_btn.setObjectName("primary")
        self._start_btn.setMinimumHeight(40)
        self._start_btn.clicked.connect(self._start)
        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.setObjectName("danger")
        self._cancel_btn.clicked.connect(self._cancel)

        row.addWidget(self._choose_output_btn, 0)
        row.addWidget(self._output_label, 1)
        row.addWidget(self._format_combo, 0)
        row.addWidget(self._start_btn, 0)
        row.addWidget(self._cancel_btn, 0)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._status_label = _label("", "hint")
        self._status_label.setVisible(False)

        layout.addWidget(bar)
        layout.addWidget(self._progress)
        layout.addWidget(self._status_label)
        return wrapper

    def _set_source_mode(self, mode: str):
        files_mode = mode == "files"
        self._source_files_btn.setChecked(files_mode)
        self._source_folder_btn.setChecked(not files_mode)

    def _choose_dir(self):
        self._set_source_mode("folder")
        start = os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(self, "选择图片文件夹", start)
        if path:
            self._set_files(_scan_images(path))

    def _choose_files(self):
        self._set_source_mode("files")
        start = os.path.expanduser("~")
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择图片",
            start,
            "Images (*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff)",
        )
        if paths:
            self._set_files(paths)

    def _choose_output_dir(self):
        start = self._output_dir or os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(self, "选择输出目录", start)
        if path:
            self._output_dir = path
            self._refresh_state()

    def _set_files(self, files: list[str]):
        seen = set()
        self._files = []
        for path in files:
            norm = os.path.abspath(path)
            if norm not in seen and _is_image(norm):
                seen.add(norm)
                self._files.append(norm)
        self._files.sort(key=lambda p: p.lower())
        self._preview_index = 0
        self._refresh_state()
        self._update_preview()

    def _refresh_state(self):
        count = len(self._files)
        self._count_label.setText(f"已选 {count} 张")
        if count:
            parent = os.path.dirname(self._files[0])
            self._input_hint.setText(f"{_display_path(parent)} 等 {count} 张图片")
            self._input_hint.setToolTip(parent)
            self._output_label.setText(_display_path(self._output_dir) if self._output_dir else "未选择输出目录")
            self._output_label.setToolTip(self._output_dir)
            self._summary_label.setText(f"共 {count} 张，当前预览第 {min(self._preview_index + 1, count)} 张")
        else:
            self._input_hint.setText("未选择图片，可拖入文件夹或多张图片")
            self._input_hint.setToolTip("")
            self._summary_label.setText("暂无待处理图片")
            self._output_label.setText(_display_path(self._output_dir) if self._output_dir else "未选择输出目录")
            self._output_label.setToolTip(self._output_dir)
        self._sync_file_list()

        running = self._runner is not None and self._runner.isRunning()
        self._start_btn.setEnabled(bool(self._files and self._output_dir) and not running)
        self._cancel_btn.setVisible(running)
        self._cancel_btn.setEnabled(running)
        self._choose_output_btn.setEnabled(not running)
        self._format_combo.setEnabled(not running)
        self._choose_files_btn.setEnabled(not running)
        self._choose_folder_btn.setEnabled(not running)
        self._source_files_btn.setEnabled(not running)
        self._source_folder_btn.setEnabled(not running)
        self._file_list.setEnabled(not running)
        for btn in self._strength_group.buttons():
            btn.setEnabled(not running)
        self._original_btn.setEnabled(self._preview_source is not None)
        self._processed_btn.setEnabled(self._preview_processed is not None)

    def _start(self):
        if not self._files:
            QMessageBox.warning(self, "提示", "请先选择要处理的图片")
            return
        if not self._output_dir:
            QMessageBox.warning(self, "提示", "请先选择输出目录")
            return

        self._runner = DewatermarkRunner(
            self._files,
            self._output_dir,
            self._current_strength(),
            self._current_output_format(),
            self,
        )
        self._runner.progress.connect(self._on_progress)
        self._runner.finished.connect(self._on_finished)
        self._progress.setVisible(True)
        self._status_label.setVisible(True)
        self._progress.setMaximum(max(1, len(self._files)))
        self._progress.setValue(0)
        self._status_label.setText("正在处理…")
        self._runner.start()
        self._refresh_state()

    def _cancel(self):
        if self._runner is not None:
            self._runner.abort()
            self._status_label.setVisible(True)
            self._status_label.setText("正在取消…")
            self._cancel_btn.setEnabled(False)

    def _on_progress(self, done: int, total: int, msg: str):
        self._progress.setMaximum(max(1, total))
        self._progress.setValue(done)
        self._status_label.setVisible(True)
        self._status_label.setText(f"处理中：{done} / {total}  {msg}")

    def _on_finished(self, success: bool, msg: str):
        self._runner = None
        self._progress.setVisible(False)
        self._status_label.setVisible(True)
        self._status_label.setText(msg)
        self._refresh_state()
        if not success and msg != "已取消":
            QMessageBox.warning(self, "处理失败", msg)

    def _current_strength(self) -> str:
        btn = self._strength_group.checkedButton()
        return btn.property("strength") if btn is not None else "medium"

    def _current_strength_label(self) -> str:
        mapping = {"low": "轻", "medium": "中", "high": "强"}
        return mapping.get(self._current_strength(), "中")

    def _current_output_format(self) -> str:
        text = self._format_combo.currentText()
        return "original" if text == "保持原格式" else text

    def _sync_file_list(self):
        self._file_list.blockSignals(True)
        self._file_list.clear()
        for path in self._files:
            item = QListWidgetItem(os.path.basename(path))
            item.setToolTip(path)
            self._file_list.addItem(item)
        if self._files:
            self._preview_index = min(self._preview_index, len(self._files) - 1)
            self._file_list.setCurrentRow(self._preview_index)
        self._file_list.blockSignals(False)

    def _on_preview_file_changed(self, row: int):
        if row < 0 or row >= len(self._files):
            return
        self._preview_index = row
        self._summary_label.setText(f"共 {len(self._files)} 张，当前预览第 {row + 1} 张")
        self._update_preview()

    def _set_preview_mode(self, mode: str):
        self._preview_mode = mode
        self._original_btn.setChecked(mode == "original")
        self._processed_btn.setChecked(mode == "processed")
        self._render_preview()

    def _update_preview(self):
        if not self._files:
            self._preview_source = None
            self._preview_processed = None
            self._preview_name.setText("未选择图片")
            self._preview_meta.setText(f"处理中强度：{self._current_strength_label()}")
            self._preview_label.setPixmap(QPixmap())
            self._preview_label.setText("选择图片后，这里会显示处理预览")
            self._refresh_state()
            return

        path = self._files[min(self._preview_index, len(self._files) - 1)]
        try:
            with Image.open(path) as img:
                self._preview_source = img.convert("RGB")
            self._preview_processed = dewatermark_image(self._preview_source.copy(), self._current_strength())
            self._preview_name.setText(os.path.basename(path))
            self._preview_meta.setText(
                f"处理中强度：{self._current_strength_label()}  ·  输出格式：{self._format_combo.currentText()}"
            )
            self._preview_tip.setText("右侧默认显示处理后图片，可切回原图")
            self._render_preview()
        except Exception as exc:
            self._preview_source = None
            self._preview_processed = None
            self._preview_name.setText(os.path.basename(path))
            self._preview_label.setPixmap(QPixmap())
            self._preview_label.setText(f"预览失败：{exc}")
        self._refresh_state()

    def _render_preview(self):
        current = self._preview_processed if self._preview_mode == "processed" else self._preview_source
        if current is None:
            return
        pixmap = _pil_to_pixmap(current)
        if pixmap.isNull():
            self._preview_label.setPixmap(QPixmap())
            self._preview_label.setText("预览生成失败")
            return
        scaled = pixmap.scaled(
            self._preview_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._preview_label.setText("")
        self._preview_label.setPixmap(scaled)

    def eventFilter(self, watched, event):
        if watched is self._preview_label and event.type() == QEvent.Type.Resize:
            self._render_preview()
        return super().eventFilter(watched, event)

    def _stylesheet(self) -> str:
        return f"""
        QWidget#dewatermark_sidebar,
        QWidget#dewatermark_scroll_body {{
            background: {_SIDE};
        }}
        QWidget#dewatermark_sidebar {{
            border-right: 1px solid {_SEP};
        }}
        QWidget#preview_frame,
        QWidget#export_bar {{
            background: {_CARD};
            border: 1px solid {_SEP};
            border-radius: 16px;
        }}
        QWidget#DewatermarkTab QLabel#h2 {{
            color: {_TEXT};
            font-size: 15px;
            font-weight: 700;
            qproperty-alignment: AlignCenter;
        }}
        QWidget#DewatermarkTab QLabel#cap {{
            color: {_TEXT2};
            font-size: 11px;
            font-weight: 500;
            qproperty-alignment: AlignCenter;
        }}
        QWidget#DewatermarkTab QLabel#hint {{
            color: {_TEXT2};
            font-size: 12px;
        }}
        QWidget#DewatermarkTab QLabel#card_title {{
            color: {_TEXT};
            font-size: 14px;
            font-weight: 700;
        }}
        QWidget#DewatermarkTab QLabel#badge_ok {{
            background: rgba(7,193,96,0.12);
            color: {_GREEN};
            font-size: 12px;
            font-weight: 600;
            padding: 4px 10px;
            border-radius: 10px;
        }}
        QWidget#DewatermarkTab QPushButton {{
            background: {_INPUT};
            color: {_TEXT};
            border: 1px solid {_SEP};
            border-radius: 10px;
            padding: 8px 14px;
            font-weight: 600;
        }}
        QWidget#DewatermarkTab QPushButton:hover {{
            background: #E8E8E8;
        }}
        QWidget#DewatermarkTab QPushButton#modeBtn {{
            border-radius: 12px;
            padding: 9px 12px;
        }}
        QWidget#DewatermarkTab QPushButton#modeBtn:checked {{
            background: {_GREEN};
            color: white;
            border-color: {_GREEN};
        }}
        QWidget#DewatermarkTab QPushButton#strengthCard {{
            text-align: center;
            border-radius: 14px;
            padding: 8px 12px;
        }}
        QWidget#DewatermarkTab QPushButton#strengthCard:checked {{
            background: #E8F8EE;
            border: 1.5px solid {_GREEN};
        }}
        QWidget#DewatermarkTab QPushButton#toggleBtn {{
            border-radius: 12px;
            padding: 8px 14px;
        }}
        QWidget#DewatermarkTab QPushButton#toggleBtn:checked {{
            background: #E8F8EE;
            color: {_GREEN};
            border-color: {_GREEN};
        }}
        QWidget#DewatermarkTab QPushButton#primary {{
            background: {_GREEN};
            color: white;
            border: none;
            border-radius: 12px;
            padding: 8px 18px;
            font-size: 14px;
        }}
        QWidget#DewatermarkTab QPushButton#danger {{
            color: {_RED};
            background: rgba(250,81,81,0.08);
            border: 1px solid rgba(250,81,81,0.18);
            border-radius: 12px;
        }}
        QWidget#DewatermarkTab QPushButton:disabled {{
            color: #B5B5B5;
            background: #F4F4F4;
        }}
        QWidget#DewatermarkTab QComboBox {{
            background: {_INPUT};
            border: 1px solid {_SEP};
            border-radius: 10px;
            padding: 8px 12px;
            color: {_TEXT};
        }}
        QWidget#DewatermarkTab QListWidget#fileList {{
            background: {_CARD};
            border: 1px solid {_SEP};
            border-radius: 12px;
            padding: 6px;
            outline: none;
        }}
        QWidget#DewatermarkTab QListWidget#fileList::item {{
            background: {_INPUT};
            color: {_TEXT};
            border-radius: 8px;
            padding: 9px 10px;
            margin: 2px 0px;
        }}
        QWidget#DewatermarkTab QListWidget#fileList::item:selected {{
            background: rgba(7,193,96,0.14);
            color: {_GREEN};
        }}
        QWidget#DewatermarkTab QLabel#preview_canvas {{
            background: {_WIN};
            color: {_TEXT2};
            border: 1px solid {_SEP};
            border-radius: 14px;
            padding: 18px;
        }}
        QWidget#DewatermarkTab QProgressBar {{
            background: rgba(0,0,0,0.08);
            border: none;
            border-radius: 3px;
            max-height: 6px;
            text-align: center;
            color: transparent;
        }}
        QWidget#DewatermarkTab QProgressBar::chunk {{
            background: {_GREEN};
            border-radius: 3px;
        }}
        """


def _label(text: str, object_name: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName(object_name)
    return label


def _sep() -> QFrame:
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.HLine)
    sep.setFrameShadow(QFrame.Shadow.Plain)
    return sep


def _is_image(path: str) -> bool:
    return os.path.isfile(path) and Path(path).suffix.lower() in _IMAGE_EXTS


def _scan_images(folder: str) -> list[str]:
    files = []
    for root, _, names in os.walk(folder):
        for name in names:
            path = os.path.join(root, name)
            if _is_image(path):
                files.append(path)
    return sorted(files, key=lambda p: p.lower())


def _pil_to_pixmap(image: Image.Image) -> QPixmap:
    rgb = image.convert("RGB")
    data = rgb.tobytes("raw", "RGB")
    qimg = QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format.Format_RGB888).copy()
    return QPixmap.fromImage(qimg)


def _display_path(path: str, max_chars: int = 52) -> str:
    if not path:
        return ""
    if len(path) <= max_chars:
        return path
    head = max_chars // 2 - 2
    tail = max_chars - head - 3
    return f"{path[:head]}...{path[-tail:]}"
