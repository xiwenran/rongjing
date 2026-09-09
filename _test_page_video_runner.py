"""V2 checks for RJ-CI-P3; artifacts are intentionally retained in qa/logs."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from unittest import mock

import av
from PIL import Image, ImageChops, ImageDraw
from PyQt6.QtWidgets import QApplication, QMessageBox

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
from models.template_model import Template


ROOT = Path(__file__).parent / "qa" / "logs" / f"coreimage-preview-p3-{uuid.uuid4().hex}"
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
        mock.patch.object(module, "select_encoder", return_value=("libx264", {"crf": "18", "preset": "veryfast"}, {})),
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
    assert codec == "h264_videotoolbox" and not options and attrs["bit_rate"] > 0
    with mock.patch.object(module, "_probe_videotoolbox", return_value=False):
        codec, options, attrs = select_encoder(1920, 1080, 30)
    assert codec == "libx264" and options["crf"] == "18" and not attrs


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
    assert window.video_table.rowCount() == 1
    assert window.video_table.item(0, 1).text() == "2 页"

    template = make_template(folder)
    template_key = window.tm.save(template)
    window._video_row_selections = {0: [template_key]}
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

    video = folder / "dummy.mp4"
    video.write_bytes(b"placeholder")
    window._populate_video_table([str(video)])
    assert window._video_input_kind == "video"
    assert window._page_video_settings_widget.isHidden()
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

    paper = make_template(folder / "paper")
    paper.name = "纸张模板"
    paper.template_type = "document_paper"
    paper_key = window.tm.save(paper)
    screen = make_template(folder / "screen")
    screen.name = "屏幕模板"
    screen_key = window.tm.save(screen)
    window._video_row_selections = {0: [paper_key, screen_key]}
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
    infos = []
    opened = []
    with (
        mock.patch.object(page_runner_module, "ImageSequenceVideoRunner", FakePreviewRunner),
        mock.patch.object(QMessageBox, "warning", side_effect=lambda *args: warnings.append(args[2])),
        mock.patch.object(QMessageBox, "information", side_effect=lambda *args: infos.append(args[2])),
        mock.patch.object(main_window_module.QDesktopServices, "openUrl", side_effect=lambda url: opened.append(url) or True),
    ):
        window._preview_page_turn()
        args, kwargs = calls[-1]
        task = args[0][0]
        assert task[1] == pages[:2]
        assert task[2][0].name == "屏幕模板"
        assert kwargs["hold_seconds"] == 0.5
        assert kwargs["turn_seconds"] == 0.7
        assert kwargs["fps"] == 15
        assert kwargs["max_output_width"] == 960
        assert kwargs["realism_enabled"] is True
        assert kwargs["realism_strength"] == 63
        assert not window.btn_run.isEnabled()
        assert not window.btn_page_preview.isEnabled()
        assert not window.btn_abort.isHidden()

        window._batch_runner.finished.emit(True, "backend=CPU 平面翻页；encoder=libx264")
        assert window.btn_run.isEnabled() and window.btn_page_preview.isEnabled()
        assert window.btn_abort.isHidden()
        assert opened and opened[-1].toLocalFile() == kwargs["output_path"]
        assert infos and "CPU 平面翻页" in infos[-1]

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
    test_even_size_cancel_bad_image_and_non_overwrite()
    test_real_encoding_and_pts()
    test_static_cache_and_cpu_fallback_call_counts()
    test_core_image_one_batch_per_pair_and_encoder_selection()
    test_offscreen_ui_routing()
    test_offscreen_preview_ui_contract()
    (ROOT / "summary.txt").write_text(
        "RJ-CI-P3 V2: preview UI and runner checks passed\n",
        encoding="utf-8",
    )
    print(f"RJ-CI-P3 V2 tests passed; evidence={ROOT.name}")


if __name__ == "__main__":
    run_tests()
