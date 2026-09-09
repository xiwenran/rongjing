"""macOS Core Image 曲面翻页批量渲染助手的薄包装。"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Iterable, Mapping


RIGHT_TO_LEFT = "right_to_left"
LEFT_TO_RIGHT = "left_to_right"
PAGE_TURN_DIRECTIONS = {RIGHT_TO_LEFT, LEFT_TO_RIGHT}


def normalize_direction(direction: str | None) -> str:
    value = RIGHT_TO_LEFT if direction is None else str(direction)
    if value not in PAGE_TURN_DIRECTIONS:
        raise ValueError(f"未知翻页方向：{value}")
    return value


def _default_helper_path() -> Path:
    """返回源码运行或 PyInstaller 冻结环境中的 helper 路径。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "helpers" / "page_curl" / "PageCurlRenderer"
    return Path(__file__).resolve().parents[1] / "build" / "page_curl" / "PageCurlRenderer"


DEFAULT_HELPER = _default_helper_path()


class PageCurlUnavailable(RuntimeError):
    """当前平台或本地 helper 不支持 Core Image 翻页。"""


class PageCurlRenderError(RuntimeError):
    """helper 执行或输出校验失败。"""


def availability(helper_path: str | os.PathLike[str] | None = None) -> tuple[bool, str]:
    """返回当前进程能否调用 macOS Swift helper。"""
    if sys.platform != "darwin":
        return False, "Core Image 曲面翻页仅支持 macOS"
    helper = Path(helper_path) if helper_path is not None else DEFAULT_HELPER
    if not helper.is_file():
        return False, f"Swift helper 不存在：{helper}"
    if not os.access(helper, os.X_OK):
        return False, f"Swift helper 不可执行：{helper}"
    return True, "available"


def _png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise PageCurlRenderError(f"helper 输出不是有效 PNG：{path}")
    return struct.unpack(">II", header[16:24])


def render_batch(
    source: str | os.PathLike[str],
    target: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    progress: Iterable[float],
    width: int,
    height: int,
    *,
    curl: Mapping[str, float] | None = None,
    helper_path: str | os.PathLike[str] | None = None,
    manifest_path: str | os.PathLike[str] | None = None,
    timeout: float = 120.0,
    direction: str = RIGHT_TO_LEFT,
) -> dict:
    """用一个 helper 进程批量渲染多个 progress，并校验结构和 PNG。"""
    helper = Path(helper_path) if helper_path is not None else DEFAULT_HELPER
    available, reason = availability(helper)
    if not available:
        raise PageCurlUnavailable(reason)

    source_path = Path(source).resolve()
    target_path = Path(target).resolve()
    destination = Path(output_dir).resolve()
    values = [float(value) for value in progress]
    normalized_direction = normalize_direction(direction)
    if not source_path.is_file() or not target_path.is_file():
        raise PageCurlRenderError("source 与 target 必须是存在的图片文件")
    if not values or any(value < 0.0 or value > 1.0 for value in values):
        raise PageCurlRenderError("progress 必须是非空的 0...1 数列")
    if int(width) <= 0 or int(height) <= 0:
        raise PageCurlRenderError("width 与 height 必须为正整数")

    destination.mkdir(parents=True, exist_ok=True)
    manifest = Path(manifest_path).resolve() if manifest_path else destination / f"manifest-{uuid.uuid4().hex}.json"
    payload = {
        "source": str(source_path),
        "target": str(target_path),
        "output_dir": str(destination),
        "progress": values,
        "width": int(width),
        "height": int(height),
        "curl": dict(curl or {}),
        "direction": normalized_direction,
    }
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        completed = subprocess.run(
            [str(helper), str(manifest)],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise PageCurlRenderError(f"Swift helper 渲染超时（{timeout:g} 秒）") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "无错误详情"
        raise PageCurlRenderError(f"Swift helper 返回 {completed.returncode}：{detail}")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise PageCurlRenderError(f"Swift helper 未返回有效 JSON：{exc}") from exc

    frames = result.get("frames") if isinstance(result, dict) else None
    if result.get("ok") is not True or not isinstance(result.get("filter"), str):
        raise PageCurlRenderError("Swift helper JSON 缺少 ok/filter")
    if not isinstance(frames, list) or len(frames) != len(values):
        raise PageCurlRenderError("Swift helper 返回的 PNG 数量与 progress 不一致")
    output_root = destination.resolve()
    for index, (frame, expected_progress) in enumerate(zip(frames, values)):
        if not isinstance(frame, dict) or frame.get("index") != index:
            raise PageCurlRenderError(f"第 {index} 帧结果结构无效")
        if abs(float(frame.get("progress", -1.0)) - expected_progress) > 1e-9:
            raise PageCurlRenderError(f"第 {index} 帧 progress 与请求不一致")
        png = Path(str(frame.get("path", ""))).resolve()
        if output_root not in png.parents or not png.is_file():
            raise PageCurlRenderError(f"第 {index} 帧路径无效或越出输出目录：{png}")
        if _png_size(png) != (int(width), int(height)):
            raise PageCurlRenderError(f"第 {index} 帧 PNG 尺寸不符：{png}")
    result["manifest"] = str(manifest)
    return result
