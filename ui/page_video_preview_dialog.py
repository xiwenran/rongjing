"""应用内页面翻页视频预览弹窗；QtMultimedia 缺失时安全降级。"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PyQt6.QtMultimediaWidgets import QVideoWidget
except ImportError as exc:  # pragma: no cover - 只在裁剪版 PyQt6 环境触发
    QAudioOutput = QMediaPlayer = QVideoWidget = None
    MULTIMEDIA_AVAILABLE = False
    MULTIMEDIA_UNAVAILABLE_REASON = str(exc)
else:
    MULTIMEDIA_AVAILABLE = True
    MULTIMEDIA_UNAVAILABLE_REASON = ""


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
            QLabel#previewStatus { color: #888888; padding: 2px 4px; }
            QPushButton { min-height: 36px; padding: 0 18px; border-radius: 18px; background: #F0F0F0; color: #191919; }
            QPushButton#primary { background: #07C160; color: white; font-weight: 600; }
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        card = QWidget(self)
        card.setObjectName("previewCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 12, 12, 12)

        self.status_label = QLabel("正在加载预览…")
        self.status_label.setObjectName("previewStatus")
        self.status_label.setWordWrap(True)

        self.player = None
        self.audio_output = None
        if MULTIMEDIA_AVAILABLE:
            self.video_widget = QVideoWidget(card)
            self.video_widget.setStyleSheet("background: #191919; border-radius: 10px;")
            self.audio_output = QAudioOutput(self)
            self.player = QMediaPlayer(self)
            self.player.setAudioOutput(self.audio_output)
            self.player.setVideoOutput(self.video_widget)
            self.player.mediaStatusChanged.connect(self._on_media_status_changed)
            self.player.errorOccurred.connect(self._on_player_error)
            self.player.playbackStateChanged.connect(self._sync_play_button)
            card_layout.addWidget(self.video_widget, 1)
        else:
            self.video_widget = QLabel("当前 PyQt6 不含 QtMultimedia，无法在应用内播放预览。", card)
            self.video_widget.setWordWrap(True)
            self.video_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            card_layout.addWidget(self.video_widget, 1)

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

        if not MULTIMEDIA_AVAILABLE:
            self.play_button.setEnabled(False)
            self.replay_button.setEnabled(False)
            self.status_label.setText(
                "应用内播放器不可用。请安装包含 QtMultimedia 的 PyQt6 后重试。"
            )

    def set_source(self, path: str) -> bool:
        if not self.player:
            return False
        source = Path(path)
        if not source.is_file():
            self.status_label.setText("预览文件不存在，请重新生成。")
            return False
        self.player.setSource(QUrl.fromLocalFile(str(source.resolve())))
        self.status_label.setText("预览已生成，正在播放…")
        self.player.play()
        return True

    def _toggle_playback(self):
        if not self.player:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _replay(self):
        if self.player:
            self.player.setPosition(0)
            self.player.play()

    def _on_media_status_changed(self, status):
        if not self.player:
            return
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.player.setPosition(0)
            self.player.play()
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self.status_label.setText("预览加载失败，请重新生成后再试。")

    def _on_player_error(self, _error, error_text=""):
        detail = str(error_text).strip()
        self.status_label.setText(
            f"预览播放失败：{detail}" if detail else "预览播放失败，请重新生成后再试。"
        )

    def _sync_play_button(self, state):
        if not self.player:
            return
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setText("暂停" if playing else "播放")

    def closeEvent(self, event):
        if self.player:
            self.player.stop()
            self.player.setSource(QUrl())
            self.player.setVideoOutput(None)
        super().closeEvent(event)
