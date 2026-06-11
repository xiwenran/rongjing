import json
import os
import shutil
import sys
import subprocess
import tempfile
import zipfile
from datetime import datetime

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget,
    QPushButton, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QFileDialog, QMessageBox, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QProgressBar, QFormLayout, QSpinBox,
    QAbstractItemView, QFrame, QScrollArea, QDialog, QCheckBox, QDialogButtonBox,
    QSizePolicy, QMenu, QWidgetAction,
)
from PyQt6.QtCore import Qt, QSize, QSettings, QPoint
from PyQt6.QtGui import QFont, QColor

from models.template_model import (
    Template,
    TemplateManager,
    normalize_render_preset,
    normalize_template_category,
)
from core.batch_runner import BatchRunner, VideoRunner, get_image_files, natural_sort_key
from core.ai_background import normalize_base_url
from core.screen_detector import detect_screen_points
from ui.canvas_widget import CanvasWidget

# ── WeChat-style Light Mode palette ──────────────────────────────────────────
_WIN   = "#F7F7F7"   # page background
_SIDE  = "#EFEFEF"   # sidebar
_CARD  = "#FFFFFF"   # card / container
_INPUT = "#F0F0F0"   # input / field background
_SEP   = "#E5E5E5"   # separator
_TEXT  = "#191919"   # primary text
_TEXT2 = "#888888"   # secondary text
_TEXT3 = "#C6C6C6"   # placeholder / disabled
_BLUE  = "#576B95"   # WeChat link blue (secondary)
_GREEN = "#07C160"   # WeChat green (primary accent)
_RED   = "#FA5151"   # WeChat red

STYLE = f"""
QMainWindow {{ background: {_WIN}; }}
QWidget      {{ color: {_TEXT}; font-size: 13px; }}

/* ── Named page/container backgrounds (explicit, not transparent) ── */
QWidget#root_bg,
QStackedWidget#pageStack,
QWidget#editor_page,
QWidget#batch_outer,
QWidget#batch_scroll_body,
QWidget#batch_content,
QWidget#batch_viewport {{ background: {_WIN}; }}

QWidget#sidebar,
QWidget#editor_viewport,
QWidget#editor_form,
QWidget#editor_bottom {{ background: {_SIDE}; }}

/* ── Containers ── */
QWidget#sidebar {{ background: {_SIDE}; border-right: 1px solid {_SEP}; }}
QWidget#card    {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {_CARD}, stop:1 #F8F8F8);
    border-radius: 16px; border: 1px solid {_SEP};
}}
QWidget#inset   {{ background: {_WIN};  border-radius: 10px; border: 1px solid {_SEP}; }}

/* ── Typography ── */
QLabel {{ background: transparent; color: {_TEXT}; }}
QLabel#h2  {{
    font-size: 15px; font-weight: 700;
    qproperty-alignment: AlignCenter;
}}
QLabel#cap    {{ color: {_TEXT2}; font-size: 11px; font-weight: 500; qproperty-alignment: AlignCenter; }}
QLabel#hint   {{ color: {_TEXT2}; font-size: 12px; }}
QLabel#badge  {{
    background: rgba(0,0,0,0.05);
    color: {_TEXT2}; font-size: 12px; font-weight: 500;
    padding: 3px 10px; border-radius: 10px;
}}
QLabel#badge_ok {{
    background: rgba(7,193,96,0.12);
    color: {_GREEN}; font-size: 12px; font-weight: 600;
    padding: 3px 10px; border-radius: 10px;
}}
QLabel#step_n  {{
    background: {_GREEN}; color: white;
    font-size: 12px; font-weight: 700;
    min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
    padding: 0px; border-radius: 14px;
}}
QWidget#tpl_list_frame {{
    background: {_CARD}; border: 1px solid {_SEP}; border-radius: 10px;
}}
QLabel#step_t  {{ font-size: 17px; font-weight: 700; color: {_TEXT}; }}
QLabel#card_title {{ font-size: 15px; font-weight: 600; color: {_TEXT}; }}

/* ── Inputs ── */
QLineEdit, QSpinBox {{
    background: {_INPUT}; border: 1px solid {_SEP}; border-radius: 8px;
    padding: 9px 12px; color: {_TEXT};
    selection-background-color: {_GREEN};
}}
QLineEdit:focus, QSpinBox:focus {{ border: 2px solid {_GREEN}; background: {_CARD}; }}
QLineEdit[readOnly="true"] {{ color: {_TEXT2}; }}
QSpinBox::up-button, QSpinBox::down-button {{
    background: rgba(0,0,0,0.06); border: none;
    width: 18px; border-radius: 3px; margin: 2px;
}}

/* ── Combo ── */
QComboBox {{
    background: {_INPUT}; border: 1px solid {_SEP};
    border-radius: 8px; padding: 9px 12px; color: {_TEXT};
}}
QComboBox:focus {{ border: 2px solid {_GREEN}; background: {_CARD}; }}
QComboBox::drop-down {{ border: none; width: 24px; subcontrol-position: right center; }}
QComboBox QAbstractItemView {{
    background: {_CARD}; border: 1px solid {_SEP};
    border-radius: 10px; padding: 4px; outline: none;
    selection-background-color: {_GREEN}; selection-color: white;
    color: {_TEXT};
}}

/* ── Message boxes ── */
QMessageBox {{
    background: {_CARD};
}}
QMessageBox QLabel {{
    color: {_TEXT}; font-size: 13px;
}}
QMessageBox QPushButton {{
    background: {_INPUT}; color: {_TEXT}; border: 1px solid {_SEP};
    border-radius: 8px; min-width: 72px; padding: 8px 16px;
}}

/* ── Buttons ── */
QPushButton {{
    background: {_INPUT}; border: none; border-radius: 18px;
    padding: 8px 16px; color: {_TEXT}; font-weight: 500;
}}
QPushButton:hover   {{ background: #E5E5E5; }}
QPushButton:pressed {{ background: #DCDCDC; }}
QPushButton:disabled {{ color: {_TEXT3}; }}

QPushButton#primary {{
    background: {_GREEN}; color: white;
    font-weight: 600; font-size: 14px; border-radius: 22px;
}}
QPushButton#primary:hover   {{ background: #06AD56; }}
QPushButton#primary:pressed {{ background: #05994B; }}

QPushButton#danger {{
    background: transparent; color: {_RED};
}}
QPushButton#danger:hover {{ background: rgba(250,81,81,0.10); }}

QPushButton#ghost {{
    background: transparent; color: {_TEXT2};
    padding: 6px 10px; font-size: 12px;
}}
QPushButton#ghost:hover {{ color: {_TEXT}; background: {_INPUT}; }}

QPushButton#scan {{
    background: rgba(7,193,96,0.10); color: {_GREEN};
    border: 1px solid rgba(7,193,96,0.35);
    font-weight: 600; border-radius: 22px; min-height: 44px;
}}
QPushButton#scan:hover {{ background: rgba(7,193,96,0.18); }}

QPushButton#autoDetect {{
    background: {_GREEN}; color: white;
    font-weight: 600; border-radius: 6px;
}}
QPushButton#autoDetect:hover {{ background: #06AD56; }}
QPushButton#autoDetect:pressed {{ background: #05994B; }}
QPushButton#autoDetect:disabled {{
    background: #DCDCDC; color: {_TEXT3};
}}

/* ── Mode buttons — styled via Python setStyleSheet() per state ── */
QPushButton#modeBtn {{
    border: none; border-radius: 22px; padding: 10px 0px; font-size: 14px;
}}

/* ── List ── */
QListWidget {{
    background: transparent; border: none;
    padding: 2px; outline: none;
}}
QListWidget::item {{
    padding: 9px 12px; border-radius: 6px;
    margin: 2px 4px; color: {_TEXT};
    background: {_INPUT};
}}
QListWidget::item:selected {{ background: rgba(7,193,96,0.18); color: {_GREEN}; }}
QListWidget::item:hover:!selected {{ background: #E8E8E8; }}

/* ── Custom nav header (replaces QTabWidget) ── */
QWidget#navHeader {{ background: {_CARD}; border-bottom: 1px solid {_SEP}; }}

/* ── Table ── */
QTableWidget {{
    background: transparent; border: none;
    gridline-color: {_SEP}; outline: none;
}}
QTableWidget::item {{ padding: 10px 14px; border: none; color: {_TEXT}; }}
QTableWidget::item:selected {{ background: rgba(7,193,96,0.12); color: {_TEXT}; }}
QHeaderView::section {{
    background: {_WIN}; color: {_TEXT2};
    padding: 8px 14px; border: none;
    border-bottom: 1px solid {_SEP};
    font-size: 11px; font-weight: 600;
}}
QHeaderView {{ background: transparent; }}
QTableCornerButton::section {{ background: transparent; border: none; }}

/* ── Progress ── */
QProgressBar {{
    background: rgba(0,0,0,0.08); border: none;
    border-radius: 3px; max-height: 6px;
    text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background: {_GREEN}; border-radius: 3px; }}

/* ── Scrollbars ── */
QScrollBar:vertical   {{ background: transparent; width: 6px; }}
QScrollBar:horizontal {{ background: transparent; height: 6px; }}
QScrollBar::handle {{ background: rgba(0,0,0,0.15); border-radius: 3px; min-height: 20px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page,  QScrollBar::sub-page  {{ background: transparent; }}

/* ── Separators ── */
QFrame[frameShape="4"] {{ color: {_SEP}; max-height: 1px; }}
QFrame[frameShape="5"] {{ color: {_SEP}; max-width:  1px; }}

/* ── Left nav sidebar ── */
QWidget#navSidebar {{
    background: {_CARD}; border-right: 1px solid {_SEP};
    min-width: 160px; max-width: 160px;
}}
QPushButton#navBtn {{
    background: transparent; color: {_TEXT2};
    border: none; border-radius: 10px;
    padding: 12px 16px; font-size: 13px; font-weight: 500;
    text-align: left;
}}
QPushButton#navBtn:hover {{ background: {_INPUT}; color: {_TEXT}; }}

/* ── Mode Radio Cards ── */
QWidget#modeCard {{
    background: {_CARD}; border: 2px solid {_SEP};
    border-radius: 14px;
}}
QWidget#modeCard[selected="true"] {{
    background: rgba(7,193,96,0.06);
    border: 2px solid {_GREEN};
}}

/* ── QMenu popup ── */
QMenu {{
    background: {_CARD}; border: 1px solid {_SEP};
    border-radius: 10px; padding: 4px;
}}
QMenu::item {{ padding: 0; background: transparent; }}
"""

OUTPUT_PRESETS = {
    "自动（背景图尺寸）":        (0, 0),
    "1080 × 1920  (9:16 竖屏)": (1080, 1920),
    "1920 × 1080  (16:9 横屏)": (1920, 1080),
    "1080 × 1440  (3:4)":       (1080, 1440),
    "1440 × 1080  (4:3)":       (1440, 1080),
    "1080 × 1080  (1:1)":       (1080, 1080),
    "自定义...":                 None,
}

BATCH_OUTPUT_WIDTH_PRESETS = {
    "高清 1920 宽（默认）": 1920,
    "原始 / 模板尺寸": 0,
    "2K 2560 宽": 2560,
    "4K 3840 宽": 3840,
}

TEMPLATE_CATEGORIES = ["教室场景", "台式机电脑", "笔记本室内", "文档纸张", "自定义场景"]
TEMPLATE_TYPES = {
    "屏幕 / 大屏": "screen",
    "文档纸张": "document_paper",
}
DOCUMENT_PRESETS = {
    "真实纸感": "paper",
}


# ── Native file/folder pickers ────────────────────────────────────────────────

def _run_osascript(script: str):
    """Run osascript. Returns (result_str, ran: bool).
    ran=True means osascript was available (even if user cancelled).
    ran=False means osascript not found → fall back to Qt dialog.
    """
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=120,
        )
        # returncode 0 = success, non-0 = user cancelled or other error
        # Either way, osascript WAS available, so don't show Qt fallback
        return r.stdout.strip() if r.returncode == 0 else "", True
    except FileNotFoundError:
        return "", False   # osascript not installed → use Qt


def pick_image(parent, title="选择图片", default_dir="") -> str:
    if sys.platform == "darwin":
        loc = f' default location (POSIX file "{default_dir}")' if default_dir and os.path.isdir(default_dir) else ""
        path, ran = _run_osascript(f'POSIX path of (choose file with prompt "{title}"{loc})')
        if ran:
            return path  # empty string if user cancelled
    opts = QFileDialog.Option.DontUseNativeDialog if sys.platform == "darwin" else QFileDialog.Option(0)
    p, _ = QFileDialog.getOpenFileName(
        parent, title, default_dir,
        "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp *.tiff)",
        options=opts,
    )
    return p


def pick_folder(parent, title="选择文件夹", default_dir="") -> str:
    if sys.platform == "darwin":
        loc = f' default location (POSIX file "{default_dir}")' if default_dir and os.path.isdir(default_dir) else ""
        path, ran = _run_osascript(f'POSIX path of (choose folder with prompt "{title}"{loc})')
        if ran:
            return path.rstrip("/") if path else ""
    opts = QFileDialog.Option.DontUseNativeDialog if sys.platform == "darwin" else QFileDialog.Option(0)
    return QFileDialog.getExistingDirectory(parent, title, default_dir, opts)


# ── Layout helpers ────────────────────────────────────────────────────────────

def _sep():
    f = QFrame(); f.setFrameShape(QFrame.Shape.HLine); return f

def _vsep():
    f = QFrame(); f.setFrameShape(QFrame.Shape.VLine); return f

def _lbl(text, obj_name=""):
    l = QLabel(text)
    if obj_name: l.setObjectName(obj_name)
    return l

def _row(*items, spacing=8) -> QHBoxLayout:
    h = QHBoxLayout(); h.setSpacing(spacing)
    for item in items:
        if item is None:              h.addStretch()
        elif isinstance(item, int):   h.addSpacing(item)
        elif isinstance(item, QWidget): h.addWidget(item)
        else:                         h.addLayout(item)
    return h

def _col(*items, spacing=8) -> QVBoxLayout:
    v = QVBoxLayout(); v.setSpacing(spacing)
    for item in items:
        if item is None:              v.addStretch()
        elif isinstance(item, int):   v.addSpacing(item)
        elif isinstance(item, QWidget): v.addWidget(item)
        else:                         v.addLayout(item)
    return v

class TemplatePickerDialog(QDialog):
    """Multi-select dialog for choosing templates."""

    def __init__(self, all_templates, preselected=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择场景模板")
        self.setMinimumWidth(480 if len(all_templates) > 6 else 340)
        self.setMinimumHeight(300)
        self.setStyleSheet(f"""
            QDialog {{ background: {_CARD}; color: {_TEXT}; font-size: 13px; }}
            QLabel {{ background: transparent; color: {_TEXT}; }}
            QScrollArea {{ background: {_INPUT}; border-radius: 8px; border: none; }}
            QWidget#scroll_inner {{ background: {_CARD}; }}
            QScrollBar:vertical {{ background: transparent; width: 6px; }}
            QScrollBar::handle {{ background: rgba(0,0,0,0.15); border-radius: 3px; }}
            QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
            QCheckBox {{ background: transparent; color: {_TEXT}; spacing: 8px; }}
            QCheckBox::indicator {{
                width: 18px; height: 18px; border-radius: 4px;
                border: 2px solid {_SEP}; background: {_CARD};
            }}
            QCheckBox::indicator:checked {{ background: {_GREEN}; border-color: {_GREEN}; }}
            QDialogButtonBox {{ background: {_CARD}; }}
            QPushButton {{
                background: {_INPUT}; color: {_TEXT}; border: 1px solid {_SEP};
                border-radius: 8px; padding: 8px 16px; font-size: 13px;
            }}
            QPushButton:hover {{ background: #E5E5E5; }}
            QDialogButtonBox QPushButton {{
                background: {_INPUT}; color: {_TEXT}; border: 1px solid {_SEP};
                border-radius: 8px; min-width: 64px; padding: 7px 18px; font-size: 13px;
            }}
            QDialogButtonBox QPushButton:hover {{ background: #E5E5E5; }}
        """)

        lv = QVBoxLayout(self)
        lv.setContentsMargins(20, 16, 20, 16)
        lv.setSpacing(10)

        title = QLabel("选择要应用的模板（可多选）：")
        title.setStyleSheet(f"font-size: 15px; font-weight: 600; color: {_TEXT};")
        lv.addWidget(title)

        # Scrollable checkbox area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        inner = QWidget()
        inner.setObjectName("scroll_inner")
        iv = QVBoxLayout(inner)
        iv.setContentsMargins(8, 8, 8, 8)
        iv.setSpacing(8)

        self._checks: list[QCheckBox] = []
        self._group_checks: list[QCheckBox] = []
        preselected = set(preselected or [])
        grouped = {}
        for t in all_templates:
            category = normalize_template_category(getattr(t, "category", "教室场景") or "教室场景")
            grouped.setdefault(category, []).append(t)
        for category in sorted(grouped, key=_category_sort_key):
            group_checks: list[QCheckBox] = []
            heading = QCheckBox(category)
            heading.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {_TEXT2}; padding: 6px 4px 2px 4px;")
            iv.addWidget(heading)
            if len(grouped[category]) > 4:
                from PyQt6.QtWidgets import QGridLayout
                grid = QGridLayout(); grid.setSpacing(8); grid.setContentsMargins(0, 0, 0, 0)
                iv.addLayout(grid)
                for idx_t, t in enumerate(grouped[category]):
                    cb = QCheckBox(t.name)
                    key = getattr(t, "storage_key", "") or t.name
                    cb.setProperty("template_name", key)
                    cb.setToolTip(f"{category} · {getattr(t, 'template_type', 'screen')}")
                    cb.setChecked(key in preselected)
                    grid.addWidget(cb, idx_t // 2, idx_t % 2)
                    self._checks.append(cb)
                    group_checks.append(cb)
            else:
                for t in grouped[category]:
                    cb = QCheckBox(t.name)
                    key = getattr(t, "storage_key", "") or t.name
                    cb.setProperty("template_name", key)
                    cb.setToolTip(f"{category} · {getattr(t, 'template_type', 'screen')}")
                    cb.setChecked(key in preselected)
                    iv.addWidget(cb)
                    self._checks.append(cb)
                    group_checks.append(cb)
            heading.setChecked(all(c.isChecked() for c in group_checks) if group_checks else False)
            heading.clicked.connect(lambda checked, checks=group_checks: [c.setChecked(checked) for c in checks])
            self._group_checks.append(heading)
        iv.addStretch()
        scroll.setWidget(inner)
        lv.addWidget(scroll)

        # Select all / none row
        row = QHBoxLayout()
        btn_all  = QPushButton("全选")
        btn_none = QPushButton("全不选")
        btn_all.clicked.connect(lambda: [c.setChecked(True) for c in self._checks])
        btn_none.clicked.connect(lambda: [c.setChecked(False) for c in self._checks])
        row.addWidget(btn_all); row.addWidget(btn_none); row.addStretch()
        lv.addLayout(row)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lv.addWidget(btns)

    def selected_names(self) -> list:
        return [c.property("template_name") or c.text() for c in self._checks if c.isChecked()]


def _category_sort_key(category: str):
    category = normalize_template_category(category)
    if category in TEMPLATE_CATEGORIES:
        return (TEMPLATE_CATEGORIES.index(category), "")
    return (len(TEMPLATE_CATEGORIES), category)


class SlowTemplateListWidget(QListWidget):
    """Keep template list wheel scrolling controllable on high-resolution mice."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0:
            return super().wheelEvent(event)
        bar = self.verticalScrollBar()
        step = -10 if delta > 0 else 10
        bar.setValue(bar.value() + step)
        event.accept()


class SlowScrollArea(QScrollArea):
    """Use small fixed wheel steps for dense sidebar forms."""

    def __init__(self, parent=None, wheel_step=16):
        super().__init__(parent)
        self._wheel_step = wheel_step

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0:
            return super().wheelEvent(event)
        bar = self.verticalScrollBar()
        step = -self._wheel_step if delta > 0 else self._wheel_step
        bar.setValue(bar.value() + step)
        event.accept()


def _set_green_selection(table_widget):
    """Force green selection by switching table to Fusion style (bypasses macOS Aqua blue)."""
    from PyQt6.QtWidgets import QStyleFactory
    from PyQt6.QtGui import QPalette
    fusion = QStyleFactory.create("Fusion")
    if fusion:
        table_widget.setStyle(fusion)
        table_widget.viewport().setStyle(fusion)
    green = QColor(7, 193, 96, 100)
    text  = QColor(25, 25, 25)
    for w in (table_widget, table_widget.viewport()):
        pal = w.palette()
        for grp in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
            pal.setColor(grp, QPalette.ColorRole.Highlight, green)
            pal.setColor(grp, QPalette.ColorRole.HighlightedText, text)
        w.setPalette(pal)
        w.setAutoFillBackground(True)

def _card(*items, pad=(20,18,20,18)) -> QWidget:
    w = QWidget(); w.setObjectName("card")
    v = QVBoxLayout(w); v.setContentsMargins(*pad); v.setSpacing(12)
    for item in items:
        if isinstance(item, QWidget): v.addWidget(item)
        else:                         v.addLayout(item)
    return w

def _step(num, title) -> QHBoxLayout:
    badge = _lbl(num, "step_n")
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl = _lbl(title, "step_t")
    return _row(badge, 4, lbl, None)

def _btn(text, slot=None, style="", w=None) -> QPushButton:
    b = QPushButton(text)
    if style: b.setObjectName(style)
    if w:     b.setFixedWidth(w)
    if slot:  b.clicked.connect(slot)
    return b


# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(
        self,
        templates_dir: str,
        backgrounds_dir: str | None = None,
        collages_dir: str | None = None,
        build: str = "dev",
        parent=None,
    ):
        super().__init__(parent)
        title = "融景" if build == "dev" else f"融景  {build}"
        self.setWindowTitle(title)
        self.resize(1340, 840)
        self.setMinimumSize(960, 640)
        self.setStyleSheet(STYLE)

        self.tm = TemplateManager(templates_dir, backgrounds_dir=backgrounds_dir)
        self._backgrounds_dir = backgrounds_dir
        self._collages_dir = collages_dir
        self._collage_tab = None
        self._ai_generate_tab = None
        self._batch_runner = None
        self._loaded_tpl_name: str = None   # track which template is currently loaded
        self._row_selections: dict = {}     # row index → list of template names
        self._picked_image_files = []
        self._last_apply_all: list = []
        # Per-picker last-used directories — persisted across sessions via QSettings
        _home = os.path.expanduser("~")
        self._settings = QSettings("融景", "RongJing")
        self._last_dir_bg      = self._settings.value("last_dir_bg",      _home)
        self._last_dir_preview = self._settings.value("last_dir_preview",  _home)
        self._last_dir_input   = self._settings.value("last_dir_input",    _home)
        self._last_dir_output  = self._settings.value("last_dir_output",   _home)
        self._last_dir_images  = self._settings.value("last_dir_images",   _home)
        self._last_dir_videos  = self._settings.value("last_dir_videos",   _home)
        self._batch_output_width = int(self._settings.value("batch_output_width", 1920))

        self._build_ui()
        self._on_template_category_changed()
        self._set_batch_mode(0)   # apply initial mode button styles
        self._refresh_template_list()

    # ── Root ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _fix_bg(widget, color: str):
        """Force a solid background on Windows.
        CSS background:transparent renders black on Windows when no parent paints;
        QPalette + autoFillBackground bypasses the stylesheet for background fill."""
        from PyQt6.QtGui import QPalette, QColor as _QColor
        pal = widget.palette()
        for role in (QPalette.ColorRole.Window,
                     QPalette.ColorRole.Base,
                     QPalette.ColorRole.Button):
            pal.setColor(role, _QColor(color))
        widget.setPalette(pal)
        widget.setAutoFillBackground(True)

    @staticmethod
    def _mark_styled_bg(widget, name: str):
        """Set objectName so the named CSS rule applies, and force styled background painting."""
        widget.setObjectName(name)
        widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def _build_ui(self):
        self._fix_bg(self, _WIN)

        root = QWidget()
        self.setCentralWidget(root)
        self._fix_bg(root, _WIN)
        self._mark_styled_bg(root, "root_bg")

        # Root is now horizontal: [navSidebar | stack]
        rl = QHBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(0)

        # ── Left nav sidebar ────────────────────────────────────────────────
        nav = QWidget(); nav.setObjectName("navSidebar")
        self._fix_bg(nav, _CARD)
        nav.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        nv = QVBoxLayout(nav)
        nv.setContentsMargins(12, 16, 12, 16); nv.setSpacing(4)

        # App title
        app_title = QLabel("融景")
        app_title.setStyleSheet(f"font-size:18px; font-weight:700; color:{_TEXT}; padding:8px 8px 16px 8px;")
        nv.addWidget(app_title)

        self._nav_btns = []
        self._page_indices = {}
        next_idx = 0
        nav_items = [("  📐  模板配置", next_idx)]
        self._page_indices["editor"] = next_idx
        next_idx += 1
        nav_items.append(("  📦  批量导出", next_idx))
        self._page_indices["batch"] = next_idx
        next_idx += 1
        if self._collages_dir is not None:
            nav_items.append(("  🧩  拼图", next_idx))
            self._page_indices["collage"] = next_idx
            next_idx += 1
        if self._backgrounds_dir is not None:
            nav_items.append(("  🎨  AI 背景", next_idx))
            self._page_indices["ai_generate"] = next_idx
            next_idx += 1
        nav_items.append(("  🛡️  去水印", next_idx))
        self._page_indices["dewatermark"] = next_idx
        next_idx += 1
        for label_text, idx in nav_items:
            btn = QPushButton(label_text)
            btn.setObjectName("navBtn")
            btn.setCheckable(True)
            btn.setFixedHeight(44)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, i=idx: self._switch_page(i))
            self._nav_btns.append(btn)
            nv.addWidget(btn)

        nv.addStretch()

        # Settings button at bottom
        btn_settings = QPushButton("  ⚙  设置")
        btn_settings.setObjectName("navBtn")
        btn_settings.setCheckable(True)
        btn_settings.setFixedHeight(44)
        btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        settings_idx = next_idx
        self._page_indices["settings"] = settings_idx
        btn_settings.clicked.connect(lambda: self._switch_page(settings_idx))
        self._nav_btns.append(btn_settings)
        nv.addWidget(btn_settings)

        rl.addWidget(nav)

        # ── Page stack ────────────────────────────────────────────────────────
        self.stack = QStackedWidget()
        self._fix_bg(self.stack, _WIN)
        self._mark_styled_bg(self.stack, "pageStack")
        self.stack.addWidget(self._build_editor_tab())
        self.stack.addWidget(self._build_batch_tab())
        if self._collages_dir is not None:
            from ui.collage_tab import CollageTab
            self._collage_tab = CollageTab(collages_dir=self._collages_dir)
            self.stack.addWidget(self._collage_tab)
        if self._backgrounds_dir is not None:
            from ui.ai_generate_tab import AIGenerateTab
            self._ai_generate_tab = AIGenerateTab(backgrounds_dir=self._backgrounds_dir)
            self._ai_generate_tab.save_finished.connect(self._on_ai_backgrounds_saved)
            self.stack.addWidget(self._ai_generate_tab)
        from ui.dewatermark_tab import DewatermarkTab
        self._dewatermark_tab = DewatermarkTab()
        self.stack.addWidget(self._dewatermark_tab)
        self.stack.addWidget(self._build_settings_tab())
        rl.addWidget(self.stack, 1)

        self._switch_page(0)

    # ── Editor tab ────────────────────────────────────────────────────────────

    def _switch_page(self, idx: int):
        self.stack.setCurrentIndex(idx)
        active   = f"QPushButton{{background:{_GREEN};color:white;font-weight:600;font-size:13px;border:none;border-radius:10px;padding:12px 16px;text-align:left;}}"
        inactive = f"QPushButton{{background:transparent;color:{_TEXT2};font-size:13px;font-weight:500;border:none;border-radius:10px;padding:12px 16px;text-align:left;}} QPushButton:hover{{background:{_INPUT};color:{_TEXT};}}"
        for i, btn in enumerate(self._nav_btns):
            btn.setChecked(i == idx)
            btn.setStyleSheet(active if i == idx else inactive)

    # ── Editor tab ────────────────────────────────────────────────────────────

    def _build_editor_tab(self):
        tab = QWidget()
        self._fix_bg(tab, _WIN)
        self._mark_styled_bg(tab, "editor_page")
        root = QHBoxLayout(tab)
        root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        # ── Sidebar ───────────────────────────────────────────────────────────
        sidebar = QWidget(); sidebar.setFixedWidth(420)
        self._fix_bg(sidebar, _SIDE)
        self._mark_styled_bg(sidebar, "sidebar")
        sv = QVBoxLayout(sidebar)
        sv.setContentsMargins(0, 0, 0, 0); sv.setSpacing(0)

        # ── Scrollable form content ────────────────────────────────────────────
        sb_scroll = SlowScrollArea(wheel_step=16)
        sb_scroll.setWidgetResizable(True)
        sb_scroll.setFrameShape(QFrame.Shape.NoFrame)
        sb_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._fix_bg(sb_scroll, _SIDE)
        self._fix_bg(sb_scroll.viewport(), _SIDE)
        self._mark_styled_bg(sb_scroll.viewport(), "editor_viewport")
        form = QWidget()
        self._fix_bg(form, _SIDE)
        self._mark_styled_bg(form, "editor_form")
        fv = QVBoxLayout(form)
        fv.setContentsMargins(14, 16, 14, 16); fv.setSpacing(0)

        # Section: template library
        fv.addWidget(_lbl("模板库", "h2"))
        fv.addSpacing(10)

        self.template_list = SlowTemplateListWidget()
        self.template_list.setMinimumHeight(90)
        self.template_list.setMaximumHeight(200)
        self.template_list.currentRowChanged.connect(self._on_template_selected)
        tpl_frame = QWidget(); tpl_frame.setObjectName("tpl_list_frame")
        tfl = QVBoxLayout(tpl_frame); tfl.setContentsMargins(4, 4, 4, 4); tfl.setSpacing(0)
        tfl.addWidget(self.template_list)
        fv.addWidget(tpl_frame)
        fv.addSpacing(8)

        tpl_row = _row(_btn("+ 新建", self._new_template), _btn("删除", self._delete_template, "danger"))
        fv.addLayout(tpl_row)
        fv.addSpacing(16)
        fv.addWidget(_sep())
        fv.addSpacing(14)

        # Section: scene settings
        fv.addWidget(_lbl("场景配置", "h2"))
        fv.addSpacing(10)

        # Name
        fv.addWidget(_lbl("名称", "cap"))
        fv.addSpacing(4)
        self.tpl_name_edit = QLineEdit(); self.tpl_name_edit.setPlaceholderText("模板名称…")
        fv.addWidget(self.tpl_name_edit)
        fv.addSpacing(10)

        fv.addWidget(_lbl("模板分类", "cap"))
        fv.addSpacing(4)
        self.tpl_category_combo = QComboBox()
        self.tpl_category_combo.addItems(TEMPLATE_CATEGORIES)
        self.tpl_category_combo.currentTextChanged.connect(self._on_template_category_changed)
        self.tpl_category_combo.activated.connect(self._on_template_category_preset_activated)
        fv.addWidget(self.tpl_category_combo)
        fv.addSpacing(4)
        self.tpl_custom_category_edit = QLineEdit()
        self.tpl_custom_category_edit.setPlaceholderText("自定义分类（可选，不填则使用上方预设）")
        self.tpl_custom_category_edit.textChanged.connect(self._on_template_category_changed)
        fv.addWidget(self.tpl_custom_category_edit)
        fv.addSpacing(10)

        self.tpl_type_combo = QComboBox()
        for label, value in TEMPLATE_TYPES.items():
            self.tpl_type_combo.addItem(label, value)
        self.tpl_type_combo.setVisible(False)

        self.tpl_preset_label = _lbl("文档预设", "cap")
        fv.addWidget(self.tpl_preset_label)
        fv.addSpacing(4)
        self.tpl_preset_combo = QComboBox()
        for label, value in DOCUMENT_PRESETS.items():
            self.tpl_preset_combo.addItem(label, value)
        fv.addWidget(self.tpl_preset_combo)
        self.tpl_preset_label.setVisible(False)
        self.tpl_preset_combo.setVisible(False)
        fv.addSpacing(10)

        # Background
        fv.addWidget(_lbl("背景图片", "cap"))
        fv.addSpacing(4)
        self.bg_path_edit = QLineEdit(); self.bg_path_edit.setReadOnly(True)
        self.bg_path_edit.setPlaceholderText("点击选择背景图片")
        btn_bg = _btn("选择", self._load_background, w=64)
        bg_row = _row(self.bg_path_edit, btn_bg, spacing=6)
        fv.addLayout(bg_row)
        fv.addSpacing(10)

        # Points
        fv.addWidget(_lbl("屏幕角点", "cap"))
        fv.addSpacing(4)
        self.points_badge = _lbl("0 / 4 个角点", "badge")
        self.auto_detect_btn = _btn("🔍 自动识别", self._auto_detect_screen, "autoDetect")
        self.auto_detect_btn.setEnabled(False)
        fv.addLayout(_row(self.points_badge, None, self.auto_detect_btn, _btn("清除", self._clear_points, "ghost")))
        fv.addSpacing(10)

        # Output size
        fv.addWidget(_lbl("输出尺寸", "cap"))
        fv.addSpacing(4)
        self.output_size_combo = QComboBox()
        for lbl in OUTPUT_PRESETS: self.output_size_combo.addItem(lbl)
        self.output_size_combo.currentTextChanged.connect(
            lambda t: self.custom_size_widget.setVisible(t == "自定义..."))
        fv.addWidget(self.output_size_combo)

        cw = QWidget()
        cl = QHBoxLayout(cw); cl.setContentsMargins(0, 4, 0, 0); cl.setSpacing(6)
        self.custom_w = QSpinBox(); self.custom_w.setRange(100, 8000); self.custom_w.setValue(1080)
        self.custom_h = QSpinBox(); self.custom_h.setRange(100, 8000); self.custom_h.setValue(1920)
        cl.addWidget(_lbl("W")); cl.addWidget(self.custom_w)
        cl.addWidget(_lbl("H")); cl.addWidget(self.custom_h)
        self.custom_size_widget = cw; cw.hide()
        fv.addWidget(cw)
        fv.addSpacing(16)
        fv.addWidget(_sep())
        fv.addSpacing(14)

        # Section: preview
        fv.addWidget(_lbl("嵌入预览（可选）", "h2"))
        fv.addSpacing(6)
        hint_prev = _lbl("加载一张 PPT 图片，实时查看嵌入效果", "hint")
        hint_prev.setWordWrap(True)
        fv.addWidget(hint_prev)
        fv.addSpacing(8)
        self.preview_path_edit = QLineEdit(); self.preview_path_edit.setReadOnly(True)
        self.preview_path_edit.setPlaceholderText("未加载预览图片")
        fv.addWidget(self.preview_path_edit)
        fv.addSpacing(6)
        fv.addLayout(_row(_btn("选择图片", self._load_preview), _btn("清除", self._clear_preview, "ghost")))
        fv.addSpacing(16)
        fv.addWidget(_sep())
        fv.addSpacing(12)

        # Hint
        hint = _lbl("左键依次点击放置 4 个角点（TL → TR → BR → BL），拖拽调整位置，右键撤销", "hint")
        hint.setWordWrap(True)
        fv.addWidget(hint)
        fv.addSpacing(16)

        sb_scroll.setWidget(form)
        sv.addWidget(sb_scroll)  # scrollable area takes all available height

        # ── Bottom panel: always visible (save + uninstall) ───────────────────
        bottom = QWidget()
        self._fix_bg(bottom, _SIDE)
        self._mark_styled_bg(bottom, "editor_bottom")
        bv = QVBoxLayout(bottom)
        bv.setContentsMargins(14, 8, 14, 10); bv.setSpacing(0)

        bv.addWidget(_sep())
        bv.addSpacing(8)
        btn_save = _btn("  保存模板", self._save_template, "primary")
        btn_save.setFixedHeight(44)
        bv.addWidget(btn_save)
        bv.addSpacing(4)

        sv.addWidget(bottom)

        root.addWidget(sidebar)

        # ── Canvas ────────────────────────────────────────────────────────────
        self.canvas = CanvasWidget()
        self.canvas.points_changed.connect(self._on_points_changed)
        root.addWidget(self.canvas, 1)
        return tab

    # ── Batch tab ─────────────────────────────────────────────────────────────

    def _build_batch_tab(self):
        # ── Root: horizontal split (left config | right assignment) ──────────
        outer = QWidget()
        self._fix_bg(outer, _WIN)
        self._mark_styled_bg(outer, "batch_outer")
        main_hl = QHBoxLayout(outer)
        main_hl.setContentsMargins(0, 0, 0, 0); main_hl.setSpacing(0)

        # ── Left sidebar (config panel, same style as editor sidebar) ─────────
        left_side = QWidget(); left_side.setFixedWidth(380)
        self._fix_bg(left_side, _SIDE)
        self._mark_styled_bg(left_side, "sidebar")
        lsv = QVBoxLayout(left_side)
        lsv.setContentsMargins(0, 0, 0, 0); lsv.setSpacing(0)

        # Fixed mode header (not inside scroll area, always fully visible)
        ls_mode_header = QWidget(); self._fix_bg(ls_mode_header, _SIDE)
        self._mark_styled_bg(ls_mode_header, "editor_bottom")
        lmhv = QVBoxLayout(ls_mode_header)
        lmhv.setContentsMargins(14, 14, 14, 10); lmhv.setSpacing(8)
        lmhv.addWidget(_lbl("处理模式", "h2"))
        mode_row = QHBoxLayout(); mode_row.setSpacing(8)
        self._mode_cards = []
        mode_data = [
            ("📁", "图片文件夹", "按子文件夹\n分组批量"),
            ("🖼", "图片批量", "手动选多图\n统一模板"),
            ("🎬", "视频文件", "视频逐帧\n嵌入背景"),
        ]
        for i, (icon, title, desc) in enumerate(mode_data):
            card = QWidget(); card.setObjectName("modeCard")
            card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            cv = QVBoxLayout(card); cv.setContentsMargins(8, 10, 8, 10); cv.setSpacing(3)
            lbl_icon = QLabel(icon); lbl_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl_icon.setStyleSheet("font-size:22px; background:transparent;")
            lbl_title = QLabel(title); lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl_title.setStyleSheet(f"font-size:12px; font-weight:700; color:{_TEXT}; background:transparent;")
            lbl_desc = QLabel(desc); lbl_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl_desc.setStyleSheet(f"font-size:10px; color:{_TEXT2}; background:transparent;")
            lbl_desc.setWordWrap(True)
            cv.addWidget(lbl_icon); cv.addWidget(lbl_title); cv.addWidget(lbl_desc)
            idx_capture = i
            card.mousePressEvent = lambda e, idx=idx_capture: self._set_batch_mode(idx)
            self._mode_cards.append(card)
            mode_row.addWidget(card)
        lmhv.addLayout(mode_row)
        self._batch_mode = 0
        lmhv.addSpacing(4); lmhv.addWidget(_sep())
        lsv.addWidget(ls_mode_header)

        # Scrollable form content (input source + output settings only)
        ls_scroll = QScrollArea(); ls_scroll.setWidgetResizable(True)
        ls_scroll.setFrameShape(QFrame.Shape.NoFrame)
        ls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._fix_bg(ls_scroll, _SIDE); self._fix_bg(ls_scroll.viewport(), _SIDE)
        self._mark_styled_bg(ls_scroll.viewport(), "editor_viewport")
        ls_form = QWidget(); self._fix_bg(ls_form, _SIDE)
        self._mark_styled_bg(ls_form, "editor_form")
        fv = QVBoxLayout(ls_form)
        fv.setContentsMargins(14, 14, 14, 16); fv.setSpacing(0)

        # Input source section
        fv.addWidget(_lbl("输入来源", "h2"))
        fv.addSpacing(10)

        # Folder mode
        c1_folder = QWidget(); self._fix_bg(c1_folder, _SIDE)
        ff = QVBoxLayout(c1_folder); ff.setContentsMargins(0, 0, 0, 0); ff.setSpacing(8)
        _h = _lbl("选择主文件夹（内含子文件夹，每个子文件夹放一组图片；若无子文件夹则直接处理根目录图片）", "hint"); _h.setWordWrap(True); ff.addWidget(_h)
        self.input_dir_edit = QLineEdit(); self.input_dir_edit.setReadOnly(True)
        self.input_dir_edit.setPlaceholderText("选择主文件夹路径…")
        ff.addLayout(_row(self.input_dir_edit, _btn("选择", self._browse_input, w=64), spacing=6))
        ff.addWidget(_btn("扫描文件夹", self._scan_subfolders, "scan"))
        fv.addWidget(c1_folder)

        # Image batch mode (hidden by default)
        c1_image = QWidget(); self._fix_bg(c1_image, _SIDE)
        fi = QVBoxLayout(c1_image); fi.setContentsMargins(0, 0, 0, 0); fi.setSpacing(8)
        fi.addWidget(_lbl("选择要嵌入的图片文件（可多选），统一应用所选模板", "hint"))
        self.image_files_label = _lbl("未选择图片", "hint")
        fi.addWidget(_btn("选择图片文件…", self._pick_image_files, "scan"))
        fi.addWidget(self.image_files_label)
        c1_image.hide(); fv.addWidget(c1_image)

        # Video mode (hidden by default) — pick button only; table goes to right panel
        c1_video = QWidget(); self._fix_bg(c1_video, _SIDE)
        fvi = QVBoxLayout(c1_video); fvi.setContentsMargins(0, 0, 0, 0); fvi.setSpacing(8)
        _hv = _lbl("选择视频录制文件（如 PPT 录屏），视频每帧将被嵌入场景模板的背景图中，输出合成视频", "hint"); _hv.setWordWrap(True); fvi.addWidget(_hv)
        fvi.addWidget(_btn("选择视频文件…", self._pick_video_files, "scan"))
        c1_video.hide(); fv.addWidget(c1_video)

        self._c1_folder = c1_folder
        self._c1_image  = c1_image
        self._c1_video  = c1_video

        fv.addSpacing(16); fv.addWidget(_sep()); fv.addSpacing(14)

        # Output settings section
        fv.addWidget(_lbl("输出设置", "h2"))
        fv.addSpacing(10)
        self.output_dir_edit = QLineEdit(); self.output_dir_edit.setReadOnly(True)
        self.output_dir_edit.setPlaceholderText("选择输出文件夹…")
        fv.addLayout(_row(self.output_dir_edit, _btn("选择", self._browse_output, w=64), spacing=6))
        fv.addSpacing(8)

        self.format_combo = QComboBox(); self.format_combo.addItems(["JPEG", "PNG"])
        self.format_combo.setFixedWidth(86)
        self.resolution_combo = QComboBox()
        self.resolution_combo.setFixedWidth(158)
        for label in BATCH_OUTPUT_WIDTH_PRESETS:
            self.resolution_combo.addItem(label)
        self._set_batch_resolution_combo(self._batch_output_width)
        self.resolution_combo.currentTextChanged.connect(self._save_batch_output_width)
        self._format_row_widget = QWidget(); self._fix_bg(self._format_row_widget, _SIDE)
        frw_layout = QHBoxLayout(self._format_row_widget)
        frw_layout.setContentsMargins(0, 0, 0, 0); frw_layout.setSpacing(8)
        frw_layout.addWidget(_lbl("图片格式:", "hint"))
        frw_layout.addWidget(self.format_combo)
        frw_layout.addWidget(_lbl("分辨率:", "hint"))
        frw_layout.addWidget(self.resolution_combo)
        frw_layout.addStretch()
        fv.addWidget(self._format_row_widget)
        fv.addSpacing(16); fv.addWidget(_sep()); fv.addSpacing(14)
        from ui.diversify_widget import DiversifyWidget
        self._batch_diversify = DiversifyWidget()
        fv.addWidget(self._batch_diversify)
        fv.addStretch()

        ls_scroll.setWidget(ls_form)
        lsv.addWidget(ls_scroll)

        # Fixed bottom: progress + run/abort
        ls_bottom = QWidget(); self._fix_bg(ls_bottom, _SIDE)
        self._mark_styled_bg(ls_bottom, "editor_bottom")
        lbv = QVBoxLayout(ls_bottom); lbv.setContentsMargins(14, 8, 14, 12); lbv.setSpacing(6)
        lbv.addWidget(_sep()); lbv.addSpacing(4)
        self.progress_bar = QProgressBar(); self.progress_bar.setVisible(False)
        lbv.addWidget(self.progress_bar)
        self.progress_label = _lbl("", "hint")
        self.progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbv.addWidget(self.progress_label)
        self.btn_run = _btn("  ▶   开始合成", self._run_batch, "primary")
        self.btn_run.setFixedHeight(48)
        self.btn_abort = _btn("  停止", self._abort_batch, "danger")
        self.btn_abort.setFixedHeight(48); self.btn_abort.setVisible(False)
        lbv.addLayout(_row(self.btn_run, self.btn_abort))
        lsv.addWidget(ls_bottom)

        main_hl.addWidget(left_side)

        # ── Right panel (template / file assignment, fills remaining width) ──
        right_scroll = QScrollArea(); right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._fix_bg(right_scroll, _WIN); self._fix_bg(right_scroll.viewport(), _WIN)
        self._mark_styled_bg(right_scroll.viewport(), "batch_viewport")
        right_body = QWidget(); self._fix_bg(right_body, _WIN)
        self._mark_styled_bg(right_body, "batch_scroll_body")
        rlv = QVBoxLayout(right_body)
        rlv.setContentsMargins(20, 20, 20, 20); rlv.setSpacing(12)

        # Template assignment card (folder / image modes)
        c2 = _card(_lbl("场景模板", "h2"))
        self._c2_hint = _lbl("每行独立选择模板，或点「统一选模板」批量设置所有行。", "hint")
        self._c2_hint.setWordWrap(True)
        c2.layout().addWidget(self._c2_hint)
        btn_qa = _btn("统一选模板…", self._apply_all, "scan")
        c2.layout().addLayout(_row(btn_qa, None))

        self.subfolder_table = QTableWidget(0, 3)
        self.subfolder_table.setHorizontalHeaderLabels(["子文件夹 / 图片组", "图片数", "已选模板（点击修改）"])
        hh = self.subfolder_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.subfolder_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.subfolder_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.subfolder_table.verticalHeader().setVisible(False)
        self.subfolder_table.setMinimumHeight(200)
        _set_green_selection(self.subfolder_table)
        tbl_wrap = QWidget(); tbl_wrap.setObjectName("inset")
        tw = QVBoxLayout(tbl_wrap); tw.setContentsMargins(0, 0, 0, 0)
        tw.addWidget(self.subfolder_table)
        c2.layout().addWidget(tbl_wrap)
        rlv.addWidget(c2)
        self._c2 = c2

        # Video table card (video mode, right panel)
        c_video_right = _card(_lbl("视频与模板", "h2"))
        c_video_right.layout().addWidget(_lbl("每行选择要嵌入的场景模板", "hint"))
        self.video_table = QTableWidget(0, 3)
        self.video_table.setHorizontalHeaderLabels(["视频文件（每帧作为 PPT 内容嵌入场景）", "时长/帧数", "场景模板"])
        vh = self.video_table.horizontalHeader()
        vh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        vh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        vh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.video_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.video_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.video_table.verticalHeader().setVisible(False)
        self.video_table.setMinimumHeight(200)
        _set_green_selection(self.video_table)
        vid_wrap = QWidget(); vid_wrap.setObjectName("inset")
        vw = QVBoxLayout(vid_wrap); vw.setContentsMargins(0, 0, 0, 0); vw.addWidget(self.video_table)
        c_video_right.layout().addWidget(vid_wrap)
        c_video_right.hide()
        rlv.addWidget(c_video_right)
        self._c_video_right = c_video_right
        self._video_row_selections = {}

        rlv.addStretch()
        right_scroll.setWidget(right_body)
        main_hl.addWidget(right_scroll, 1)

        return outer

    # ── Settings tab ──────────────────────────────────────────────────────────

    def _build_settings_tab(self):
        page = QWidget()
        self._fix_bg(page, _WIN)
        self._mark_styled_bg(page, "batch_outer")
        lv = QVBoxLayout(page)
        lv.setContentsMargins(40, 40, 40, 40); lv.setSpacing(16)

        title = QLabel("设置")
        title.setStyleSheet(f"font-size:22px; font-weight:700; color:{_TEXT};")
        lv.addWidget(title)

        # App info card
        info_card = _card(_lbl("关于融景", "h2"))
        info_card.layout().addWidget(_lbl("将 PPT 截图通过透视变换嵌入实拍背景图，批量生成合成图片或视频。", "hint"))
        info_card.layout().addSpacing(4)
        lv.addWidget(info_card)

        # AI background API card
        ai_card = _card(_lbl("AI 背景图 — API 配置", "h2"))
        ai_form = QFormLayout()
        ai_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        ai_form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        ai_form.setHorizontalSpacing(12)
        ai_form.setVerticalSpacing(10)

        self.ai_api_key_edit = QLineEdit()
        self.ai_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.ai_api_key_edit.setPlaceholderText("OpenAI API Key")
        self.ai_api_key_edit.setText(str(self._settings.value("ai/api_key", "") or ""))

        self.ai_base_url_edit = QLineEdit()
        self.ai_base_url_edit.setPlaceholderText("https://api.openai.com/v1")
        self.ai_base_url_edit.setText(str(self._settings.value("ai/base_url", "https://api.openai.com/v1") or "https://api.openai.com/v1"))

        self.ai_model_edit = QLineEdit()
        self.ai_model_edit.setPlaceholderText("gpt-image-2")
        self.ai_model_edit.setText(str(self._settings.value("ai/model", "gpt-image-2") or "gpt-image-2"))

        ai_form.addRow("API Key", self.ai_api_key_edit)
        ai_form.addRow("Base URL", self.ai_base_url_edit)
        ai_form.addRow("模型名", self.ai_model_edit)
        ai_card.layout().addLayout(ai_form)
        ai_card.layout().addWidget(_lbl("支持兼容 OpenAI Images API 的第三方中转站。", "hint"))
        for key, edit, default in (
            ("ai/api_key", self.ai_api_key_edit, ""),
            ("ai/base_url", self.ai_base_url_edit, "https://api.openai.com/v1"),
            ("ai/model", self.ai_model_edit, "gpt-image-2"),
        ):
            edit.editingFinished.connect(lambda k=key, e=edit, d=default: self._save_ai_setting(k, e, d))
        lv.addWidget(ai_card)

        # Data backup card
        backup_card = _card(_lbl("数据备份与恢复", "h2"))
        backup_card.layout().addWidget(_lbl("导出或导入所有模板和背景图数据。", "hint"))
        backup_card.layout().addSpacing(8)
        btn_export = QPushButton("导出备份 (.zip)")
        btn_export.clicked.connect(self._export_backup)
        btn_import = QPushButton("导入备份 (.zip)")
        btn_import.clicked.connect(self._import_backup)
        backup_card.layout().addLayout(_row(btn_export, btn_import, None))
        lv.addWidget(backup_card)

        # Data management card
        data_card = _card(_lbl("数据管理", "h2"))
        data_card.layout().addWidget(_lbl("清除所有模板数据，用于卸载前清理本地存储。", "hint"))
        data_card.layout().addSpacing(8)
        btn_uninstall = QPushButton("清除所有数据（卸载前使用）")
        btn_uninstall.setObjectName("danger")
        btn_uninstall.clicked.connect(self._uninstall_data)
        data_card.layout().addWidget(btn_uninstall)
        lv.addWidget(data_card)

        lv.addStretch()
        return page

    def _save_ai_setting(self, key: str, edit: QLineEdit, default: str):
        value = edit.text().strip()
        if self._try_apply_ai_connection_json(value):
            return
        if key == "ai/base_url":
            value = normalize_base_url(value or default)
            edit.setText(value)
        if not value and default:
            value = default
            edit.setText(default)
        self._settings.setValue(key, value)

    def _try_apply_ai_connection_json(self, value: str) -> bool:
        if not value.startswith("{"):
            return False
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return False
        if not isinstance(payload, dict):
            return False

        api_key = str(payload.get("key") or payload.get("api_key") or "").strip()
        base_url = str(payload.get("url") or payload.get("base_url") or "").strip()
        model = str(payload.get("model") or "").strip()
        if not api_key and not base_url and not model:
            return False

        if api_key:
            self.ai_api_key_edit.setText(api_key)
            self._settings.setValue("ai/api_key", api_key)
        if base_url:
            normalized = normalize_base_url(base_url)
            self.ai_base_url_edit.setText(normalized)
            self._settings.setValue("ai/base_url", normalized)
        if model:
            self.ai_model_edit.setText(model)
            self._settings.setValue("ai/model", model)
        return True

    def _on_ai_backgrounds_saved(self, saved_paths: list, context: dict | None = None):
        if not saved_paths:
            return
        context = context or {}
        first = saved_paths[0]
        self._switch_page(self._page_indices.get("editor", 0))
        self._new_template()
        self.bg_path_edit.setText(first)
        self.canvas.set_background(first)
        self._set_template_category(context.get("category", "教室场景"))
        self._set_document_preset(context.get("render_preset", "paper"))
        self._on_template_category_changed()
        self._update_auto_detect_enabled()
        self._save_dir("bg", os.path.dirname(first))

    # ── Template management ───────────────────────────────────────────────────

    def _refresh_template_list(self):
        self.template_list.clear()
        grouped = {}
        for t in self.tm.load_all():
            key = getattr(t, "storage_key", "") or t.name
            loaded = self.tm.load(key) or t
            category = normalize_template_category(getattr(loaded, "category", "教室场景") or "教室场景")
            grouped.setdefault(category, []).append((key, loaded))
        for category in sorted(grouped, key=_category_sort_key):
            heading = QListWidgetItem(category)
            heading.setFlags(Qt.ItemFlag.NoItemFlags)
            heading.setForeground(QColor(_TEXT2))
            heading.setData(Qt.ItemDataRole.UserRole, None)
            font = heading.font()
            font.setBold(True)
            heading.setFont(font)
            self.template_list.addItem(heading)
            for key, loaded in grouped[category]:
                prefix = "⚠ " if loaded.is_broken else ""
                item = QListWidgetItem(f"  {prefix}{loaded.name}")
                item.setData(Qt.ItemDataRole.UserRole, key)
                item.setToolTip(category)
                if loaded.is_broken:
                    item.setForeground(QColor(_RED))
                    item.setToolTip("此模板的背景图已不存在，请重新选择背景图")
                self.template_list.addItem(item)

    def _on_template_selected(self, row):
        if row < 0: return
        item = self.template_list.item(row)
        if not item or item.data(Qt.ItemDataRole.UserRole) is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole) or item.text()
        tpl = self.tm.load(key)
        if not tpl: return
        self._loaded_tpl_name = key
        self.tpl_name_edit.setText(tpl.name)
        self.bg_path_edit.setText(tpl.background_path)
        self._set_template_category(getattr(tpl, "category", "教室场景"))
        self._set_document_preset(getattr(tpl, "render_preset", "paper"))
        if os.path.exists(tpl.background_path):
            self.canvas.set_background(tpl.background_path)
            self.canvas.set_points(tpl.screen_points)
        else:
            self.canvas.clear_all()
            self.points_badge.setText("⚠ 此模板的背景图已不存在，请重新选择背景图")
            self.points_badge.setObjectName("badge")
            self.points_badge.setStyleSheet(f"color:{_RED};")
            QMessageBox.warning(self, "背景图片丢失", f"此模板的背景图已不存在，请重新选择背景图：\n{tpl.background_path}")
        self._update_auto_detect_enabled()
        if tpl.output_width == 0:
            self.output_size_combo.setCurrentIndex(0)
        else:
            found = False
            for lbl, size in OUTPUT_PRESETS.items():
                if size and size == (tpl.output_width, tpl.output_height):
                    self.output_size_combo.setCurrentText(lbl); found = True; break
            if not found:
                self.output_size_combo.setCurrentText("自定义...")
                self.custom_w.setValue(tpl.output_width)
                self.custom_h.setValue(tpl.output_height)

    def _new_template(self):
        self.template_list.clearSelection()
        self._loaded_tpl_name = None
        self.tpl_name_edit.clear()
        self.bg_path_edit.clear()
        self.preview_path_edit.clear()
        self._set_template_category("教室场景")
        self._set_document_preset("paper")
        self.canvas.clear_all()
        self._update_auto_detect_enabled()

    def _set_template_category(self, category: str):
        category = normalize_template_category(category)
        if category in TEMPLATE_CATEGORIES:
            self.tpl_custom_category_edit.clear()
            self.tpl_category_combo.setCurrentText(category or "教室场景")
        else:
            self.tpl_category_combo.setCurrentText("自定义场景")
            self.tpl_custom_category_edit.setText(category)
        self._on_template_category_changed()

    def _set_template_type(self, template_type: str):
        idx = self.tpl_type_combo.findData(template_type or "screen")
        self.tpl_type_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._on_template_category_changed()

    def _set_document_preset(self, render_preset: str):
        idx = self.tpl_preset_combo.findData(normalize_render_preset("文档纸张", render_preset))
        self.tpl_preset_combo.setCurrentIndex(idx if idx >= 0 else 0)

    def _current_template_type(self) -> str:
        category = normalize_template_category(self._selected_template_category())
        return "document_paper" if category == "文档纸张" else "screen"

    def _selected_template_category(self) -> str:
        custom = self.tpl_custom_category_edit.text().strip()
        return normalize_template_category(custom or self.tpl_category_combo.currentText().strip() or "教室场景")

    def _on_template_category_preset_activated(self, *_):
        if self.tpl_custom_category_edit.text().strip():
            self.tpl_custom_category_edit.clear()

    def _on_template_category_changed(self, *_):
        template_type = self._current_template_type()
        idx = self.tpl_type_combo.findData(template_type)
        if idx >= 0:
            self.tpl_type_combo.setCurrentIndex(idx)
        is_document = template_type == "document_paper"
        self.tpl_preset_label.setVisible(False)
        self.tpl_preset_combo.setVisible(False)

    def _delete_template(self):
        item = self.template_list.currentItem()
        if not item: return
        key = item.data(Qt.ItemDataRole.UserRole) or item.text()
        tpl = self.tm.load(key)
        label = tpl.name if tpl else key
        if QMessageBox.question(self, "确认删除", f"删除模板「{label}」？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.Yes:
            self.tm.delete(key)
            if self._loaded_tpl_name == key:
                self._loaded_tpl_name = None
            self._refresh_template_list()

    def _uninstall_data(self):
        data_dir = os.path.dirname(self.tm.templates_dir)
        ret = QMessageBox.warning(
            self, "清除所有数据",
            f"将删除所有模板和设置数据：\n{data_dir}\n\n此操作不可恢复，确认继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if ret == QMessageBox.StandardButton.Yes:
            try:
                self._settings.clear()
                shutil.rmtree(data_dir, ignore_errors=True)
                QMessageBox.information(self, "完成", "数据已清除，请重新启动或直接删除 app。")
                self._refresh_template_list()
            except Exception as e:
                QMessageBox.critical(self, "错误", str(e))

    def _export_backup(self):
        default_name = f"融景_backup_{datetime.now().strftime('%Y%m%d')}.zip"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出备份", default_name, "Zip Files (*.zip)"
        )
        if not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"

        templates = self._backup_files(self.tm.templates_dir, ".json")
        backgrounds_dir = getattr(self.tm, "backgrounds_dir", None) or os.path.join(
            os.path.dirname(self.tm.templates_dir), "backgrounds"
        )
        backgrounds = self._backup_files(
            backgrounds_dir,
            (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".heic"),
        )
        collages = self._backup_files(self._collages_dir, ".json")
        manifest = {
            "version": 1,
            "created": datetime.now().isoformat(timespec="seconds"),
            "templates_count": len(templates),
            "collages_count": len(collages),
            "backgrounds_count": len(backgrounds),
        }

        try:
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
                for src in templates:
                    zf.write(src, os.path.join("templates", os.path.basename(src)))
                for src in backgrounds:
                    zf.write(src, os.path.join("backgrounds", os.path.basename(src)))
                for src in collages:
                    zf.write(src, os.path.join("collages", os.path.basename(src)))
                zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            QMessageBox.information(self, "导出完成", f"备份已导出：\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _import_backup(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入备份", "", "Zip Files (*.zip)"
        )
        if not path:
            return

        staging_dir = None
        try:
            with zipfile.ZipFile(path, "r") as zf:
                names = zf.namelist()
                if "manifest.json" not in names:
                    QMessageBox.critical(self, "导入失败", "备份文件缺少 manifest.json。")
                    return

                mode = self._ask_backup_import_mode()
                if mode is None:
                    return

                templates_dir = self.tm.templates_dir
                backgrounds_dir = getattr(self.tm, "backgrounds_dir", None) or os.path.join(
                    os.path.dirname(self.tm.templates_dir), "backgrounds"
                )
                collages_dir = self._collages_dir or os.path.join(
                    os.path.dirname(self.tm.templates_dir), "collages"
                )

                for directory in (templates_dir, backgrounds_dir, collages_dir):
                    os.makedirs(directory, exist_ok=True)

                if mode == "overwrite":
                    staging_dir = tempfile.mkdtemp(prefix="rongjing_backup_")
                    extract_templates_dir = os.path.join(staging_dir, "templates")
                    extract_backgrounds_dir = os.path.join(staging_dir, "backgrounds")
                    extract_collages_dir = os.path.join(staging_dir, "collages")
                    for directory in (
                        extract_templates_dir,
                        extract_backgrounds_dir,
                        extract_collages_dir,
                    ):
                        os.makedirs(directory, exist_ok=True)
                else:
                    extract_templates_dir = templates_dir
                    extract_backgrounds_dir = backgrounds_dir
                    extract_collages_dir = collages_dir

                background_names = {
                    os.path.basename(name)
                    for name in names
                    if self._is_backup_member(name, "backgrounds") and not name.endswith("/")
                }
                for name in names:
                    if self._is_backup_member(name, "backgrounds"):
                        self._extract_backup_member(zf, name, extract_backgrounds_dir, mode)
                    elif self._is_backup_member(name, "collages", ".json"):
                        self._extract_backup_member(zf, name, extract_collages_dir, mode)
                    elif self._is_backup_member(name, "templates", ".json"):
                        self._extract_template_member(
                            zf,
                            name,
                            extract_templates_dir,
                            backgrounds_dir,
                            background_names,
                            mode,
                        )

                if mode == "overwrite":
                    self._clear_backup_target_dirs(templates_dir, backgrounds_dir, collages_dir)
                    for src_dir, dst_dir in (
                        (extract_templates_dir, templates_dir),
                        (extract_backgrounds_dir, backgrounds_dir),
                        (extract_collages_dir, collages_dir),
                    ):
                        for filename in os.listdir(src_dir):
                            shutil.move(
                                os.path.join(src_dir, filename),
                                os.path.join(dst_dir, filename),
                            )

            self._refresh_template_list()
            if self._collage_tab is not None and hasattr(self._collage_tab, "_load_template_list"):
                self._collage_tab._load_template_list()
            QMessageBox.information(self, "导入完成", "备份数据已导入。")
        except zipfile.BadZipFile:
            QMessageBox.critical(self, "导入失败", "请选择有效的 .zip 备份文件。")
        except Exception as e:
            QMessageBox.critical(self, "导入失败", str(e))
        finally:
            if staging_dir is not None:
                shutil.rmtree(staging_dir, ignore_errors=True)

    @staticmethod
    def _backup_files(directory: str | None, suffixes) -> list[str]:
        if not directory or not os.path.isdir(directory):
            return []
        if isinstance(suffixes, str):
            suffixes = (suffixes,)
        suffixes = tuple(s.lower() for s in suffixes)
        return [
            os.path.join(directory, fn)
            for fn in sorted(os.listdir(directory), key=natural_sort_key)
            if os.path.isfile(os.path.join(directory, fn)) and fn.lower().endswith(suffixes)
        ]

    @staticmethod
    def _is_backup_member(name: str, folder: str, suffix: str | None = None) -> bool:
        normalized = name.replace("\\", "/")
        if normalized != os.path.normpath(normalized).replace("\\", "/"):
            return False
        prefix = f"{folder}/"
        if not normalized.startswith(prefix) or normalized.endswith("/"):
            return False
        filename = normalized[len(prefix):]
        if "/" in filename or not filename:
            return False
        return suffix is None or filename.lower().endswith(suffix)

    @staticmethod
    def _clear_backup_target_dirs(*directories: str):
        seen = set()
        for directory in directories:
            abspath = os.path.abspath(directory)
            if abspath in seen:
                continue
            seen.add(abspath)
            shutil.rmtree(abspath, ignore_errors=True)
            os.makedirs(abspath, exist_ok=True)

    def _ask_backup_import_mode(self) -> str | None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("导入备份")
        box.setText("请选择导入方式：")
        merge_btn = box.addButton("合并（保留已有、导入新增）", QMessageBox.ButtonRole.AcceptRole)
        overwrite_btn = box.addButton("覆盖（清空后全量导入）", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked == merge_btn:
            return "merge"
        if clicked == overwrite_btn:
            return "overwrite"
        return None

    def _extract_backup_member(self, zf: zipfile.ZipFile, name: str, target_dir: str, mode: str) -> bool:
        target = os.path.join(target_dir, os.path.basename(name))
        if mode == "merge" and os.path.exists(target):
            return False
        with zf.open(name) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
        return True

    def _extract_template_member(
        self,
        zf: zipfile.ZipFile,
        name: str,
        target_dir: str,
        backgrounds_dir: str,
        background_names: set[str],
        mode: str,
    ) -> bool:
        target = os.path.join(target_dir, os.path.basename(name))
        if mode == "merge" and os.path.exists(target):
            return False

        data = json.loads(zf.read(name).decode("utf-8"))
        background_path = data.get("background_path")
        if isinstance(background_path, str):
            background_name = os.path.basename(background_path)
            if background_name in background_names:
                data["background_path"] = os.path.join(backgrounds_dir, background_name)
        with open(target, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True

    def _save_template(self):
        name = self.tpl_name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "请填写模板名称"); return
        bg = self.bg_path_edit.text().strip()
        if not bg:
            QMessageBox.warning(self, "提示", "请选择背景图片"); return
        if len(self.canvas.points) != 4:
            QMessageBox.warning(self, "提示", "请在背景图片上放置 4 个角点"); return

        w, h = self._editor_output_size()
        category = self._selected_template_category()
        template_type = self._current_template_type()

        existing_key = self.tm.key_for_name_category(name, category)
        if existing_key and existing_key != self._loaded_tpl_name:
            reply = QMessageBox.question(self, "已存在同名模板",
                f"「{category}」分类下已存在模板「{name}」，是否覆盖？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return

        target_key = existing_key or self._loaded_tpl_name
        storage_key = self.tm.save(Template(
            name,
            bg,
            self.canvas.points,
            w,
            h,
            category=category,
            template_type=template_type,
            render_preset=("paper" if template_type == "document_paper" else "clear"),
        ), storage_key=target_key)
        self._loaded_tpl_name = storage_key
        self._refresh_template_list()
        for i in range(self.template_list.count()):
            item = self.template_list.item(i)
            if (item.data(Qt.ItemDataRole.UserRole) or item.text()) == storage_key:
                self.template_list.setCurrentRow(i); break
        QMessageBox.information(self, "已保存", f"模板「{name}」保存成功")

    def _editor_output_size(self):
        lbl = self.output_size_combo.currentText()
        size = OUTPUT_PRESETS.get(lbl)
        return (self.custom_w.value(), self.custom_h.value()) if size is None else size

    # ── Editor canvas actions ─────────────────────────────────────────────────

    def _load_background(self):
        path = pick_image(self, "选择背景图片", self._last_dir_bg)
        if path:
            self._save_dir("bg", os.path.dirname(path))
            self.bg_path_edit.setText(path)
            self.canvas.set_background(path)
            self._update_auto_detect_enabled()

    def _load_preview(self):
        path = pick_image(self, "选择 PPT 预览图片", self._last_dir_preview)
        if path:
            self._save_dir("preview", os.path.dirname(path))
            self.preview_path_edit.setText(path)
            self.canvas.set_preview(path)

    def _clear_preview(self):
        self.preview_path_edit.clear(); self.canvas.clear_preview()

    def _clear_points(self):
        self.canvas.clear_points()

    def _update_auto_detect_enabled(self):
        if not hasattr(self, "auto_detect_btn"):
            return
        bg = self.bg_path_edit.text().strip()
        self.auto_detect_btn.setEnabled(bool(bg and os.path.exists(bg)))

    def _auto_detect_screen(self):
        bg = self.bg_path_edit.text().strip()
        if not bg or not os.path.exists(bg):
            self._update_auto_detect_enabled()
            return

        points = detect_screen_points(bg)
        if points is None:
            QMessageBox.warning(self, "提示", "未能自动识别屏幕区域，请手动标注")
            return

        self.canvas.set_points(points)

    def _on_points_changed(self, points):
        n = len(points)
        if n == 4:
            self.points_badge.setText("✓  4 / 4 个角点")
            self.points_badge.setObjectName("badge_ok")
        else:
            self.points_badge.setText(f"{n} / 4 个角点")
            self.points_badge.setObjectName("badge")
        self.points_badge.setStyleSheet("")   # force QSS refresh

    # ── Batch mode ────────────────────────────────────────────────────────────

    def _set_batch_mode(self, idx: int):
        self._batch_mode = idx
        for i, card in enumerate(self._mode_cards):
            card.setProperty("selected", i == idx)
            card.style().unpolish(card)
            card.style().polish(card)
        self._c1_folder.setVisible(idx == 0)
        self._c1_image.setVisible(idx == 1)
        self._c1_video.setVisible(idx == 2)
        self._c2.setVisible(idx != 2)
        self._c_video_right.setVisible(idx == 2)
        self._format_row_widget.setVisible(idx != 2)  # no format selector for video
        self._batch_diversify.setVisible(idx != 2)
        # Update hint to match current mode
        if idx == 0:
            self._c2_hint.setText("每行独立选择模板，或点「统一选模板」批量设置所有行。")
        else:
            self._c2_hint.setText("为所有图片统一选择模板，或点「统一选模板」批量设置。")

    def _save_dir(self, key: str, path: str):
        """Persist a picker's last-used directory to QSettings."""
        setattr(self, f"_last_dir_{key}", path)
        self._settings.setValue(f"last_dir_{key}", path)

    def _set_batch_resolution_combo(self, output_width: int):
        for label, width in BATCH_OUTPUT_WIDTH_PRESETS.items():
            if width == output_width:
                self.resolution_combo.setCurrentText(label)
                return
        self.resolution_combo.setCurrentIndex(0)
        self._batch_output_width = BATCH_OUTPUT_WIDTH_PRESETS[self.resolution_combo.currentText()]

    def _save_batch_output_width(self, *_):
        self._batch_output_width = BATCH_OUTPUT_WIDTH_PRESETS.get(
            self.resolution_combo.currentText(),
            1920,
        )
        self._settings.setValue("batch_output_width", self._batch_output_width)

    # ── Batch actions ─────────────────────────────────────────────────────────

    def _browse_input(self):
        path = pick_folder(self, "选择 PPT 图片主文件夹", self._last_dir_input)
        if path:
            self._save_dir("input", path)
            self.input_dir_edit.setText(path)

    def _browse_output(self):
        path = pick_folder(self, "选择输出文件夹", self._last_dir_output)
        if path:
            self._save_dir("output", path)
            self.output_dir_edit.setText(path)

    def _templates_for_picker(self, screen_only: bool = False) -> list:
        templates = self.tm.load_all()
        if screen_only:
            templates = [
                t for t in templates
                if (getattr(t, "template_type", "screen") or "screen") == "screen"
            ]
        return templates

    def _template_label(self, key: str) -> str:
        return self.tm.display_label(key)

    def _template_button_text(self, keys: list) -> str:
        if not keys:
            return "选择模板 ▾"
        if len(keys) == 1:
            label = self._template_label(keys[0])
            return (label[:12] + "… ▾") if len(label) > 12 else (label + " ▾")
        return f"已选 {len(keys)} 个模板 ▾"

    def _template_tooltip(self, keys: list) -> str:
        return "、".join(self._template_label(key) for key in keys)

    def _make_tpl_btn(self, row: int, selected_names: list) -> QPushButton:
        """Create a template-picker button for a table row."""
        def label():
            return self._template_button_text(self._row_selections.get(row, []))

        btn = QPushButton(label())
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {_INPUT}; color: {_TEXT};
                border: 1px solid {_SEP}; border-radius: 6px;
                padding: 6px 8px; font-size: 13px;
                text-align: left;
            }}
            QPushButton:hover {{ background: #E5E5E5; }}
        """)

        def open_picker():
            templates = self._templates_for_picker()
            if not templates:
                QMessageBox.warning(self, "提示", "请先在「模板配置」中创建并保存场景模板"); return
            current = self._row_selections.get(row, [])
            dlg = TemplatePickerDialog(templates, current, self)
            dlg.move(btn.mapToGlobal(QPoint(0, btn.height() + 4)))
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self._row_selections[row] = dlg.selected_names()
                btn.setText(label())
                btn.setToolTip(self._template_tooltip(self._row_selections[row]))

        btn.clicked.connect(open_picker)
        btn._label_fn = label
        self._row_selections[row] = list(selected_names)
        btn.setText(label())
        btn.setToolTip(self._template_tooltip(selected_names))
        return btn

    def _make_video_tpl_btn(self, row: int, selected_names: list) -> QPushButton:
        """Create a template-picker button for a video table row."""
        def label():
            return self._template_button_text(self._video_row_selections.get(row, []))
        btn = QPushButton(label())
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {_INPUT}; color: {_TEXT};
                border: 1px solid {_SEP}; border-radius: 6px;
                padding: 6px 8px; font-size: 13px;
                text-align: left;
            }}
            QPushButton:hover {{ background: #E5E5E5; }}
        """)
        def open_picker():
            templates = self._templates_for_picker(screen_only=True)
            if not templates:
                QMessageBox.warning(self, "提示", "视频模式只支持屏幕类模板，请先创建 screen 类型模板"); return
            current = self._video_row_selections.get(row, [])
            dlg = TemplatePickerDialog(templates, current, self)
            dlg.move(btn.mapToGlobal(QPoint(0, btn.height() + 4)))
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self._video_row_selections[row] = dlg.selected_names()
                btn.setText(label())
                btn.setToolTip(self._template_tooltip(self._video_row_selections[row]))
        btn.clicked.connect(open_picker)
        btn._label_fn = label
        self._video_row_selections[row] = list(selected_names)
        btn.setText(label())
        btn.setToolTip(self._template_tooltip(selected_names))
        return btn

    def _scan_subfolders(self):
        input_dir = self.input_dir_edit.text().strip()
        if not input_dir or not os.path.isdir(input_dir):
            QMessageBox.warning(self, "提示", "请先选择有效的输入文件夹"); return
        templates = self._templates_for_picker()
        if not templates:
            QMessageBox.warning(self, "提示", "请先在「模板配置」中创建并保存场景模板"); return
        subfolders = sorted((d for d in os.listdir(input_dir)
                             if os.path.isdir(os.path.join(input_dir, d))),
                            key=natural_sort_key)
        self._row_selections = {}
        self.subfolder_table.setRowCount(0)
        for sf in subfolders:
            row = self.subfolder_table.rowCount()
            self.subfolder_table.insertRow(row)
            self.subfolder_table.setItem(row, 0, QTableWidgetItem(sf))
            n = len(get_image_files(os.path.join(input_dir, sf)))
            ni = QTableWidgetItem(str(n))
            ni.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.subfolder_table.setItem(row, 1, ni)
            self.subfolder_table.setRowHeight(row, 44)
            btn = self._make_tpl_btn(row, [])
            self.subfolder_table.setCellWidget(row, 2, btn)
        if not subfolders:
            # Check for images directly in input_dir
            imgs = get_image_files(input_dir)
            if imgs:
                row = self.subfolder_table.rowCount()
                self.subfolder_table.insertRow(row)
                self.subfolder_table.setItem(row, 0, QTableWidgetItem("(根目录)"))
                ni = QTableWidgetItem(str(len(imgs)))
                ni.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.subfolder_table.setItem(row, 1, ni)
                self.subfolder_table.setRowHeight(row, 44)
                btn = self._make_tpl_btn(row, [])
                self.subfolder_table.setCellWidget(row, 2, btn)
            else:
                QMessageBox.information(self, "提示", "该文件夹内没有子文件夹也没有图片文件")

    def _apply_all(self):
        templates = self._templates_for_picker()
        if not templates:
            QMessageBox.warning(self, "提示", "暂无模板"); return
        dlg = TemplatePickerDialog(templates, self._last_apply_all, self)
        dlg.setWindowTitle("统一设置所有行的场景模板")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        names = dlg.selected_names()
        if not names:
            return
        self._last_apply_all = list(names)
        for row in range(self.subfolder_table.rowCount()):
            self._row_selections[row] = list(names)
            btn = self.subfolder_table.cellWidget(row, 2)
            if btn:
                btn.setText(self._template_button_text(names))
                btn.setToolTip(self._template_tooltip(names))

    def _pick_image_files(self):
        if sys.platform == "darwin":
            loc = f' default location (POSIX file "{self._last_dir_images}")' if self._last_dir_images else ""
            script = f'set f to (choose file with prompt "选择图片文件" of type {{"public.image"}}{loc} with multiple selections allowed)\nset out to ""\nrepeat with p in f\n    set out to out & POSIX path of p & "\\n"\nend repeat\nout'
            result, ran = _run_osascript(script)
            if ran:
                paths = [p for p in result.strip().split("\n") if p]
                paths.sort(key=lambda p: natural_sort_key(os.path.basename(p)))
                if paths:
                    self._save_dir("images", os.path.dirname(paths[0]))
                self._picked_image_files = paths
                self.image_files_label.setText(f"已选择 {len(paths)} 张图片" if paths else "未选择图片")
                if paths:
                    self._populate_image_mode_table()
                return
        # Qt fallback
        opts = QFileDialog.Option.DontUseNativeDialog if sys.platform == "darwin" else QFileDialog.Option(0)
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择图片文件", self._last_dir_images,
            "图片 (*.png *.jpg *.jpeg *.bmp *.webp *.tiff)",
            options=opts,
        )
        if paths:
            paths.sort(key=lambda p: natural_sort_key(os.path.basename(p)))
            self._save_dir("images", os.path.dirname(paths[0]))
            self._picked_image_files = paths
            self.image_files_label.setText(f"已选择 {len(paths)} 张图片")
            self._populate_image_mode_table()

    def _populate_image_mode_table(self):
        self._row_selections = {}
        self.subfolder_table.setRowCount(0)
        row = 0
        self.subfolder_table.insertRow(row)
        self.subfolder_table.setItem(row, 0, QTableWidgetItem("图片批量"))
        ni = QTableWidgetItem(str(len(self._picked_image_files)))
        ni.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subfolder_table.setItem(row, 1, ni)
        self.subfolder_table.setRowHeight(row, 44)
        btn = self._make_tpl_btn(row, [])
        self.subfolder_table.setCellWidget(row, 2, btn)

    def _pick_video_files(self):
        if sys.platform == "darwin":
            loc = f' default location (POSIX file "{self._last_dir_videos}")' if self._last_dir_videos else ""
            script = f'set f to (choose file with prompt "选择视频文件"{loc} with multiple selections allowed)\nset out to ""\nrepeat with p in f\n    set out to out & POSIX path of p & "\\n"\nend repeat\nout'
            result, ran = _run_osascript(script)
            if ran:
                paths = [p for p in result.strip().split("\n") if p]
                if paths:
                    self._save_dir("videos", os.path.dirname(paths[0]))
                    self._populate_video_table(paths)
                return
        opts = QFileDialog.Option.DontUseNativeDialog if sys.platform == "darwin" else QFileDialog.Option(0)
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择视频文件", self._last_dir_videos,
            "视频 (*.mp4 *.mov *.avi *.mkv *.m4v *.wmv)",
            options=opts,
        )
        if paths:
            self._save_dir("videos", os.path.dirname(paths[0]))
            self._populate_video_table(paths)

    def _populate_video_table(self, paths: list):
        import av
        self._video_row_selections = {}
        self.video_table.setRowCount(0)
        for vp in paths:
            try:
                with av.open(vp) as c:
                    vs = c.streams.video[0]
                    n = vs.frames if vs.frames else 0
                    fps = float(vs.average_rate or 25)
            except Exception:
                n, fps = 0, 25.0
            dur = f"{int(n/fps//60):02d}:{int(n/fps%60):02d}  ({n}帧)"
            row = self.video_table.rowCount()
            self.video_table.insertRow(row)
            self.video_table.setItem(row, 0, QTableWidgetItem(os.path.basename(vp)))
            self.video_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, vp)
            di = QTableWidgetItem(dur)
            di.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.video_table.setItem(row, 1, di)
            self.video_table.setRowHeight(row, 44)
            # Template picker button (column 2)
            tpl_btn = self._make_video_tpl_btn(row, [])
            self.video_table.setCellWidget(row, 2, tpl_btn)

    def _run_batch(self):
        output_dir = self.output_dir_edit.text().strip()
        if not output_dir:
            QMessageBox.warning(self, "提示", "请选择输出文件夹"); return

        if self._batch_mode == 2:
            # Video mode
            self._run_video_batch(output_dir)
            return

        # Folder mode or image mode
        if self._batch_mode == 0:
            input_dir = self.input_dir_edit.text().strip()
            if not input_dir or not os.path.isdir(input_dir):
                QMessageBox.warning(self, "提示", "请选择有效的输入文件夹"); return

        if self.subfolder_table.rowCount() == 0:
            QMessageBox.warning(self, "提示", "请先扫描文件夹或选择图片"); return

        tasks = []
        for row in range(self.subfolder_table.rowCount()):
            group_name = self.subfolder_table.item(row, 0).text()
            names = self._row_selections.get(row, [])
            templates = [t for name in names for t in [self.tm.load(name)] if t]
            if not templates: continue

            if self._batch_mode == 0:
                # Folder mode
                input_dir = self.input_dir_edit.text().strip()
                if group_name == "(根目录)":
                    files = get_image_files(input_dir)
                else:
                    files = get_image_files(os.path.join(input_dir, group_name))
            else:
                # Image mode
                files = self._picked_image_files

            if files:
                tasks.append((group_name, files, templates))

        if not tasks:
            QMessageBox.warning(self, "提示", "请为至少一个组选择模板"); return

        self._batch_runner = BatchRunner(
            tasks, output_dir,
            self.format_combo.currentText(),
            output_width=self._batch_output_width,
            diversify_config=self._batch_diversify.get_config(),
        )
        self._batch_runner.progress.connect(self._on_progress)
        self._batch_runner.finished.connect(self._on_finished)
        self.btn_run.setVisible(False); self.btn_abort.setVisible(True)
        self.progress_bar.setVisible(True); self.progress_bar.setValue(0)
        self.progress_label.setText("正在处理…")
        self._batch_runner.start()

    def _run_video_batch(self, output_dir: str):
        from core.batch_runner import VideoRunner
        if self.video_table.rowCount() == 0:
            QMessageBox.warning(self, "提示", "请先选择视频文件"); return
        tasks = []
        for row in range(self.video_table.rowCount()):
            item = self.video_table.item(row, 0)
            video_path = item.data(Qt.ItemDataRole.UserRole)
            names = self._video_row_selections.get(row, [])
            templates = [t for name in names for t in [self.tm.load(name)] if t]
            if not templates:
                QMessageBox.warning(self, "提示", f"请为「{item.text()}」选择场景模板"); return
            if any((getattr(t, "template_type", "screen") or "screen") != "screen" for t in templates):
                QMessageBox.warning(self, "提示", "视频模式只支持屏幕类模板，请重新选择模板"); return
            tasks.append((video_path, templates))
        self._batch_runner = VideoRunner(tasks, output_dir)
        self._batch_runner.progress.connect(self._on_progress)
        self._batch_runner.finished.connect(self._on_finished)
        self.btn_run.setVisible(False); self.btn_abort.setVisible(True)
        self.progress_bar.setVisible(True); self.progress_bar.setValue(0)
        self.progress_label.setText("正在处理视频…")
        self._batch_runner.start()

    def _abort_batch(self):
        if self._batch_runner: self._batch_runner.abort()

    def _on_progress(self, done, total, msg):
        self.progress_bar.setMaximum(total); self.progress_bar.setValue(done)
        self.progress_label.setText(f"[{done} / {total}]  {msg}")

    def _on_finished(self, success, msg):
        self.btn_run.setVisible(True); self.btn_abort.setVisible(False)
        self.progress_label.setText(msg)
        if success: QMessageBox.information(self, "完成", msg)
        else:       QMessageBox.warning(self, "处理结果", msg)
