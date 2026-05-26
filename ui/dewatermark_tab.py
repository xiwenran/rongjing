from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.dewatermark import DewatermarkRunner


_WIN = "#F7F7F7"
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
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        body = QWidget()
        body.setObjectName("body")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        layout.addWidget(self._step_input())
        layout.addWidget(self._step_strength())
        layout.addWidget(self._step_output())
        layout.addWidget(self._step_action())
        layout.addStretch(1)

        scroll.setWidget(body)
        root.addWidget(scroll)

    def _step_input(self) -> QWidget:
        card = _card()
        layout = card.layout()
        layout.addWidget(_title("步骤 1 输入"))

        row = QHBoxLayout()
        row.setSpacing(10)
        self._choose_dir_btn = QPushButton("选择文件夹")
        self._choose_dir_btn.clicked.connect(self._choose_dir)
        self._choose_files_btn = QPushButton("选择多张图")
        self._choose_files_btn.clicked.connect(self._choose_files)
        row.addWidget(self._choose_dir_btn)
        row.addWidget(self._choose_files_btn)
        layout.addLayout(row)

        self._input_hint = QLabel("未选择图片，可拖入文件夹或多张图片")
        self._input_hint.setObjectName("hint")
        self._input_hint.setWordWrap(True)
        layout.addWidget(self._input_hint)

        self._count_label = QLabel("已选 0 张")
        self._count_label.setObjectName("badge")
        layout.addWidget(self._count_label, 0, Qt.AlignmentFlag.AlignLeft)
        return card

    def _step_strength(self) -> QWidget:
        card = _card()
        layout = card.layout()
        layout.addWidget(_title("步骤 2 强度"))

        self._strength_group = QButtonGroup(self)
        self._strength_group.setExclusive(True)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        options = [
            ("轻", "low", "只剥元数据，像素不变"),
            ("中", "medium", "剥元数据 + 轻微 resize 来回 + 重编码"),
            ("强", "high", "剥元数据 + resize + 微噪声 + 颜色微抖动 + 重编码"),
        ]
        for idx, (label, value, desc) in enumerate(options):
            btn = QPushButton(label)
            btn.setObjectName("strengthBtn")
            btn.setCheckable(True)
            btn.setProperty("strength", value)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._strength_group.addButton(btn)
            grid.addWidget(btn, 0, idx)

            hint = QLabel(desc)
            hint.setObjectName("hint")
            hint.setWordWrap(True)
            grid.addWidget(hint, 1, idx)
            if value == "medium":
                btn.setChecked(True)
        layout.addLayout(grid)
        return card

    def _step_output(self) -> QWidget:
        card = _card()
        layout = card.layout()
        layout.addWidget(_title("步骤 3 输出"))

        row = QHBoxLayout()
        row.setSpacing(10)
        self._choose_output_btn = QPushButton("输出目录")
        self._choose_output_btn.clicked.connect(self._choose_output_dir)
        self._format_combo = QComboBox()
        self._format_combo.addItems(["JPEG", "PNG", "保持原格式"])
        row.addWidget(self._choose_output_btn)
        row.addWidget(self._format_combo)
        layout.addLayout(row)

        self._output_label = QLabel("未选择输出目录")
        self._output_label.setObjectName("hint")
        self._output_label.setWordWrap(True)
        layout.addWidget(self._output_label)
        return card

    def _step_action(self) -> QWidget:
        card = _card()
        layout = card.layout()
        layout.addWidget(_title("步骤 4 操作"))

        row = QHBoxLayout()
        row.setSpacing(10)
        self._start_btn = QPushButton("开始处理")
        self._start_btn.setObjectName("primaryBtn")
        self._start_btn.clicked.connect(self._start)
        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.setObjectName("dangerBtn")
        self._cancel_btn.clicked.connect(self._cancel)
        row.addWidget(self._start_btn)
        row.addWidget(self._cancel_btn)
        layout.addLayout(row)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        layout.addWidget(self._progress)
        self._status_label = QLabel("")
        self._status_label.setObjectName("hint")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status_label)
        return card

    def _choose_dir(self):
        start = os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(self, "选择图片文件夹", start)
        if path:
            self._set_files(_scan_images(path))

    def _choose_files(self):
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
            self._output_label.setText(path)
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
        self._refresh_state()

    def _refresh_state(self):
        count = len(self._files)
        self._count_label.setText(f"已选 {count} 张")
        if count:
            parent = os.path.dirname(self._files[0])
            self._input_hint.setText(f"{parent} 等 {count} 张图片")
        else:
            self._input_hint.setText("未选择图片，可拖入文件夹或多张图片")

        running = self._runner is not None and self._runner.isRunning()
        self._start_btn.setEnabled(bool(self._files and self._output_dir) and not running)
        self._cancel_btn.setEnabled(running)
        self._choose_dir_btn.setEnabled(not running)
        self._choose_files_btn.setEnabled(not running)
        self._choose_output_btn.setEnabled(not running)
        self._format_combo.setEnabled(not running)
        for btn in self._strength_group.buttons():
            btn.setEnabled(not running)

    def _start(self):
        if not self._files:
            QMessageBox.warning(self, "提示", "请先选择要处理的图片")
            return
        if not self._output_dir:
            QMessageBox.warning(self, "提示", "请先选择输出目录")
            return

        strength = self._current_strength()
        output_format = self._current_output_format()
        self._runner = DewatermarkRunner(self._files, self._output_dir, strength, output_format, self)
        self._runner.progress.connect(self._on_progress)
        self._runner.finished.connect(self._on_finished)
        self._progress.setVisible(True)
        self._progress.setMaximum(max(1, len(self._files)))
        self._progress.setValue(0)
        self._status_label.setText("正在处理…")
        self._runner.start()
        self._refresh_state()

    def _cancel(self):
        if self._runner is not None:
            self._runner.abort()
            self._status_label.setText("正在取消…")
            self._cancel_btn.setEnabled(False)

    def _on_progress(self, done: int, total: int, msg: str):
        self._progress.setMaximum(max(1, total))
        self._progress.setValue(done)
        self._status_label.setText(f"处理中：{done} / {total}  {msg}")

    def _on_finished(self, success: bool, msg: str):
        self._status_label.setText(msg)
        self._runner = None
        self._refresh_state()
        if not success and msg != "已取消":
            QMessageBox.warning(self, "处理失败", msg)

    def _current_strength(self) -> str:
        btn = self._strength_group.checkedButton()
        return btn.property("strength") if btn is not None else "medium"

    def _current_output_format(self) -> str:
        text = self._format_combo.currentText()
        return "original" if text == "保持原格式" else text

    def _stylesheet(self) -> str:
        return f"""
        QWidget#DewatermarkTab, QWidget#body {{ background: {_WIN}; }}
        QLabel {{ background: transparent; color: {_TEXT}; font-size: 13px; }}
        QLabel#h2 {{
            color: {_TEXT}; font-size: 15px; font-weight: 700;
            border-left: 4px solid {_GREEN}; padding-left: 8px;
        }}
        QLabel#hint {{ color: {_TEXT2}; font-size: 12px; }}
        QLabel#badge {{
            background: rgba(7,193,96,0.12); color: {_GREEN}; font-size: 12px;
            font-weight: 600; padding: 4px 10px; border-radius: 10px;
        }}
        QWidget#card {{
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {_CARD}, stop:1 #F8F8F8);
            border-radius: 16px; border: 1px solid {_SEP};
        }}
        QPushButton {{
            background: {_INPUT}; border: 1px solid {_SEP}; border-radius: 18px;
            padding: 9px 16px; color: {_TEXT}; font-weight: 600;
        }}
        QPushButton:hover {{ background: #EAEAEA; }}
        QPushButton:disabled {{ color: #B0B0B0; background: #F4F4F4; }}
        QPushButton#primaryBtn {{
            background: {_GREEN}; color: white; border: none; border-radius: 22px;
            padding: 12px 22px; font-size: 14px;
        }}
        QPushButton#dangerBtn {{
            color: {_RED}; background: rgba(250,81,81,0.08); border: 1px solid rgba(250,81,81,0.18);
            border-radius: 22px; padding: 12px 22px;
        }}
        QPushButton#strengthBtn:checked {{
            background: {_GREEN}; color: white; border-color: {_GREEN};
        }}
        QComboBox {{
            background: {_INPUT}; border: 1px solid {_SEP};
            border-radius: 8px; padding: 9px 12px; color: {_TEXT};
        }}
        QProgressBar {{
            background: {_INPUT}; border: none; border-radius: 8px; height: 12px;
            text-align: center; color: transparent;
        }}
        QProgressBar::chunk {{ background: {_GREEN}; border-radius: 8px; }}
        """


def _card() -> QWidget:
    card = QWidget()
    card.setObjectName("card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(10)
    return card


def _title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("h2")
    return label


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
