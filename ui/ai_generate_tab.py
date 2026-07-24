from __future__ import annotations

import io
import json
import os
import random
import shutil
import time
from typing import Iterable

from PIL import Image
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.ai_background import (
    AIBackgroundError,
    AIBaseURLError,
    AIConfig,
    AIAuthError,
    AINetworkError,
    AIQuotaError,
    AIRateLimitError,
    generate_backgrounds,
)
from core.bg_prompt import build_prompt


_GREEN = "#07C160"
_SIDE = "#EFEFEF"
_CARD = "#FFFFFF"
_INPUT = "#F0F0F0"
_SEP = "#E5E5E5"
_TEXT = "#191919"
_TEXT2 = "#888888"
_RED = "#FA5151"
_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".rongjing", "ai_cache")

_TARGET_TYPES = ["教室场景", "台式机电脑", "笔记本室内", "文档纸张", "自定义场景"]
_TARGET_DEVICES = {
    "教室场景": ["希沃白板", "教室大屏", "多媒体大屏"],
    "台式机电脑": ["台式显示器", "一体机屏幕", "双屏桌面"],
    "笔记本室内": ["笔记本电脑", "笔记本外接屏"],
    "文档纸张": ["A4 竖版纸", "A4 横版纸"],
    "自定义场景": ["屏幕区域", "纸张区域"],
}
_TARGET_SCENES = {
    "教室场景": ["小学教室", "中学教室", "多媒体教室", "教师办公室"],
    "台式机电脑": ["教师办公桌", "家里书桌", "校园办公室", "教研室"],
    "笔记本室内": ["家里书桌", "教师办公桌", "居家备课", "宿舍"],
    "文档纸张": ["木质桌面", "暖光书桌", "冷白办公桌", "浅色桌面"],
    "自定义场景": ["教师办公桌", "家里书桌", "校园办公室", "木质桌面"],
}
_CLASSROOM_SCENES = ["小学教室", "中学教室", "多媒体教室"]

# 桌面摆件（台式机/笔记本/文档纸张/自定义场景使用）
_DESK_DECOR = ["有植物", "有咖啡杯", "有书本", "有小摆件", "极简"]
# 教室元素（教室场景专用，替代桌面摆件组）
_CLASSROOM_ELEMENTS = ["学生背影", "粉笔槽板擦", "标语横幅", "国旗", "极简"]

# 灯光选项按背景场景过滤，避免不契合组合（如教室大屏配夜晚暖光台灯）
_LIGHT_OPTIONS = {
    "教室场景": ["自然光", "柔光", "冷白光"],
    "台式机电脑": ["暖色灯光", "自然光", "冷白光"],
    "笔记本室内": ["暖色灯光", "自然光", "偏暗氛围"],
    "文档纸张": ["自然光", "柔光", "冷白光"],
    "自定义场景": ["暖色灯光", "自然光", "冷白光", "柔光", "偏暗氛围"],
}
_ANGLE_OPTIONS = ["正面平视", "略偏侧角", "略微仰视"]


class TagButton(QPushButton):
    state_changed = pyqtSignal(bool)

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("tagButton")
        self.clicked.connect(self._emit_state)

    def _emit_state(self):
        self.state_changed.emit(self.isChecked())


class TagGroup(QWidget):
    selection_changed = pyqtSignal(str)

    def __init__(self, label: str, options: Iterable[str], parent=None):
        super().__init__(parent)
        self._label = label
        self._buttons: list[TagButton] = []
        self._build_ui()
        self.replace_options(list(options))

    def set_label(self, text: str):
        self._label = text
        self._title_label.setText(text)

    def get_selection(self) -> str:
        for btn in self._buttons:
            if btn.isChecked():
                return btn.text()
        return ""

    def set_selection(self, text: str):
        current = self.get_selection()
        found = False
        for btn in self._buttons:
            checked = bool(text and btn.text() == text)
            btn.blockSignals(True)
            btn.setChecked(checked)
            btn.blockSignals(False)
            found = found or checked
        if text and not found:
            text = ""
        if text != current:
            self.selection_changed.emit(text)

    def random_select(self, rng: random.Random):
        opts = self.options()
        if opts:
            self.set_selection(rng.choice(opts))

    def replace_options(self, new_list: list[str]):
        old = self.get_selection()
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self._buttons = []
        for idx, text in enumerate(new_list):
            btn = TagButton(text)
            btn.clicked.connect(lambda checked=False, b=btn: self._on_button_clicked(b))
            self._buttons.append(btn)
            self._grid.addWidget(btn, idx // 2, idx % 2)
        if old in new_list:
            self.set_selection(old)
        elif old:
            self.selection_changed.emit("")

    def options(self) -> list[str]:
        return [btn.text() for btn in self._buttons]

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._title_label = QLabel(self._label)
        self._title_label.setObjectName("cap")
        layout.addWidget(self._title_label)
        self._grid = QGridLayout()
        self._grid.setHorizontalSpacing(6)
        self._grid.setVerticalSpacing(6)
        layout.addLayout(self._grid)

    def _on_button_clicked(self, button: TagButton):
        selected = button.isChecked()
        for btn in self._buttons:
            if btn is not button and btn.isChecked():
                btn.blockSignals(True)
                btn.setChecked(False)
                btn.blockSignals(False)
        self.selection_changed.emit(button.text() if selected else "")


class _GenerateWorker(QThread):
    """后台线程调用 AI 生成 API，避免阻塞 UI。"""
    finished_ok = pyqtSignal(list)       # List[Image.Image]
    failed = pyqtSignal(str)             # error message
    progress = pyqtSignal(int, int)      # (current, total)

    def __init__(self, config, prompt: str, n: int, aspect_ratio: str, parent=None):
        super().__init__(parent)
        self._config = config
        self._prompt = prompt
        self._n = n
        self._aspect_ratio = aspect_ratio

    def run(self):
        try:
            # gpt-image-2 不支持 n>1，需要循环调用
            images: list = []
            for i in range(self._n):
                batch = generate_backgrounds(
                    self._config, self._prompt,
                    n=1, aspect_ratio=self._aspect_ratio,
                )
                images.extend(batch)
                self.progress.emit(i + 1, self._n)
            self.finished_ok.emit(images)
        except Exception as exc:
            self.failed.emit(str(exc))


class _HistoryDialog(QDialog):
    """历史生成记录对话框。"""
    load_requested = pyqtSignal(list, dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("历史生成记录")
        self.setMinimumSize(640, 480)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QLabel("历史生成记录")
        header.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {_TEXT};")
        layout.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._list_widget = QWidget()
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._list_layout.setSpacing(8)
        scroll.setWidget(self._list_widget)
        layout.addWidget(scroll, 1)

        bottom = QHBoxLayout()
        open_btn = QPushButton("📂 在 Finder 中打开")
        open_btn.clicked.connect(self._open_in_finder)
        clear_btn = QPushButton("🗑 清除所有历史")
        clear_btn.clicked.connect(self._clear_all)
        bottom.addWidget(open_btn)
        bottom.addStretch()
        bottom.addWidget(clear_btn)
        layout.addLayout(bottom)

        self._refresh_list()

    def _refresh_list(self):
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not os.path.isdir(_CACHE_DIR):
            self._list_layout.addWidget(QLabel("暂无历史记录"))
            return

        batches = sorted(
            [d for d in os.listdir(_CACHE_DIR)
             if os.path.isdir(os.path.join(_CACHE_DIR, d))],
            reverse=True,
        )
        if not batches:
            self._list_layout.addWidget(QLabel("暂无历史记录"))
            return

        for batch_name in batches:
            batch_dir = os.path.join(_CACHE_DIR, batch_name)
            imgs = sorted(f for f in os.listdir(batch_dir) if f.lower().endswith((".png", ".jpg")))
            if not imgs:
                continue

            try:
                ts = time.strptime(batch_name, "%Y%m%d_%H%M%S")
                display_time = time.strftime("%Y-%m-%d %H:%M:%S", ts)
            except ValueError:
                display_time = batch_name

            # 读取 meta 信息
            meta_path = os.path.join(batch_dir, "meta.json")
            meta_info = ""
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, encoding="utf-8") as f:
                        meta = json.load(f)
                    device = meta.get("device", "")
                    scene = meta.get("scene", "")
                    if device or scene:
                        meta_info = f"  ·  {device} {scene}".strip()
                except Exception:
                    pass

            card = QFrame()
            card.setStyleSheet(
                "QFrame { background: white; border: 1px solid #E5E5E5; border-radius: 8px; }"
            )
            card_layout = QHBoxLayout(card)
            card_layout.setContentsMargins(10, 8, 10, 8)
            card_layout.setSpacing(10)

            # 缩略图
            thumb_label = QLabel()
            thumb_label.setFixedSize(56, 56)
            pix = QPixmap(os.path.join(batch_dir, imgs[0]))
            if not pix.isNull():
                pix = pix.scaled(56, 56, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
                thumb_label.setPixmap(pix)
            card_layout.addWidget(thumb_label)

            info_label = QLabel(f"{display_time}{meta_info}\n{len(imgs)} 张图片")
            info_label.setStyleSheet(f"color: {_TEXT}; font-size: 13px; border: none;")
            card_layout.addWidget(info_label, 1)

            load_btn = QPushButton("加载")
            load_btn.setFixedWidth(56)
            load_btn.setStyleSheet(
                f"background: {_GREEN}; color: white; border: none; border-radius: 6px; padding: 6px;"
            )
            load_btn.clicked.connect(lambda _=False, bd=batch_dir: self._load_batch(bd))
            card_layout.addWidget(load_btn)

            del_btn = QPushButton("删除")
            del_btn.setFixedWidth(56)
            del_btn.setStyleSheet(
                "background: #F5F5F5; color: #999; border: none; border-radius: 6px; padding: 6px;"
            )
            del_btn.clicked.connect(lambda _=False, bd=batch_dir: self._delete_batch(bd))
            card_layout.addWidget(del_btn)

            self._list_layout.addWidget(card)

    def _load_batch(self, batch_dir: str):
        images: list[Image.Image] = []
        for f in sorted(os.listdir(batch_dir)):
            if f.lower().endswith((".png", ".jpg")):
                images.append(Image.open(os.path.join(batch_dir, f)).copy())
        template_context = {}
        meta_path = os.path.join(batch_dir, "meta.json")
        if os.path.exists(meta_path):
            try:
                with open(meta_path, encoding="utf-8") as f:
                    meta = json.load(f)
                raw_context = meta.get("template_context", {})
                if isinstance(raw_context, dict):
                    template_context = raw_context
            except Exception:
                template_context = {}
        if images:
            self.load_requested.emit(images, template_context)
            self.accept()

    def _delete_batch(self, batch_dir: str):
        reply = QMessageBox.question(self, "确认删除", "确定要删除这条历史记录吗？")
        if reply == QMessageBox.StandardButton.Yes:
            shutil.rmtree(batch_dir, ignore_errors=True)
            self._refresh_list()

    def _open_in_finder(self):
        os.makedirs(_CACHE_DIR, exist_ok=True)
        os.system(f'open "{_CACHE_DIR}"')

    def _clear_all(self):
        reply = QMessageBox.question(self, "确认清除", "确定要清除所有历史记录吗？不可恢复。")
        if reply == QMessageBox.StandardButton.Yes:
            shutil.rmtree(_CACHE_DIR, ignore_errors=True)
            self._refresh_list()


class _ImageTile(QFrame):
    def __init__(self, image: Image.Image, aspect_ratio: str, parent=None):
        super().__init__(parent)
        self._image = image
        self._aspect_ratio = aspect_ratio
        self.setObjectName("imageTile")
        self.setMinimumSize(320, 220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QGridLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)
        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setObjectName("imagePreview")
        self._label.setScaledContents(False)
        self._check = QCheckBox()
        self._check.setChecked(True)
        self._check.toggled.connect(self._update_selection_style)
        layout.addWidget(self._label, 0, 0)
        layout.addWidget(self._check, 0, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        self._refresh_pixmap()
        self._update_selection_style()

    def is_checked(self) -> bool:
        return self._check.isChecked()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_pixmap()

    def heightForWidth(self, width: int) -> int:
        ratio = {"4:3": 4 / 3, "3:4": 3 / 4, "16:9": 16 / 9, "1:1": 1}.get(self._aspect_ratio, 4 / 3)
        return max(120, int(width / ratio))

    def hasHeightForWidth(self) -> bool:
        return True

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._check.setChecked(not self._check.isChecked())
        super().mousePressEvent(event)

    def _update_selection_style(self, _checked=True):
        if self._check.isChecked():
            self.setProperty("selected", True)
        else:
            self.setProperty("selected", False)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def _refresh_pixmap(self):
        pix = _pil_to_pixmap(self._image)
        target = self._label.size()
        if target.width() > 8 and target.height() > 8:
            pix = pix.scaled(target, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self._label.setPixmap(pix)


class AIGenerateTab(QWidget):
    save_finished = pyqtSignal(list, dict)

    def __init__(self, backgrounds_dir: str, parent=None):
        super().__init__(parent)
        self._backgrounds_dir = backgrounds_dir
        self._images: list[Image.Image] = []
        self._tiles: list[_ImageTile] = []
        self._loaded_template_context: dict | None = None
        self._build_ui()
        self._wire_signals()

    def _build_ui(self):
        self.setObjectName("AIGenerateTab")
        self.setStyleSheet(
            f"""
            QWidget#AIGenerateTab, QWidget#ai_right_body {{ background: #F7F7F7; }}
            QWidget#ai_sidebar, QWidget#ai_scroll_body {{ background: {_SIDE}; }}
            QWidget#ai_sidebar {{ border-right: 1px solid {_SEP}; }}
            QLabel#h2 {{ color: {_TEXT}; font-size: 15px; font-weight: 700; qproperty-alignment: AlignCenter; }}
            QLabel#cap {{ color: {_TEXT2}; font-size: 11px; font-weight: 600; qproperty-alignment: AlignCenter; }}
            QLabel#hint {{ color: {_TEXT2}; font-size: 12px; }}
            QPushButton#tagButton {{
                background: {_INPUT}; color: {_TEXT}; border: 1px solid {_SEP};
                border-radius: 8px; padding: 7px 10px; font-weight: 600;
            }}
            QPushButton#tagButton:checked {{ background: {_GREEN}; color: white; border-color: {_GREEN}; }}
            QPushButton#secondary {{
                background: rgba(7,193,96,0.08); color: {_GREEN};
                border: 1px dashed {_GREEN}; border-radius: 18px;
                padding: 8px 12px; font-weight: 600;
            }}
            QPushButton#primary {{
                background: {_GREEN}; color: white; border: none;
                border-radius: 22px; padding: 10px 16px; font-weight: 700;
            }}
            QFrame#aiCard {{
                background: {_CARD}; border: 1px solid {_SEP}; border-radius: 10px;
            }}
            QFrame#imageTile {{
                background: {_CARD}; border: 2px solid {_GREEN}; border-radius: 10px;
            }}
            QFrame#imageTile[selected="false"] {{
                border: 1px solid {_SEP};
            }}
            QLabel#imagePreview {{ background: {_INPUT}; border-radius: 8px; }}
            """
        )

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar(), 0)
        root.addWidget(self._build_right_panel(), 1)

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("ai_sidebar")
        sidebar.setFixedWidth(380)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        body = QWidget()
        body.setObjectName("ai_scroll_body")
        content = QVBoxLayout(body)
        content.setContentsMargins(16, 18, 16, 18)
        content.setSpacing(10)

        content.addWidget(_label("AI 背景图", "h2"))
        self._target_group = TagGroup("背景场景", _TARGET_TYPES)
        self._device_group = TagGroup("主体类型", _TARGET_DEVICES["教室场景"])
        self._scene_group = TagGroup("环境位置", _TARGET_SCENES["教室场景"])
        self._light_group = TagGroup("灯光", _LIGHT_OPTIONS["教室场景"])
        self._angle_group = TagGroup("拍摄角度", _ANGLE_OPTIONS)
        self._decor_group = TagGroup("教室元素", _CLASSROOM_ELEMENTS)
        self._tag_groups = [
            self._target_group,
            self._device_group,
            self._scene_group,
            self._light_group,
            self._angle_group,
            self._decor_group,
        ]
        for group in self._tag_groups:
            content.addWidget(group)

        content.addWidget(_label("额外描述（可选）", "cap"))
        self._extra_edit = QLineEdit()
        self._extra_edit.setPlaceholderText("如：木质桌面、暖色台灯")
        content.addWidget(self._extra_edit)

        row = QHBoxLayout()
        row.setSpacing(8)
        count_box = QWidget()
        count_layout = QVBoxLayout(count_box)
        count_layout.setContentsMargins(0, 0, 0, 0)
        count_layout.setSpacing(4)
        count_layout.addWidget(_label("生成张数", "cap"))
        self._count_spin = QSpinBox()
        self._count_spin.setRange(1, 8)
        self._count_spin.setValue(4)
        count_layout.addWidget(self._count_spin)

        aspect_box = QWidget()
        aspect_layout = QVBoxLayout(aspect_box)
        aspect_layout.setContentsMargins(0, 0, 0, 0)
        aspect_layout.setSpacing(4)
        aspect_layout.addWidget(_label("图片比例", "cap"))
        self._aspect_combo = QComboBox()
        self._aspect_combo.addItems(["3:4", "4:3", "1:1", "16:9"])
        self._aspect_combo.setCurrentText("3:4")
        aspect_layout.addWidget(self._aspect_combo)
        row.addWidget(count_box)
        row.addWidget(aspect_box)
        content.addLayout(row)

        self._greenscreen_check = QCheckBox("屏幕显示绿幕（推荐，保存后自动识别秒出角点）")
        self._greenscreen_check.setChecked(True)
        content.addWidget(self._greenscreen_check)

        self._random_btn = QPushButton("🎲 随机未选项")
        self._random_btn.setObjectName("secondary")
        content.addWidget(self._random_btn)
        content.addStretch(1)

        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        # 「开始生成」固定在侧边栏底部，不随内容滚动
        bottom = QWidget()
        bottom.setObjectName("ai_scroll_body")
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(16, 10, 16, 16)
        bottom_layout.setSpacing(6)
        self._generate_btn = QPushButton("✨ 开始生成")
        self._generate_btn.setObjectName("primary")
        self._generate_btn.setFixedHeight(44)
        bottom_layout.addWidget(self._generate_btn)
        self._history_btn = QPushButton("📂 历史记录")
        self._history_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._history_btn.setStyleSheet(
            f"background: transparent; color: {_TEXT2}; border: none; "
            f"font-size: 12px; padding: 4px;"
        )
        bottom_layout.addWidget(self._history_btn)
        hint = _label("API 配置在「设置」页自动保存。", "hint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bottom_layout.addWidget(hint)
        layout.addWidget(bottom)
        return sidebar

    def _build_right_panel(self) -> QWidget:
        wrapper = QWidget()
        wrapper_layout = QVBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("ai_right_body")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(14)

        results_card = _card("生成结果")
        self._results_grid = QGridLayout()
        self._results_grid.setHorizontalSpacing(12)
        self._results_grid.setVerticalSpacing(12)
        self._empty_label = _label("生成后在这里预览结果。", "hint")
        results_card.layout().addWidget(self._empty_label)
        results_card.layout().addLayout(self._results_grid)
        layout.addWidget(results_card, 1)

        scroll.setWidget(body)
        wrapper_layout.addWidget(scroll, 1)

        # 保存按钮固定在底部，不随内容滚动
        save_bar = QFrame()
        save_bar.setObjectName("ai_save_bar")
        save_bar.setStyleSheet(
            f"QFrame#ai_save_bar {{ background: #F7F7F7; border-top: 1px solid {_SEP}; }}"
        )
        save_layout = QHBoxLayout(save_bar)
        save_layout.setContentsMargins(28, 12, 28, 16)
        save_layout.setSpacing(16)
        save_layout.addWidget(_label("勾选图片，保存后跳转标注角点", "hint"), 1)
        self._save_btn = QPushButton("保存选中 → 跳转标注角点")
        self._save_btn.setObjectName("primary")
        self._save_btn.setFixedHeight(44)
        self._save_btn.setEnabled(False)
        save_layout.addWidget(self._save_btn)
        wrapper_layout.addWidget(save_bar)

        return wrapper

    def _wire_signals(self):
        self._target_group.selection_changed.connect(self._on_target_changed)
        self._device_group.selection_changed.connect(self._on_device_changed)
        self._random_btn.clicked.connect(lambda: self._random_select_unset())
        self._generate_btn.clicked.connect(self._generate)
        self._history_btn.clicked.connect(self._show_history)
        self._save_btn.clicked.connect(self._save_selected)

    def _on_target_changed(self, value: str):
        target = value or "教室场景"
        self._device_group.replace_options(_TARGET_DEVICES.get(target, _TARGET_DEVICES["自定义场景"]))
        self._scene_group.replace_options(_TARGET_SCENES.get(target, _TARGET_SCENES["自定义场景"]))
        self._light_group.replace_options(_LIGHT_OPTIONS.get(target, _LIGHT_OPTIONS["自定义场景"]))
        if target == "教室场景":
            self._decor_group.set_label("教室元素")
            self._decor_group.replace_options(_CLASSROOM_ELEMENTS)
        else:
            self._decor_group.set_label("桌面摆件")
            self._decor_group.replace_options(_DESK_DECOR)
        # 绿幕开关只对「有屏幕」的场景有意义，文档纸张场景没有屏幕概念
        self._greenscreen_check.setVisible(target != "文档纸张")

    def _on_device_changed(self, value: str):
        if value in ("希沃白板", "教室大屏", "多媒体大屏"):
            self._scene_group.replace_options(_CLASSROOM_SCENES)

    def _random_select_unset(self, rng: random.Random | None = None):
        rng = rng or random.Random()
        for group in self._tag_groups:
            if not group.get_selection():
                group.random_select(rng)

    def _build_prompt(self) -> str:
        # 拼装逻辑与词表已抽取到 core/bg_prompt.py（单一规范源，CLI 的
        # `bg-prompt` 子命令共用同一实现），这里只负责从 GUI 控件收集选择
        # 并委托调用，输出与抽取前逐字一致。
        target = self._target_group.get_selection()
        device = self._device_group.get_selection()
        scene = self._scene_group.get_selection()
        decor = self._decor_group.get_selection()
        light = self._light_group.get_selection()
        angle = self._angle_group.get_selection()
        extra = self._extra_edit.text().strip()
        return build_prompt(
            target, device, scene, self._greenscreen_check.isChecked(),
            decor=decor, light=light, angle=angle, extra=extra,
        )

    def _template_context(self) -> dict:
        target = self._target_group.get_selection()
        device = self._device_group.get_selection()
        if self._is_document_target(target, device):
            return {"category": "文档纸张", "template_type": "document_paper", "render_preset": "paper"}
        if target in ("台式机电脑", "笔记本室内", "自定义场景"):
            return {"category": target, "template_type": "screen", "render_preset": "clear"}
        return {"category": "教室场景", "template_type": "screen", "render_preset": "clear"}

    @staticmethod
    def _is_document_target(target: str, device: str) -> bool:
        return target == "文档纸张" or device in ("纸张区域", "A4 竖版纸", "A4 横版纸")

    def _generate(self):
        from PyQt6.QtCore import QSettings

        settings = QSettings("融景", "RongJing")
        api_key = str(settings.value("ai/api_key", "") or "").strip()
        base_url = str(settings.value("ai/base_url", "https://api.openai.com/v1") or "").strip()
        model = str(settings.value("ai/model", "gpt-image-2") or "").strip()
        if not api_key:
            QMessageBox.warning(self, "缺少 API Key", "请先到「设置」页填写 AI 背景图 API Key。")
            return

        self._generate_btn.setEnabled(False)
        self._generate_btn.setText("⏳ 生成中，请稍候…")
        self._clear_results()
        self._empty_label.setVisible(True)
        self._empty_label.setText("正在调用 AI 生成背景图，通常需要 30-60 秒…")
        self._save_btn.setEnabled(False)

        config = AIConfig(api_key=api_key, base_url=base_url, model=model)
        self._gen_worker = _GenerateWorker(
            config, self._build_prompt(),
            n=self._count_spin.value(),
            aspect_ratio=self._aspect_combo.currentText(),
            parent=self,
        )
        self._gen_worker.finished_ok.connect(self._on_generate_ok)
        self._gen_worker.failed.connect(self._on_generate_failed)
        self._gen_worker.progress.connect(self._on_generate_progress)
        self._gen_worker.finished.connect(self._on_generate_done)
        self._gen_worker.start()

    def _on_generate_progress(self, current: int, total: int):
        self._generate_btn.setText(f"⏳ 生成中 ({current}/{total})…")
        self._empty_label.setText(f"正在生成第 {current}/{total} 张背景图…")

    def _on_generate_ok(self, images: list):
        self._loaded_template_context = None
        self._images = images
        self._save_to_cache(images)
        self._render_results()

    def _on_generate_failed(self, error: str):
        self._empty_label.setText("生成后在这里预览结果。")
        QMessageBox.warning(self, "生成失败", error)

    def _on_generate_done(self):
        self._generate_btn.setEnabled(True)
        self._generate_btn.setText("✨ 开始生成")
        self._gen_worker = None

    # ── 历史记录 ────────────────────────────────────────────────

    def _save_to_cache(self, images: list):
        """将生成的图片自动保存到本地缓存目录。"""
        batch_dir = os.path.join(_CACHE_DIR, time.strftime("%Y%m%d_%H%M%S"))
        os.makedirs(batch_dir, exist_ok=True)
        for i, img in enumerate(images, 1):
            img.save(os.path.join(batch_dir, f"{i}.png"))
        # 保存 meta 信息
        meta = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "target": self._target_group.get_selection(),
            "device": self._device_group.get_selection(),
            "scene": self._scene_group.get_selection(),
            "count": len(images),
            "aspect_ratio": self._aspect_combo.currentText(),
            "template_context": self._template_context(),
        }
        with open(os.path.join(batch_dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    def _show_history(self):
        dlg = _HistoryDialog(self)
        dlg.load_requested.connect(self._load_history_images)
        dlg.exec()

    def _load_history_images(self, images: list, template_context: dict):
        """从历史记录加载图片到预览区。"""
        self._loaded_template_context = dict(template_context) if template_context else None
        self._images = images
        self._render_results()

    def _render_results(self):
        self._clear_results(clear_context=False)
        self._empty_label.setVisible(not self._images)
        aspect = self._aspect_combo.currentText()
        cols = 1 if len(self._images) <= 2 else 2
        for idx, image in enumerate(self._images):
            tile = _ImageTile(image, aspect)
            self._tiles.append(tile)
            self._results_grid.addWidget(tile, idx // cols, idx % cols)
        self._save_btn.setEnabled(bool(self._images))

    def _clear_results(self, clear_context: bool = True):
        while self._results_grid.count():
            item = self._results_grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self._tiles = []
        if clear_context:
            self._loaded_template_context = None

    def _save_selected(self):
        os.makedirs(self._backgrounds_dir, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        saved = []
        for idx, (image, tile) in enumerate(zip(self._images, self._tiles), start=1):
            if not tile.is_checked():
                continue
            path = os.path.join(self._backgrounds_dir, f"ai_{timestamp}_{idx}.png")
            image.save(path)
            saved.append(path)
        if not saved:
            QMessageBox.information(self, "未选择图片", "请至少勾选一张图片。")
            return
        template_context = self._loaded_template_context or self._template_context()
        self.save_finished.emit(saved, template_context)


def _pil_to_pixmap(image: Image.Image) -> QPixmap:
    buf = io.BytesIO()
    image.convert("RGBA").save(buf, format="PNG")
    pix = QPixmap()
    pix.loadFromData(buf.getvalue(), "PNG")
    return pix


def _label(text: str, object_name: str = "") -> QLabel:
    lbl = QLabel(text)
    if object_name:
        lbl.setObjectName(object_name)
    return lbl


def _card(title: str) -> QFrame:
    frame = QFrame()
    frame.setObjectName("aiCard")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(12)
    layout.addWidget(_label(title, "h2"))
    return frame
