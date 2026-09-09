"""Turn a naturally sorted page-image sequence into a composited H.264 video."""

from __future__ import annotations

import math
import os
import sys
import tempfile
import time
import uuid
from fractions import Fraction
from pathlib import Path
from typing import Iterable, Sequence

from PIL import Image, ImageDraw, UnidentifiedImageError
from PyQt6.QtCore import QThread, pyqtSignal

from core.file_policy import is_valid_input_file, natural_sort_key, scan_input_files
from core.core_image_page_curl import PageCurlRenderError, PageCurlUnavailable, availability, render_batch
from core.image_processor import embed_image_pil, embed_image_pil_fast, precompute_template_cache
from core.output_paths import allocate_unique_directory, allocate_unique_file
from core.realism_filter import apply_realism, precompute_realism


MAX_CURVE_FRAMES = 8
PROGRESS_INTERVAL_SECONDS = 0.15


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
    following = (
        following.convert("RGB")
        if following.size == current.size
        else fit_page(following, current.size)
    )
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


def transition_progresses(turn_frames: int, max_curve_frames: int = MAX_CURVE_FRAMES) -> list[float]:
    """Return the independently rendered transition times, capped for performance."""
    count = min(max(1, int(turn_frames)), max(1, int(max_curve_frames)))
    return [(index + 1) / count for index in range(count)]


def transition_frame_indices(turn_frames: int, rendered_count: int) -> list[int]:
    """Map the full output timeline onto a bounded set of rendered curve frames."""
    if turn_frames <= 0 or rendered_count <= 0:
        return []
    return [
        min(rendered_count - 1, math.ceil((index + 1) * rendered_count / turn_frames) - 1)
        for index in range(turn_frames)
    ]


def _probe_videotoolbox(width: int, height: int) -> bool:
    """Open the real encoder at the target size; registration alone is insufficient."""
    if sys.platform != "darwin":
        return False
    try:
        import av

        context = av.codec.CodecContext.create("h264_videotoolbox", "w")
        context.width = int(width)
        context.height = int(height)
        context.pix_fmt = "yuv420p"
        context.open()
        return True
    except Exception:
        return False


def select_encoder(width: int, height: int, fps: int) -> tuple[str, dict, dict]:
    """Use VideoToolbox when it really opens, otherwise explicitly select libx264."""
    if _probe_videotoolbox(width, height):
        bit_rate = min(int(width * height * fps * 0.07), 20_000_000)
        return "h264_videotoolbox", {}, {"bit_rate": bit_rate}
    return "libx264", {"crf": "18", "preset": "veryfast"}, {}


def allocate_page_curl_work_dir() -> Path:
    """Allocate a unique cache item covered by the existing cache-cleanup UI."""
    cache_root = Path(
        os.environ.get("RONGJING_PAGE_CURL_CACHE_DIR", "~/.rongjing/ai_cache")
    ).expanduser()
    work_dir = cache_root / f"page_curl-{uuid.uuid4().hex}"
    try:
        work_dir.mkdir(parents=True, exist_ok=False)
        return work_dir
    except OSError:
        # Sandboxed/test environments may deny the app cache root. The OS temp
        # root still gives this run a collision-free workspace without touching
        # user-selected output files.
        return Path(tempfile.mkdtemp(prefix="rongjing-page-curl-"))


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
        output_path: str | None = None,
        max_output_width: int | None = None,
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
        self.output_path = output_path
        self.max_output_width = int(max_output_width) if max_output_width else None
        if self.output_path:
            if len(self.tasks) != 1 or len(self.tasks[0][2]) != 1:
                raise ValueError("明确输出文件只支持一个页面来源和一个模板")
            explicit_parent = str(Path(self.output_path).parent)
            os.makedirs(explicit_parent, exist_ok=True)
            self.output_dirs = [explicit_parent]
        else:
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
        self.work_dirs: list[str] = []
        self.actual_backends: list[str] = []
        self.actual_encoders: list[str] = []
        self._last_progress_at = 0.0

    def abort(self):
        self._abort = True

    def _emit_progress(self, done: int, total: int, message: str, *, force: bool = False) -> None:
        now = time.monotonic()
        if force or done >= total or now - self._last_progress_at >= PROGRESS_INTERVAL_SECONDS:
            self.progress.emit(done, total, message)
            self._last_progress_at = now

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

    def _prepare_transitions(
        self,
        pages: Sequence[Image.Image],
        turn_frames: int,
        source_name: str,
    ) -> tuple[list[list[Image.Image]], str, str | None]:
        """Render every adjacent pair once; never invoke Core Image for static holds."""
        if len(pages) < 2 or turn_frames <= 0:
            return [], "无转场（单页）", None

        progresses = transition_progresses(turn_frames)
        ci_available, unavailable_reason = availability()
        if ci_available:
            work_dir = allocate_page_curl_work_dir()
            self.work_dirs.append(str(work_dir))
            page_paths = []
            for index, page in enumerate(pages):
                page_path = work_dir / f"page_{index:04d}.png"
                page.save(page_path, "PNG")
                page_paths.append(page_path)
            rendered_pairs: list[list[Image.Image]] = []
            filter_names: set[str] = set()
            try:
                for pair_index in range(len(pages) - 1):
                    if self._abort:
                        return [], "Core Image（取消）", None
                    pair_dir = work_dir / f"pair_{pair_index:04d}"
                    result = render_batch(
                        page_paths[pair_index],
                        page_paths[pair_index + 1],
                        pair_dir,
                        progresses,
                        pages[0].width,
                        pages[0].height,
                        manifest_path=pair_dir / "manifest.json",
                    )
                    filter_names.add(result["filter"])
                    pair_frames = []
                    for frame_info in result["frames"]:
                        with Image.open(frame_info["path"]) as frame:
                            frame.load()
                            pair_frames.append(frame.convert("RGB"))
                    rendered_pairs.append(pair_frames)
                filter_name = ", ".join(sorted(filter_names))
                return rendered_pairs, f"Core Image（{filter_name}）", None
            except (PageCurlUnavailable, PageCurlRenderError, OSError, ValueError) as exc:
                unavailable_reason = str(exc)

        cpu_pairs = [
            [render_page_turn(pages[index], pages[index + 1], progress) for progress in progresses]
            for index in range(len(pages) - 1)
        ]
        return cpu_pairs, "CPU 平面翻页", unavailable_reason

    def _encode_template_video(
        self,
        *,
        source_name: str,
        template,
        source_output_dir: str,
        pages: Sequence[Image.Image],
        transitions: Sequence[Sequence[Image.Image]],
        hold_frames: int,
        turn_frames: int,
        total: int,
        done: int,
    ) -> tuple[int, str, str]:
        with Image.open(template.background_path) as opened_bg:
            opened_bg.load()
            source_size = opened_bg.size
            if self.max_output_width and source_size[0] > self.max_output_width:
                scale = self.max_output_width / source_size[0]
                scaled_size = (self.max_output_width, max(2, round(source_size[1] * scale)))
            else:
                scaled_size = source_size
            target_size = even_size(scaled_size)
            bg_img = opened_bg.convert("RGB").resize(target_size, Image.Resampling.BILINEAR)
        scale_x = target_size[0] / source_size[0]
        scale_y = target_size[1] / source_size[1]
        points = [[x * scale_x, y * scale_y] for x, y in template.screen_points]
        cache = precompute_template_cache(bg_img, points, ppt_size=pages[0].size)
        realism_cache = precompute_realism(
            bg_img,
            points,
            strength=self.realism_strength if self.realism_enabled else 0,
        )

        # Static pages deliberately keep one fixed realism-noise sample. Reusing the
        # final composited frame avoids repeating perspective/filter work during holds.
        static_frames = [
            apply_realism(embed_image_pil_fast(page, cache), realism_cache, frame_index=index)
            for index, page in enumerate(pages)
        ]
        transition_frames: list[list[Image.Image]] = []
        for pair_index, pair in enumerate(transitions):
            cached_pair = []
            for sample_index, page in enumerate(pair):
                if sample_index == len(pair) - 1:
                    cached_pair.append(static_frames[pair_index + 1])
                else:
                    embedded = embed_image_pil_fast(page, cache)
                    cached_pair.append(
                        apply_realism(
                            embedded,
                            realism_cache,
                            frame_index=len(pages) + pair_index * MAX_CURVE_FRAMES + sample_index,
                        )
                    )
            transition_frames.append(cached_pair)

        if self.output_path:
            output_path = Path(self.output_path)
            if output_path.exists():
                raise FileExistsError(f"预览文件已存在：{output_path.name}")
        else:
            template_dir = Path(source_output_dir) / template.name
            template_dir.mkdir(parents=True, exist_ok=True)
            output_path = allocate_unique_file(template_dir, f"{Path(source_name).stem}.mp4")
        time_base = Fraction(1, self.fps)
        mapping = transition_frame_indices(turn_frames, len(transitions[0])) if transitions else []

        def frame_plan():
            for page_index, static_frame in enumerate(static_frames):
                for _ in range(hold_frames):
                    yield static_frame
                if page_index < len(transition_frames):
                    for index in mapping:
                        yield transition_frames[page_index][index]

        selected = select_encoder(*target_size, self.fps)
        attempts = [selected]
        if selected[0] == "h264_videotoolbox":
            attempts.append(("libx264", {"crf": "18", "preset": "veryfast"}, {}))

        last_error = None
        for codec_name, codec_options, codec_attrs in attempts:
            attempt_path = allocate_unique_file(
                output_path.parent,
                f"{output_path.stem}.{codec_name}-attempt-{uuid.uuid4().hex[:8]}.mp4",
            )
            try:
                encoded_count = self._encode_video_attempt(
                    attempt_path=attempt_path,
                    codec_name=codec_name,
                    codec_options=codec_options,
                    codec_attrs=codec_attrs,
                    target_size=target_size,
                    time_base=time_base,
                    frames=frame_plan(),
                    done=done,
                    total=total,
                    progress_message=f"{source_name} → {template.name}",
                )
            except Exception as exc:
                last_error = exc
                if codec_name == "h264_videotoolbox":
                    continue
                raise RuntimeError(f"libx264 回退编码失败：{exc}") from exc

            if self._abort:
                self.output_paths.append(str(attempt_path))
                return done + encoded_count, codec_name, str(attempt_path)
            if output_path.exists():
                raise FileExistsError(f"输出文件已存在：{output_path.name}")
            attempt_path.rename(output_path)
            self.output_paths.append(str(output_path))
            return done + encoded_count, codec_name, str(output_path)

        raise RuntimeError(f"VideoToolbox 编码失败：{last_error}")

    def _encode_video_attempt(
        self,
        *,
        attempt_path: Path,
        codec_name: str,
        codec_options: dict,
        codec_attrs: dict,
        target_size: tuple[int, int],
        time_base: Fraction,
        frames: Iterable[Image.Image],
        done: int,
        total: int,
        progress_message: str,
    ) -> int:
        """Encode one replayable frame-plan attempt; callers retain failed artifacts."""
        import av

        encoded_count = 0
        with av.open(str(attempt_path), "w", format="mp4") as container:
            stream = container.add_stream(codec_name, rate=self.fps)
            stream.width, stream.height = target_size
            stream.pix_fmt = "yuv420p"
            stream.time_base = time_base
            stream.codec_context.time_base = time_base
            if codec_options:
                stream.options = codec_options
            if "bit_rate" in codec_attrs:
                stream.codec_context.bit_rate = codec_attrs["bit_rate"]

            for output_frame in frames:
                if self._abort:
                    break
                frame = av.VideoFrame.from_image(output_frame)
                frame.pts = encoded_count
                frame.time_base = time_base
                for packet in stream.encode(frame):
                    container.mux(packet)
                encoded_count += 1
                self._emit_progress(
                    done + encoded_count, total, progress_message
                )
            if not self._abort:
                for packet in stream.encode():
                    container.mux(packet)
        return encoded_count

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
                normalized_pages = [fit_page(page, pages[0].size) for page in pages]
                hold, turn, count = frame_counts(
                    len(pages), self.hold_seconds, self.turn_seconds, self.fps
                )
                total += count * len(templates)
                prepared.append(
                    (source_name, normalized_pages, templates, output_dir, hold, turn, count)
                )

            done = 0
            backend_notes = []
            encoder_names = []
            for source_name, pages, templates, source_output_dir, hold, turn, _count in prepared:
                transitions, backend, fallback_reason = self._prepare_transitions(
                    pages, turn, source_name
                )
                self.actual_backends.append(backend)
                backend_note = f"{source_name}: {backend}"
                if fallback_reason:
                    backend_note += f"（回退原因：{fallback_reason}）"
                backend_notes.append(backend_note)
                for template in templates:
                    if self._abort:
                        self.finished.emit(False, "已取消；已生成的半成品保留在输出目录")
                        return
                    done, encoder, _path = self._encode_template_video(
                        source_name=source_name,
                        template=template,
                        source_output_dir=source_output_dir,
                        pages=pages,
                        transitions=transitions,
                        hold_frames=hold,
                        turn_frames=turn,
                        total=total,
                        done=done,
                    )
                    self.actual_encoders.append(encoder)
                    encoder_names.append(encoder)
                    if self._abort:
                        self.finished.emit(False, "已取消；已生成的半成品保留在输出目录")
                        return

            skipped_text = f"；跳过 {len(skipped_all)} 张坏图" if skipped_all else ""
            paths_text = "\n".join(self.output_paths)
            self._emit_progress(done, total, "页面翻页视频完成", force=True)
            backend_text = "；".join(backend_notes)
            encoder_text = ", ".join(sorted(set(encoder_names))) or "无"
            self.finished.emit(
                True,
                f"页面翻页视频完成{skipped_text}。backend={backend_text}；encoder={encoder_text}。"
                f"实际输出：\n{paths_text}",
            )
        except Exception as exc:
            self.finished.emit(False, f"页面翻页视频失败：{exc}；半成品已保留")
