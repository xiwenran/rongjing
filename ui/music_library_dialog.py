"""Music-library dialog and page-video background-music selection card."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Iterable

from PyQt6.QtCore import QItemSelectionModel, QSettings, QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.music_library import AUDIO_EXTENSIONS, MusicLibrary, VIDEO_EXTENSIONS


MODE_NONE = "none"
MODE_FIXED = "fixed"
MODE_RANDOM = "random"
VALID_MODES = {MODE_NONE, MODE_FIXED, MODE_RANDOM}

_GREEN = "#07C160"
_TEXT = "#191919"
_TEXT2 = "#888888"
_INPUT = "#F0F0F0"
_SEP = "#E5E5E5"
_CARD = "#FFFFFF"


def _parse_ids(value) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = []
    if not isinstance(value, (list, tuple)):
        return []
    return list(dict.fromkeys(item for item in value if isinstance(item, str) and item))


def _format_duration(seconds: float) -> str:
    total = max(0, round(float(seconds or 0)))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


class MusicImportWorker(QThread):
    """Run mixed music imports away from the GUI thread."""

    progress = pyqtSignal(int, int, str)
    import_finished = pyqtSignal(dict)

    def __init__(self, library: MusicLibrary, sources: Iterable[str], parent=None):
        super().__init__(parent)
        self._library = library
        self._sources = list(sources)

    def run(self) -> None:
        try:
            result = self._library.import_many(
                self._sources,
                recursive=True,
                progress=self.progress.emit,
            )
        except Exception:
            result = {
                "items": [{
                    "status": "failed",
                    "name": "音乐库",
                    "source_kind": None,
                    "message": "音乐库无法读取，导入已停止",
                }],
                "counts": {"imported": 0, "existing": 0, "failed": 1, "skipped": 0},
            }
        self.import_finished.emit(result)


class MusicLibraryDialog(QDialog):
    """Import, inspect, and choose tracks by stable library item id."""

    def __init__(
        self,
        library: MusicLibrary,
        mode: str,
        selected_ids: Iterable[str] = (),
        parent=None,
        *,
        worker_factory: Callable | None = None,
    ):
        super().__init__(parent)
        self._library = library
        self._mode = mode if mode in VALID_MODES else MODE_NONE
        self._selected_ids = _parse_ids(list(selected_ids))
        self._worker_factory = worker_factory or MusicImportWorker
        self._import_worker = None
        self._tracks: list[dict] = []

        self.setWindowTitle("音乐库")
        self.setFixedSize(760, 520)
        self.setStyleSheet(self._dialog_style())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        heading = QLabel("背景音乐库")
        heading.setObjectName("musicHeading")
        hint = QLabel("导入音频，或从视频中提取第一条完整音轨。名称相同的音乐也会按内容分别保存。")
        hint.setObjectName("musicHint")
        hint.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(hint)

        action_row = QHBoxLayout()
        self.import_files_button = QPushButton("导入音频 / 视频…")
        self.import_folder_button = QPushButton("导入文件夹…")
        self.import_files_button.setObjectName("musicImport")
        self.import_folder_button.setObjectName("musicImport")
        self.import_files_button.clicked.connect(self._pick_files)
        self.import_folder_button.clicked.connect(self._pick_folder)
        action_row.addWidget(self.import_files_button)
        action_row.addWidget(self.import_folder_button)
        action_row.addStretch()
        layout.addLayout(action_row)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["名称", "时长", "来源类型"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        if self._mode == MODE_FIXED:
            self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        elif self._mode == MODE_RANDOM:
            self.table.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        else:
            self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, 1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_label = QLabel("")
        self.progress_label.setObjectName("musicHint")
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.progress_label)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._confirm_selection)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.refresh_tracks()

    @staticmethod
    def _dialog_style() -> str:
        return f"""
        QDialog {{ background: #F7F7F7; color: {_TEXT}; }}
        QLabel#musicHeading {{ font-size: 19px; font-weight: 700; color: {_TEXT}; }}
        QLabel#musicHint {{ font-size: 12px; color: {_TEXT2}; }}
        QTableWidget {{ background: {_CARD}; border: 1px solid {_SEP}; border-radius: 12px; outline: none; }}
        QTableWidget::item {{ padding: 9px 12px; border: none; }}
        QTableWidget::item:selected {{ background: rgba(7,193,96,0.16); color: {_TEXT}; }}
        QHeaderView::section {{ background: {_INPUT}; color: {_TEXT2}; padding: 8px 12px; border: none; border-bottom: 1px solid {_SEP}; }}
        QPushButton {{ background: {_INPUT}; border: none; border-radius: 16px; padding: 8px 16px; }}
        QPushButton#musicImport {{ background: rgba(7,193,96,0.10); color: {_GREEN}; border: 1px solid rgba(7,193,96,0.35); font-weight: 600; }}
        QPushButton:hover {{ background: #E5E5E5; }}
        QPushButton:disabled {{ color: #C6C6C6; }}
        QProgressBar {{ background: rgba(0,0,0,0.08); border: none; border-radius: 3px; max-height: 6px; color: transparent; }}
        QProgressBar::chunk {{ background: {_GREEN}; border-radius: 3px; }}
        """

    def refresh_tracks(self) -> None:
        try:
            self._tracks = self._library.list()
        except Exception:
            self._tracks = []
            self.progress_label.setText("音乐库索引无法读取。")
        valid_ids = {track.get("id") for track in self._tracks}
        self._selected_ids = [track_id for track_id in self._selected_ids if track_id in valid_ids]

        self.table.setRowCount(0)
        for track in self._tracks:
            row = self.table.rowCount()
            self.table.insertRow(row)
            name_item = QTableWidgetItem(track.get("display_name") or "未命名音乐")
            name_item.setData(Qt.ItemDataRole.UserRole, track.get("id"))
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, QTableWidgetItem(_format_duration(track.get("duration", 0))))
            source_text = "音频" if track.get("source_kind") == "audio" else "视频提取"
            self.table.setItem(row, 2, QTableWidgetItem(source_text))
            self.table.setRowHeight(row, 40)
            if track.get("id") in self._selected_ids:
                self.table.selectionModel().select(
                    self.table.model().index(row, 0),
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows,
                )
        if not self._tracks and not self.progress_label.text():
            self.progress_label.setText("音乐库为空，可先导入音频或视频。")

    def selected_ids(self) -> list[str]:
        ids = []
        for index in self.table.selectionModel().selectedRows():
            item = self.table.item(index.row(), 0)
            if item and item.data(Qt.ItemDataRole.UserRole):
                ids.append(item.data(Qt.ItemDataRole.UserRole))
        return ids

    def _pick_files(self) -> None:
        audio_patterns = " ".join(f"*{ext}" for ext in sorted(AUDIO_EXTENSIONS))
        video_patterns = " ".join(f"*{ext}" for ext in sorted(VIDEO_EXTENSIONS))
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "导入音频或视频",
            str(Path.home()),
            f"音频或视频 ({audio_patterns} {video_patterns})",
        )
        if paths:
            self._start_import(paths)

    def _pick_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "导入音乐文件夹", str(Path.home()))
        if path:
            self._start_import([path])

    def _start_import(self, sources: list[str]) -> None:
        if self._import_worker is not None and self._import_worker.isRunning():
            return
        self._set_importing(True)
        self.progress_bar.setRange(0, 0)
        self.progress_label.setText("正在扫描可导入的音乐…")
        self._import_worker = self._worker_factory(self._library, sources)
        self._import_worker.progress.connect(self._on_import_progress)
        self._import_worker.import_finished.connect(self._on_import_finished)
        self._import_worker.start()

    def _on_import_progress(self, done: int, total: int, message: str) -> None:
        self.progress_bar.setRange(0, max(1, total))
        self.progress_bar.setValue(done)
        self.progress_label.setText(message[:100])

    def _on_import_finished(self, result: dict) -> None:
        self._set_importing(False)
        counts = result.get("counts", {})
        parts = []
        for key, label in (("imported", "导入"), ("existing", "已存在"), ("failed", "失败"), ("skipped", "跳过")):
            count = int(counts.get(key, 0))
            if count:
                parts.append(f"{label} {count} 个")
        failures = [
            f"{item.get('name', '文件')}：{item.get('message', '无法导入')}"
            for item in result.get("items", [])
            if item.get("status") in {"failed", "skipped"}
        ]
        summary = "；".join(parts) or "没有找到可导入的音乐"
        if failures:
            summary += "。" + "；".join(failures[:2])
            if len(failures) > 2:
                summary += f"；另有 {len(failures) - 2} 项"
        self.progress_label.setText(summary[:180])
        self.refresh_tracks()

    def _set_importing(self, importing: bool) -> None:
        self.import_files_button.setEnabled(not importing)
        self.import_folder_button.setEnabled(not importing)
        self.buttons.setEnabled(not importing)
        self.progress_bar.setVisible(importing)

    def _confirm_selection(self) -> None:
        selected = self.selected_ids()
        if self._mode == MODE_FIXED and len(selected) != 1:
            QMessageBox.warning(self, "请选择音乐", "固定配乐需要选择 1 首音乐。")
            return
        if self._mode == MODE_RANDOM and not selected:
            QMessageBox.warning(self, "请选择音乐", "随机配乐至少需要选择 1 首音乐。")
            return
        self._selected_ids = [] if self._mode == MODE_NONE else selected
        self.accept()

    def closeEvent(self, event) -> None:
        if self._import_worker is not None and self._import_worker.isRunning():
            self.progress_label.setText("导入完成后即可关闭音乐库。")
            event.ignore()
            return
        super().closeEvent(event)


class BackgroundMusicCard(QWidget):
    """Persist page-video music mode, ids, and volume without touching runners."""

    selection_changed = pyqtSignal(str, list, int)

    def __init__(
        self,
        settings: QSettings,
        library: MusicLibrary,
        parent=None,
        *,
        dialog_factory: Callable | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("card")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._settings = settings
        self._library = library
        self._dialog_factory = dialog_factory or MusicLibraryDialog
        self._tracks_by_id: dict[str, dict] = {}

        mode = str(settings.value("page_video/music_mode", MODE_NONE))
        self._mode = mode if mode in VALID_MODES else MODE_NONE
        self._selected_ids = _parse_ids(settings.value("page_video/music_selected_ids", "[]"))
        try:
            volume = int(settings.value("page_video/music_volume", 35))
        except (TypeError, ValueError):
            volume = 35
        self._volume = min(100, max(0, volume))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 13, 14, 13)
        layout.setSpacing(9)
        title = QLabel("背景音乐")
        title.setStyleSheet(f"font-size:14px; font-weight:700; color:{_TEXT}; background:transparent;")
        layout.addWidget(title)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(7)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("不使用（默认）", MODE_NONE)
        self.mode_combo.addItem("固定配乐", MODE_FIXED)
        self.mode_combo.addItem("随机配乐", MODE_RANDOM)
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(self._mode)))
        self.volume_spin = QSpinBox()
        self.volume_spin.setRange(0, 100)
        self.volume_spin.setSuffix("%")
        self.volume_spin.setValue(self._volume)
        form.addRow("模式", self.mode_combo)
        form.addRow("音量", self.volume_spin)
        layout.addLayout(form)

        select_row = QHBoxLayout()
        self.library_button = QPushButton("音乐库…")
        self.library_button.setObjectName("scan")
        self.library_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.selection_label = QLabel("")
        self.selection_label.setStyleSheet(f"color:{_GREEN}; font-weight:600; background:transparent;")
        select_row.addWidget(self.library_button)
        select_row.addWidget(self.selection_label, 1)
        layout.addLayout(select_row)

        explanation = QLabel("合成时从音乐开头开始，自动匹配视频时长。")
        explanation.setWordWrap(True)
        explanation.setStyleSheet(f"color:{_TEXT2}; font-size:11px; background:transparent;")
        layout.addWidget(explanation)

        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.volume_spin.valueChanged.connect(self._on_volume_changed)
        self.library_button.clicked.connect(self._open_library)
        self.refresh_library()
        self._sync_controls()

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def selected_ids(self) -> list[str]:
        return list(self._selected_ids)

    @property
    def volume(self) -> int:
        return self._volume

    def refresh_library(self) -> None:
        try:
            tracks = self._library.list()
        except Exception:
            tracks = []
        self._tracks_by_id = {track["id"]: track for track in tracks if track.get("id")}
        valid = [track_id for track_id in self._selected_ids if track_id in self._tracks_by_id]
        if self._mode == MODE_FIXED:
            valid = valid[:1]
        if valid != self._selected_ids:
            self._selected_ids = valid
            self._save_selected_ids()
        self._sync_controls()

    def set_selected_ids(self, selected_ids: Iterable[str]) -> None:
        valid = [track_id for track_id in _parse_ids(list(selected_ids)) if track_id in self._tracks_by_id]
        self._selected_ids = valid[:1] if self._mode == MODE_FIXED else valid
        if self._mode == MODE_NONE:
            self._selected_ids = []
        self._save_selected_ids()
        self._sync_controls()
        self.selection_changed.emit(self._mode, self.selected_ids, self._volume)

    def _on_mode_changed(self, _index: int) -> None:
        self._mode = self.mode_combo.currentData() or MODE_NONE
        if self._mode == MODE_NONE:
            self._selected_ids = []
        elif self._mode == MODE_FIXED:
            self._selected_ids = self._selected_ids[:1]
        self._settings.setValue("page_video/music_mode", self._mode)
        self._save_selected_ids()
        self._sync_controls()
        self.selection_changed.emit(self._mode, self.selected_ids, self._volume)

    def _on_volume_changed(self, value: int) -> None:
        self._volume = value
        self._settings.setValue("page_video/music_volume", value)
        self.selection_changed.emit(self._mode, self.selected_ids, self._volume)

    def _save_selected_ids(self) -> None:
        self._settings.setValue(
            "page_video/music_selected_ids",
            json.dumps(self._selected_ids, ensure_ascii=False),
        )

    def _sync_controls(self) -> None:
        enabled = self._mode != MODE_NONE
        self.volume_spin.setEnabled(enabled)
        if self._mode == MODE_NONE:
            self.selection_label.setText("未启用")
        elif not self._selected_ids:
            self.selection_label.setText("未选择")
        elif self._mode == MODE_FIXED:
            track = self._tracks_by_id.get(self._selected_ids[0], {})
            self.selection_label.setText(track.get("display_name") or "已选 1 首")
        else:
            self.selection_label.setText(f"已选 {len(self._selected_ids)} 首")

    def _open_library(self) -> None:
        self.refresh_library()
        dialog = self._dialog_factory(
            self._library,
            self._mode,
            self._selected_ids,
            self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh_library()
            self.set_selected_ids(dialog.selected_ids())
