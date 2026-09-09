"""应用内页面翻页视频预览弹窗；使用 PyAV 解码后逐帧显示。"""

from __future__ import annotations

from pathlib import Path

import av
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


MAX_PREVIEW_FRAMES = 120
DEFAULT_PREVIEW_FPS = 15.0


class PageVideoPreviewDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("翻页预览")
        self.setFixedSize(900, 560)
        self.setModal(True)
        self.setStyleSheet(
            """
            QDialog { background: #F7F7F7; }
            QWidget#previewCard { background: #FFFFFF; border: 1px solid #E5E5E5; border-radius: 16px; }
            QLabel#previewFrame { background: #191919; border-radius: 10px; }
            QLabel#previewStatus { color: #888888; padding: 2px 4px; }
            QPushButton { min-height: 36px; padding: 0 18px; border-radius: 18px; background: #F0F0F0; color: #191919; }
            QPushButton#primary { background: #07C160; color: white; font-weight: 600; }
            """
        )

        self.frames: list[QImage] = []
        self.source: Path | None = None
        self.frame_index = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._advance_frame)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        card = QWidget(self)
        card.setObjectName("previewCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 12, 12, 12)

        self.video_label = QLabel(card)
        self.video_label.setObjectName("previewFrame")
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setMinimumSize(1, 1)
        self.status_label = QLabel("正在加载预览…")
        self.status_label.setObjectName("previewStatus")
        self.status_label.setWordWrap(True)
        card_layout.addWidget(self.video_label, 1)
        card_layout.addWidget(self.status_label)
        layout.addWidget(card, 1)

        controls = QHBoxLayout()
        controls.addStretch(1)
        self.play_button = QPushButton("暂停")
        self.play_button.setObjectName("primary")
        self.replay_button = QPushButton("重新播放")
        self.close_button = QPushButton("关闭")
        self.play_button.clicked.connect(self._toggle_playback)
        self.replay_button.clicked.connect(self._replay)
        self.close_button.clicked.connect(self.close)
        controls.addWidget(self.play_button)
        controls.addWidget(self.replay_button)
        controls.addWidget(self.close_button)
        layout.addLayout(controls)

    def set_source(self, path: str) -> bool:
        self._release_source()
        source = Path(path)
        if not source.is_file():
            return self._show_load_error("预览文件不存在，请重新生成。")

        try:
            with av.open(str(source)) as container:
                stream = container.streams.video[0]
                rate = stream.average_rate or stream.guessed_rate
                fps = float(rate) if rate else DEFAULT_PREVIEW_FPS
                if fps <= 0:
                    fps = DEFAULT_PREVIEW_FPS
                decoded: list[QImage] = []
                for frame in container.decode(stream):
                    if len(decoded) >= MAX_PREVIEW_FRAMES:
                        raise ValueError(
                            f"预览超过 {MAX_PREVIEW_FRAMES} 帧，请缩短预览后重试。"
                        )
                    array = frame.to_ndarray(format="rgb24")
                    height, width, _channels = array.shape
                    image = QImage(
                        array.data,
                        width,
                        height,
                        int(array.strides[0]),
                        QImage.Format.Format_RGB888,
                    ).copy()
                    if image.isNull():
                        raise ValueError("视频帧转换失败。")
                    decoded.append(image)
        except Exception as exc:
            detail = str(exc).strip()
            return self._show_load_error(
                f"预览加载失败：{detail}" if detail else "预览加载失败，请重新生成后再试。"
            )

        if not decoded:
            return self._show_load_error("预览中没有可播放的视频帧。")

        self.frames = decoded
        self.source = source.resolve()
        self.frame_index = 0
        self.timer.setInterval(max(1, round(1000 / fps)))
        self._show_current_frame()
        self.status_label.setText("预览已生成，正在播放…")
        self.play_button.setText("暂停")
        self.play_button.setEnabled(True)
        self.replay_button.setEnabled(True)
        self.timer.start()
        return True

    def _show_load_error(self, message: str) -> bool:
        self.video_label.clear()
        self.video_label.setText("预览暂不可用")
        self.status_label.setText(message)
        self.play_button.setText("播放")
        self.play_button.setEnabled(False)
        self.replay_button.setEnabled(False)
        return False

    def _show_current_frame(self):
        if not self.frames:
            return
        image = self.frames[self.frame_index]
        pixmap = QPixmap.fromImage(image).scaled(
            self.video_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.video_label.setPixmap(pixmap)

    def _advance_frame(self):
        if not self.frames:
            return
        self.frame_index = (self.frame_index + 1) % len(self.frames)
        self._show_current_frame()

    def _toggle_playback(self):
        if not self.frames:
            return
        if self.timer.isActive():
            self.timer.stop()
            self.play_button.setText("播放")
            self.status_label.setText("预览已暂停")
        else:
            self.timer.start()
            self.play_button.setText("暂停")
            self.status_label.setText("预览已生成，正在播放…")

    def _replay(self):
        if not self.frames:
            return
        self.frame_index = 0
        self._show_current_frame()
        self.timer.start()
        self.play_button.setText("暂停")
        self.status_label.setText("预览已生成，正在播放…")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._show_current_frame()

    def _release_source(self):
        self.timer.stop()
        self.frames.clear()
        self.source = None
        self.frame_index = 0

    def closeEvent(self, event):
        self._release_source()
        self.video_label.clear()
        super().closeEvent(event)
