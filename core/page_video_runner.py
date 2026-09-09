"""Turn a naturally sorted page-image sequence into a composited H.264 video."""

from __future__ import annotations

import math
import os
from fractions import Fraction
from pathlib import Path
from typing import Iterable, Sequence

from PIL import Image, ImageDraw, UnidentifiedImageError
from PyQt6.QtCore import QThread, pyqtSignal

from core.file_policy import is_valid_input_file, natural_sort_key, scan_input_files
from core.image_processor import embed_image_pil, embed_image_pil_fast, precompute_template_cache
from core.output_paths import allocate_unique_directory, allocate_unique_file
from core.realism_filter import apply_realism, precompute_realism


def classify_media_paths(paths: Sequence[str]) -> str:
    """Return ``video`` or ``image``; reject empty, unsupported, or mixed input."""
    kinds = set()
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_dir() or is_valid_input_file(path, "image"):
            kinds.add("image")
        elif is_valid_input_file(path, "video"):
            kinds.add("video")
        # Hidden files, Office temporaries, and unsupported extensions are
        # ignored by the shared policy instead of entering sorting/counting.
    if not kinds:
        raise ValueError("未选择视频或页面图片")
    if len(kinds) != 1:
        raise ValueError("不能在同一任务中混合视频和页面图片")
    return kinds.pop()


def normalize_page_paths(paths: Iterable[str]) -> list[str]:
    """Apply the shared file policy, expand folders, and naturally sort pages."""
    pages: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_dir():
            pages.extend(scan_input_files(path, "image"))
        elif is_valid_input_file(path, "image"):
            pages.append(path)
    unique = {os.path.abspath(os.fspath(path)): path for path in pages}
    return [str(unique[key]) for key in sorted(unique, key=natural_sort_key)]


def frame_counts(page_count: int, hold_seconds: float, turn_seconds: float, fps: int) -> tuple[int, int, int]:
    hold_frames = max(1, round(hold_seconds * fps))
    turn_frames = max(1, round(turn_seconds * fps)) if page_count > 1 else 0
    total = page_count * hold_frames + max(0, page_count - 1) * turn_frames
    return hold_frames, turn_frames, total


def even_size(size: tuple[int, int]) -> tuple[int, int]:
    """Return a positive yuv420p-compatible size without enlarging the source."""
    width, height = size
    return max(2, width - width % 2), max(2, height - height % 2)


def fit_page(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Contain a page on a black-free white canvas while preserving its aspect ratio."""
    image = image.convert("RGB")
    target_w, target_h = size
    scale = min(target_w / image.width, target_h / image.height)
    resized = image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.Resampling.BILINEAR,
    )
    canvas = Image.new("RGB", size, "white")
    canvas.paste(resized, ((target_w - resized.width) // 2, (target_h - resized.height) // 2))
    return canvas


def render_page_turn(current: Image.Image, following: Image.Image, progress: float) -> Image.Image:
    """Render a right-to-left planar page turn with a moving-edge/spine shadow."""
    progress = min(1.0, max(0.0, float(progress)))
    current = current.convert("RGB")
    following = fit_page(following, current.size)
    if progress <= 0.0:
        return current.copy()
    if progress >= 1.0:
        return following.copy()

    width, height = current.size
    moving_x = max(1, round(width * (1.0 - progress)))
    inset = min(height // 5, round(height * 0.11 * math.sin(progress * math.pi)))
    points = [[0, 0], [moving_x, inset], [moving_x, height - inset], [0, height]]
    frame = embed_image_pil(current, following, points, feather=0).convert("RGB")

    # A soft-looking stepped shadow follows the moving edge; a lighter spine
    # shadow keeps the page grounded as the visible sheet narrows.
    strength = math.sin(progress * math.pi)
    overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    band = max(2, round(width * 0.08))
    for offset in range(band):
        alpha = round(105 * strength * (1.0 - offset / band) ** 2)
        x = min(width - 1, moving_x + offset)
        draw.line((x, inset, x, height - inset), fill=(0, 0, 0, alpha))
    spine_band = max(1, round(width * 0.025))
    for x in range(spine_band):
        alpha = round(35 * strength * (1.0 - x / spine_band))
        draw.line((x, 0, x, height), fill=(0, 0, 0, alpha))
    return Image.alpha_composite(frame.convert("RGBA"), overlay).convert("RGB")


def iter_page_frames(
    pages: Sequence[Image.Image], hold_seconds: float, turn_seconds: float, fps: int
):
    """Yield frames one at a time; the encoded sequence is never materialized."""
    if not pages:
        return
    size = pages[0].size
    normalized = [fit_page(page, size) for page in pages]
    hold_frames, turn_frames, _ = frame_counts(len(normalized), hold_seconds, turn_seconds, fps)
    for index, page in enumerate(normalized):
        for _ in range(hold_frames):
            yield page
        if index + 1 < len(normalized):
            for turn_index in range(turn_frames):
                progress = (turn_index + 1) / turn_frames
                yield render_page_turn(page, normalized[index + 1], progress)


class ImageSequenceVideoRunner(QThread):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(bool, str)

    def __init__(
        self,
        tasks,
        output_dir: str,
        *,
        hold_seconds: float = 2.0,
        turn_seconds: float = 0.7,
        fps: int = 25,
        realism_enabled: bool = True,
        realism_strength: int = 70,
        parent=None,
    ):
        """Tasks are ``(source_name, page_paths, screen_templates)`` tuples."""
        super().__init__(parent)
        os.makedirs(output_dir, exist_ok=True)
        self.tasks = [
            (source_name, normalize_page_paths(page_paths), templates)
            for source_name, page_paths, templates in tasks
        ]
        self.output_dir = output_dir
        self.output_dirs = [
            str(allocate_unique_directory(output_dir, source_name))
            for source_name, _page_paths, _templates in self.tasks
        ]
        self.hold_seconds = float(hold_seconds)
        self.turn_seconds = float(turn_seconds)
        self.fps = int(fps)
        self.realism_enabled = realism_enabled
        self.realism_strength = realism_strength
        self._abort = False
        self.output_paths: list[str] = []

    def abort(self):
        self._abort = True

    @staticmethod
    def _load_pages(paths: Sequence[str]) -> tuple[list[Image.Image], list[str]]:
        pages = []
        skipped = []
        for path in paths:
            try:
                with Image.open(path) as image:
                    image.load()
                    pages.append(image.convert("RGB"))
            except (UnidentifiedImageError, OSError, ValueError):
                skipped.append(path)
        return pages, skipped

    def run(self):
        try:
            import av

            prepared = []
            total = 0
            skipped_all: list[str] = []
            for (source_name, page_paths, templates), output_dir in zip(self.tasks, self.output_dirs):
                pages, skipped = self._load_pages(page_paths)
                skipped_all.extend(skipped)
                if not pages:
                    raise ValueError(f"「{source_name}」过滤或跳过坏图后没有有效页面图片")
                _hold, _turn, count = frame_counts(
                    len(pages), self.hold_seconds, self.turn_seconds, self.fps
                )
                total += count * len(templates)
                prepared.append((source_name, pages, templates, output_dir, count))

            done = 0
            time_base = Fraction(1, self.fps)
            for source_name, pages, templates, source_output_dir, _count in prepared:
                for template in templates:
                    if self._abort:
                        self.finished.emit(False, "已取消；已生成的半成品保留在输出目录")
                        return
                    with Image.open(template.background_path) as source_bg:
                        source_bg = source_bg.convert("RGB")
                        target_size = even_size(source_bg.size)
                        bg_img = source_bg.resize(target_size, Image.Resampling.BILINEAR)
                    scale_x = target_size[0] / source_bg.width
                    scale_y = target_size[1] / source_bg.height
                    points = [[x * scale_x, y * scale_y] for x, y in template.screen_points]
                    cache = precompute_template_cache(bg_img, points, ppt_size=pages[0].size)
                    realism_cache = precompute_realism(
                        bg_img,
                        points,
                        strength=self.realism_strength if self.realism_enabled else 0,
                    )

                    template_dir = Path(source_output_dir) / template.name
                    template_dir.mkdir(parents=True, exist_ok=True)
                    filename = f"{Path(source_name).stem}.mp4"
                    output_path = allocate_unique_file(template_dir, filename)
                    self.output_paths.append(str(output_path))

                    with av.open(str(output_path), "w", format="mp4") as container:
                        stream = container.add_stream("libx264", rate=self.fps)
                        stream.width, stream.height = target_size
                        stream.pix_fmt = "yuv420p"
                        stream.time_base = time_base
                        stream.codec_context.time_base = time_base
                        stream.options = {"crf": "18", "preset": "veryfast"}
                        for frame_index, page_frame in enumerate(
                            iter_page_frames(pages, self.hold_seconds, self.turn_seconds, self.fps)
                        ):
                            if self._abort:
                                break
                            result = embed_image_pil_fast(page_frame, cache)
                            result = apply_realism(result, realism_cache, frame_index=frame_index)
                            frame = av.VideoFrame.from_image(result)
                            frame.pts = frame_index
                            frame.time_base = time_base
                            for packet in stream.encode(frame):
                                container.mux(packet)
                            done += 1
                            self.progress.emit(done, total, f"{source_name} → {template.name}")
                        if not self._abort:
                            for packet in stream.encode():
                                container.mux(packet)
                    if self._abort:
                        self.finished.emit(False, "已取消；已生成的半成品保留在输出目录")
                        return

            skipped_text = f"；跳过 {len(skipped_all)} 张坏图" if skipped_all else ""
            paths_text = "\n".join(self.output_paths)
            self.finished.emit(True, f"页面翻页视频完成{skipped_text}。实际输出：\n{paths_text}")
        except Exception as exc:
            self.finished.emit(False, f"页面翻页视频失败：{exc}；半成品已保留")
