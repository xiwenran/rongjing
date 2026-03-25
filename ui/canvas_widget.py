from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, pyqtSignal, QPointF, QRectF
from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QColor, QPixmap, QImage, QFont,
)
from PIL import Image


POINT_LABELS = ["TL", "TR", "BR", "BL"]
POINT_COLORS = [
    QColor(255, 80,  80),
    QColor(80,  220, 80),
    QColor(80,  120, 255),
    QColor(255, 220, 50),
]
POINT_RADIUS = 9


def _pil_to_pixmap(img: Image.Image) -> QPixmap:
    rgba = img.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    qimg = QImage(data, rgba.width, rgba.height, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qimg)


class CanvasWidget(QWidget):
    points_changed = pyqtSignal(list)   # list of [x, y] in bg-image coords

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 300)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self._bg_pil: Image.Image = None
        self._preview_pil: Image.Image = None
        self.points: list = []          # up to 4, [x, y] in bg image coords
        self._drag_idx: int = -1
        self._display_pixmap: QPixmap = None

    # ── Public API ────────────────────────────────────────────────────────────

    def set_background(self, path: str):
        self._bg_pil = Image.open(path).convert("RGBA")
        self.points = []
        self._rebuild()
        self.points_changed.emit([])

    def set_preview(self, path: str):
        self._preview_pil = Image.open(path).convert("RGBA") if path else None
        self._rebuild()

    def clear_preview(self):
        self._preview_pil = None
        self._rebuild()

    def set_points(self, points: list):
        self.points = [list(p) for p in points]
        self._rebuild()
        self.points_changed.emit(self.points)

    def clear_points(self):
        self.points = []
        self._rebuild()
        self.points_changed.emit([])

    def clear_all(self):
        """Reset canvas to empty state (new template)."""
        self._bg_pil = None
        self._preview_pil = None
        self.points = []
        self._display_pixmap = None
        self.update()
        self.points_changed.emit([])

    # ── Coordinate helpers ────────────────────────────────────────────────────

    def _transform(self):
        """Return (scale, ox, oy) mapping bg-image → canvas coords."""
        if not self._bg_pil:
            return 1.0, 0.0, 0.0
        bw, bh = self._bg_pil.size
        cw, ch = self.width(), self.height()
        scale = min(cw / bw, ch / bh)
        ox = (cw - bw * scale) / 2
        oy = (ch - bh * scale) / 2
        return scale, ox, oy

    def _to_canvas(self, x, y):
        s, ox, oy = self._transform()
        return x * s + ox, y * s + oy

    def _to_image(self, x, y):
        s, ox, oy = self._transform()
        return (x - ox) / s, (y - oy) / s

    # ── Display rebuild ───────────────────────────────────────────────────────

    def _rebuild(self):
        if not self._bg_pil:
            self._display_pixmap = None
            self.update()
            return

        cw = max(self.width(), 1)
        ch = max(self.height(), 1)
        bw, bh = self._bg_pil.size
        scale = min(cw / bw, ch / bh)
        dw = max(int(bw * scale), 1)
        dh = max(int(bh * scale), 1)

        display_bg = self._bg_pil.resize((dw, dh), Image.LANCZOS)

        if self._preview_pil and len(self.points) == 4:
            try:
                from core.image_processor import embed_image_pil
                display_pts = [[p[0] * scale, p[1] * scale] for p in self.points]
                composite = embed_image_pil(self._preview_pil, display_bg, display_pts)
            except Exception:
                composite = display_bg
        else:
            composite = display_bg

        self._display_pixmap = _pil_to_pixmap(composite)
        self.update()

    def resizeEvent(self, event):
        self._rebuild()
        super().resizeEvent(event)

    # ── Painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(40, 40, 40))

        if not self._bg_pil:
            painter.setPen(QColor(150, 150, 150))
            painter.setFont(QFont("Arial", 14))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "← 点击左侧按钮加载背景图片")
            return

        _, ox, oy = self._transform()
        if self._display_pixmap:
            painter.drawPixmap(int(ox), int(oy), self._display_pixmap)

        # Quadrilateral outline
        n = len(self.points)
        if n >= 2:
            painter.setPen(QPen(QColor(255, 220, 0), 2, Qt.PenStyle.SolidLine))
            for i in range(n - 1):
                p1 = QPointF(*self._to_canvas(*self.points[i]))
                p2 = QPointF(*self._to_canvas(*self.points[i + 1]))
                painter.drawLine(p1, p2)
            if n == 4:
                painter.drawLine(
                    QPointF(*self._to_canvas(*self.points[3])),
                    QPointF(*self._to_canvas(*self.points[0])),
                )

        # Point handles
        for i, pt in enumerate(self.points):
            sx, sy = self._to_canvas(*pt)
            color = POINT_COLORS[i] if i < 4 else QColor(200, 200, 200)
            painter.setPen(QPen(Qt.GlobalColor.white, 1.5))
            painter.setBrush(QBrush(color))
            painter.drawEllipse(QPointF(sx, sy), POINT_RADIUS, POINT_RADIUS)
            label = POINT_LABELS[i] if i < 4 else str(i + 1)
            painter.setPen(Qt.GlobalColor.black)
            painter.setFont(QFont("Arial", 7, int(QFont.Weight.Bold)))
            painter.drawText(
                QRectF(sx - 12, sy + POINT_RADIUS + 2, 24, 14),
                Qt.AlignmentFlag.AlignCenter, label,
            )

        # Instruction text above the image
        if n < 4:
            next_lbl = POINT_LABELS[n] if n < 4 else ""
            painter.setPen(QColor(255, 220, 0))
            painter.setFont(QFont("Arial", 10))
            msg = f"左键点击放置角点 {next_lbl}（还需 {4 - n} 个）  右键撤销"
            if self._display_pixmap:
                painter.drawText(
                    QRectF(ox, oy - 22, self._display_pixmap.width(), 20),
                    Qt.AlignmentFlag.AlignCenter, msg,
                )

    # ── Mouse events ──────────────────────────────────────────────────────────

    def _nearest_point(self, x, y, thresh=15):
        for i, pt in enumerate(self.points):
            sx, sy = self._to_canvas(*pt)
            if (sx - x) ** 2 + (sy - y) ** 2 <= thresh ** 2:
                return i
        return -1

    def mousePressEvent(self, event):
        if not self._bg_pil:
            return
        x, y = event.position().x(), event.position().y()
        if event.button() == Qt.MouseButton.LeftButton:
            idx = self._nearest_point(x, y)
            if idx >= 0:
                self._drag_idx = idx
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            elif len(self.points) < 4:
                ix, iy = self._to_image(x, y)
                bw, bh = self._bg_pil.size
                if 0 <= ix <= bw and 0 <= iy <= bh:
                    self.points.append([ix, iy])
                    self._rebuild()
                    self.points_changed.emit(self.points)
        elif event.button() == Qt.MouseButton.RightButton:
            if self.points:
                self.points.pop()
                self._rebuild()
                self.points_changed.emit(self.points)

    def mouseMoveEvent(self, event):
        x, y = event.position().x(), event.position().y()
        if self._drag_idx >= 0:
            ix, iy = self._to_image(x, y)
            if self._bg_pil:
                bw, bh = self._bg_pil.size
                ix = max(0.0, min(float(bw), ix))
                iy = max(0.0, min(float(bh), iy))
            self.points[self._drag_idx] = [ix, iy]
            self._rebuild()
            self.points_changed.emit(self.points)
        else:
            idx = self._nearest_point(x, y)
            self.setCursor(
                Qt.CursorShape.OpenHandCursor if idx >= 0 else Qt.CursorShape.CrossCursor
            )

    def mouseReleaseEvent(self, event):
        self._drag_idx = -1
        self.setCursor(Qt.CursorShape.CrossCursor)
