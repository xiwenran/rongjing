"""cli.py 的 dewatermark 子命令回归测试。

验证：走 CLI 子进程（不实例化任何 PyQt6 窗口）对带 EXIF 的临时图片做去水印，
三档强度（low/medium/high，真实档位，非 light/medium/strong）都跑通，且输出
图片的元数据（EXIF）被清空。临时图片全部用 tempfile 建在系统临时目录，
测试结束由 TemporaryDirectory 自动清理，不手动 rm 任何文件。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

from PIL import Image

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


def _make_source_image_with_exif(path: str) -> None:
    img = Image.new("RGB", (64, 48), color=(120, 60, 30))
    exif = img.getexif()
    exif[0x0131] = "TestSoftwareTag"  # Software 标签，用于确认源图确实带 EXIF
    exif[0x010E] = "TestImageDescription"
    img.save(path, "JPEG", exif=exif)


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, os.path.join(REPO_ROOT, "cli.py"), *args]
    # 不额外设置 QT_QPA_PLATFORM，证明子命令本身无需专门的无显示器环境变量兜底
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, (
        f"cli.py {' '.join(args)} 退出码非 0：{proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    return proc


def test_dewatermark_cli_strips_metadata_all_strengths():
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "src.jpg")
        _make_source_image_with_exif(src)

        with Image.open(src) as check_img:
            assert len(check_img.getexif()) > 0, "前置条件失败：源图未带 EXIF，测试无意义"

        for strength in ("low", "medium", "high"):
            out = os.path.join(tmp, f"out_{strength}.jpg")
            proc = _run_cli(
                "dewatermark", "--input", src, "--output", out,
                "--strength", strength, "--json-result",
            )
            payload = json.loads(proc.stdout.strip().splitlines()[-1])
            assert payload["strength"] == strength
            assert payload["metadata_stripped"] is True
            assert os.path.abspath(payload["output"]) == os.path.abspath(out)
            assert os.path.abspath(payload["input"]) == os.path.abspath(src)

            assert os.path.exists(out), f"strength={strength} 时输出文件未生成"
            with Image.open(out) as result_img:
                assert len(result_img.getexif()) == 0, f"strength={strength} 时 EXIF 未清除"
                assert not result_img.info.get("exif"), f"strength={strength} 时 .info 仍残留 exif 字节串"


def test_dewatermark_cli_png_output_strips_metadata():
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "src.jpg")
        _make_source_image_with_exif(src)
        out = os.path.join(tmp, "out.png")

        _run_cli("dewatermark", "--input", src, "--output", out, "--strength", "medium")

        with Image.open(out) as result_img:
            assert result_img.format == "PNG"
            assert len(result_img.getexif()) == 0


def test_dewatermark_cli_help_runs_without_gui_display():
    proc = _run_cli("dewatermark", "--help")
    assert "--strength" in proc.stdout
    assert "low" in proc.stdout and "medium" in proc.stdout and "high" in proc.stdout


if __name__ == "__main__":
    test_dewatermark_cli_strips_metadata_all_strengths()
    test_dewatermark_cli_png_output_strips_metadata()
    test_dewatermark_cli_help_runs_without_gui_display()
    print("全部通过")
