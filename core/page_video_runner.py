"""Turn a naturally sorted page-image sequence into a composited H.264 video."""

from __future__ import annotations

import math
import os
import secrets
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
from core.core_image_page_curl import (
    RIGHT_TO_LEFT,
    PageCurlRenderError,
    PageCurlUnavailable,
    availability,
    normalize_direction,
    render_batch,
)
from core.image_processor import embed_image_pil, embed_image_pil_fast, precompute_template_cache
from core.batch_runner import scaled_size_for_width, scale_points_for_size
from core.music_library import MusicLibrary, MusicLibraryError
from core.output_paths import (
    allocate_unique_directory,
    allocate_unique_file,
    move_file_noreplace,
    move_unique_file,
)
from core.realism_filter import apply_realism, precompute_realism


MAX_CURVE_FRAMES = 8
PROGRESS_INTERVAL_SECONDS = 0.15
AUDIO_SAMPLE_RATE = 48_000


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


def group_page_image_sources(paths: Iterable[str]) -> list[tuple[str, list[str]]]:
    """Turn selected images/folders into independent page-sequence sources.

    A selected folder containing image-bearing child folders creates one source
    per child folder. Otherwise the selected folder itself is one source.
    """
    groups: list[tuple[str, list[str]]] = []
    loose_images: list[str] = []
    for raw_path in paths:
        path = Path(raw_path)
        if is_valid_input_file(path, "image"):
            loose_images.append(str(path))
            continue
        if not path.is_dir():
            continue

        child_groups: list[tuple[str, list[str]]] = []
        try:
            child_dirs = sorted(
                (
                    child
                    for child in path.iterdir()
                    if not child.name.startswith(".") and child.is_dir()
                ),
                key=lambda child: natural_sort_key(child.name),
            )
        except OSError:
            child_dirs = []
        for child in child_dirs:
            pages = normalize_page_paths([str(child)])
            if pages:
                child_groups.append((child.name, pages))
        if child_groups:
            groups.extend(child_groups)
            continue

        pages = normalize_page_paths([str(path)])
        if pages:
            groups.append((path.name or "页面图片", pages))

    if loose_images:
        pages = normalize_page_paths(loose_images)
        if pages:
            parent_name = Path(pages[0]).parent.name or "页面图片"
            groups.append((parent_name, pages))
    return groups


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
    if image.size == size:
        return image.copy()
    target_w, target_h = size
    scale = min(target_w / image.width, target_h / image.height)
    resized = image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    if resized.size == size:
        return resized
    canvas = Image.new("RGB", size, "white")
    canvas.paste(resized, ((target_w - resized.width) // 2, (target_h - resized.height) // 2))
    return canvas


def render_page_turn(
    current: Image.Image,
    following: Image.Image,
    progress: float,
    direction: str = RIGHT_TO_LEFT,
) -> Image.Image:
    """Render a planar page turn; left-to-right is a true horizontal mirror."""
    direction = normalize_direction(direction)
    if direction != RIGHT_TO_LEFT:
        rendered = render_page_turn(
            current.transpose(Image.Transpose.FLIP_LEFT_RIGHT),
            following.transpose(Image.Transpose.FLIP_LEFT_RIGHT),
            progress,
            RIGHT_TO_LEFT,
        )
        return rendered.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
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
        bit_rate = min(max(int(width * height * fps * 0.07), 8_000_000), 20_000_000)
        return "h264_videotoolbox", {}, {"bit_rate": bit_rate}
    return "libx264", {"crf": "17", "preset": "veryfast"}, {}


def allocate_page_curl_work_dir(root: str | os.PathLike[str] | None = None) -> Path:
    """Allocate a unique cache item covered by the existing cache-cleanup UI."""
    cache_root = Path(root).expanduser() if root is not None else Path(
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
    pages: Sequence[Image.Image], hold_seconds: float, turn_seconds: float, fps: int,
    direction: str = RIGHT_TO_LEFT,
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
                yield render_page_turn(page, normalized[index + 1], progress, direction)


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
        output_width: int = 0,
        direction: str = RIGHT_TO_LEFT,
        page_curl_work_root: str | os.PathLike[str] | None = None,
        music_library: MusicLibrary | None = None,
        music_mode: str = "none",
        music_selected_ids: Sequence[str] = (),
        music_volume: int = 35,
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
        self.output_width = int(output_width)
        self.direction = normalize_direction(direction)
        self.page_curl_work_root = page_curl_work_root
        self.music_library = music_library
        self.music_mode = str(music_mode)
        self.music_selected_ids = list(music_selected_ids)
        self.music_volume = min(100, max(0, int(music_volume)))
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
        self.actual_music: list[dict] = []
        self._music_pool: list[dict] = []
        self._music_random = secrets.SystemRandom()
        self._last_progress_at = 0.0

    def abort(self):
        self._abort = True

    def _emit_progress(self, done: int, total: int, message: str, *, force: bool = False) -> None:
        now = time.monotonic()
        if force or done >= total or now - self._last_progress_at >= PROGRESS_INTERVAL_SECONDS:
            self.progress.emit(done, total, message)
            self._last_progress_at = now

    def _resolve_music_pool(self) -> list[dict]:
        if self.music_mode == "none":
            return []
        if self.music_mode not in {"fixed", "random"}:
            raise ValueError(f"不支持的配乐模式：{self.music_mode}")
        if self.music_library is None:
            raise MusicLibraryError("已选择配乐，但音乐库不可用")
        selected_ids = list(dict.fromkeys(
            track_id for track_id in self.music_selected_ids
            if isinstance(track_id, str) and track_id
        ))
        if self.music_mode == "fixed" and len(selected_ids) != 1:
            raise MusicLibraryError("固定配乐必须选择 1 个有效音乐库 ID")
        if self.music_mode == "random" and not selected_ids:
            raise MusicLibraryError("随机配乐池为空")
        tracks = self.music_library.resolve_tracks(selected_ids)
        if self.music_mode == "fixed" and len(tracks) != 1:
            raise MusicLibraryError("固定配乐必须解析到 1 首有效音乐")
        if self.music_mode == "random" and not tracks:
            raise MusicLibraryError("随机配乐池没有有效音乐")
        return tracks

    def _choose_music_track(self) -> dict | None:
        if not self._music_pool:
            return None
        if self.music_mode == "fixed":
            return self._music_pool[0]
        return self._music_random.choice(self._music_pool)

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
            work_dir = allocate_page_curl_work_dir(self.page_curl_work_root)
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
                        direction=self.direction,
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
            [
                render_page_turn(
                    pages[index], pages[index + 1], progress, self.direction
                )
                for progress in progresses
            ]
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
            base_size = (
                (template.output_width, template.output_height)
                if template.output_width > 0 else source_size
            )
            scaled_size = scaled_size_for_width(base_size, self.output_width) or base_size
            target_size = even_size(scaled_size)
            bg_img = opened_bg.convert("RGB")
            if bg_img.size != target_size:
                bg_img = bg_img.resize(target_size, Image.Resampling.LANCZOS)
        points = scale_points_for_size(template.screen_points, source_size, target_size)
        cache = precompute_template_cache(bg_img, points, ppt_size=pages[0].size)
        realism_cache = precompute_realism(
            bg_img,
            points,
            strength=self.realism_strength if self.realism_enabled else 0,
        )

        # Static pages deliberately keep one fixed realism-noise sample. Reusing the
        # final composited frame avoids repeating perspective/filter work during holds.
        self._emit_progress(
            done,
            total,
            f"{source_name} → {template.name}（正在准备模板画面）",
            force=True,
        )
        static_frames = [
            apply_realism(embed_image_pil_fast(page, cache), realism_cache, frame_index=index)
            for index, page in enumerate(pages)
        ]

        if self.output_path:
            output_path = Path(self.output_path)
            if output_path.exists():
                raise FileExistsError(f"预览文件已存在：{output_path.name}")
        else:
            template_dir = Path(source_output_dir) / template.name
            template_dir.mkdir(parents=True, exist_ok=True)
            output_path = template_dir / f"{Path(source_name).stem}.mp4"
        time_base = Fraction(1, self.fps)
        mapping = transition_frame_indices(turn_frames, len(transitions[0])) if transitions else []

        def frame_plan():
            for page_index, static_frame in enumerate(static_frames):
                for _ in range(hold_frames):
                    yield static_frame
                if page_index < len(transitions):
                    # Keep only one composited transition pair in memory. The old
                    # all-pairs cache could exceed 1 GB at 1080p and stall between
                    # templates while macOS swapped memory.
                    self._emit_progress(
                        done + page_index * (hold_frames + turn_frames) + hold_frames,
                        total,
                        f"{source_name} → {template.name}（正在准备第 {page_index + 1} 页翻页）",
                        force=True,
                    )
                    pair = transitions[page_index]
                    cached_pair = []
                    for sample_index, page in enumerate(pair):
                        if sample_index == len(pair) - 1:
                            cached_pair.append(static_frames[page_index + 1])
                        else:
                            embedded = embed_image_pil_fast(page, cache)
                            cached_pair.append(
                                apply_realism(
                                    embedded,
                                    realism_cache,
                                    frame_index=(
                                        len(pages)
                                        + page_index * MAX_CURVE_FRAMES
                                        + sample_index
                                    ),
                                )
                            )
                    for index in mapping:
                        yield cached_pair[index]

        music_track = self._choose_music_track()

        selected = select_encoder(*target_size, self.fps)
        attempts = [selected]
        if selected[0] == "h264_videotoolbox":
            attempts.append(("libx264", {"crf": "17", "preset": "veryfast"}, {}))

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
                    music_track=music_track,
                )
            except Exception as exc:
                last_error = exc
                if codec_name == "h264_videotoolbox":
                    continue
                raise RuntimeError(f"libx264 回退编码失败：{exc}") from exc

            if self._abort:
                self.output_paths.append(str(attempt_path))
                return done + encoded_count, codec_name, str(attempt_path)
            if self.output_path:
                published_path = move_file_noreplace(attempt_path, output_path)
            else:
                published_path = move_unique_file(
                    attempt_path, output_path.parent, output_path.name
                )
            self.output_paths.append(str(published_path))
            if music_track is not None:
                self.actual_music.append({
                    "output_path": str(published_path),
                    "track_id": music_track["id"],
                    "display_name": music_track.get("display_name") or "未命名音乐",
                })
            return done + encoded_count, codec_name, str(published_path)

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
        music_track: dict | None = None,
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
            audio_stream = None
            if music_track is not None:
                audio_stream = container.add_stream("aac", rate=AUDIO_SAMPLE_RATE)
                audio_stream.layout = "stereo"

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
                self._emit_progress(
                    done + encoded_count,
                    total,
                    f"{progress_message}（正在完成画面编码）",
                    force=True,
                )
                for packet in stream.encode():
                    container.mux(packet)
                if music_track is not None:
                    self._emit_progress(
                        done + encoded_count,
                        total,
                        f"{progress_message}（正在封装配乐）",
                        force=True,
                    )
                    self._encode_bgm(
                        container,
                        audio_stream,
                        music_track,
                        target_samples=round(encoded_count * AUDIO_SAMPLE_RATE / self.fps),
                    )
                self._emit_progress(
                    done + encoded_count,
                    total,
                    f"{progress_message}（正在写入视频文件）",
                    force=True,
                )
        return encoded_count

    def _iter_bgm_frames(self, track: dict, target_samples: int):
        """Decode from the source beginning and loop until the exact sample target."""
        import av

        remaining = int(target_samples)
        audio_pts = 0
        gain = self.music_volume / 100.0
        time_base = Fraction(1, AUDIO_SAMPLE_RATE)
        while remaining > 0:
            produced_this_cycle = 0
            with av.open(track["path"], mode="r") as source:
                streams = list(source.streams.audio)
                if not streams:
                    raise MusicLibraryError(f"配乐无法解码：{track.get('display_name') or track['id']}")
                resampler = av.AudioResampler(
                    format="fltp", layout="stereo", rate=AUDIO_SAMPLE_RATE
                )
                decoded_frames = source.decode(streams[0])
                for decoded in decoded_frames:
                    for converted in _resampled_audio_frames(resampler.resample(decoded)):
                        samples = converted.to_ndarray()
                        if not samples.shape[1]:
                            continue
                        take = min(remaining, samples.shape[1])
                        samples = (samples[:, :take] * gain).copy()
                        output = av.AudioFrame.from_ndarray(
                            samples, format="fltp", layout="stereo"
                        )
                        output.sample_rate = AUDIO_SAMPLE_RATE
                        output.pts = audio_pts
                        output.time_base = time_base
                        audio_pts += take
                        remaining -= take
                        produced_this_cycle += take
                        yield output
                        if remaining == 0:
                            return
                for converted in _resampled_audio_frames(resampler.resample(None)):
                    samples = converted.to_ndarray()
                    if not samples.shape[1]:
                        continue
                    take = min(remaining, samples.shape[1])
                    samples = (samples[:, :take] * gain).copy()
                    output = av.AudioFrame.from_ndarray(
                        samples, format="fltp", layout="stereo"
                    )
                    output.sample_rate = AUDIO_SAMPLE_RATE
                    output.pts = audio_pts
                    output.time_base = time_base
                    audio_pts += take
                    remaining -= take
                    produced_this_cycle += take
                    yield output
                    if remaining == 0:
                        return
            if produced_this_cycle == 0:
                raise MusicLibraryError(
                    f"配乐没有可用音频：{track.get('display_name') or track['id']}"
                )

    def _encode_bgm(self, container, audio_stream, track: dict, *, target_samples: int) -> None:
        for frame in self._iter_bgm_frames(track, target_samples):
            for packet in audio_stream.encode(frame):
                container.mux(packet)
        for packet in audio_stream.encode(None):
            container.mux(packet)

    def run(self):
        try:
            import av

            self._music_pool = self._resolve_music_pool()

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
            result_details = f"backend={backend_text}；encoder={encoder_text}"
            if self.actual_music:
                result_details += "；配乐=" + "；".join(
                    f"{item['track_id']}（{item['display_name']}）"
                    for item in self.actual_music
                )
            self.finished.emit(
                True,
                f"页面翻页视频完成{skipped_text}。{result_details}。"
                f"实际输出：\n{paths_text}",
            )
        except Exception as exc:
            self.finished.emit(False, f"页面翻页视频失败：{exc}；半成品已保留")


def _resampled_audio_frames(value):
    if value is None:
        return ()
    if isinstance(value, list):
        return value
    return (value,)
