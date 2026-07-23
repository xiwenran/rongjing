"""VLM 粗定位：调用本机 ark-worker 打杂端（无头 claude + 火山 Coding Plan）估计
背景图里屏幕区域的四个角，供 core/screen_detector.py 的经典算法做候选筛选/兜底。

设计原则：
- ark-worker 不可用（脚本缺失/超时/无网/解析失败）时一律返回 None，调用方必须
  完全回退到纯经典算法，不得报错、不得假装成功。
- 坐标必须按 VLM 回报的 image_size 与图片真实尺寸的比例换算（VLM 可能读到缩略图）。
- 每张图默认最多调用本模块函数若干次（由调用方控制次数），本模块自身不做重试。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Optional

PointList = list[list[float]]

_ARK_RUN_SH = os.path.expanduser("~/Echo/tools/ark-worker/run.sh")
_DEFAULT_TIMEOUT = 120


def _log_vlm_error(stage: str, detail: str) -> None:
    """复用 screen_detector 的落盘约定：写同一个 detect_error.log，便于统一排查。"""
    try:
        from main import get_data_dir
        log_path = os.path.join(get_data_dir(), "detect_error.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"===== vlm_locator: {stage} =====\n{detail}\n")
    except Exception:
        pass


def _extract_json(text: str) -> Optional[dict]:
    """从模型输出中提取最后一个合法 JSON 对象（模型有时会在 JSON 前后加说明文字）。"""
    text = text.strip()
    # 优先尝试整体解析
    try:
        return json.loads(text)
    except Exception:
        pass
    # 退而找最后一个 { ... } 片段
    matches = re.findall(r"\{[^{}]*\"points\"[^{}]*\[[^\]]*\][^{}]*\}", text, re.S)
    for candidate in reversed(matches):
        try:
            return json.loads(candidate)
        except Exception:
            continue
    return None


def _scale_points(points, reported_size, real_size) -> Optional[PointList]:
    if not reported_size or len(reported_size) != 2:
        return None
    rw, rh = float(reported_size[0]), float(reported_size[1])
    real_w, real_h = float(real_size[0]), float(real_size[1])
    if rw <= 0 or rh <= 0:
        return None
    sx = real_w / rw
    sy = real_h / rh
    out = []
    for p in points:
        if len(p) != 2:
            return None
        out.append([float(p[0]) * sx, float(p[1]) * sy])
    return out


def locate_screen_quad(image_path: str, real_size: tuple[int, int],
                        timeout: int = _DEFAULT_TIMEOUT) -> Optional[PointList]:
    """调用 ark-worker 粗定位屏幕四角，返回原图像素坐标（TL, TR, BR, BL），失败返回 None。

    real_size: (width, height)，真实图片尺寸，用于按比例换算 VLM 可能基于的缩略图坐标。
    """
    if not os.path.isfile(_ARK_RUN_SH):
        _log_vlm_error("locate_screen_quad", f"ark-worker 脚本不存在：{_ARK_RUN_SH}")
        return None
    if not os.path.isfile(image_path):
        _log_vlm_error("locate_screen_quad", f"图片不存在：{image_path}")
        return None

    prompt = (
        f"用 Read 工具读取图片 {image_path} 。"
        "图中有一块电子屏幕/显示器/黑屏笔记本屏幕区域（可能已关闭显示黑屏，也可能亮着显示内容）。"
        "请先在心里定位屏幕四条边界（注意区分屏幕本身和周围的边框/键盘/桌面/背景墙），"
        "再估计屏幕显示区域四个角在原图中的像素坐标，顺序固定为：左上、右上、右下、左下。"
        "只输出一行 JSON，不要输出任何其他文字、不要用 markdown 代码块，格式："
        '{"image_size":[w,h],"points":[[x,y],[x,y],[x,y],[x,y]]}'
    )

    try:
        proc = subprocess.run(
            [_ARK_RUN_SH, "-p", prompt, "--disallowedTools", "Write,Edit,NotebookEdit,Bash"],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        _log_vlm_error("locate_screen_quad timeout", f"image={image_path}, timeout={timeout}s")
        return None
    except Exception as e:
        _log_vlm_error("locate_screen_quad exec error", f"image={image_path}, error={e}")
        return None

    if proc.returncode != 0:
        _log_vlm_error("locate_screen_quad nonzero exit",
                        f"image={image_path}, code={proc.returncode}, stderr={proc.stderr[-2000:]}")
        return None

    data = _extract_json(proc.stdout)
    if data is None:
        _log_vlm_error("locate_screen_quad parse failed", f"image={image_path}, stdout={proc.stdout[-2000:]}")
        return None

    points = data.get("points")
    reported_size = data.get("image_size")
    if not isinstance(points, list) or len(points) != 4:
        _log_vlm_error("locate_screen_quad bad shape", f"image={image_path}, data={data}")
        return None

    scaled = _scale_points(points, reported_size, real_size)
    if scaled is None:
        _log_vlm_error("locate_screen_quad scale failed", f"image={image_path}, data={data}, real_size={real_size}")
        return None

    return scaled


def is_available() -> bool:
    """快速判断 ark-worker 是否具备调用条件（脚本存在 + 密钥文件存在），不做真实调用。"""
    if not os.path.isfile(_ARK_RUN_SH):
        return False
    key_file = os.path.expanduser("~/.config/ark/ark.env")
    return os.path.isfile(key_file)
