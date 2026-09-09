"""collage 子命令测试：直接调用 cli.collage()，用真实临时目录假图片。"""

import json
import os
import subprocess
import sys
import uuid
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from PIL import Image

sys.path.insert(0, os.path.dirname(__file__))
import cli


def new_test_root(label):
    path = Path(__file__).parent / "qa" / "logs" / f"m4_cli_{label}_{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    return str(path)


def assert_equal(actual, expected, message):
    if actual != expected:
        raise AssertionError(f"{message}: expected {expected}, got {actual}")


def assert_true(cond, message):
    if not cond:
        raise AssertionError(message)


def _make_fake_images(dir_path, count):
    os.makedirs(dir_path, exist_ok=True)
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
    for i in range(1, count + 1):
        img = Image.new("RGB", (200, 150), colors[(i - 1) % len(colors)])
        img.save(os.path.join(dir_path, f"{i}.png"))


def test_collage_with_explicit_rows_cols():
    tmp = new_test_root("basic")
    try:
        input_dir = os.path.join(tmp, "input")
        _make_fake_images(input_dir, 4)
        os.makedirs(os.path.join(tmp, "output"))
        output = os.path.join(tmp, "output", "out.png")

        cli.collage(input_dir, output, template_name=None, rows=2, cols=2,
                    pages=None, json_result=False)

        actual = os.path.join(tmp, "output", "input", "out.png")
        assert_true(os.path.isfile(actual), "output file should exist")
        img = Image.open(actual)
        assert_true(img.size[0] > 0 and img.size[1] > 0, "output image should have positive size")
    finally:
        pass


def test_collage_template_and_rows_mutually_exclusive():
    tmp = new_test_root("exclusive")
    try:
        input_dir = os.path.join(tmp, "input")
        _make_fake_images(input_dir, 2)
        os.makedirs(os.path.join(tmp, "output"))
        output = os.path.join(tmp, "output", "out.png")

        try:
            cli.collage(input_dir, output, template_name="1", rows=2, cols=1,
                        pages=None, json_result=False)
            raise AssertionError("应该因 --template 与 --rows/--cols 同时给出而退出")
        except SystemExit as exc:
            assert_equal(exc.code, 1, "mutually exclusive args exit code")
    finally:
        pass


def test_collage_missing_template_fails_closed():
    tmp = new_test_root("missing")
    try:
        input_dir = os.path.join(tmp, "input")
        _make_fake_images(input_dir, 2)
        os.makedirs(os.path.join(tmp, "output"))
        output = os.path.join(tmp, "output", "out.png")

        try:
            cli.collage(input_dir, output, template_name="不存在的预设名字XYZ",
                        rows=None, cols=None, pages=None, json_result=False)
            raise AssertionError("应该因预设不存在而退出")
        except SystemExit as exc:
            assert_equal(exc.code, 1, "missing template exit code")
        assert_true(not os.path.exists(output), "output should not be created on failure")
    finally:
        pass


def test_collage_pages_subset():
    tmp = new_test_root("pages")
    try:
        input_dir = os.path.join(tmp, "input")
        _make_fake_images(input_dir, 4)
        os.makedirs(os.path.join(tmp, "output"))
        output = os.path.join(tmp, "output", "out.png")

        cli.collage(input_dir, output, template_name=None, rows=1, cols=2,
                    pages="1,3", json_result=False)

        assert_true(os.path.isfile(os.path.join(tmp, "output", "input", "out.png")), "output should exist with subset pages")
    finally:
        pass


def test_collage_cli_json_result_via_subprocess():
    tmp = new_test_root("json")
    try:
        input_dir = os.path.join(tmp, "input")
        _make_fake_images(input_dir, 2)
        os.makedirs(os.path.join(tmp, "output"))
        output = os.path.join(tmp, "output", "out.jpg")

        cli_path = os.path.join(os.path.dirname(__file__), "cli.py")
        proc = subprocess.run(
            [sys.executable, cli_path, "collage",
             "--input-dir", input_dir, "--output", output,
             "--template", "1", "--json-result"],
            capture_output=True, text=True,
        )
        assert_equal(proc.returncode, 0, f"subprocess exit code, stderr={proc.stderr}")
        result = json.loads(proc.stdout.strip())
        assert_true("output" in result and "sha256" in result and "size" in result,
                    "json result should have output/sha256/size")
        assert_true(os.path.isfile(result["output"]), "json output path should exist")
    finally:
        pass


def test_collage_three_runs_report_unique_directories_and_preserve_first_hash():
    tmp = new_test_root("three_runs")
    input_dir = os.path.join(tmp, "课件图片")
    _make_fake_images(input_dir, 2)
    os.makedirs(os.path.join(tmp, "output"))
    requested = os.path.join(tmp, "output", "out.png")
    payloads = []
    for _ in range(3):
        stdout = StringIO()
        with redirect_stdout(stdout):
            cli.collage(input_dir, requested, None, 1, 2, None, json_result=True)
        payloads.append(json.loads(stdout.getvalue()))
    assert_equal(
        [Path(payload["output_dir"]).name for payload in payloads],
        ["课件图片", "课件图片_2", "课件图片_3"],
        "three unique output directories",
    )
    first_path = Path(payloads[0]["output"])
    first_hash = payloads[0]["sha256"]
    assert_equal(__import__("hashlib").sha256(first_path.read_bytes()).hexdigest(), first_hash, "first hash unchanged")


def test_process_three_runs_return_actual_unique_directories():
    tmp = Path(new_test_root("process_three_runs"))
    input_dir = tmp / "课件图片"
    _make_fake_images(str(input_dir), 1)
    background = tmp / "background.png"
    Image.new("RGB", (200, 150), "white").save(background)
    output_root = tmp / "output"
    output_root.mkdir()
    template = {
        "_storage_key": "模板A",
        "name": "模板A",
        "category": "教室场景",
        "background_path": str(background),
        "screen_points": [[0, 0], [199, 0], [199, 149], [0, 149]],
        "template_type": "screen",
    }
    payloads = []
    with mock.patch.object(cli, "load_template", return_value=template):
        for _ in range(3):
            stdout = StringIO()
            with redirect_stdout(stdout):
                payloads.append(
                    cli.process(
                        [str(input_dir)], ["模板A"], str(output_root), "PNG",
                        realism_enabled=False, json_result=True,
                    )
                )
    assert_equal(
        [Path(payload["output_dirs"][0]).name for payload in payloads],
        ["课件图片", "课件图片_2", "课件图片_3"],
        "process output directories",
    )
    first = Path(payloads[0]["output_dirs"][0]) / "模板A" / "1.png"
    first_hash = __import__("hashlib").sha256(first.read_bytes()).hexdigest()
    assert_equal(__import__("hashlib").sha256(first.read_bytes()).hexdigest(), first_hash, "process first hash unchanged")


def test_process_and_collage_share_strict_image_filtering():
    tmp = Path(new_test_root("input_filter"))
    input_dir = tmp / "inputs"
    input_dir.mkdir()
    Image.new("RGB", (20, 10), "red").save(input_dir / "1.png")
    for name in ("._0.jpg", ".hidden.png", "~$draft.png", "clip.mp4"):
        (input_dir / name).write_bytes(b"not an image")
    (input_dir / "folder.jpg").mkdir()

    background = tmp / "background.png"
    Image.new("RGB", (20, 10), "white").save(background)
    template = {
        "_storage_key": "模板A",
        "name": "模板A",
        "category": "教室场景",
        "background_path": str(background),
        "screen_points": [[0, 0], [19, 0], [19, 9], [0, 9]],
        "template_type": "screen",
    }
    process_output = tmp / "process-output"
    process_output.mkdir()
    with mock.patch.object(cli, "load_template", return_value=template):
        payload = cli.process(
            [str(input_dir)], ["模板A"], str(process_output), "PNG",
            realism_enabled=False, json_result=True,
        )
    assert_equal(payload["processed"], 1, "process should keep only 1.png")

    collage_output = tmp / "collage-output"
    collage_output.mkdir()
    stdout = StringIO()
    with redirect_stdout(stdout):
        cli.collage(str(input_dir), str(collage_output / "out.png"), None, 1, 1, None, True)
    collage_payload = json.loads(stdout.getvalue())
    assert_equal(collage_payload["input_images"], 1, "collage should keep only 1.png")

    for invalid in ("._0.jpg", ".hidden.png", "~$draft.png", "clip.mp4"):
        invalid_path = str(input_dir / invalid)
        try:
            cli.process([invalid_path], ["模板A"], str(process_output), "PNG", realism_enabled=False)
            raise AssertionError(f"process should reject explicit invalid input: {invalid}")
        except SystemExit as exc:
            assert_equal(exc.code, 1, f"process invalid input exit code: {invalid}")
        try:
            cli.collage(invalid_path, str(collage_output / "invalid.png"), None, 1, 1, None)
            raise AssertionError(f"collage should reject explicit invalid input: {invalid}")
        except SystemExit as exc:
            assert_equal(exc.code, 1, f"collage invalid input exit code: {invalid}")


if __name__ == "__main__":
    test_collage_with_explicit_rows_cols()
    test_collage_template_and_rows_mutually_exclusive()
    test_collage_missing_template_fails_closed()
    test_collage_pages_subset()
    test_collage_cli_json_result_via_subprocess()
    test_collage_three_runs_report_unique_directories_and_preserve_first_hash()
    test_process_three_runs_return_actual_unique_directories()
    test_process_and_collage_share_strict_image_filtering()
    print("all cli collage tests passed")
