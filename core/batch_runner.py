import os
import random
import re
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from fractions import Fraction
from typing import List, Optional, Tuple

from PyQt6.QtCore import QThread, pyqtSignal

from PIL import Image, UnidentifiedImageError

from models.template_model import Template
from core.image_processor import (
    embed_document_paper_pil,
    embed_image_pil_fast,
    precompute_template_cache,
)
from core.realism_filter import apply_realism, precompute_realism

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tiff"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".wmv"}


def scaled_size_for_width(source_size: Tuple[int, int], target_width: int) -> Optional[Tuple[int, int]]:
    if target_width <= 0:
        return None
    source_w, source_h = source_size
    if source_w <= 0 or source_h <= 0:
        return None
    target_height = max(1, round(target_width * source_h / source_w))
    return target_width, target_height


def scale_points_for_size(
    points: List[List[float]],
    source_size: Tuple[int, int],
    target_size: Tuple[int, int],
) -> List[List[float]]:
    source_w, source_h = source_size
    target_w, target_h = target_size
    if source_w <= 0 or source_h <= 0:
        return points
    scale_x = target_w / source_w
    scale_y = target_h / source_h
    return [[point[0] * scale_x, point[1] * scale_y] for point in points]


def natural_sort_key(s: str):
    """按数字块/非数字块拆分字符串，数字块转整数比较，实现自然排序。
    例：['1','2','10','11'] 而非字典序 ['1','10','11','2']"""
    return [int(c) if c.isdigit() else c.lower()
            for c in re.split(r'(\d+)', s)]


def get_image_files(folder: str):
    files = []
    for fn in sorted(os.listdir(folder), key=natural_sort_key):
        if fn.startswith("."):
            continue
        if os.path.splitext(fn)[1].lower() in IMAGE_EXTS:
            files.append(os.path.join(folder, fn))
    return files


class BatchRunner(QThread):
    progress = pyqtSignal(int, int, str)   # done, total, status_msg
    finished = pyqtSignal(bool, str)       # success, message

    def __init__(
        self,
        tasks,               # List of (group_name: str, file_list: List[str], templates: List[Template])
        output_dir: str,
        output_format: str = "JPEG",   # "PNG" or "JPEG"
        output_width: int = 0,
        diversify_config=None,
        realism_enabled: bool = True,
        realism_strength: int = 70,
        parent=None,
    ):
        super().__init__(parent)
        self.tasks = tasks
        self.output_dir = output_dir
        self.output_format = output_format
        self.output_width = output_width
        self.diversify_config = diversify_config
        self.realism_enabled = realism_enabled
        self.realism_strength = realism_strength
        self._diversify_run_seed = random.SystemRandom().getrandbits(64)
        self._abort = False

    def abort(self):
        self._abort = True

    def run(self):
        try:
            # Match VideoRunner's worker cap: use CPU cores, but keep I/O and UI responsive.
            num_workers = max(1, min(6, (os.cpu_count() or 2) - 1))
            os.makedirs(self.output_dir, exist_ok=True)

            total = sum(len(files) * len(templates) for _, files, templates in self.tasks)
            done = 0
            skipped = 0

            for group_name, files, templates in self.tasks:
                output_name_counts = Counter(t.name for t in templates)
                for template in templates:
                    if self._abort:
                        self.finished.emit(False, "已取消"); return

                    template_out_name = template.name
                    if output_name_counts[template.name] > 1:
                        category = getattr(template, "category", "模板") or "模板"
                        template_out_name = f"{category}-{template.name}"
                    out_sub = os.path.join(self.output_dir, group_name, template_out_name)
                    os.makedirs(out_sub, exist_ok=True)

                    # 0 = use template/background size. Otherwise keep aspect ratio at target width.
                    template_output_size = (
                        (template.output_width, template.output_height)
                        if template.output_width > 0 else None
                    )

                    # Precompute mask + bg array once per template (shared across all files)
                    ppt_size = self._first_readable_image_size(files)
                    if ppt_size is None:
                        skipped += len(files)
                        done += len(files)
                        self.progress.emit(done, total, f"{group_name}/{template_out_name} 没有可识别的图片")
                        continue
                    with Image.open(template.background_path) as bg_img:
                        bg_size = bg_img.size
                        render_size = template_output_size
                        if self.output_width > 0:
                            render_size = scaled_size_for_width(
                                render_size or bg_size,
                                self.output_width,
                            )
                        if render_size:
                            render_bg = bg_img.convert("RGB").resize(render_size, Image.LANCZOS)
                            render_points = scale_points_for_size(
                                template.screen_points,
                                bg_size,
                                render_size,
                            )
                        else:
                            render_bg = bg_img
                            render_points = template.screen_points
                        render_bg = render_bg.convert("RGB")
                        render_type = getattr(template, "template_type", "screen") or "screen"
                        if render_type != "document_paper":
                            render_type = "screen"
                        cache = None
                        if render_type == "screen":
                            cache = precompute_template_cache(
                                render_bg, render_points, ppt_size=ppt_size
                            )
                        else:
                            render_bg = render_bg.copy()

                        realism_strength = self.realism_strength if self.realism_enabled else 0
                        realism_cache = precompute_realism(
                            render_bg, render_points, strength=realism_strength
                        )

                    def _process_one_image(i: int, img_path: str):
                        if self._abort:
                            raise RuntimeError("已取消")

                        ext = ".jpg" if self.output_format == "JPEG" else ".png"
                        out_path = os.path.join(out_sub, f"{i}{ext}")

                        try:
                            with Image.open(img_path) as ppt_img:
                                if render_type == "document_paper":
                                    result = embed_document_paper_pil(
                                        ppt_img,
                                        render_bg.copy(),
                                        render_points,
                                        render_preset=getattr(template, "render_preset", "clear"),
                                    )
                                else:
                                    result = embed_image_pil_fast(ppt_img, cache)
                        except (UnidentifiedImageError, OSError) as exc:
                            return i, ext, False, f"跳过无法识别的图片 {os.path.basename(img_path)}：{exc}"

                        result = apply_realism(result, realism_cache)

                        if self._abort:
                            raise RuntimeError("已取消")

                        seed = None
                        if self.diversify_config is not None and getattr(self.diversify_config, "enabled", False):
                            from core.diversifier import diversify_image

                            seed = hash((self._diversify_run_seed, template.name, group_name, i))
                            result = diversify_image(result, self.diversify_config, seed=seed)

                        if self._abort:
                            raise RuntimeError("已取消")

                        if self.output_format == "JPEG":
                            quality = 95
                            if seed is not None:
                                from core.diversifier import randomize_jpeg_quality

                                quality = randomize_jpeg_quality(
                                    95,
                                    self.diversify_config.jpeg_quality_range,
                                    random.Random(seed),
                                )
                            result.convert("RGB").save(out_path, "JPEG", quality=quality)
                        else:
                            result.save(out_path, "PNG")

                        return i, ext, True, ""

                    futures = []
                    with ThreadPoolExecutor(max_workers=num_workers) as pool:
                        for i, img_path in enumerate(files, 1):
                            if self._abort:
                                self.finished.emit(False, "已取消"); return
                            futures.append(pool.submit(_process_one_image, i, img_path))

                        for fut in as_completed(futures):
                            if self._abort:
                                for pending in futures:
                                    pending.cancel()
                                self.finished.emit(False, "已取消"); return
                            try:
                                i, ext, ok, skip_msg = fut.result()
                            except Exception as exc:
                                if self._abort or str(exc) == "已取消":
                                    for pending in futures:
                                        pending.cancel()
                                    self.finished.emit(False, "已取消"); return
                                raise

                            done += 1
                            if ok:
                                self.progress.emit(done, total, f"{group_name}/{template_out_name}/{i}{ext}")
                            else:
                                skipped += 1
                                self.progress.emit(done, total, skip_msg)

            if skipped:
                self.finished.emit(True, f"完成！成功 {done - skipped} 张，跳过 {skipped} 张无法识别的图片")
            else:
                self.finished.emit(True, f"完成！共处理 {done} 张图片")

        except Exception as e:
            import traceback
            self.finished.emit(False, f"错误: {str(e)}\n{traceback.format_exc()}")

    def _first_readable_image_size(self, files: List[str]) -> Optional[Tuple[int, int]]:
        for path in files:
            try:
                with Image.open(path) as img:
                    return img.size
            except (UnidentifiedImageError, OSError):
                continue
        return None


class VideoRunner(QThread):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(bool, str)

    def __init__(self, tasks, output_dir, realism_enabled: bool = True, realism_strength: int = 70, parent=None):
        """
        tasks: List of (video_path: str, templates: List[Template])
        Each video frame is treated as PPT content; template's background is the scene.
        Audio is preserved via PyAV (no external ffmpeg needed).
        """
        super().__init__(parent)
        self.tasks = tasks
        self.output_dir = output_dir
        self.realism_enabled = realism_enabled
        self.realism_strength = realism_strength
        self._abort = False
        self._user_abort = False

    def abort(self):
        self._user_abort = True
        self._abort = True

    def run(self):
        import av
        import queue
        import threading
        import traceback

        # Use N-1 CPU cores for frame processing; leave one for decode/encode I/O.
        # PIL's C code and numpy release the GIL, so threads give real parallelism.
        num_workers = max(1, min(6, (os.cpu_count() or 2) - 1))

        try:
            def _probe_videotoolbox() -> bool:
                """真实打开一次 h264_videotoolbox 编码器再判断是否可用。

                关键：av.codec.Codec(name) 只查 FFmpeg 是否注册了这个编码器
                名字，不会调 avcodec_open2。打包成 .app 后 FFmpeg 常常注册了
                名字但实际 open 失败（VideoToolbox 框架链接/硬件会话问题）。
                必须真正 .open() 一次才能确认编码器能用。
                """
                try:
                    cc = av.codec.CodecContext.create("h264_videotoolbox", "w")
                    cc.width = 64
                    cc.height = 64
                    cc.pix_fmt = "yuv420p"
                    cc.open()  # 真正触发 avcodec_open2
                    # CodecContext 无公开 close()，出作用域自动回收
                    return True
                except Exception:
                    return False

            videotoolbox_runtime_ok = _probe_videotoolbox()

            def _detect_encoder(
                bg_w: int, bg_h: int, fps_value: float, use_videotoolbox: bool
            ):
                if use_videotoolbox:
                    bit_rate = int(bg_w * bg_h * fps_value * 0.07)
                    bit_rate = min(bit_rate, 20_000_000)
                    return "h264_videotoolbox", {}, {"bit_rate": bit_rate}
                return "libx264", {"crf": "18", "preset": "veryfast"}, {}

            def _is_videotoolbox_open_failure(exc: Exception) -> bool:
                text = str(exc).lower()
                return "videotoolbox" in text or "avcodec_open2" in text

            def _remove_partial_output(out_path: str):
                try:
                    if os.path.exists(out_path):
                        os.remove(out_path)
                except OSError:
                    pass

            os.makedirs(self.output_dir, exist_ok=True)

            # Pre-scan frame counts
            meta = []
            total = 0
            for video_path, templates in self.tasks:
                with av.open(video_path) as c:
                    vs = c.streams.video[0]
                    n = vs.frames if vs.frames else 0
                    fps = float(vs.average_rate or 25)
                    ppt_size = (vs.width, vs.height) if vs.width and vs.height else None
                meta.append((n, fps, ppt_size))
                total += max(n, 1) * len(templates)

            done = 0
            for (video_path, templates), (n_frames, fps, ppt_size) in zip(self.tasks, meta):
                if self._abort:
                    self.finished.emit(False, "已取消"); return

                vid_name = os.path.splitext(os.path.basename(video_path))[0]

                for template in templates:
                    if self._abort:
                        self.finished.emit(False, "已取消"); return

                    out_dir = os.path.join(self.output_dir, vid_name, template.name)
                    os.makedirs(out_dir, exist_ok=True)
                    out_path = os.path.join(out_dir, f"{vid_name}.mp4")

                    def _process_one(use_videotoolbox: bool):
                        nonlocal done

                        # Precompute mask + bg (RGB) + perspective coefficients once per template.
                        # Passing ppt_size pre-populates _coeffs so the cache is read-only
                        # during parallel use — no locking required.
                        bg_img = Image.open(template.background_path).convert("RGB")
                        cache = precompute_template_cache(
                            bg_img, template.screen_points, ppt_size=ppt_size
                        )
                        bg_w, bg_h = cache["bg_size"]

                        realism_strength = self.realism_strength if self.realism_enabled else 0
                        realism_cache = precompute_realism(
                            bg_img, template.screen_points, strength=realism_strength
                        )

                        def _embed_and_realism(pil, cache, frame_i, realism_cache=realism_cache):
                            embedded = embed_image_pil_fast(pil, cache)
                            return apply_realism(embedded, realism_cache, frame_index=frame_i)

                        with av.open(video_path) as inp, \
                             av.open(out_path, "w", format="mp4") as outp, \
                             ThreadPoolExecutor(max_workers=num_workers) as pool:

                            in_vs = inp.streams.video[0]

                            # Output video stream (H.264)
                            codec_name, codec_options, codec_attrs = _detect_encoder(
                                bg_w, bg_h, fps, use_videotoolbox
                            )
                            out_vs = outp.add_stream(codec_name)
                            out_vs.width = bg_w
                            out_vs.height = bg_h
                            out_vs.pix_fmt = "yuv420p"
                            video_time_base = in_vs.time_base or Fraction(1, 90000)
                            out_vs.time_base = video_time_base
                            out_vs.codec_context.time_base = video_time_base
                            if codec_options:
                                out_vs.options = codec_options
                            if "bit_rate" in codec_attrs:
                                out_vs.codec_context.bit_rate = codec_attrs["bit_rate"]

                            # Output audio streams (AAC re-encode)
                            out_as_list = []
                            resamplers = []
                            for in_as in inp.streams.audio:
                                sr = in_as.codec_context.sample_rate or 44100
                                layout = in_as.codec_context.layout or "stereo"
                                out_as = outp.add_stream("aac", rate=sr)
                                resampler = av.AudioResampler(
                                    format="fltp", layout=layout, rate=sr
                                )
                                out_as_list.append(out_as)
                                resamplers.append(resampler)

                            streams = [in_vs] + list(inp.streams.audio)
                            in_audio_list = list(inp.streams.audio)
                            audio_pts = [0] * len(out_as_list)
                            audio_pts_initialized = [False] * len(out_as_list)
                            audio_sample_rates = [
                                stream.codec_context.sample_rate or 44100
                                for stream in in_audio_list
                            ]
                            stream_video_start_seconds = (
                                float(in_vs.start_time * in_vs.time_base)
                                if in_vs.start_time is not None and in_vs.time_base is not None
                                else None
                            )
                            stream_audio_start_seconds = [
                                float(stream.start_time * stream.time_base)
                                if stream.start_time is not None and stream.time_base is not None
                                else None
                                for stream in in_audio_list
                            ]
                            nominal_frame_interval = 1.0 / fps if fps > 0 else 1.0 / 25.0
                            first_video_pts_seconds = None
                            last_video_pts_seconds = None
                            thread_errors = []
                            error_lock = threading.Lock()

                            decode_q = queue.Queue(maxsize=num_workers * 2)
                            audio_q = queue.Queue(maxsize=64)
                            encode_q = queue.Queue(maxsize=num_workers)
                            pending_audio_before_video = [[] for _ in out_as_list]

                            def _record_thread_error(exc: Exception):
                                with error_lock:
                                    thread_errors.append(
                                        f"{str(exc)}\n{traceback.format_exc()}"
                                    )
                                self._abort = True

                            def _put_with_abort(q, item):
                                while not self._abort:
                                    try:
                                        q.put(item, timeout=0.1)
                                        return True
                                    except queue.Full:
                                        continue
                                return False

                            def _send_sentinel(q, consumer=None):
                                # If the consumer thread has died (e.g. crashed before
                                # setting _abort), free a slot so we never block forever.
                                while True:
                                    try:
                                        q.put(None, timeout=0.1)
                                        return
                                    except queue.Full:
                                        if self._abort or (
                                            consumer is not None and not consumer.is_alive()
                                        ):
                                            try:
                                                q.get_nowait()
                                            except queue.Empty:
                                                pass

                            def _pts_to_seconds(pts, time_base):
                                if pts is None or time_base is None:
                                    return None
                                return float(pts * time_base)

                            def _seconds_to_pts(seconds: float) -> int:
                                pts = int(round(seconds / float(video_time_base)))
                                return pts

                            def _resolve_video_pts_seconds(frame) -> float:
                                nonlocal first_video_pts_seconds, last_video_pts_seconds
                                pts_seconds = _pts_to_seconds(frame.pts, frame.time_base)
                                if pts_seconds is None:
                                    if last_video_pts_seconds is not None:
                                        pts_seconds = last_video_pts_seconds + nominal_frame_interval
                                    elif stream_video_start_seconds is not None:
                                        pts_seconds = stream_video_start_seconds
                                    else:
                                        pts_seconds = 0.0
                                if last_video_pts_seconds is not None and pts_seconds < last_video_pts_seconds:
                                    pts_seconds = last_video_pts_seconds
                                if first_video_pts_seconds is None:
                                    first_video_pts_seconds = pts_seconds
                                last_video_pts_seconds = pts_seconds
                                return pts_seconds

                            def _ensure_audio_pts_initialized(idx: int, origin_seconds):
                                if audio_pts_initialized[idx]:
                                    return
                                if origin_seconds is None:
                                    origin_seconds = stream_audio_start_seconds[idx]
                                if origin_seconds is None:
                                    origin_seconds = first_video_pts_seconds or 0.0
                                base_seconds = first_video_pts_seconds or 0.0
                                audio_pts[idx] = int(round((origin_seconds - base_seconds) * audio_sample_rates[idx]))
                                audio_pts_initialized[idx] = True

                            def _queue_audio_frames(idx: int, origin_seconds, resampled_frames):
                                _ensure_audio_pts_initialized(idx, origin_seconds)
                                for resampled in resampled_frames:
                                    if self._abort:
                                        break
                                    resampled.pts = audio_pts[idx]
                                    audio_pts[idx] += resampled.samples
                                    if not _put_with_abort(audio_q, ("audio", idx, resampled)):
                                        break

                            def _flush_pending_audio():
                                if first_video_pts_seconds is None:
                                    return
                                for idx, buffered_items in enumerate(pending_audio_before_video):
                                    while buffered_items and not self._abort:
                                        origin_seconds, resampled_frames = buffered_items.pop(0)
                                        _queue_audio_frames(idx, origin_seconds, resampled_frames)

                            def _decoder_worker():
                                frame_i = 0
                                try:
                                    for packet in inp.demux(*streams):
                                        if self._abort:
                                            break
                                        # NOTE: don't skip dts=None packets — the final
                                        # flush packet (dts=None) is what drains the
                                        # decoder's reorder buffer (last B-frames).

                                        if packet.stream == in_vs:
                                            for frame in packet.decode():
                                                if self._abort:
                                                    break
                                                pts_seconds = _resolve_video_pts_seconds(frame)
                                                _flush_pending_audio()
                                                pil = frame.to_image().convert("RGB")
                                                if not _put_with_abort(decode_q, (frame_i, pts_seconds, pil)):
                                                    break
                                                frame_i += 1

                                        elif packet.stream in in_audio_list:
                                            idx = in_audio_list.index(packet.stream)
                                            if idx < len(out_as_list):
                                                for frame in packet.decode():
                                                    if self._abort:
                                                        break
                                                    origin_seconds = _pts_to_seconds(
                                                        frame.pts, frame.time_base
                                                    )
                                                    resampled_frames = list(
                                                        resamplers[idx].resample(frame)
                                                    )
                                                    if first_video_pts_seconds is None:
                                                        pending_audio_before_video[idx].append(
                                                            (origin_seconds, resampled_frames)
                                                        )
                                                        continue
                                                    _queue_audio_frames(
                                                        idx, origin_seconds, resampled_frames
                                                    )
                                    _flush_pending_audio()
                                except Exception as exc:
                                    _record_thread_error(exc)
                                finally:
                                    _flush_pending_audio()
                                    _send_sentinel(decode_q)
                                    _send_sentinel(audio_q, encoder)

                            def _encode_audio_item(item):
                                _, idx, resampled = item
                                for p in out_as_list[idx].encode(resampled):
                                    outp.mux(p)

                            def _encoder_worker():
                                nonlocal done
                                video_done = False
                                audio_done = False
                                last_video_out_pts = None
                                try:
                                    while not (video_done and audio_done):
                                        while not audio_done:
                                            try:
                                                audio_item = audio_q.get_nowait()
                                            except queue.Empty:
                                                break
                                            if audio_item is None:
                                                audio_done = True
                                                break
                                            _encode_audio_item(audio_item)

                                        if self._abort:
                                            video_done = True
                                            audio_done = True
                                            break

                                        if video_done:
                                            if not audio_done:
                                                try:
                                                    audio_item = audio_q.get(timeout=0.1)
                                                except queue.Empty:
                                                    continue
                                                if audio_item is None:
                                                    audio_done = True
                                                else:
                                                    _encode_audio_item(audio_item)
                                            continue

                                        try:
                                            video_item = encode_q.get(timeout=0.1)
                                        except queue.Empty:
                                            continue

                                        if video_item is None:
                                            video_done = True
                                            continue

                                        fi, pts_seconds, rgb_result = video_item
                                        out_frame = av.VideoFrame.from_image(rgb_result)
                                        relative_seconds = max(
                                            0.0, pts_seconds - (first_video_pts_seconds or 0.0)
                                        )
                                        out_pts = _seconds_to_pts(relative_seconds)
                                        if last_video_out_pts is not None and out_pts <= last_video_out_pts:
                                            out_pts = last_video_out_pts + 1
                                        out_frame.pts = out_pts
                                        out_frame.time_base = video_time_base
                                        last_video_out_pts = out_pts
                                        for p in out_vs.encode(out_frame):
                                            outp.mux(p)

                                        done += 1
                                        if fi % 30 == 0 or fi == 1:
                                            self.progress.emit(
                                                done, total,
                                                f"{vid_name}/{template.name}  {fi}/{n_frames} 帧"
                                            )

                                    if not self._abort:
                                        for p in out_vs.encode(None):
                                            outp.mux(p)

                                        for i, (out_as, resampler) in enumerate(
                                            zip(out_as_list, resamplers)
                                        ):
                                            for resampled in resampler.resample(None):
                                                resampled.pts = audio_pts[i]
                                                audio_pts[i] += resampled.samples
                                                for p in out_as.encode(resampled):
                                                    outp.mux(p)
                                            for p in out_as.encode():
                                                outp.mux(p)
                                except Exception as exc:
                                    _record_thread_error(exc)

                            # Sliding window of in-flight futures: deque of (frame_i, Future).
                            # We keep at most num_workers*2 frames in flight so memory stays
                            # bounded, then drain from the front (in order) to enqueue.
                            pending: deque = deque()
                            window = num_workers * 2

                            def _drain(max_pending: int):
                                """Enqueue completed futures from the front, keeping ≤ max_pending."""
                                while (
                                    not self._abort
                                    and not thread_errors
                                    and len(pending) > max_pending
                                ):
                                    fi, pts_seconds, fut = pending.popleft()
                                    rgb_result = fut.result()   # blocks until this frame is ready
                                    if not _put_with_abort(encode_q, (fi, pts_seconds, rgb_result)):
                                        break

                            decoder = threading.Thread(
                                target=_decoder_worker,
                                name="VideoRunnerDecode",
                                daemon=True,
                            )
                            encoder = threading.Thread(
                                target=_encoder_worker,
                                name="VideoRunnerEncode",
                                daemon=True,
                            )
                            decoder.start()
                            encoder.start()

                            try:
                                try:
                                    decode_done = False
                                    while not self._abort and not thread_errors and not decode_done:
                                        try:
                                            item = decode_q.get(timeout=0.1)
                                        except queue.Empty:
                                            _drain(window)
                                            continue

                                        if item is None:
                                            decode_done = True
                                            break

                                        fi, pts_seconds, pil = item
                                        fut = pool.submit(_embed_and_realism, pil, cache, fi)
                                        pending.append((fi, pts_seconds, fut))
                                        _drain(window)

                                    _drain(0)
                                except Exception:
                                    self._abort = True
                                    raise
                            finally:
                                _send_sentinel(encode_q, encoder)
                                decoder.join()
                                encoder.join()

                            if thread_errors:
                                raise RuntimeError(thread_errors[0])

                            if self._abort:
                                raise RuntimeError("已取消")

                        self.progress.emit(done, total, f"✓ {vid_name}/{template.name}.mp4")

                    done_before = done
                    use_videotoolbox = videotoolbox_runtime_ok
                    while True:
                        try:
                            _process_one(use_videotoolbox)
                            break
                        except Exception as exc:
                            if self._user_abort or (self._abort and "已取消" in str(exc)):
                                self.finished.emit(False, "已取消"); return
                            if use_videotoolbox and _is_videotoolbox_open_failure(exc):
                                videotoolbox_runtime_ok = False
                                done = done_before
                                self._abort = False
                                _remove_partial_output(out_path)
                                use_videotoolbox = False
                                continue
                            raise

            self.finished.emit(True, f"完成！共处理 {done} 帧")

        except Exception as e:
            import traceback
            self.finished.emit(False, f"错误: {str(e)}\n{traceback.format_exc()}")
