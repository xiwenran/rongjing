import hashlib
import math
import os
import shutil
import subprocess
from collections import OrderedDict
from pathlib import Path
from typing import Optional

from PIL import Image
from PyQt6.QtCore import QSettings, QSignalBlocker, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QDragEnterEvent, QDropEvent, QImage, QPixmap
from PyQt6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QAbstractItemView,
    QButtonGroup,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.collage_batch_runner import CollageBatchRunner
from core.collage_processor import calculate_auto_layout, calculate_auto_split, create_collage
from core.diversifier import DiversifyConfig, diversify_image
from models.collage_model import CollageManager, CollageTemplate
from ui.diversify_widget import DiversifyWidget


_GREEN = "#07C160"
_SIDE = "#EFEFEF"
_CARD = "#FFFFFF"
_INPUT = "#F0F0F0"
_SEP = "#E5E5E5"
_TEXT = "#191919"
_TEXT2 = "#888888"
_RED = "#FA5151"

_PRESETS = ("2×1", "3×1", "4×1", "2×2", "3×2", "4×2")
_ASPECTS = {
    "16:9": 16 / 9,
    "4:3": 4 / 3,
    "3:4": 3 / 4,
    "1:1": 1,
    "自适应": 0,
}

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}

_BG_COLORS = [
    ("#FFFFFF", "白色"),
    ("#F5F5F5", "浅灰"),
    ("#E0E0E0", "灰色"),
    ("#000000", "黑色"),
    ("#FFF8E1", "米黄"),
    ("#E8F5E9", "浅绿"),
    ("#E3F2FD", "浅蓝"),
    ("#FCE4EC", "浅粉"),
]


class _PPTImportWorker(QThread):
    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, pptx_path: str, export_dir: str, parent=None):
        super().__init__(parent)
        self._pptx_path = pptx_path
        self._export_dir = export_dir

    def run(self):
        os.makedirs(self._export_dir, exist_ok=True)
        pdf_path = os.path.join(self._export_dir, "slides.pdf")

        script = (
            'on run argv\n'
            '    set pptxFile to (POSIX file (item 1 of argv))\n'
            '    set pdfFile to (POSIX file (item 2 of argv))\n'
            '    tell application "Microsoft PowerPoint"\n'
            '        open pptxFile\n'
            '        delay 2\n'
            '        set pres to active presentation\n'
            '        save pres in pdfFile as save as PDF\n'
            '        delay 1\n'
            '        close pres saving no\n'
            '    end tell\n'
            'end run'
        )

        self.progress.emit("正在通过 PowerPoint 导出 PDF…")
        try:
            result = subprocess.run(
                ["osascript", "-e", script, self._pptx_path, pdf_path],
                capture_output=True, text=True, timeout=120,
            )
        except subprocess.TimeoutExpired:
            self.failed.emit("PPT 导出超时\nPowerPoint 导出超过 2 分钟，请重试。")
            return

        if result.returncode != 0 or not os.path.isfile(pdf_path):
            self.failed.emit(
                "PPT 导出失败\n"
                "无法调用 PowerPoint 导出 PDF。\n"
                "请确认已安装 Microsoft PowerPoint for Mac。\n\n"
                f"错误信息：{result.stderr[:200]}"
            )
            return

        self.progress.emit("正在生成图片…")
        try:
            import fitz
            doc = fitz.open(pdf_path)
            for i, page in enumerate(doc):
                pix = page.get_pixmap(dpi=200)
                out_path = os.path.join(self._export_dir, f"slide-{i + 1:03d}.png")
                pix.save(out_path)
            doc.close()
        except ImportError:
            self.failed.emit(
                "缺少 PyMuPDF\n"
                "未找到 fitz 模块。\n请在终端执行：pip install PyMuPDF"
            )
            return
        except Exception as exc:
            self.failed.emit(f"图片转换失败\nPDF 转 PNG 失败。\n\n错误信息：{str(exc)[:200]}")
            return

        try:
            os.remove(pdf_path)
        except OSError:
            pass

        self.finished_ok.emit(self._export_dir)


class CollageTab(QWidget):
    """拼图 Tab — 固定高度两栏布局，预览区占右侧主体。"""

    config_changed = pyqtSignal(object)

    def __init__(self, collages_dir: str, parent=None):
        super().__init__(parent)
        self._mgr = CollageManager(collages_dir)
        self._current_collage: CollageTemplate | None = None
        self._preset_buttons: dict[str, QPushButton] = {}
        self._preview_cells: list[QLabel] = []
        self._suppress_emit = False
        self._image_files: list[str] = []
        self._input_dir = ""
        self._source_name = ""
        self._output_dir = ""
        self._excluded_indices: set[int] = set()
        self._preview_collage_index = 0
        self._collage_runner: CollageBatchRunner | None = None
        self._ppt_import_worker: _PPTImportWorker | None = None
        self._ppt_import_current_name = ""
        self._ppt_import_queue: list[str] = []
        self._ppt_import_results: list[tuple[str, str]] = []
        self._cached_preview: Image.Image | None = None
        self._preview_image_cache: OrderedDict[str, Image.Image] = OrderedDict()
        self._preview_refresh_timer = QTimer(self)
        self._preview_refresh_timer.setSingleShot(True)
        self._preview_refresh_timer.timeout.connect(self._refresh_collage_preview)
        self._batch_mode = False
        self._auto_adapt_active = False
        self._subfolder_items: list[tuple[str, list[str]]] = []
        self._source_states: dict[str, dict] = {}
        self._active_source_key = ""

        self._app_data_dir = str(Path(collages_dir).parent)

        self.setAcceptDrops(True)
        self._build_ui()
        self._load_template_list()
        self._wire_signals()
        self._sync_layout_buttons()
        self._refresh_preset_state()
        self._refresh_mini_preview()
        self._restore_last_dirs()

    # ── Public API ────────────────────────────────────────────────
    def get_current_config(self) -> CollageTemplate:
        name = self._current_collage.name if self._current_collage else "未命名拼图"
        return CollageTemplate(
            name=name,
            layout=self._layout_combo.currentData() if hasattr(self, "_layout_combo") else "grid",
            rows=self._row_spin.value(),
            cols=self._col_spin.value(),
            gap=self._gap_spin.value(),
            padding=self._padding_spin.value(),
            background_color=self._background_edit.text().strip() or "#FFFFFF",
            cell_aspect_ratio=self._current_aspect_ratio(),
            output_width=1920,
            output_height=0,
            output_count=self._output_count_spin.value() if hasattr(self, "_output_count_spin") else 0,
        )

    def set_config(self, tpl: CollageTemplate):
        self._suppress_emit = True
        blockers = [
            QSignalBlocker(self._row_spin),
            QSignalBlocker(self._col_spin),
            QSignalBlocker(self._gap_spin),
            QSignalBlocker(self._padding_spin),
            QSignalBlocker(self._aspect_combo),
            QSignalBlocker(self._background_edit),
            QSignalBlocker(self._layout_combo),
        ]
        self._current_collage = tpl
        layout_index = max(0, self._layout_combo.findData(tpl.layout))
        self._layout_combo.setCurrentIndex(layout_index)
        self._row_spin.setValue(tpl.rows)
        self._col_spin.setValue(tpl.cols)
        self._gap_spin.setValue(tpl.gap)
        self._padding_spin.setValue(tpl.padding)
        self._aspect_combo.setCurrentText(self._aspect_label_for(tpl.cell_aspect_ratio))
        self._background_edit.setText(tpl.background_color)
        if tpl.output_count > 0 and hasattr(self, "_output_count_spin"):
            self._output_count_spin.setValue(tpl.output_count)
        self._update_color_swatch()
        self._sync_layout_buttons()
        self._refresh_preset_state()
        self._refresh_mini_preview()
        del blockers
        self._suppress_emit = False
        self._save_current_source_state()
        self._refresh_state()

    def get_diversify_config(self):
        return self._diversify.get_config()

    # ── Drag & drop ───────────────────────────────────────────────
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if not urls:
            return
        path = urls[0].toLocalFile()
        if path.lower().endswith((".pptx", ".ppt")):
            self._start_ppt_imports([path])
        elif os.path.isdir(path):
            self._set_input_dir(path)
        elif os.path.isfile(path) and os.path.splitext(path)[1].lower() in _IMAGE_EXTS:
            self._set_input_dir(os.path.dirname(path))

    # ── UI build ──────────────────────────────────────────────────
    def _build_ui(self):
        self.setObjectName("CollageTab")
        self.setStyleSheet(self._stylesheet())

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_sidebar(), 0)
        root.addWidget(self._build_right_panel(), 1)

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("collage_sidebar")
        sidebar.setFixedWidth(340)
        outer = QVBoxLayout(sidebar)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        body = QWidget()
        body.setObjectName("collage_scroll_body")
        body.setMaximumWidth(340)
        content = QVBoxLayout(body)
        content.setContentsMargins(16, 18, 16, 18)
        content.setSpacing(10)

        self._add_input_section(content)
        content.addWidget(self._sep())
        self._add_layout_section(content)
        content.addWidget(self._sep())
        self._add_template_section(content)
        content.addWidget(self._sep())
        content.addWidget(self._label("批次差异化", "h2"))
        self._diversify = DiversifyWidget(self)
        content.addWidget(self._diversify)
        content.addStretch(1)

        scroll.setWidget(body)
        outer.addWidget(scroll)
        return sidebar

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        layout.addWidget(self._build_preview_area(), 1)
        layout.addWidget(self._build_thumbnail_area(), 0)
        layout.addWidget(self._sep())
        layout.addWidget(self._build_export_bar(), 0)
        return panel

    # ── Sidebar sections ──────────────────────────────────────────
    def _add_input_section(self, content: QVBoxLayout):
        content.addWidget(self._label("输入来源", "h2"))

        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        self._import_folder_btn = QPushButton("导入文件夹")
        self._import_folder_btn.setObjectName("primary")
        self._import_file_btn = QPushButton("导入文件")
        self._import_file_btn.setObjectName("preset_btn")
        self._import_folder_btn.setToolTip("选择一个图片文件夹；若里面有多个子文件夹，会按批量文件夹处理")
        self._import_file_btn.setToolTip("选择一张或多张图片，也可选择一个或多个 PPT")
        mode_row.addWidget(self._import_folder_btn)
        mode_row.addWidget(self._import_file_btn)
        content.addLayout(mode_row)

        self._input_path_label = self._label("未选择（也可直接拖入文件夹、图片或 PPT）", "hint")
        self._input_path_label.setWordWrap(True)
        content.addWidget(self._input_path_label)
        self._input_count_label = self._label("", "hint")
        content.addWidget(self._input_count_label)

        self._subfolder_list = QListWidget()
        self._subfolder_list.setMaximumHeight(140)
        self._subfolder_list.setVisible(False)
        content.addWidget(self._subfolder_list)

    def _add_layout_section(self, content: QVBoxLayout):
        content.addWidget(self._label("拼图布局", "h2"))
        content.addWidget(self._label("快捷预设", "cap"))

        preset_row = QHBoxLayout()
        preset_row.setSpacing(6)
        for name in _PRESETS:
            btn = QPushButton(name)
            btn.setObjectName("preset_btn")
            btn.setCheckable(True)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            preset_row.addWidget(btn)
            self._preset_buttons[name] = btn
        content.addLayout(preset_row)

        adapt_row = QHBoxLayout()
        adapt_row.setSpacing(0)
        self._auto_adapt_btn = QPushButton("✦  自动适配")
        self._auto_adapt_btn.setCheckable(True)
        self._auto_adapt_btn.setObjectName("auto_adapt_btn")
        self._auto_adapt_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        adapt_row.addWidget(self._auto_adapt_btn)
        content.addLayout(adapt_row)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self._row_spin = self._spin(1, 4, 3)
        self._col_spin = self._spin(1, 6, 4)
        self._gap_spin = self._spin(0, 20, 4)
        self._padding_spin = self._spin(0, 40, 0)
        self._layout_combo = QComboBox()
        self._layout_combo.addItem("普通网格", "grid")
        self._layout_combo.addItem("上大图", "hero")
        self._layout_mode_group = QButtonGroup(self)
        self._layout_mode_group.setExclusive(True)
        self._layout_grid_btn = QPushButton("普通网格")
        self._layout_grid_btn.setCheckable(True)
        self._layout_grid_btn.setObjectName("preset_btn")
        self._layout_hero_btn = QPushButton("上大图")
        self._layout_hero_btn.setCheckable(True)
        self._layout_hero_btn.setObjectName("preset_btn")
        self._layout_mode_group.addButton(self._layout_grid_btn)
        self._layout_mode_group.addButton(self._layout_hero_btn)
        layout_type_row = QHBoxLayout()
        layout_type_row.setSpacing(6)
        layout_type_row.addWidget(self._layout_grid_btn)
        layout_type_row.addWidget(self._layout_hero_btn)
        self._add_grid_field(grid, 0, 0, "布局类型", layout_type_row)
        self._add_grid_field(grid, 0, 1, "下方列数", self._col_spin)
        self._add_grid_field(grid, 1, 0, "下方行数", self._row_spin)
        self._add_grid_field(grid, 1, 1, "间距 (px)", self._gap_spin)
        self._add_grid_field(grid, 2, 0, "边距 (px)", self._padding_spin)

        self._aspect_combo = QComboBox()
        self._aspect_combo.addItems(_ASPECTS.keys())
        self._aspect_combo.setCurrentText("自适应")
        self._background_edit = QLineEdit()
        self._background_edit.setMaxLength(7)
        self._background_edit.setText(self._last_background_color())
        self._color_swatch = QLabel()
        self._color_swatch.setObjectName("color_swatch")
        self._color_swatch.setFixedSize(28, 28)
        self._color_swatch.setCursor(Qt.CursorShape.PointingHandCursor)
        self._color_swatch.mousePressEvent = lambda event: self._choose_background_color()
        bg_row = QHBoxLayout()
        bg_row.setSpacing(6)
        bg_row.addWidget(self._background_edit, 1)
        bg_row.addWidget(self._color_swatch, 0)
        self._add_grid_field(grid, 2, 1, "单元格比例", self._aspect_combo)
        self._add_grid_field(grid, 3, 0, "背景色", bg_row)

        color_presets_row = QHBoxLayout()
        color_presets_row.setSpacing(2)
        color_presets_row.setContentsMargins(0, 0, 0, 0)
        for hex_color, tip in _BG_COLORS:
            swatch = QWidget()
            swatch.setFixedHeight(28)
            swatch.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            swatch.setCursor(Qt.CursorShape.PointingHandCursor)
            swatch.setToolTip(f"{tip} ({hex_color})")
            border_color = "#CCC" if hex_color == "#FFFFFF" else _SEP
            swatch.setStyleSheet(
                f"background: {hex_color}; border: 1px solid {border_color}; border-radius: 3px;"
            )
            swatch.mousePressEvent = lambda event, c=hex_color: self._set_bg_color(c)
            color_presets_row.addWidget(swatch, 1)
        grid.addLayout(color_presets_row, 4, 0, 1, 2)

        content.addLayout(grid)
        self._update_color_swatch()

        self._mini_preview_label = self._label("", "cap")
        content.addWidget(self._mini_preview_label)
        self._mini_grid_frame = QWidget()
        self._mini_grid_frame.setObjectName("mini_grid_frame")
        self._mini_grid = QGridLayout(self._mini_grid_frame)
        self._mini_grid.setContentsMargins(8, 8, 8, 8)
        self._mini_grid.setSpacing(5)
        content.addWidget(self._mini_grid_frame)

        split_box = QVBoxLayout()
        split_box.setSpacing(4)
        split_box.addWidget(self._label("自动拆分", "cap"))
        split_out_row = QHBoxLayout()
        split_out_row.setSpacing(6)
        self._output_count_spin = self._spin(1, 1, 1)
        self._output_count_spin.setFixedWidth(70)
        split_out_row.addWidget(self._output_count_spin)
        self._pages_per_label = self._label("张，每张 0 页", "cap")
        split_out_row.addWidget(self._pages_per_label)
        split_out_row.addStretch(1)
        self._selected_pages_label = self._label("已选 0/0 页", "hint")
        split_out_row.addWidget(self._selected_pages_label)
        split_box.addLayout(split_out_row)
        content.addLayout(split_box)

    def _add_template_section(self, content: QVBoxLayout):
        content.addWidget(self._label("拼图模板", "h2"))
        tpl_frame = QWidget()
        tpl_frame.setObjectName("tpl_list_frame")
        frame_layout = QVBoxLayout(tpl_frame)
        frame_layout.setContentsMargins(4, 4, 4, 4)
        self._template_list = QListWidget()
        self._template_list.setMinimumHeight(80)
        self._template_list.setMaximumHeight(150)
        self._template_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        frame_layout.addWidget(self._template_list)
        content.addWidget(tpl_frame)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._save_template_btn = QPushButton("保存模板")
        self._delete_template_btn = QPushButton("批量删除")
        self._delete_template_btn.setObjectName("danger")
        row.addWidget(self._save_template_btn)
        row.addWidget(self._delete_template_btn)
        content.addLayout(row)

    # ── Right panel sections ──────────────────────────────────────
    def _build_preview_area(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._collage_preview_label = QLabel("选择文件夹或拖入 PPT 开始预览")
        self._collage_preview_label.setObjectName("hint")
        self._collage_preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._collage_preview_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._collage_preview_label.setStyleSheet(
            f"background: {_CARD}; border: 1px solid {_SEP}; border-radius: 8px;"
        )
        self._collage_preview_label.setMouseTracking(True)
        self._collage_preview_label.mouseMoveEvent = self._on_preview_mouse_move
        self._collage_preview_label.leaveEvent = self._on_preview_mouse_leave
        self._original_pixmap: QPixmap | None = None
        self._diversified_pixmap: QPixmap | None = None
        layout.addWidget(self._collage_preview_label, 1)

        nav_row = QHBoxLayout()
        nav_row.setSpacing(8)
        self._preview_prev_btn = QPushButton("◀ 上一张")
        self._preview_next_btn = QPushButton("下一张 ▶")
        self._preview_prev_btn.clicked.connect(lambda: self._change_preview(-1))
        self._preview_next_btn.clicked.connect(lambda: self._change_preview(1))
        self._preview_info_label = self._label("", "hint")
        self._preview_info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        nav_row.addWidget(self._preview_prev_btn)
        nav_row.addWidget(self._preview_info_label, 1)
        nav_row.addWidget(self._preview_next_btn)
        layout.addLayout(nav_row)

        return container

    def _build_thumbnail_area(self) -> QWidget:
        container = QWidget()
        container.setFixedHeight(170)
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        header = QHBoxLayout()
        header.addWidget(self._label("页面缩略图（点击排除/恢复）", "cap"))
        header.addStretch(1)
        outer.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._thumb_body = QWidget()
        self._thumb_grid = QHBoxLayout(self._thumb_body)
        self._thumb_grid.setContentsMargins(0, 0, 0, 0)
        self._thumb_grid.setSpacing(6)
        scroll.setWidget(self._thumb_body)
        outer.addWidget(scroll, 1)

        return container

    def _build_export_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(12)

        self._choose_output_btn = QPushButton("输出文件夹")
        self._choose_output_btn.clicked.connect(self._choose_output_dir)
        self._output_path_label = self._label("未选择（成品会按来源名称新建子文件夹）", "hint")
        self._output_path_label.setMinimumWidth(60)
        self._format_combo = QComboBox()
        self._format_combo.addItems(["JPEG", "PNG"])
        self._run_collage_btn = QPushButton("开始导出")
        self._run_collage_btn.setObjectName("primary")
        self._run_collage_btn.setMinimumHeight(40)
        self._run_collage_btn.clicked.connect(self._run_collage_batch)
        self._run_all_collage_btn = QPushButton("导出全部")
        self._run_all_collage_btn.setObjectName("primary")
        self._run_all_collage_btn.setMinimumHeight(40)
        self._run_all_collage_btn.setVisible(False)
        self._run_all_collage_btn.clicked.connect(self._run_all_collage_batch)
        self._cancel_collage_btn = QPushButton("取消")
        self._cancel_collage_btn.setVisible(False)
        self._cancel_collage_btn.clicked.connect(self._abort_collage_batch)

        layout.addWidget(self._choose_output_btn, 0)
        layout.addWidget(self._output_path_label, 1)
        layout.addWidget(self._format_combo, 0)
        layout.addWidget(self._run_collage_btn, 0)
        layout.addWidget(self._run_all_collage_btn, 0)
        layout.addWidget(self._cancel_collage_btn, 0)

        self._collage_progress = QProgressBar()
        self._collage_progress.setVisible(False)
        self._collage_status = self._label("", "hint")
        self._collage_status.setVisible(False)

        wrapper = QWidget()
        wl = QVBoxLayout(wrapper)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(4)
        wl.addWidget(bar)
        wl.addWidget(self._collage_progress)
        wl.addWidget(self._collage_status)
        return wrapper

    # ── Signal wiring ─────────────────────────────────────────────
    def _wire_signals(self):
        for name, btn in self._preset_buttons.items():
            btn.clicked.connect(lambda checked=False, n=name: self._on_preset_clicked(n))
        for spin in (self._row_spin, self._col_spin, self._gap_spin, self._padding_spin):
            spin.valueChanged.connect(self._on_form_changed)
        self._layout_combo.currentIndexChanged.connect(self._on_layout_changed)
        self._layout_grid_btn.clicked.connect(lambda: self._set_layout_mode("grid"))
        self._layout_hero_btn.clicked.connect(lambda: self._set_layout_mode("hero"))
        self._aspect_combo.currentTextChanged.connect(self._on_form_changed)
        self._background_edit.textChanged.connect(self._on_background_changed)
        self._template_list.itemClicked.connect(self._on_template_item_clicked)
        self._save_template_btn.clicked.connect(self._save_template_to_disk)
        self._delete_template_btn.clicked.connect(self._delete_selected_template)
        self._diversify.config_changed.connect(self._on_diversify_changed)
        self.config_changed.connect(lambda _cfg: self._refresh_state())
        self._output_count_spin.valueChanged.connect(self._on_output_count_changed)
        self._import_folder_btn.clicked.connect(self._choose_input_dir)
        self._import_file_btn.clicked.connect(self._choose_files)
        self._subfolder_list.currentRowChanged.connect(self._on_subfolder_selected)
        self._auto_adapt_btn.clicked.connect(self._on_auto_adapt_clicked)

    # ── Input handling ────────────────────────────────────────────
    def _choose_input_dir(self):
        start = self._input_dir or ""
        path = QFileDialog.getExistingDirectory(self, "选择图片文件夹", start)
        if path:
            self._set_input_dir(path)

    def _choose_files(self):
        start = self._input_dir or ""
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择图片或 PPT 文件",
            start,
            "图片或 PPT (*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff *.pptx *.ppt)",
        )
        if not paths:
            return
        ppt_paths = [p for p in paths if p.lower().endswith((".pptx", ".ppt"))]
        image_paths = [p for p in paths if os.path.splitext(p)[1].lower() in _IMAGE_EXTS]
        if ppt_paths:
            self._start_ppt_imports(ppt_paths)
        elif image_paths:
            self._set_image_files(image_paths, "已选图片", os.path.dirname(image_paths[0]))

    def _start_ppt_imports(self, ppt_paths: list[str]):
        if self._ppt_import_worker and self._ppt_import_worker.isRunning():
            QMessageBox.warning(self, "PPT 正在导入", "请等待当前 PPT 导入完成。")
            return
        self._ppt_import_queue = list(ppt_paths)
        self._ppt_import_results = []
        self._ppt_import_current_name = ""
        self._import_next_ppt()

    def _import_next_ppt(self):
        if not self._ppt_import_queue:
            self._finish_ppt_imports()
            return
        self._import_pptx(self._ppt_import_queue.pop(0))

    def _import_pptx(self, pptx_path: str):
        if self._ppt_import_worker and self._ppt_import_worker.isRunning():
            QMessageBox.warning(self, "PPT 正在导入", "请等待当前 PPT 导入完成。")
            return

        display_name = self._unique_import_name(Path(pptx_path).stem)
        self._ppt_import_current_name = display_name
        export_dir = self._ppt_export_dir(pptx_path)
        cached_pngs = []
        if os.path.isdir(export_dir):
            cached_pngs = [
                name for name in os.listdir(export_dir)
                if os.path.splitext(name)[1].lower() == ".png"
            ]
        if cached_pngs:
            answer = QMessageBox.question(
                self,
                "使用已导出的图片",
                f"已导出过 {len(cached_pngs)} 页，是否直接使用？",
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._ppt_import_results.append((display_name, export_dir))
                self._import_next_ppt()
                return

        if os.path.isdir(export_dir):
            shutil.rmtree(export_dir, ignore_errors=True)
        os.makedirs(export_dir, exist_ok=True)

        self._import_folder_btn.setEnabled(False)
        self._import_file_btn.setEnabled(False)
        self._ppt_import_worker = _PPTImportWorker(pptx_path, export_dir, self)
        self._ppt_import_worker.progress.connect(self._on_ppt_import_progress)
        self._ppt_import_worker.finished_ok.connect(self._on_ppt_import_done)
        self._ppt_import_worker.failed.connect(self._on_ppt_import_failed)
        self._ppt_import_worker.finished.connect(self._ppt_import_worker.deleteLater)
        self._ppt_import_worker.finished.connect(
            lambda worker=self._ppt_import_worker: self._clear_ppt_import_worker(worker)
        )
        self._ppt_import_worker.start()

    def _on_ppt_import_progress(self, msg: str):
        self._input_path_label.setText(msg)
        self._collage_preview_label.setPixmap(QPixmap())
        self._collage_preview_label.setText(f"📎 PPT 导入中\n\n{msg}\n\n如需授权 PowerPoint，请切换到 PowerPoint 窗口点击允许")

    def _on_ppt_import_done(self, export_dir: str):
        name = self._ppt_import_current_name or Path(export_dir).name
        self._ppt_import_results.append((name, export_dir))
        self._ppt_import_current_name = ""
        self._restore_ppt_import_buttons()
        self._import_next_ppt()

    def _on_ppt_import_failed(self, error: str):
        title, _, message = error.partition("\n")
        QMessageBox.warning(self, title or "PPT 导入失败", message or error)
        self._input_path_label.setText(title or "PPT 导入失败")
        self._collage_preview_label.setText("选择文件夹或拖入 PPT 开始预览")
        self._ppt_import_current_name = ""
        self._restore_ppt_import_buttons()

    def _restore_ppt_import_buttons(self):
        self._import_folder_btn.setEnabled(True)
        self._import_file_btn.setEnabled(True)

    def _clear_ppt_import_worker(self, worker: _PPTImportWorker):
        if self._ppt_import_worker is worker:
            self._ppt_import_worker = None

    def _set_input_dir(self, path: str):
        self._input_dir = path
        QSettings("融景", "RongJing").setValue("collage/last_input_dir", path)
        subfolders = self._find_image_subfolders(path)
        if subfolders:
            self._batch_mode = True
            self._scan_subfolders(path)
            return
        self._batch_mode = False
        self._set_image_files(self._scan_image_files(path), Path(path).name or "图片文件夹", path)

    def _set_batch_mode(self, batch: bool):
        self._batch_mode = batch
        self._subfolder_list.setVisible(batch)
        if batch and self._input_dir:
            self._scan_subfolders(self._input_dir)
        elif not batch and self._input_dir:
            self._set_input_dir(self._input_dir)

    def _scan_subfolders(self, path: str):
        self._save_current_source_state()
        self._source_states = {}
        self._active_source_key = ""
        self._subfolder_items = []
        self._subfolder_list.clear()
        if not os.path.isdir(path):
            return
        for name, files in self._find_image_subfolders(path):
            self._subfolder_items.append((name, files))
            self._subfolder_list.addItem(f"{name}  ({len(files)} 张)")

        total_files = sum(len(f) for _, f in self._subfolder_items)
        self._input_path_label.setText(path)
        self._input_count_label.setText(
            f"共 {len(self._subfolder_items)} 个子文件夹，{total_files} 张图片"
        )

        if self._subfolder_items:
            self._subfolder_list.setCurrentRow(0)
            self._on_subfolder_selected(0)
        elif self._scan_image_files(path):
            self._set_batch_mode(False)

    def _on_subfolder_selected(self, row: int):
        if row < 0 or row >= len(self._subfolder_items):
            return
        self._save_current_source_state()
        name, files = self._subfolder_items[row]
        self._image_files = files
        self._source_name = name
        self._preview_image_cache.clear()
        self._restore_source_state(name, files)
        self._refresh_thumbnails()
        self._refresh_state()

    def _set_image_files(self, files: list[str], source_name: str, input_dir: str):
        self._save_current_source_state()
        self._image_files = files
        self._source_name = source_name or "拼图"
        self._input_dir = input_dir or self._input_dir
        self._preview_image_cache.clear()
        self._source_states = {}
        self._active_source_key = ""
        self._subfolder_items = []
        self._subfolder_list.clear()
        self._subfolder_list.setVisible(False)
        self._input_path_label.setText(input_dir or self._source_name)
        self._input_count_label.setText(f"已扫描到 {len(self._image_files)} 张图片")
        self._restore_source_state(self._source_name, self._image_files)
        self._refresh_thumbnails()
        self._refresh_state()

    def _finish_ppt_imports(self):
        if not self._ppt_import_results:
            return
        self._save_current_source_state()
        self._source_states = {}
        self._active_source_key = ""
        if len(self._ppt_import_results) == 1:
            name, export_dir = self._ppt_import_results[0]
            self._batch_mode = False
            self._set_image_files(self._scan_image_files(export_dir), name, export_dir)
            return
        items = [
            (name, self._scan_image_files(export_dir))
            for name, export_dir in self._ppt_import_results
        ]
        self._subfolder_items = [(name, files) for name, files in items if files]
        self._batch_mode = True
        self._image_files = []
        self._excluded_indices = set()
        self._preview_image_cache.clear()
        self._subfolder_list.clear()
        for name, files in self._subfolder_items:
            self._subfolder_list.addItem(f"{name}  ({len(files)} 张)")
        self._subfolder_list.setVisible(True)
        total_files = sum(len(files) for _, files in self._subfolder_items)
        self._input_path_label.setText("多个 PPT")
        self._input_count_label.setText(
            f"共 {len(self._subfolder_items)} 个 PPT，{total_files} 张图片"
        )
        if self._subfolder_items:
            self._subfolder_list.setCurrentRow(0)
            self._on_subfolder_selected(0)

    def _choose_output_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择输出文件夹", self._output_dir)
        if path:
            self._output_dir = path
            QSettings("融景", "RongJing").setValue("collage/last_output_dir", path)
            self._output_path_label.setText(f"{path}（按来源名称建子文件夹）")

    def _restore_last_dirs(self):
        s = QSettings("融景", "RongJing")
        last_in = s.value("collage/last_input_dir", "", type=str)
        if last_in:
            self._input_path_label.setText(last_in)
        last_out = s.value("collage/last_output_dir", "", type=str)
        if last_out:
            self._output_dir = last_out
            self._output_path_label.setText(f"{last_out}（按来源名称建子文件夹）")

    # ── Scan files (recursive) ────────────────────────────────────
    def _scan_image_files(self, path: str) -> list[str]:
        if not os.path.isdir(path):
            return []
        files = []
        for root, _dirs, names in os.walk(path):
            for name in sorted(names):
                if os.path.splitext(name)[1].lower() in _IMAGE_EXTS:
                    files.append(os.path.join(root, name))
        files.sort(key=lambda p: self._natural_key(p))
        return files

    def _find_image_subfolders(self, path: str) -> list[tuple[str, list[str]]]:
        if not os.path.isdir(path):
            return []
        items = []
        for name in sorted(os.listdir(path), key=self._natural_key):
            sub = os.path.join(path, name)
            if os.path.isdir(sub):
                files = self._scan_image_files(sub)
                if files:
                    items.append((name, files))
        return items

    @staticmethod
    def _natural_key(s: str):
        import re
        return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', s)]

    # ── Preview ───────────────────────────────────────────────────
    def _schedule_collage_preview_refresh(self):
        self._preview_refresh_timer.start(90)

    def _refresh_collage_preview(self):
        ranges = self._current_ranges()
        if not ranges:
            self._cached_preview = None
            self._collage_preview_label.setPixmap(QPixmap())
            self._collage_preview_label.setText("选择文件夹或拖入 PPT 开始预览")
            self._preview_info_label.setText("")
            return

        idx = min(self._preview_collage_index, len(ranges) - 1)
        self._preview_collage_index = idx
        selected = self._selected_image_files()
        start, end = ranges[idx]
        images = []
        try:
            for p in selected[start:end]:
                images.append(self._load_preview_image(p).copy())
            cfg = self.get_current_config()
            preview = create_collage(
                images, cfg.layout, cfg.rows, cfg.cols,
                gap=cfg.gap, padding=cfg.padding,
                background_color=cfg.background_color,
                cell_aspect_ratio=cfg.cell_aspect_ratio,
                output_width=1200, output_height=0,
            )
            self._cached_preview = preview
            self._display_preview(preview)
            self._preview_info_label.setText(
                f"第 {idx + 1}/{len(ranges)} 张 · {end - start} 页"
            )
        except Exception as exc:
            self._cached_preview = None
            self._collage_preview_label.setPixmap(QPixmap())
            self._collage_preview_label.setText(f"预览失败：{exc}")

    def _load_preview_image(self, path: str) -> Image.Image:
        cached = self._preview_image_cache.get(path)
        if cached is not None:
            self._preview_image_cache.move_to_end(path)
            return cached
        with Image.open(path) as img:
            loaded = img.convert("RGB")
            loaded.thumbnail((1600, 1600), Image.LANCZOS)
        self._preview_image_cache[path] = loaded
        while len(self._preview_image_cache) > 40:
            self._preview_image_cache.popitem(last=False)
        return loaded

    def _display_preview(self, img: Image.Image):
        label = self._collage_preview_label
        lw, lh = label.width() - 4, label.height() - 4
        if lw < 100 or lh < 100:
            lw, lh = 600, 400
        display = img.copy()
        display.thumbnail((lw, lh), Image.LANCZOS)
        self._original_pixmap = self._pil_to_pixmap(display)

        cfg = self._diversify.get_config()
        if cfg.enabled:
            import time
            varied = diversify_image(img, cfg, seed=hash(time.time_ns()))
            display_v = varied.copy()
            display_v.thumbnail((lw, lh), Image.LANCZOS)
            self._diversified_pixmap = self._pil_to_pixmap(display_v)
        else:
            self._diversified_pixmap = None

        label.setText("")
        label.setPixmap(self._original_pixmap)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._cached_preview:
            QTimer.singleShot(50, lambda: self._display_preview(self._cached_preview))

    def _change_preview(self, delta: int):
        ranges = self._current_ranges()
        if not ranges:
            return
        new_idx = self._preview_collage_index + delta
        if 0 <= new_idx < len(ranges):
            self._preview_collage_index = new_idx
            self._save_current_source_state()
            self._refresh_state()

    def _on_preview_mouse_move(self, event):
        if self._original_pixmap is None or self._diversified_pixmap is None:
            return
        from PyQt6.QtGui import QPainter, QPen
        label = self._collage_preview_label
        pm = label.pixmap()
        if pm is None or pm.isNull():
            return
        pm_w = self._original_pixmap.width()
        x_offset = (label.width() - pm_w) / 2
        mouse_x = event.position().x() - x_offset
        x_ratio = max(0.0, min(1.0, mouse_x / max(1, pm_w)))
        split_x = int(x_ratio * pm_w)
        composite = self._original_pixmap.copy()
        painter = QPainter(composite)
        src_rect = composite.rect()
        src_rect.setLeft(split_x)
        painter.drawPixmap(src_rect, self._diversified_pixmap, src_rect)
        pen = QPen(QColor(_GREEN))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawLine(split_x, 0, split_x, composite.height())
        painter.end()
        label.setPixmap(composite)

    def _on_preview_mouse_leave(self, _event):
        if self._original_pixmap is not None:
            self._collage_preview_label.setPixmap(self._original_pixmap)

    # ── Thumbnails ────────────────────────────────────────────────
    def _refresh_thumbnails(self):
        while self._thumb_grid.count():
            item = self._thumb_grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        for index, path in enumerate(self._image_files):
            self._thumb_grid.addWidget(self._thumbnail_widget(index, path))
        self._thumb_grid.addStretch(1)

    def _thumbnail_widget(self, index: int, path: str) -> QWidget:
        tile = QWidget()
        tile.setObjectName("thumb_tile")
        tile.setFixedSize(80, 100)
        excluded = index in self._excluded_indices
        border = _RED if excluded else _GREEN
        tile.setStyleSheet(
            f"QWidget#thumb_tile {{ background: white; border: 2px solid {border}; border-radius: 6px; }}"
        )
        layout = QVBoxLayout(tile)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)

        image_label = QLabel()
        image_label.setFixedSize(72, 72)
        image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image_label.setPixmap(self._load_thumb_pixmap(path))

        mark = QLabel("×" if excluded else "✓", tile)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setStyleSheet(
            f"background: {border}; color: white; border-radius: 8px; font-weight: 700;"
        )
        mark.setFixedSize(16, 16)
        mark.move(58, 3)

        num = QLabel(f"P{index + 1}")
        num.setAlignment(Qt.AlignmentFlag.AlignCenter)
        num.setStyleSheet("border: 0; color: #666; font-size: 10px;")
        layout.addWidget(image_label, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(num)
        tile.setCursor(Qt.CursorShape.PointingHandCursor)
        tile.mousePressEvent = lambda event, i=index: self._toggle_excluded(i)
        return tile

    def _toggle_excluded(self, index: int):
        if index < 0 or index >= len(self._image_files):
            return
        if index in self._excluded_indices:
            self._excluded_indices.remove(index)
        else:
            self._excluded_indices.add(index)
        self._save_current_source_state()
        self._refresh_thumbnails()
        self._refresh_state()

    # ── Batch run ─────────────────────────────────────────────────
    def _run_collage_batch(self, callback=None):
        if not self._output_dir:
            QMessageBox.warning(self, "提示", "请先选择输出文件夹")
            return

        self._run_batch_single(callback)

    def _run_all_collage_batch(self, callback=None):
        if not self._output_dir:
            QMessageBox.warning(self, "提示", "请先选择输出文件夹")
            return
        self._run_batch_multi_folder(callback)

    def _run_batch_single(self, callback=None):
        if not self._image_files:
            QMessageBox.warning(self, "提示", "请先选择图片文件夹")
            return
        cfg = self.get_current_config()
        diversify_cfg = self._diversify.get_config()
        self._collage_runner = CollageBatchRunner(
            self._image_files, cfg, self._output_dir_for_source(self._source_name),
            self._format_combo.currentText(),
            self._output_count_spin.value(),
            set(self._excluded_indices),
            diversify_cfg, self,
        )
        self._start_runner(callback)

    def _run_batch_multi_folder(self, callback=None):
        if not self._subfolder_items:
            QMessageBox.warning(self, "提示", "未找到包含图片的子文件夹")
            return

        self._save_current_source_state()
        cfg = self.get_current_config()
        diversify_cfg = self._diversify.get_config()

        self._batch_queue = [
            self._build_source_run_item(name, files, cfg)
            for name, files in self._subfolder_items
        ]
        self._batch_done = 0
        self._batch_total = len(self._batch_queue)
        self._batch_callback = callback
        self._batch_diversify_cfg = diversify_cfg
        self._run_next_in_queue()

    def _run_next_in_queue(self):
        if not self._batch_queue:
            self._on_collage_finished(True, f"全部导出完成，共处理 {self._batch_total} 份来源。")
            if self._batch_callback:
                self._batch_callback(True, "")
            return

        name, files, cfg, output_count, excluded = self._batch_queue.pop(0)
        out_dir = self._output_dir_for_source(name)

        self._collage_runner = CollageBatchRunner(
            files, cfg, out_dir,
            self._format_combo.currentText(),
            output_count, excluded,
            self._batch_diversify_cfg, self,
        )
        self._collage_runner.progress.connect(self._on_collage_progress)
        self._collage_runner.finished.connect(self._on_batch_item_finished)
        self._show_running_ui()
        self._collage_status.setText(f"[{self._batch_done + 1}/{self._batch_total}] {name}")
        self._collage_runner.start()

    def _build_source_run_item(
        self, name: str, files: list[str], default_cfg: CollageTemplate
    ):
        state = self._source_states.get(self._source_key(name, files))
        cfg = self._config_from_state(state, default_cfg)
        excluded = self._excluded_from_state(state, len(files))
        selected_count = max(0, len(files) - len(excluded))
        min_outputs = max(1, math.ceil(selected_count / max(1, cfg.total_cells))) if selected_count else 1
        output_count = int(state.get("output_count", 0)) if state else 0
        output_count = min(max(min_outputs, output_count), max(1, selected_count))
        return name, list(files), cfg, output_count, set(excluded)

    def _output_dir_for_source(self, source_name: str) -> str:
        clean = self._safe_folder_name(source_name or "拼图")
        return os.path.join(self._output_dir, clean)

    @staticmethod
    def _safe_folder_name(name: str) -> str:
        cleaned = "".join("_" if ch in '/\\:*?"<>|' else ch for ch in name).strip()
        return cleaned or "拼图"

    def _on_batch_item_finished(self, success: bool, msg: str):
        self._batch_done += 1
        if not success:
            self._on_collage_finished(False, msg)
            return
        self._run_next_in_queue()

    def _start_runner(self, callback=None):
        self._collage_runner.progress.connect(self._on_collage_progress)
        self._collage_runner.finished.connect(self._on_collage_finished)
        if callback:
            self._collage_runner.finished.connect(callback)
        self._show_running_ui()
        self._collage_runner.start()

    def _show_running_ui(self):
        self._run_collage_btn.setVisible(False)
        self._run_all_collage_btn.setVisible(False)
        self._cancel_collage_btn.setVisible(True)
        self._collage_progress.setVisible(True)
        self._collage_status.setVisible(True)
        self._collage_progress.setValue(0)
        self._collage_status.setText("正在拼图…")

    def _abort_collage_batch(self):
        if self._collage_runner:
            self._collage_runner.abort()
        self._batch_queue = []

    def _on_collage_progress(self, done: int, total: int, msg: str):
        self._collage_progress.setMaximum(max(1, total))
        self._collage_progress.setValue(done)
        self._collage_status.setText(f"[{done}/{total}] {msg}")

    def _on_collage_finished(self, success: bool, msg: str):
        self._refresh_export_buttons()
        self._cancel_collage_btn.setVisible(False)
        self._collage_status.setText(msg)
        if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            if success:
                QMessageBox.information(self, "完成", msg)
            else:
                QMessageBox.warning(self, "处理结果", msg)

    # ── Split mode ────────────────────────────────────────────────
    def _on_auto_adapt_clicked(self):
        selected_count = len(self._selected_image_files())
        if selected_count == 0:
            self._auto_adapt_btn.setChecked(False)
            return
        output_count = self._output_count_spin.value() if hasattr(self, "_output_count_spin") else 1
        per_collage = math.ceil(selected_count / max(1, output_count))
        rows, cols = calculate_auto_layout(per_collage)
        blockers = [QSignalBlocker(self._row_spin), QSignalBlocker(self._col_spin)]
        self._row_spin.setValue(rows)
        self._col_spin.setValue(cols)
        del blockers
        self._auto_adapt_active = True
        self._current_collage = None
        self._refresh_preset_state()
        self._refresh_mini_preview()
        self._save_current_source_state()
        self._refresh_state()
        self._emit_config_changed()

    # ── State refresh ─────────────────────────────────────────────
    def _refresh_state(self):
        if not hasattr(self, "_output_count_spin"):
            return
        selected = self._selected_image_files()
        total = len(self._image_files)
        selected_count = len(selected)
        max_outputs = max(1, selected_count)
        min_outputs = self._minimum_output_count(selected_count)
        blocker = QSignalBlocker(self._output_count_spin)
        if self._output_count_spin.minimum() != min_outputs:
            self._output_count_spin.setMinimum(min_outputs)
        if self._output_count_spin.maximum() != max_outputs:
            self._output_count_spin.setMaximum(max_outputs)
        if self._output_count_spin.value() > max_outputs:
            self._output_count_spin.setValue(max_outputs)
        if self._output_count_spin.value() < min_outputs:
            self._output_count_spin.setValue(min_outputs)
        del blocker
        ranges = self._current_ranges()
        pages_per = max((end - start for start, end in ranges), default=0)
        self._selected_pages_label.setText(f"已选 {selected_count}/{total} 页")
        self._pages_per_label.setText(f"张，每张 {pages_per} 页")
        self._preview_prev_btn.setEnabled(self._preview_collage_index > 0)
        self._preview_next_btn.setEnabled(self._preview_collage_index < len(ranges) - 1)
        self._refresh_export_buttons(len(ranges))

        self._schedule_collage_preview_refresh()

    def _refresh_export_buttons(self, output_count: int | None = None):
        if not hasattr(self, "_run_all_collage_btn"):
            return
        if output_count is None:
            output_count = len(self._current_ranges())
        has_many_sources = self._batch_mode and len(self._subfolder_items) > 1
        self._run_collage_btn.setVisible(True)
        self._run_collage_btn.setText(
            f"导出当前（{output_count} 张）" if has_many_sources else f"开始导出（{output_count} 张）"
        )
        self._run_all_collage_btn.setVisible(has_many_sources)
        self._run_all_collage_btn.setText(f"导出全部（{len(self._subfolder_items)} 份）")

    def _reset_output_count(self):
        selected_count = len(self._selected_image_files())
        value = self._minimum_output_count(selected_count)
        blocker = QSignalBlocker(self._output_count_spin)
        self._output_count_spin.setRange(value, max(1, selected_count))
        self._output_count_spin.setValue(value)
        del blocker

    def _selected_image_files(self) -> list[str]:
        return [p for i, p in enumerate(self._image_files) if i not in self._excluded_indices]

    def _current_ranges(self):
        return calculate_auto_split(
            len(self._selected_image_files()),
            self._output_count_spin.value(),
            max(1, self.get_current_config().total_cells),
        )

    def _minimum_output_count(self, selected_count: int) -> int:
        if selected_count <= 0:
            return 1
        cells = max(1, self.get_current_config().total_cells)
        return max(1, math.ceil(selected_count / cells))

    def _minimum_output_count_for_state(self, state: dict | None, selected_count: int) -> int:
        if selected_count <= 0:
            return 1
        cfg = self._config_from_state(state, self.get_current_config())
        return max(1, math.ceil(selected_count / max(1, cfg.total_cells)))

    # ── Behaviors ─────────────────────────────────────────────────
    def _on_preset_clicked(self, name: str):
        rows, cols = (int(part) for part in name.split("×", 1))
        layout_blocker = QSignalBlocker(self._layout_combo)
        self._layout_combo.setCurrentIndex(max(0, self._layout_combo.findData("grid")))
        del layout_blocker
        self._sync_layout_buttons()
        blockers = [QSignalBlocker(self._row_spin), QSignalBlocker(self._col_spin)]
        self._row_spin.setValue(rows)
        self._col_spin.setValue(cols)
        del blockers
        self._auto_adapt_active = False
        self._reset_output_count()
        self._refresh_preset_state()
        self._refresh_mini_preview()
        self._save_current_source_state()
        self._refresh_state()
        self._emit_config_changed()

    def _on_layout_changed(self, *_args):
        self._sync_layout_buttons()
        if self._layout_combo.currentData() == "hero" and self._row_spin.value() == 3 and self._col_spin.value() == 4:
            blockers = [QSignalBlocker(self._row_spin), QSignalBlocker(self._col_spin)]
            self._row_spin.setValue(2)
            self._col_spin.setValue(2)
            del blockers
        self._reset_output_count()
        self._on_form_changed()

    def _set_layout_mode(self, mode: str):
        idx = self._layout_combo.findData(mode)
        if idx >= 0 and idx != self._layout_combo.currentIndex():
            self._layout_combo.setCurrentIndex(idx)
        else:
            self._on_layout_changed()

    def _sync_layout_buttons(self):
        if not hasattr(self, "_layout_grid_btn"):
            return
        mode = self._layout_combo.currentData()
        blockers = [QSignalBlocker(self._layout_grid_btn), QSignalBlocker(self._layout_hero_btn)]
        self._layout_grid_btn.setChecked(mode == "grid")
        self._layout_hero_btn.setChecked(mode == "hero")
        del blockers

    def _on_form_changed(self, *_args):
        self._current_collage = None
        self._auto_adapt_active = False
        if self.sender() in (self._row_spin, self._col_spin):
            self._reset_output_count()
        self._refresh_preset_state()
        self._refresh_mini_preview()
        self._save_current_source_state()
        self._refresh_state()
        self._emit_config_changed()

    def _on_background_changed(self, *_args):
        self._current_collage = None
        self._update_color_swatch()
        self._remember_background_color()
        self._save_current_source_state()
        self._refresh_state()
        self._emit_config_changed()

    def _on_output_count_changed(self, *_args):
        self._save_current_source_state()
        self._refresh_state()

    def _on_template_item_clicked(self, item: QListWidgetItem):
        tpl = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(tpl, CollageTemplate):
            self._auto_adapt_active = False
            self.set_config(tpl)

    def _on_diversify_changed(self, _cfg):
        if self._cached_preview:
            self._display_preview(self._cached_preview)
        self._emit_config_changed()

    def _save_template_to_disk(self, checked=False, name: str | None = None):
        del checked
        if name is None:
            name, ok = QInputDialog.getText(self, "保存模板", "模板名称：")
            if not ok:
                return
        name = name.strip()
        if not name:
            return
        if self._mgr.load(name) is not None:
            answer = QMessageBox.question(self, "覆盖模板", f"模板「{name}」已存在，是否覆盖？")
            if answer != QMessageBox.StandardButton.Yes:
                return
        tpl = self.get_current_config()
        tpl.name = name
        self._mgr.save(tpl)
        self._current_collage = tpl
        self._load_template_list(select_name=name)

    def _delete_selected_template(self):
        items = self._template_list.selectedItems()
        if not items:
            return
        names = [item.text() for item in items]
        if len(names) == 1:
            prompt = f"确定删除「{names[0]}」？"
        else:
            listing = "\n".join(f"- {name}" for name in names[:12])
            if len(names) > 12:
                listing += f"\n... 另有 {len(names) - 12} 个"
            prompt = f"确定删除以下 {len(names)} 个拼图模板？\n\n{listing}"
        if QMessageBox.question(self, "删除模板", prompt) != QMessageBox.StandardButton.Yes:
            return
        for name in names:
            self._mgr.delete(name)
        if self._current_collage and self._current_collage.name in names:
            self._current_collage = None
        self._load_template_list()

    def _set_bg_color(self, hex_color: str):
        self._background_edit.setText(hex_color)

    def _choose_background_color(self):
        color = QColorDialog.getColor(QColor(self._background_edit.text()), self, "选择背景色")
        if color.isValid():
            self._background_edit.setText(color.name().upper())

    def _emit_config_changed(self):
        if not self._suppress_emit:
            self.config_changed.emit(self.get_current_config())

    # ── Helpers ───────────────────────────────────────────────────
    def _source_key(self, name: str, files: list[str]) -> str:
        first = files[0] if files else ""
        return f"{name}\n{first}\n{len(files)}"

    def _ppt_export_dir(self, pptx_path: str) -> str:
        digest = hashlib.sha1(os.path.abspath(pptx_path).encode("utf-8")).hexdigest()[:10]
        return os.path.join(self._app_data_dir, "ppt_export", f"{Path(pptx_path).stem}-{digest}")

    def _unique_import_name(self, base_name: str) -> str:
        used = {name for name, _files in self._ppt_import_results}
        if base_name not in used:
            return base_name
        counter = 2
        while f"{base_name} {counter}" in used:
            counter += 1
        return f"{base_name} {counter}"

    def _save_current_source_state(self):
        if not self._active_source_key or not self._image_files or self._suppress_emit:
            return
        cfg = self.get_current_config()
        self._source_states[self._active_source_key] = {
            "layout": cfg.layout,
            "rows": cfg.rows,
            "cols": cfg.cols,
            "gap": cfg.gap,
            "padding": cfg.padding,
            "background_color": cfg.background_color,
            "cell_aspect_ratio": cfg.cell_aspect_ratio,
            "output_count": self._output_count_spin.value(),
            "excluded_indices": sorted(self._excluded_indices),
            "preview_collage_index": self._preview_collage_index,
        }

    def _restore_source_state(self, name: str, files: list[str]):
        key = self._source_key(name, files)
        self._active_source_key = key
        state = self._source_states.get(key)
        if not state:
            self._excluded_indices = set()
            self._preview_collage_index = 0
            self._reset_output_count()
            return

        self._suppress_emit = True
        blockers = [
            QSignalBlocker(self._layout_combo),
            QSignalBlocker(self._row_spin),
            QSignalBlocker(self._col_spin),
            QSignalBlocker(self._gap_spin),
            QSignalBlocker(self._padding_spin),
            QSignalBlocker(self._aspect_combo),
            QSignalBlocker(self._background_edit),
            QSignalBlocker(self._output_count_spin),
        ]
        layout_index = max(0, self._layout_combo.findData(state.get("layout", "grid")))
        self._layout_combo.setCurrentIndex(layout_index)
        self._row_spin.setValue(int(state.get("rows", self._row_spin.value())))
        self._col_spin.setValue(int(state.get("cols", self._col_spin.value())))
        self._gap_spin.setValue(int(state.get("gap", self._gap_spin.value())))
        self._padding_spin.setValue(int(state.get("padding", self._padding_spin.value())))
        self._background_edit.setText(str(state.get("background_color", "#FFFFFF")))
        self._aspect_combo.setCurrentText(
            self._aspect_label_for(float(state.get("cell_aspect_ratio", 0)))
        )
        excluded = self._excluded_from_state(state, len(files))
        selected_count = len(files) - len(excluded)
        min_outputs = self._minimum_output_count_for_state(state, selected_count)
        self._output_count_spin.setRange(min_outputs, max(1, selected_count))
        self._output_count_spin.setValue(
            min(max(min_outputs, int(state.get("output_count", 1))), max(1, selected_count))
        )
        del blockers
        self._suppress_emit = False

        self._current_collage = None
        self._auto_adapt_active = False
        self._excluded_indices = excluded
        self._preview_collage_index = max(0, int(state.get("preview_collage_index", 0)))
        self._update_color_swatch()
        self._sync_layout_buttons()
        self._refresh_preset_state()
        self._refresh_mini_preview()

    def _config_from_state(
        self, state: dict | None, default: CollageTemplate
    ) -> CollageTemplate:
        if not state:
            return default
        return CollageTemplate(
            name=default.name,
            layout=str(state.get("layout", default.layout)),
            rows=int(state.get("rows", default.rows)),
            cols=int(state.get("cols", default.cols)),
            gap=int(state.get("gap", default.gap)),
            padding=int(state.get("padding", default.padding)),
            background_color=str(state.get("background_color", default.background_color)),
            cell_aspect_ratio=float(state.get("cell_aspect_ratio", default.cell_aspect_ratio)),
            output_width=default.output_width,
            output_height=default.output_height,
            output_count=int(state.get("output_count", default.output_count)),
        )

    def _excluded_from_state(self, state: dict | None, total: int) -> set[int]:
        if not state:
            return set()
        return {
            int(i) for i in state.get("excluded_indices", [])
            if isinstance(i, int) and 0 <= i < total
        }

    def _load_template_list(self, select_name: str | None = None):
        self._template_list.clear()
        for tpl in self._mgr.load_all():
            item = QListWidgetItem(tpl.name)
            item.setData(Qt.ItemDataRole.UserRole, tpl)
            self._template_list.addItem(item)
            if tpl.name == select_name:
                self._template_list.setCurrentItem(item)

    def _refresh_preset_state(self):
        current = f"{self._row_spin.value()}×{self._col_spin.value()}"
        matched_preset = False
        for name, btn in self._preset_buttons.items():
            is_match = self._layout_combo.currentData() == "grid" and name == current
            btn.setChecked(is_match)
            if is_match:
                matched_preset = True
        blocker = QSignalBlocker(self._auto_adapt_btn)
        self._auto_adapt_btn.setChecked(self._auto_adapt_active and not matched_preset)
        del blocker

    def _refresh_mini_preview(self):
        while self._mini_grid.count():
            item = self._mini_grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        rows = self._row_spin.value()
        cols = self._col_spin.value()
        is_hero = self._layout_combo.currentData() == "hero"
        total = 1 + rows * cols if is_hero else rows * cols
        if is_hero:
            self._mini_preview_label.setText(f"布局预览  上方大图 + 下方 {rows}×{cols} = {total} 张/页")
            hero = QLabel("大图")
            hero.setObjectName("mini_cell")
            hero.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hero.setMinimumHeight(34)
            self._mini_grid.addWidget(hero, 0, 0, 1, cols)
        else:
            self._mini_preview_label.setText(f"布局预览  {rows}×{cols} = {total} 张/页")
        start_row = 1 if is_hero else 0
        for index in range(rows * cols):
            cell = QLabel(str(index + 1))
            cell.setObjectName("mini_cell")
            cell.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell.setMinimumHeight(26)
            label = str(index + 2) if is_hero else str(index + 1)
            cell.setText(label)
            self._mini_grid.addWidget(cell, start_row + index // cols, index % cols)

    def _current_aspect_ratio(self) -> float:
        return _ASPECTS.get(self._aspect_combo.currentText(), 0)

    def _aspect_label_for(self, ratio: float) -> str:
        for label, value in _ASPECTS.items():
            if abs(value - ratio) < 0.001:
                return label
        return "自适应"

    def _last_background_color(self) -> str:
        return QSettings("融景", "RongJing").value(
            "collage/last_background_color", "#FFFFFF", type=str
        )

    def _remember_background_color(self):
        text = self._background_edit.text().strip()
        if QColor(text).isValid():
            QSettings("融景", "RongJing").setValue("collage/last_background_color", text.upper())

    def _update_color_swatch(self):
        color = self._background_edit.text().strip()
        if not QColor(color).isValid():
            color = "#FFFFFF"
        self._color_swatch.setStyleSheet(f"QLabel#color_swatch {{ background: {color}; }}")

    def _load_thumb_pixmap(self, path: str) -> QPixmap:
        try:
            with Image.open(path) as img:
                img.thumbnail((72, 72), Image.LANCZOS)
                return self._pil_to_pixmap(img.convert("RGB"))
        except Exception:
            return QPixmap()

    def _pil_to_pixmap(self, img: Image.Image) -> QPixmap:
        rgb = img.convert("RGB")
        data = rgb.tobytes("raw", "RGB")
        qimg = QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format.Format_RGB888).copy()
        return QPixmap.fromImage(qimg)

    def _spin(self, minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setKeyboardTracking(False)
        return spin

    def _add_grid_field(self, grid: QGridLayout, row: int, col: int, label: str, field):
        box = QVBoxLayout()
        box.setSpacing(4)
        box.addWidget(self._label(label, "cap"))
        if isinstance(field, QHBoxLayout):
            box.addLayout(field)
        else:
            box.addWidget(field)
        grid.addLayout(box, row, col)

    def _label(self, text: str, name: str | None = None) -> QLabel:
        label = QLabel(text)
        if name:
            label.setObjectName(name)
        return label

    def _sep(self) -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Plain)
        return sep

    def _stylesheet(self) -> str:
        return f"""
        QWidget#collage_sidebar,
        QWidget#collage_scroll_body {{
            background: {_SIDE};
        }}
        QWidget#collage_sidebar {{
            border-right: 1px solid {_SEP};
        }}
        QWidget#CollageTab QLabel#h2 {{
            color: {_TEXT};
            font-size: 15px;
            font-weight: 700;
            qproperty-alignment: AlignCenter;
        }}
        QWidget#CollageTab QLabel#cap {{
            color: {_TEXT2};
            font-size: 11px;
            font-weight: 500;
            qproperty-alignment: AlignCenter;
        }}
        QWidget#CollageTab QLabel#hint {{
            color: {_TEXT2};
            font-size: 12px;
        }}
        QWidget#CollageTab QWidget#tpl_list_frame,
        QWidget#CollageTab QWidget#mini_grid_frame {{
            background: {_CARD};
            border: 1px solid {_SEP};
            border-radius: 10px;
        }}
        QWidget#CollageTab QPushButton#preset_btn {{
            background: {_INPUT};
            color: {_TEXT};
            border: 1px solid {_SEP};
            border-radius: 8px;
            padding: 7px 10px;
            font-weight: 600;
        }}
        QWidget#CollageTab QPushButton#preset_btn:checked {{
            background: {_GREEN};
            color: white;
            border-color: {_GREEN};
        }}
        QWidget#CollageTab QPushButton#auto_adapt_btn {{
            background: #E8F8EE;
            color: {_GREEN};
            border: 1px solid {_GREEN};
            border-radius: 8px;
            padding: 7px 10px;
            font-weight: 600;
        }}
        QWidget#CollageTab QPushButton#auto_adapt_btn:checked {{
            background: {_GREEN};
            color: white;
            border: 1.5px solid {_GREEN};
        }}
        QWidget#CollageTab QPushButton#primary {{
            background: {_GREEN};
            color: white;
            border: none;
            border-radius: 8px;
            padding: 7px 16px;
            font-weight: 600;
            font-size: 14px;
        }}
        QWidget#CollageTab QPushButton#danger {{
            color: {_RED};
        }}
        QWidget#CollageTab QLabel#color_swatch {{
            border: 1px solid {_SEP};
            border-radius: 6px;
        }}
        QWidget#CollageTab QLabel#mini_cell {{
            background: {_INPUT};
            color: {_TEXT2};
            border: 1px solid {_SEP};
            border-radius: 4px;
            font-size: 11px;
        }}
        """
