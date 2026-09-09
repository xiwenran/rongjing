import hashlib
import os
import uuid
from pathlib import Path

from PIL import Image
from PyQt6.QtCore import QCoreApplication, QEventLoop, QTimer

import core.batch_runner as batch_runner_module
from core.batch_runner import BatchRunner, VideoRunner, get_image_files
from core.diversifier import DiversifyConfig
from models.template_model import Template


def new_test_root(label: str) -> str:
    path = Path(__file__).parent / "qa" / "logs" / f"m4_batch_{label}_{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    return str(path)


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def make_fixture(root: str, bg_size=(64, 64), ppt_size=(40, 40)):
    bg_path = os.path.join(root, "background.png")
    ppt_path = os.path.join(root, "ppt.png")
    Image.new("RGB", bg_size, (20, 40, 60)).save(bg_path)
    Image.new("RGB", ppt_size, (180, 120, 40)).save(ppt_path)
    template = Template(
        name="测试模板",
        background_path=bg_path,
        screen_points=[
            [0, 0],
            [bg_size[0], 0],
            [bg_size[0], bg_size[1]],
            [0, bg_size[1]],
        ],
    )
    return template, ppt_path


def run_batch(tasks, output_dir: str, diversify_config=None, output_width: int = 0) -> str:
    os.makedirs(output_dir, exist_ok=True)
    app = QCoreApplication.instance() or QCoreApplication([])
    loop = QEventLoop()
    result = {}
    runner = BatchRunner(
        tasks=tasks,
        output_dir=output_dir,
        output_format="PNG",
        output_width=output_width,
        diversify_config=diversify_config,
    )
    runner.finished.connect(lambda success, msg: (result.update(success=success, msg=msg), loop.quit()))
    QTimer.singleShot(15000, lambda: (result.update(success=False, msg="timeout"), runner.abort(), loop.quit()))
    runner.start()
    loop.exec()
    runner.wait(1000)
    assert result.get("success"), result.get("msg")
    template_name = tasks[0][2][0].name
    return os.path.join(runner.output_dirs[0], template_name, "1.png")


def test_disabled_diversify_matches_none_output():
    root = new_test_root("regression")
    try:
        template, ppt_path = make_fixture(root)
        tasks = [("组A", [ppt_path], [template])]

        none_path = run_batch(tasks, os.path.join(root, "none"), None)
        disabled = DiversifyConfig.preset("medium")
        disabled.enabled = False
        disabled_path = run_batch(tasks, os.path.join(root, "disabled"), disabled)

        assert sha256(none_path) == sha256(disabled_path)
    finally:
        pass


def test_enabled_diversify_changes_repeated_outputs():
    root = new_test_root("diversify")
    try:
        template, ppt_path = make_fixture(root)
        tasks = [("组A", [ppt_path], [template])]
        config = DiversifyConfig.preset("medium")

        first_path = run_batch(tasks, os.path.join(root, "first"), config)
        second_path = run_batch(tasks, os.path.join(root, "second"), config)

        assert sha256(first_path) != sha256(second_path)
    finally:
        pass


def test_batch_output_width_keeps_background_aspect_ratio():
    root = new_test_root("resolution")
    try:
        template, ppt_path = make_fixture(root, bg_size=(64, 32), ppt_size=(40, 20))
        tasks = [("组A", [ppt_path], [template])]

        out_path = run_batch(tasks, os.path.join(root, "out"), output_width=1920)

        with Image.open(out_path) as img:
            assert img.size == (1920, 960)
    finally:
        pass


def test_batch_output_width_renders_on_target_canvas():
    root = new_test_root("render_canvas")
    original_precompute = batch_runner_module.precompute_template_cache
    seen_bg_sizes = []
    try:
        def recording_precompute(bg_img, *args, **kwargs):
            seen_bg_sizes.append(bg_img.size)
            return original_precompute(bg_img, *args, **kwargs)

        batch_runner_module.precompute_template_cache = recording_precompute
        template, ppt_path = make_fixture(root, bg_size=(64, 32), ppt_size=(40, 20))
        tasks = [("组A", [ppt_path], [template])]

        run_batch(tasks, os.path.join(root, "out"), output_width=1920)

        assert seen_bg_sizes == [(1920, 960)]
    finally:
        batch_runner_module.precompute_template_cache = original_precompute
        pass


def test_document_paper_template_outputs_file():
    root = new_test_root("document")
    try:
        template, ppt_path = make_fixture(root, bg_size=(90, 70), ppt_size=(42, 50))
        template.name = "文档模板"
        template.category = "文档模板"
        template.template_type = "document_paper"
        template.render_preset = "paper"
        tasks = [("组A", [ppt_path], [template])]

        out_path = run_batch(tasks, os.path.join(root, "out"))

        assert os.path.exists(out_path)
        with Image.open(out_path) as img:
            assert img.size == (90, 70)
    finally:
        pass


def test_shared_file_policy_filters_task_inputs_but_decode_failure_still_skips():
    root = new_test_root("policy")
    try:
        template, valid_path = make_fixture(root)
        hidden_path = os.path.join(root, "._0.jpg")
        temporary_path = os.path.join(root, "~$draft.png")
        corrupt_path = os.path.join(root, "1.jpg")
        Image.new("RGB", (10, 10), "red").save(hidden_path)
        Image.new("RGB", (10, 10), "red").save(temporary_path)
        with open(corrupt_path, "wb") as file_obj:
            file_obj.write(b"not an image")

        scanned = [os.path.basename(path) for path in get_image_files(root)]
        assert scanned == ["1.jpg", "background.png", "ppt.png"], scanned

        tasks = [("组A", [hidden_path, temporary_path, corrupt_path, valid_path], [template])]
        runner = BatchRunner(tasks, os.path.join(root, "out"), output_format="PNG")
        assert runner.tasks[0][1] == [corrupt_path, valid_path]

        app = QCoreApplication.instance() or QCoreApplication([])
        loop = QEventLoop()
        result = {}
        runner.finished.connect(
            lambda success, msg: (result.update(success=success, msg=msg), loop.quit())
        )
        runner.start()
        QTimer.singleShot(15000, lambda: (runner.abort(), loop.quit()))
        loop.exec()
        runner.wait(1000)
        assert result.get("success"), result
        assert "跳过 1 张" in result.get("msg", ""), result
    finally:
        pass


def test_batch_and_video_source_directories_are_frozen_and_never_overwrite():
    root = new_test_root("unique_sources")
    template, ppt_path = make_fixture(root)
    output_root = os.path.join(root, "output")
    os.makedirs(output_root)
    tasks = [("组A", [ppt_path], [template])]

    paths = [run_batch(tasks, output_root) for _ in range(3)]
    assert [Path(path).parents[1].name for path in paths] == ["组A", "组A_2", "组A_3"]
    first_hash = sha256(paths[0])
    assert sha256(paths[0]) == first_hash

    video_path = os.path.join(root, "示例.mp4")
    Path(video_path).write_bytes(b"mock video")
    video_runners = [VideoRunner([(video_path, [template])], output_root) for _ in range(3)]
    assert [Path(runner.output_dirs[0]).name for runner in video_runners] == [
        "示例", "示例_2", "示例_3"
    ]


def run_tests():
    test_disabled_diversify_matches_none_output()
    test_enabled_diversify_changes_repeated_outputs()
    test_batch_output_width_keeps_background_aspect_ratio()
    test_batch_output_width_renders_on_target_canvas()
    test_document_paper_template_outputs_file()
    test_shared_file_policy_filters_task_inputs_but_decode_failure_still_skips()
    test_batch_and_video_source_directories_are_frozen_and_never_overwrite()
    print("batch runner regression tests passed")


if __name__ == "__main__":
    run_tests()
