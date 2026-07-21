"""collage 子命令测试：直接调用 cli.collage()，用真实临时目录假图片。"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

from PIL import Image

sys.path.insert(0, os.path.dirname(__file__))
import cli


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
    tmp = tempfile.mkdtemp(prefix="rongjing_test_collage_")
    try:
        input_dir = os.path.join(tmp, "input")
        _make_fake_images(input_dir, 4)
        output = os.path.join(tmp, "out.png")

        cli.collage(input_dir, output, template_name=None, rows=2, cols=2,
                    pages=None, json_result=False)

        assert_true(os.path.isfile(output), "output file should exist")
        img = Image.open(output)
        assert_true(img.size[0] > 0 and img.size[1] > 0, "output image should have positive size")
    finally:
        shutil.rmtree(tmp)


def test_collage_template_and_rows_mutually_exclusive():
    tmp = tempfile.mkdtemp(prefix="rongjing_test_collage_")
    try:
        input_dir = os.path.join(tmp, "input")
        _make_fake_images(input_dir, 2)
        output = os.path.join(tmp, "out.png")

        try:
            cli.collage(input_dir, output, template_name="1", rows=2, cols=1,
                        pages=None, json_result=False)
            raise AssertionError("应该因 --template 与 --rows/--cols 同时给出而退出")
        except SystemExit as exc:
            assert_equal(exc.code, 1, "mutually exclusive args exit code")
    finally:
        shutil.rmtree(tmp)


def test_collage_missing_template_fails_closed():
    tmp = tempfile.mkdtemp(prefix="rongjing_test_collage_")
    try:
        input_dir = os.path.join(tmp, "input")
        _make_fake_images(input_dir, 2)
        output = os.path.join(tmp, "out.png")

        try:
            cli.collage(input_dir, output, template_name="不存在的预设名字XYZ",
                        rows=None, cols=None, pages=None, json_result=False)
            raise AssertionError("应该因预设不存在而退出")
        except SystemExit as exc:
            assert_equal(exc.code, 1, "missing template exit code")
        assert_true(not os.path.exists(output), "output should not be created on failure")
    finally:
        shutil.rmtree(tmp)


def test_collage_pages_subset():
    tmp = tempfile.mkdtemp(prefix="rongjing_test_collage_")
    try:
        input_dir = os.path.join(tmp, "input")
        _make_fake_images(input_dir, 4)
        output = os.path.join(tmp, "out.png")

        cli.collage(input_dir, output, template_name=None, rows=1, cols=2,
                    pages="1,3", json_result=False)

        assert_true(os.path.isfile(output), "output should exist with subset pages")
    finally:
        shutil.rmtree(tmp)


def test_collage_cli_json_result_via_subprocess():
    tmp = tempfile.mkdtemp(prefix="rongjing_test_collage_")
    try:
        input_dir = os.path.join(tmp, "input")
        _make_fake_images(input_dir, 2)
        output = os.path.join(tmp, "out.jpg")

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
        shutil.rmtree(tmp)


if __name__ == "__main__":
    test_collage_with_explicit_rows_cols()
    test_collage_template_and_rows_mutually_exclusive()
    test_collage_missing_template_fails_closed()
    test_collage_pages_subset()
    test_collage_cli_json_result_via_subprocess()
    print("all cli collage tests passed")
