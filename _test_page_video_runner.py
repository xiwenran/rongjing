"""V2 checks for RJ-CI-P3; artifacts are intentionally retained in qa/logs."""

from __future__ import annotations

import json
import math
import os
import struct
import subprocess
import uuid
import wave
from pathlib import Path
from unittest import mock

import av
import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter
from PyQt6.QtWidgets import QApplication, QLabel, QMessageBox

from core.page_video_runner import (
    MAX_CURVE_FRAMES,
    ImageSequenceVideoRunner,
    classify_media_paths,
    even_size,
    frame_counts,
    iter_page_frames,
    normalize_page_paths,
    render_page_turn,
    select_encoder,
    transition_frame_indices,
    transition_progresses,
)
from core.batch_runner import scaled_size_for_width
from core.core_image_page_curl import LEFT_TO_RIGHT, RIGHT_TO_LEFT, normalize_direction
from core.page_preview_cache import allocate_preview_file, cleanup_expired_preview_cache
from core.music_library import MusicLibrary, MusicLibraryError
from models.template_model import Template


BGM_RUN_ID = os.environ.get("RJ_BGM_M3_RUN_ID")
ROOT = Path(__file__).parent / "qa" / "logs" / (
    f"page-video-bgm-m3-{BGM_RUN_ID}"
    if BGM_RUN_ID else f"coreimage-preview-p3-{uuid.uuid4().hex}"
)
ROOT.mkdir(parents=True)
os.environ["RONGJING_PAGE_CURL_CACHE_DIR"] = str(ROOT / "work-cache")
APP = QApplication.instance() or QApplication([])


def make_pages(
    folder: Path,
    colors=((230, 30, 30), (20, 80, 230), (30, 190, 80)),
    size=(80, 48),
):
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for index, color in enumerate(colors, 1):
        path = folder / f"page{index}.png"
        Image.new("RGB", size, color).save(path)
        paths.append(str(path))
    return paths


def make_template(folder: Path, size=(97, 65)) -> Template:
    folder.mkdir(parents=True, exist_ok=True)
    bg_path = folder / "background.png"
    Image.new("RGB", size, (12, 12, 12)).save(bg_path)
    return Template(
        name="M5测试模板",
        background_path=str(bg_path),
        screen_points=[[0, 0], [size[0], 0], [size[0], size[1]], [0, size[1]]],
    )


def run_runner(runner: ImageSequenceVideoRunner):
    result = {}
    runner.finished.connect(lambda success, message: result.update(success=success, message=message))
    runner.run()
    assert result, "Runner 未发出 finished 信号"
    return result


def make_bgm_wav(path: Path, *, frequency: float) -> None:
    """Write a short tone beginning at sample zero so a longer output must loop."""
    sample_rate = 48_000
    duration = 0.18
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        samples = []
        for index in range(round(sample_rate * duration)):
            value = round(18_000 * math.sin(2 * math.pi * frequency * index / sample_rate))
            samples.append(struct.pack("<h", value))
        output.writeframes(b"".join(samples))


def test_bgm_m3_real_mux_random_none_and_invalid_id():
    import core.page_video_runner as module

    pages = make_pages(ROOT / "pages", colors=((220, 40, 40), (40, 80, 220)), size=(96, 64))
    template = make_template(ROOT / "template", size=(96, 64))
    library = MusicLibrary(ROOT / "music-library")
    first_wav = ROOT / "tone-a.wav"
    second_wav = ROOT / "tone-b.wav"
    make_bgm_wav(first_wav, frequency=440.0)
    make_bgm_wav(second_wav, frequency=880.0)
    first = library.import_audio(first_wav)["track"]
    second = library.import_audio(second_wav)["track"]
    assert library.resolve_tracks([first["id"]])[0]["duration"] < 1.0

    zero_library = MusicLibrary(ROOT / "zero-duration-library")
    zero_track = zero_library.import_audio(first_wav)["track"]
    zero_index = json.loads(zero_library.index_path.read_text(encoding="utf-8"))
    zero_index["tracks"][0]["duration"] = 0
    zero_library.index_path.write_text(
        json.dumps(zero_index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    try:
        zero_library.resolve_tracks([zero_track["id"]])
        raise AssertionError("零时长配乐应失败")
    except MusicLibraryError as exc:
        assert "配乐时长无效" in str(exc)

    output = ROOT / "output"
    output.mkdir()
    runner = ImageSequenceVideoRunner(
        [("两页短样本", pages, [template])],
        str(output),
        hold_seconds=1.1,
        turn_seconds=0.2,
        fps=10,
        realism_enabled=False,
        music_library=library,
        music_mode="random",
        music_selected_ids=[first["id"], second["id"]],
        music_volume=35,
    )
    with (
        mock.patch.object(module, "availability", return_value=(False, "M3 CPU 小样")),
        mock.patch.object(module, "select_encoder", return_value=("libx264", {"crf": "17", "preset": "veryfast"}, {})),
        mock.patch.object(runner._music_random, "choice", side_effect=lambda pool: pool[1]) as choice_mock,
    ):
        result = run_runner(runner)
    assert result["success"], result["message"]
    assert choice_mock.call_count == 1
    assert runner.actual_music == [{
        "output_path": runner.output_paths[0],
        "track_id": second["id"],
        "display_name": second["display_name"],
    }]
    assert second["id"] in result["message"] and second["display_name"] in result["message"]

    bgm_video = Path(runner.output_paths[0])
    with av.open(str(bgm_video)) as container:
        assert len(container.streams.video) == 1
        assert len(container.streams.audio) == 1
        video_stream = container.streams.video[0]
        audio_stream = container.streams.audio[0]
        assert audio_stream.codec_context.name == "aac"
        video_frames = list(container.decode(video_stream))
    with av.open(str(bgm_video)) as container:
        audio_frames = list(container.decode(container.streams.audio[0]))
    assert len(video_frames) == 24
    decoded_samples = sum(frame.samples for frame in audio_frames)
    target_samples = round(len(video_frames) * 48_000 / 10)
    assert abs(decoded_samples - target_samples) <= 1024, (decoded_samples, target_samples)
    decoded = np.concatenate([frame.to_ndarray()[0] for frame in audio_frames]).astype(np.float32)
    audio_peak = float(np.abs(decoded).max())
    assert audio_peak > 0.05, audio_peak
    assert audio_frames[0].pts is not None and audio_peak < 0.5
    early = decoded[: round(0.2 * 48_000)]
    early_rms = float(np.sqrt(np.mean(early * early)))
    assert early_rms > 0.02
    reference = np.sin(2 * np.pi * 880.0 * np.arange(round(0.12 * 48_000)) / 48_000)
    correlations = []
    for lag in range(0, 2049, 64):
        window = decoded[lag:lag + len(reference)]
        if len(window) == len(reference) and np.linalg.norm(window) > 0:
            correlations.append(abs(float(np.dot(window, reference) / (np.linalg.norm(window) * np.linalg.norm(reference)))))
    start_correlation = max(correlations, default=0.0)
    assert correlations and start_correlation > 0.7, start_correlation
    assert decoded_samples > round(second["duration"] * 48_000) * 2

    no_output = ROOT / "no-output"
    no_output.mkdir()
    no_runner = ImageSequenceVideoRunner(
        [("无配乐", pages[:1], [template])],
        str(no_output), hold_seconds=0.2, turn_seconds=0.1, fps=10,
        realism_enabled=False, music_mode="none",
    )
    with (
        mock.patch.object(module, "availability", return_value=(False, "M3 CPU 小样")),
        mock.patch.object(module, "select_encoder", return_value=("libx264", {"crf": "17", "preset": "veryfast"}, {})),
    ):
        no_result = run_runner(no_runner)
    assert no_result["success"], no_result["message"]
    with av.open(no_runner.output_paths[0]) as container:
        assert len(container.streams.audio) == 0

    invalid_runner = ImageSequenceVideoRunner(
        [("失效 ID", pages[:1], [template])],
        str(ROOT / "invalid-output"),
        hold_seconds=0.2, turn_seconds=0.1, fps=10,
        realism_enabled=False,
        music_library=library,
        music_mode="fixed",
        music_selected_ids=["missing-track-id"],
    )
    invalid_result = run_runner(invalid_runner)
    assert not invalid_result["success"] and "配乐 ID 已失效" in invalid_result["message"]
    assert invalid_runner.output_paths == []

    manifest = {
        "task_id": "RJ-BGM-M3",
        "video": str(bgm_video.relative_to(ROOT)),
        "video_frames": len(video_frames),
        "video_seconds": len(video_frames) / 10,
        "audio_codec": "aac",
        "audio_samples": decoded_samples,
        "target_samples": target_samples,
        "audio_peak": audio_peak,
        "first_200ms_rms": early_rms,
        "source_start_correlation": start_correlation,
        "selected_track": runner.actual_music[0],
        "checks": {
            "start_at_zero": "passed_non_silent_correlated_start",
            "short_audio_loop": "passed",
            "random_record": "passed",
            "none_has_no_audio": "passed",
            "invalid_id_fails": "passed",
        },
    }
    (ROOT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def test_policy_and_natural_sort():
    folder = ROOT / "policy"
    folder.mkdir()
    Image.new("RGB", (8, 8), "white").save(folder / "10.jpg")
    Image.new("RGB", (8, 8), "white").save(folder / "2.jpg")
    Image.new("RGB", (8, 8), "white").save(folder / "._0.jpg")
    Image.new("RGB", (8, 8), "white").save(folder / ".hidden.png")
    Image.new("RGB", (8, 8), "white").save(folder / "~$draft.png")
    (folder / "notes.txt").write_text("not an image", encoding="utf-8")
    assert [Path(p).name for p in normalize_page_paths([str(folder)])] == ["2.jpg", "10.jpg"]
    assert classify_media_paths([str(folder)]) == "image"
    video = folder / "clip.mp4"
    video.write_bytes(b"placeholder")
    assert classify_media_paths([str(video)]) == "video"
    try:
        classify_media_paths([str(video), str(folder / "2.jpg")])
    except ValueError as exc:
        assert "混合" in str(exc)
    else:
        raise AssertionError("混合输入未被拒绝")


def test_counts_pts_plan_and_transition_geometry():
    red = Image.new("RGB", (80, 48), (240, 20, 20))
    blue = Image.new("RGB", (80, 48), (20, 20, 240))
    assert frame_counts(1, 1.2, 0.7, 10) == (12, 0, 12)
    assert frame_counts(3, 0.2, 0.3, 10) == (2, 3, 12)
    assert len(list(iter_page_frames([red], 0.3, 0.5, 10))) == 3
    assert len(list(iter_page_frames([red, blue], 0.2, 0.3, 10))) == 7
    assert len(transition_progresses(40)) == MAX_CURVE_FRAMES
    assert transition_progresses(3) == [1 / 3, 2 / 3, 1.0]
    mapped = transition_frame_indices(20, MAX_CURVE_FRAMES)
    assert len(mapped) == 20 and mapped[-1] == MAX_CURVE_FRAMES - 1
    assert len(set(mapped)) == MAX_CURVE_FRAMES

    first = render_page_turn(red, blue, 0.0)
    middle = render_page_turn(red, blue, 0.5)
    last = render_page_turn(red, blue, 1.0)
    assert ImageChops.difference(first, red).getbbox() is None
    assert ImageChops.difference(last, blue).getbbox() is None
    assert ImageChops.difference(middle, first).getbbox() is not None
    assert ImageChops.difference(middle, last).getbbox() is not None
    assert middle.getpixel((10, 24))[0] > middle.getpixel((70, 24))[0]


def test_page_turn_direction_mirror_contract():
    current = Image.new("RGB", (80, 48), (240, 20, 20))
    following = Image.new("RGB", (80, 48), (20, 20, 240))
    rtl = render_page_turn(current, following, 0.5, RIGHT_TO_LEFT)
    ltr = render_page_turn(current, following, 0.5, LEFT_TO_RIGHT)
    mirrored_rtl = render_page_turn(
        current.transpose(Image.Transpose.FLIP_LEFT_RIGHT),
        following.transpose(Image.Transpose.FLIP_LEFT_RIGHT),
        0.5,
        RIGHT_TO_LEFT,
    ).transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    assert ImageChops.difference(ltr, mirrored_rtl).getbbox() is None
    assert rtl.getpixel((10, 24))[0] > rtl.getpixel((70, 24))[0]
    assert ltr.getpixel((70, 24))[0] > ltr.getpixel((10, 24))[0]
    try:
        normalize_direction("sideways")
    except ValueError as exc:
        assert "未知翻页方向" in str(exc)
    else:
        raise AssertionError("未知方向未被拒绝")


def test_preview_cache_cleanup_is_flat_and_fd_bounded():
    folder = ROOT / "preview-cache"
    cache_root = folder / "page_preview_cache"
    old_file = cache_root / "page-turn-preview-old.mp4"
    young_file = cache_root / "page-turn-preview-young.mp4"
    old_other = cache_root / "old-file.tmp"
    old_dir = cache_root / "old-dir"
    outside = folder / "outside.txt"
    old_dir.mkdir(parents=True)
    old_file.write_bytes(b"old")
    young_file.write_bytes(b"young")
    old_other.write_text("old", encoding="utf-8")
    outside.write_text("outside", encoding="utf-8")
    symlink = cache_root / "outside-link.mp4"
    symlink.symlink_to(outside)
    now = 2_000_000_000.0
    old = now - 25 * 60 * 60
    os.utime(old_dir, (old, old))
    os.utime(old_file, (old, old))
    os.utime(old_other, (old, old))
    os.utime(young_file, (now, now))
    stats = cleanup_expired_preview_cache(cache_root, now=now)
    assert stats["removed"] == 1
    assert stats["kept"] == 1
    assert stats["skipped_symlinks"] == 1
    assert stats["skipped"] == 2
    assert not old_file.exists() and young_file.exists()
    assert old_other.exists() and old_dir.is_dir()
    assert outside.exists() and symlink.is_symlink()

    allocated = allocate_preview_file(cache_root)
    assert allocated.parent == cache_root
    assert allocated.name.startswith("page-turn-preview-") and allocated.suffix == ".mp4"
    assert not allocated.exists()

    for invalid_root in ("", Path(""), ".", "relative/page_preview_cache", folder / "not-preview-cache"):
        rejected = cleanup_expired_preview_cache(invalid_root, now=now)
        assert rejected["removed"] == 0 and rejected["errors"]


def test_preview_cache_root_replacement_does_not_redirect_deletion():
    folder = ROOT / "preview-cache-root-swap"
    cache_root = folder / "page_preview_cache"
    held_root = folder / "held-original-root"
    cache_root.mkdir(parents=True)
    original = cache_root / "page-turn-preview-original.mp4"
    original.write_bytes(b"original")
    now = 2_000_000_000.0
    old = now - 25 * 60 * 60
    os.utime(original, (old, old))
    real_scandir = os.scandir

    def swap_root_then_scan(root_fd):
        cache_root.rename(held_root)
        cache_root.mkdir()
        replacement = cache_root / "page-turn-preview-replacement.mp4"
        replacement.write_bytes(b"replacement")
        os.utime(replacement, (old, old))
        return real_scandir(root_fd)

    with mock.patch("core.page_preview_cache._scandir_fd", side_effect=swap_root_then_scan):
        stats = cleanup_expired_preview_cache(cache_root, now=now)
    assert stats["removed"] == 1
    assert not (held_root / original.name).exists()
    assert (cache_root / "page-turn-preview-replacement.mp4").exists()


def test_offscreen_preview_dialog_contract():
    from ui.page_video_preview_dialog import PageVideoPreviewDialog

    video = ROOT / "dialog" / "preview.mp4"
    video.parent.mkdir(parents=True)
    container = av.open(str(video), "w")
    stream = container.add_stream("libx264", rate=15)
    stream.width = 96
    stream.height = 64
    stream.pix_fmt = "yuv420p"
    for color in ((220, 30, 30), (30, 180, 80), (30, 80, 220)):
        image = Image.new("RGB", (96, 64), color)
        for packet in stream.encode(av.VideoFrame.from_image(image)):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()

    dialog = PageVideoPreviewDialog()
    assert dialog.size().width() == 900 and dialog.size().height() == 560
    assert dialog.set_source(str(video))
    assert len(dialog.frames) == 3
    assert dialog.source == video.resolve()
    assert dialog.timer.isActive()
    first_index = dialog.frame_index
    dialog._advance_frame()
    assert dialog.frame_index != first_index
    dialog._toggle_playback()
    assert not dialog.timer.isActive() and dialog.play_button.text() == "播放"
    dialog._replay()
    assert dialog.frame_index == 0 and dialog.timer.isActive()
    assert dialog.replay_button.text() == "重新播放"
    assert dialog.close_button.text() == "关闭"
    dialog.close()
    APP.processEvents()
    assert not dialog.timer.isActive()
    assert dialog.frames == [] and dialog.source is None


def test_even_size_cancel_bad_image_and_non_overwrite():
    assert even_size((97, 65)) == (96, 64)
    folder = ROOT / "boundaries"
    pages = make_pages(folder, colors=((200, 10, 10),))
    corrupt = folder / "2.jpg"
    corrupt.write_bytes(b"broken")
    template = make_template(folder)
    output = folder / "output"
    output.mkdir()

    runners = [
        ImageSequenceVideoRunner(
            [("同名来源", pages + [str(corrupt)], [template])],
            str(output), hold_seconds=0.1, turn_seconds=0.1, fps=12, realism_enabled=False,
        )
        for _ in range(3)
    ]
    allocated_names = [Path(r.output_dirs[0]).name for r in runners]
    assert allocated_names[1:] == [f"{allocated_names[0]}_2", f"{allocated_names[0]}_3"]
    runners[0].abort()
    cancelled = run_runner(runners[0])
    assert not cancelled["success"] and "已取消" in cancelled["message"]

    bad_only = ImageSequenceVideoRunner(
        [("全坏", [str(corrupt)], [template])], str(output), realism_enabled=False
    )
    failed = run_runner(bad_only)
    assert not failed["success"] and "没有有效页面图片" in failed["message"]


def test_real_encoding_and_pts():
    folder = ROOT / "real-sample"
    pages = make_pages(folder, size=(640, 360))
    for index, path in enumerate(pages, 1):
        with Image.open(path) as opened:
            page = opened.convert("RGB")
        draw = ImageDraw.Draw(page)
        draw.rectangle((20, 20, 620, 340), outline="black", width=8)
        draw.text((60, 60), f"PAGE {index}", fill="black")
        draw.polygon([(90, 140), (260, 180), (90, 220)], fill="white")
        page.save(path)
    corrupt = folder / "page4.jpg"
    corrupt.write_bytes(b"broken")
    template = make_template(folder, size=(640, 360))
    output = folder / "output"
    output.mkdir()
    runner = ImageSequenceVideoRunner(
        [("三页样本", pages + [str(corrupt)], [template])],
        str(output), hold_seconds=0.2, turn_seconds=0.2, fps=10, realism_enabled=False,
    )
    result = run_runner(runner)
    assert result["success"], result["message"]
    assert "跳过 1 张坏图" in result["message"]
    assert len(runner.output_paths) == 1
    assert "backend=" in result["message"] and "encoder=" in result["message"]
    assert runner.actual_backends and runner.actual_encoders
    if os.environ.get("RONGJING_CI_REQUIRE_RUNNER") == "1":
        assert runner.actual_backends[0].startswith("Core Image（"), result["message"]
    video_path = Path(runner.output_paths[0])
    assert video_path.exists() and str(video_path) in result["message"]

    with av.open(str(video_path)) as container:
        stream = container.streams.video[0]
        codec = stream.codec_context.name
        fps = float(stream.average_rate)
        size = (stream.width, stream.height)
        frames = list(container.decode(stream))
    assert codec == "h264", codec
    assert fps == 10.0, fps
    assert size == (640, 360), size
    assert len(frames) == 10, len(frames)
    pts = [frame.pts for frame in frames]
    assert all(a < b for a, b in zip(pts, pts[1:])), pts

    selected = {"first": frames[0], "mid-turn": frames[2], "next-static": frames[4]}
    for name, frame in selected.items():
        frame.to_image().save(folder / f"{name}.png")
    first = selected["first"].to_image()
    middle = selected["mid-turn"].to_image()
    following = selected["next-static"].to_image()
    assert ImageChops.difference(middle, first).getbbox() is not None
    assert ImageChops.difference(middle, following).getbbox() is not None

    (folder / "encoding-report.txt").write_text(
        f"codec={codec}\nfps={fps}\nsize={size[0]}x{size[1]}\nframes={len(frames)}\n"
        f"pts={pts}\nbackend={runner.actual_backends}\nencoder={runner.actual_encoders}\n"
        f"video={video_path.relative_to(ROOT)}\n",
        encoding="utf-8",
    )


def test_formal_resolution_and_static_clarity_short_sample():
    import core.page_video_runner as module

    folder = ROOT / "clarity-audio-v2"
    pages = make_pages(folder / "pages", colors=((245, 245, 245), (225, 235, 250)), size=(1024, 768))
    for index, path in enumerate(pages, 1):
        with Image.open(path) as opened:
            page = opened.convert("RGB")
        draw = ImageDraw.Draw(page)
        for y in range(80, 690, 36):
            draw.line((90, y, 930, y), fill=(10, 10, 10), width=3)
        draw.text((100, 30), f"CLARITY {index}", fill=(0, 0, 0))
        page.save(path)
    template = make_template(folder / "template", size=(1024, 768))

    assert scaled_size_for_width((1024, 768), 2560) == (2560, 1920)
    assert scaled_size_for_width((1024, 768), 3840) == (3840, 2880)
    unchanged = Image.new("RGB", (32, 24), "white")
    fitted = module.fit_page(unchanged, unchanged.size)
    assert fitted.size == unchanged.size and fitted.tobytes() == unchanged.tobytes() and fitted is not unchanged

    captured = {}
    original_encode = module.ImageSequenceVideoRunner._encode_video_attempt

    def capture_attempt(self, *, target_size, frames, **kwargs):
        materialized = list(frames)
        captured[target_size] = materialized[0].copy()
        materialized[0].save(folder / f"pre-encode-{target_size[0]}x{target_size[1]}.png")
        return original_encode(
            self,
            target_size=target_size,
            frames=iter(materialized),
            **kwargs,
        )

    decoded = {}
    for output_width, expected_size in ((1920, (1920, 1440)), (0, (1024, 768))):
        output = folder / f"output-{output_width}"
        output.mkdir(parents=True)
        runner = ImageSequenceVideoRunner(
            [(f"两页-{output_width}", pages, [template])],
            str(output),
            hold_seconds=0.1,
            turn_seconds=0.1,
            fps=10,
            realism_enabled=False,
            output_width=output_width,
        )
        with (
            mock.patch.object(module, "availability", return_value=(False, "V2 CPU 短样本")),
            mock.patch.object(module, "select_encoder", return_value=("libx264", {"crf": "17", "preset": "veryfast"}, {})),
            mock.patch.object(module.ImageSequenceVideoRunner, "_encode_video_attempt", new=capture_attempt),
        ):
            result = run_runner(runner)
        assert result["success"], result["message"]
        with av.open(runner.output_paths[0]) as container:
            stream = container.streams.video[0]
            assert (stream.width, stream.height) == expected_size
            first_frame = next(container.decode(stream)).to_image().convert("RGB")
        first_frame.save(folder / f"decoded-{expected_size[0]}x{expected_size[1]}.png")
        decoded[expected_size] = first_frame

    roi = (120, 100, 1800, 1340)
    before = captured[(1920, 1440)].crop(roi).convert("L").filter(ImageFilter.FIND_EDGES)
    after = decoded[(1920, 1440)].crop(roi).convert("L").filter(ImageFilter.FIND_EDGES)
    before_energy = float(np.asarray(before, dtype=np.float32).mean())
    after_energy = float(np.asarray(after, dtype=np.float32).mean())
    assert before_energy > 0 and after_energy > 0
    (folder / "clarity-report.json").write_text(
        json.dumps({
            "formal_size": [1920, 1440],
            "original_size": [1024, 768],
            "libx264": {"crf": 17, "preset": "veryfast"},
            "edge_energy": {"pre_encode": before_energy, "decoded": after_energy},
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def test_static_cache_and_cpu_fallback_call_counts():
    import core.page_video_runner as module

    folder = ROOT / "cache-counts"
    pages = make_pages(folder, colors=((200, 10, 10), (10, 10, 200)))
    template = make_template(folder)
    output = folder / "output"
    output.mkdir()
    original_embed = module.embed_image_pil_fast
    original_realism = module.apply_realism
    with (
        mock.patch.object(module, "availability", return_value=(False, "helper unavailable")),
        mock.patch.object(module, "select_encoder", return_value=("libx264", {"crf": "17", "preset": "veryfast"}, {})),
        mock.patch.object(module, "embed_image_pil_fast", wraps=original_embed) as embed_mock,
        mock.patch.object(module, "apply_realism", wraps=original_realism) as realism_mock,
    ):
        runner = ImageSequenceVideoRunner(
            [("缓存计数", pages, [template])],
            str(output), hold_seconds=0.5, turn_seconds=1.0, fps=20, realism_enabled=False,
        )
        result = run_runner(runner)
    assert result["success"], result["message"]
    assert runner.actual_backends == ["CPU 平面翻页"]
    assert "回退原因：helper unavailable" in result["message"]
    # 2 static pages + 7 non-terminal transition samples; 40 output frames are encoded.
    assert embed_mock.call_count == 2 + MAX_CURVE_FRAMES - 1, embed_mock.call_count
    assert realism_mock.call_count == 2 + MAX_CURVE_FRAMES - 1, realism_mock.call_count


def test_core_image_one_batch_per_pair_and_encoder_selection():
    import core.page_video_runner as module

    pages = [
        Image.new("RGB", (80, 48), color)
        for color in ((200, 10, 10), (10, 200, 10), (10, 10, 200))
    ]
    runner = ImageSequenceVideoRunner([], str(ROOT / "ci-unit-output"), fps=30)

    def fake_render_batch(source, target, output_dir, progresses, width, height, **_kwargs):
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        frames = []
        for index, progress in enumerate(progresses):
            path = output_dir / f"frame_{index:04d}.png"
            Image.blend(Image.open(source).convert("RGB"), Image.open(target).convert("RGB"), progress).save(path)
            frames.append({"index": index, "progress": progress, "path": str(path)})
        return {"ok": True, "filter": "CIPageCurlWithShadowTransition", "frames": frames}

    with (
        mock.patch.object(module, "availability", return_value=(True, "available")),
        mock.patch.object(module, "render_batch", side_effect=fake_render_batch) as render_mock,
    ):
        pairs, backend, reason = runner._prepare_transitions(pages, 30, "三页")
    assert reason is None
    assert backend == "Core Image（CIPageCurlWithShadowTransition）"
    assert render_mock.call_count == 2
    assert [len(pair) for pair in pairs] == [MAX_CURVE_FRAMES, MAX_CURVE_FRAMES]
    assert all(len(call.args[3]) == MAX_CURVE_FRAMES for call in render_mock.call_args_list)
    with mock.patch.object(module, "_probe_videotoolbox", return_value=True):
        codec, options, attrs = select_encoder(1920, 1080, 30)
    assert codec == "h264_videotoolbox" and not options and attrs["bit_rate"] == 8_000_000
    with mock.patch.object(module, "_probe_videotoolbox", return_value=True):
        _codec, _options, attrs = select_encoder(3840, 2160, 60)
    assert attrs["bit_rate"] == 20_000_000
    with mock.patch.object(module, "_probe_videotoolbox", return_value=False):
        codec, options, attrs = select_encoder(1920, 1080, 30)
    assert codec == "libx264" and options["crf"] == "17" and not attrs


def test_helper_timeout_falls_back_to_cpu():
    import core.page_video_runner as module
    import core.core_image_page_curl as helper_module

    pages = [Image.new("RGB", (80, 48), color) for color in ((200, 10, 10), (10, 10, 200))]
    runner = ImageSequenceVideoRunner([], str(ROOT / "timeout-fallback-output"), fps=10)
    with (
        mock.patch.object(module, "availability", return_value=(True, "available")),
        mock.patch.object(helper_module, "availability", return_value=(True, "available")),
        mock.patch.object(
            helper_module.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired("helper", 120),
        ),
    ):
        pairs, backend, reason = runner._prepare_transitions(pages, 10, "超时样本")
    assert pairs and backend == "CPU 平面翻页"
    assert reason == "Swift helper 渲染超时（120 秒）"


def test_videotoolbox_actual_failure_retries_libx264_without_overwrite():
    import core.page_video_runner as module

    folder = ROOT / "encoder-fallback"
    pages = make_pages(folder, colors=((200, 10, 10),))
    template = make_template(folder)
    output = folder / "output"
    output.mkdir()
    final_path = output / "preview.mp4"
    calls = []

    def fake_attempt(self, *, attempt_path, codec_name, frames, **_kwargs):
        calls.append((codec_name, Path(attempt_path), len(list(frames))))
        Path(attempt_path).write_bytes(codec_name.encode("ascii"))
        if codec_name == "h264_videotoolbox":
            raise RuntimeError("actual add_stream failure")
        return calls[-1][2]

    runner = ImageSequenceVideoRunner(
        [("编码回退", pages, [template])],
        str(output), hold_seconds=0.2, turn_seconds=0.1, fps=10,
        realism_enabled=False, output_path=str(final_path),
    )
    with (
        mock.patch.object(
            module,
            "select_encoder",
            return_value=("h264_videotoolbox", {}, {"bit_rate": 1_000_000}),
        ),
        mock.patch.object(module.ImageSequenceVideoRunner, "_encode_video_attempt", new=fake_attempt),
    ):
        result = run_runner(runner)
    assert result["success"], result["message"]
    assert runner.actual_encoders == ["libx264"]
    assert "encoder=libx264" in result["message"]
    assert final_path.read_bytes() == b"libx264"
    assert calls[0][1] != calls[1][1] and calls[1][1] != final_path
    assert calls[0][1].exists(), "VideoToolbox 失败半成品应保留"
    assert calls[0][2] == calls[1][2] == 2, "回退必须重放完整帧计划"


def test_formal_output_publishes_attempt_without_final_placeholder():
    import core.page_video_runner as module

    folder = ROOT / "reserved-output"
    pages = make_pages(folder, colors=((200, 10, 10),))
    template = make_template(folder)
    output = folder / "output"
    output.mkdir()
    attempts = []

    def fake_attempt(self, *, attempt_path, frames, **_kwargs):
        attempts.append(Path(attempt_path))
        assert not (attempts[-1].parent / "正式导出.mp4").exists()
        attempts[-1].write_bytes(b"encoded-video")
        return len(list(frames))

    runner = ImageSequenceVideoRunner(
        [("正式导出", pages, [template])],
        str(output), hold_seconds=0.2, turn_seconds=0.1, fps=10,
        realism_enabled=False,
    )
    with (
        mock.patch.object(module, "select_encoder", return_value=("libx264", {}, {})),
        mock.patch.object(module.ImageSequenceVideoRunner, "_encode_video_attempt", new=fake_attempt),
    ):
        result = run_runner(runner)
    final_path = Path(runner.output_paths[0])
    assert result["success"], result["message"]
    assert final_path.read_bytes() == b"encoded-video"
    assert not attempts[0].exists(), "成功 attempt 应原子移动为正式目标"


def test_preview_publish_race_preserves_external_content_and_attempt():
    import core.page_video_runner as module

    folder = ROOT / "reserved-output-conflict"
    pages = make_pages(folder, colors=((200, 10, 10),))
    template = make_template(folder)
    output = folder / "output"
    output.mkdir()
    preview_path = output / "preview.mp4"
    attempt = None

    def fake_attempt(self, *, attempt_path, frames, **_kwargs):
        nonlocal attempt
        attempt = Path(attempt_path)
        attempt.write_bytes(b"encoded-attempt")
        preview_path.write_bytes(b"external-content")
        return len(list(frames))

    runner = ImageSequenceVideoRunner(
        [("预览竞争", pages, [template])],
        str(output), hold_seconds=0.2, turn_seconds=0.1, fps=10,
        realism_enabled=False, output_path=str(preview_path),
    )
    with (
        mock.patch.object(module, "select_encoder", return_value=("libx264", {}, {})),
        mock.patch.object(module.ImageSequenceVideoRunner, "_encode_video_attempt", new=fake_attempt),
    ):
        result = run_runner(runner)
    assert not result["success"] and "页面翻页视频失败" in result["message"]
    assert preview_path.read_bytes() == b"external-content"
    assert attempt.read_bytes() == b"encoded-attempt"
    assert runner.output_paths == []


def test_explicit_preview_output_still_rejects_existing_target():
    folder = ROOT / "preview-existing-output"
    pages = make_pages(folder, colors=((200, 10, 10),))
    template = make_template(folder)
    output = folder / "output"
    output.mkdir()
    preview_path = output / "preview.mp4"
    preview_path.write_bytes(b"existing-preview")
    runner = ImageSequenceVideoRunner(
        [("预览冲突", pages, [template])],
        str(output), hold_seconds=0.2, turn_seconds=0.1, fps=10,
        realism_enabled=False, output_path=str(preview_path),
    )
    result = run_runner(runner)
    assert not result["success"] and "预览文件已存在" in result["message"]
    assert preview_path.read_bytes() == b"existing-preview"


def test_offscreen_ui_routing():
    import core.batch_runner as batch_runner_module
    import core.page_video_runner as page_runner_module
    from ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    folder = ROOT / "ui"
    pages = make_pages(folder, colors=((1, 2, 3), (4, 5, 6)))
    templates = folder / "templates"
    backgrounds = folder / "backgrounds"
    templates.mkdir(); backgrounds.mkdir()
    window = MainWindow(str(templates), str(backgrounds), str(folder / "collages"))
    window._set_batch_mode(2)
    window._populate_video_table(pages)
    assert window._video_input_kind == "image"
    assert not window._page_video_settings_widget.isHidden()
    assert not window._format_row_widget.isHidden()
    assert window.format_combo.isHidden()
    assert not window.resolution_combo.isHidden()
    labels = [label.text() for label in window._background_music_card.findChildren(QLabel)]
    assert "合成时从音乐开头开始，自动匹配视频时长。" in labels
    assert window.video_table.rowCount() == 1
    assert window.video_table.item(0, 1).text() == "2 页"

    template = make_template(folder)
    template_key = window.tm.save(template)
    window._video_row_selections = {0: [template_key]}
    window._background_music_card._mode = "random"
    window._background_music_card._selected_ids = ["track-a", "track-b"]
    window._background_music_card._volume = 42
    window._set_batch_resolution_combo(1920)
    output = folder / "output"
    output.mkdir()

    calls = []

    class FakeSignal:
        def connect(self, _slot):
            pass

    class FakeRunner:
        def __init__(self, *args, **kwargs):
            calls.append((args, kwargs))
            self.progress = FakeSignal()
            self.finished = FakeSignal()

        def start(self):
            pass

        def abort(self):
            pass

    original_page_runner = page_runner_module.ImageSequenceVideoRunner
    page_runner_module.ImageSequenceVideoRunner = FakeRunner
    try:
        window._run_video_batch(str(output))
    finally:
        page_runner_module.ImageSequenceVideoRunner = original_page_runner
    assert calls[-1][1]["hold_seconds"] == window.page_hold_spin.value()
    assert calls[-1][1]["turn_seconds"] == window.page_turn_spin.value()
    assert calls[-1][1]["fps"] == window.page_fps_spin.value()
    assert window.page_direction_combo.currentData() == RIGHT_TO_LEFT
    assert calls[-1][1]["direction"] == RIGHT_TO_LEFT
    assert calls[-1][1]["music_library"] is window._music_library
    assert calls[-1][1]["music_mode"] == "random"
    assert calls[-1][1]["music_selected_ids"] == ["track-a", "track-b"]
    assert calls[-1][1]["music_volume"] == 42
    assert calls[-1][1]["output_width"] == 1920

    video = folder / "dummy.mp4"
    video.write_bytes(b"placeholder")
    window._populate_video_table([str(video)])
    assert window._video_input_kind == "video"
    assert window._page_video_settings_widget.isHidden()
    assert window._format_row_widget.isHidden()
    window._video_row_selections = {0: [template_key]}
    original_video_runner = batch_runner_module.VideoRunner
    batch_runner_module.VideoRunner = FakeRunner
    try:
        window._run_video_batch(str(output))
    finally:
        batch_runner_module.VideoRunner = original_video_runner
    assert "hold_seconds" not in calls[-1][1]
    assert "turn_seconds" not in calls[-1][1]
    assert "fps" not in calls[-1][1]
    assert "music_mode" not in calls[-1][1]
    assert "output_width" not in calls[-1][1]

    warnings = []
    original_warning = QMessageBox.warning
    try:
        QMessageBox.warning = lambda *args: warnings.append(args[2])
        window._populate_video_table([str(video), pages[0]])
    finally:
        QMessageBox.warning = original_warning
    assert warnings and "混合" in warnings[-1]
    window.close()
    app.processEvents()


def test_offscreen_preview_ui_contract():
    import ui.main_window as main_window_module
    import core.page_video_runner as page_runner_module
    from ui.main_window import MainWindow

    folder = ROOT / "preview-ui"
    pages = make_pages(folder, colors=((1, 2, 3), (4, 5, 6), (7, 8, 9)))
    corrupt = folder / "page0.png"
    corrupt.write_bytes(b"broken")
    templates = folder / "templates"
    backgrounds = folder / "backgrounds"
    templates.mkdir(); backgrounds.mkdir()
    window = MainWindow(str(templates), str(backgrounds), str(folder / "collages"))
    window._set_batch_mode(2)
    window._populate_video_table([str(corrupt), *pages])
    assert not window.btn_page_preview.isHidden()
    window._set_batch_mode(1)
    assert window.btn_page_preview.isHidden() and not window.btn_page_preview.isEnabled()
    window._set_batch_running(True)
    window._set_batch_mode(2)
    assert not window.btn_page_preview.isHidden() and not window.btn_page_preview.isEnabled()
    window._set_batch_running(False)
    assert window.btn_page_preview.isEnabled()

    paper = make_template(folder / "paper")
    paper.name = "纸张模板"
    paper.template_type = "document_paper"
    paper_key = window.tm.save(paper)
    screen = make_template(folder / "screen")
    screen.name = "屏幕模板"
    screen_key = window.tm.save(screen)
    window._video_row_selections = {0: [paper_key, screen_key]}
    window._background_music_card._mode = "fixed"
    window._background_music_card._selected_ids = ["preview-track"]
    window._background_music_card._volume = 35
    window.realism_check.setChecked(True)
    window.realism_strength_spin.setValue(63)

    calls = []

    class FakeSignal:
        def __init__(self):
            self.slots = []

        def connect(self, slot):
            self.slots.append(slot)

        def emit(self, *args):
            for slot in self.slots:
                slot(*args)

    class FakePreviewRunner:
        def __init__(self, *args, **kwargs):
            calls.append((args, kwargs))
            self.progress = FakeSignal()
            self.finished = FakeSignal()
            self.output_paths = [kwargs["output_path"]]
            Path(kwargs["output_path"]).parent.mkdir(parents=True, exist_ok=True)
            Path(kwargs["output_path"]).write_bytes(b"preview")

        def start(self):
            pass

        def abort(self):
            pass

    warnings = []
    dialogs = []

    class FakeDialog:
        def __init__(self, _parent):
            dialogs.append(self)
            self.source = None
            self.opened = False

        def set_source(self, path):
            self.source = path
            return True

        def open(self):
            self.opened = True

        def close(self):
            pass

    with (
        mock.patch.object(page_runner_module, "ImageSequenceVideoRunner", FakePreviewRunner),
        mock.patch.object(QMessageBox, "warning", side_effect=lambda *args: warnings.append(args[2])),
        mock.patch.object(main_window_module, "PageVideoPreviewDialog", FakeDialog),
    ):
        window._preview_page_turn()
        args, kwargs = calls[-1]
        task = args[0][0]
        assert task[1] == pages[:2]
        assert task[2][0].name == "屏幕模板"
        assert kwargs["hold_seconds"] == 0.5
        assert kwargs["turn_seconds"] == 0.7
        assert kwargs["fps"] == 15
        assert kwargs["output_width"] == 960
        assert kwargs["realism_enabled"] is True
        assert kwargs["realism_strength"] == 63
        assert kwargs["direction"] == RIGHT_TO_LEFT
        assert kwargs["music_library"] is window._music_library
        assert kwargs["music_mode"] == "fixed"
        assert kwargs["music_selected_ids"] == ["preview-track"]
        assert kwargs["music_volume"] == 35
        preview_path = Path(kwargs["output_path"])
        assert preview_path.parent == window._preview_cache_root
        assert preview_path.name.startswith("page-turn-preview-") and preview_path.suffix == ".mp4"
        assert "page_curl_work_root" not in kwargs
        assert not window.btn_run.isEnabled()
        assert not window.btn_page_preview.isEnabled()
        assert not window.btn_abort.isHidden()

        window._batch_runner.finished.emit(True, "backend=CPU 平面翻页；encoder=libx264")
        assert window.btn_run.isEnabled() and window.btn_page_preview.isEnabled()
        assert window.btn_abort.isHidden()
        assert dialogs and dialogs[-1].source == kwargs["output_path"]
        assert dialogs[-1].opened
        assert window.progress_label.text() == "预览已生成；预览弹窗当前仅看画面，成品含配乐"

        window._set_batch_running(True, preview=True)
        window._on_preview_finished(False, "页面翻页视频失败：实际错误")
        assert window.btn_run.isEnabled() and window.btn_page_preview.isEnabled()
        assert warnings and warnings[-1] == "页面翻页视频失败：实际错误"

    video = folder / "dummy.mp4"
    video.write_bytes(b"placeholder")
    window._populate_video_table([str(video)])
    assert window.btn_page_preview.isHidden()
    window.close()
    APP.processEvents()


def run_tests():
    test_policy_and_natural_sort()
    test_counts_pts_plan_and_transition_geometry()
    test_page_turn_direction_mirror_contract()
    test_preview_cache_cleanup_is_flat_and_fd_bounded()
    test_preview_cache_root_replacement_does_not_redirect_deletion()
    test_offscreen_preview_dialog_contract()
    test_even_size_cancel_bad_image_and_non_overwrite()
    test_real_encoding_and_pts()
    test_formal_resolution_and_static_clarity_short_sample()
    test_static_cache_and_cpu_fallback_call_counts()
    test_core_image_one_batch_per_pair_and_encoder_selection()
    test_helper_timeout_falls_back_to_cpu()
    test_videotoolbox_actual_failure_retries_libx264_without_overwrite()
    test_formal_output_publishes_attempt_without_final_placeholder()
    test_preview_publish_race_preserves_external_content_and_attempt()
    test_explicit_preview_output_still_rejects_existing_target()
    test_offscreen_ui_routing()
    test_offscreen_preview_ui_contract()
    (ROOT / "summary.txt").write_text(
        "RJ-CI-P3 V2: preview UI and runner checks passed\n",
        encoding="utf-8",
    )
    print(f"RJ-CI-P3 V2 tests passed; evidence={ROOT.name}")


if __name__ == "__main__":
    if BGM_RUN_ID:
        test_bgm_m3_real_mux_random_none_and_invalid_id()
        test_offscreen_ui_routing()
        test_offscreen_preview_ui_contract()
        print(f"RJ-BGM-M3 V2 tests passed; evidence={ROOT.name}")
    else:
        run_tests()
