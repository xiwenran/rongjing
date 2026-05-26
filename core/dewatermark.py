from __future__ import annotations

import os
import traceback
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance
from PyQt6.QtCore import QThread, pyqtSignal

from core.diversifier import strip_metadata


_IMAGE_SAVE_EXTS = {
    "JPEG": ".jpg",
    "PNG": ".png",
}


def dewatermark_image(img: Image.Image, strength: str) -> Image.Image:
    if strength not in {"low", "medium", "high"}:
        raise ValueError("strength must be 'low', 'medium', or 'high'")

    result = _strip_to_rgb(img)
    if strength == "low":
        return result

    width, height = result.size
    scale = 0.99 if strength == "medium" else 0.98
    resized_size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
    before_resize = result
    result = result.resize(resized_size, Image.Resampling.BILINEAR)
    result = result.resize((width, height), Image.Resampling.BILINEAR)
    result = _jpeg_roundtrip(result)
    if np.array_equal(np.asarray(before_resize), np.asarray(result)):
        result = _nudge_one_pixel(result)

    if strength == "medium":
        return result

    arr = np.array(result, dtype=np.int16)
    noise = np.random.randint(-2, 3, arr.shape, dtype=np.int8).astype(np.int16)
    arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
    result = Image.fromarray(arr, "RGB")

    brightness = 1.01 if np.random.random() >= 0.5 else 0.99
    contrast = 1.01 if np.random.random() >= 0.5 else 0.99
    result = ImageEnhance.Brightness(result).enhance(brightness)
    result = ImageEnhance.Contrast(result).enhance(contrast)
    return result


def _strip_to_rgb(img: Image.Image) -> Image.Image:
    source = img.convert("RGB") if img.mode != "RGB" else img
    return strip_metadata(source).convert("RGB")


def _jpeg_roundtrip(img: Image.Image) -> Image.Image:
    buffer = BytesIO()
    img.convert("RGB").save(buffer, "JPEG", quality=95)
    buffer.seek(0)
    with Image.open(buffer) as reencoded:
        return reencoded.convert("RGB")


def _nudge_one_pixel(img: Image.Image) -> Image.Image:
    arr = np.array(img, dtype=np.uint8)
    arr[0, 0, 0] = np.uint8((int(arr[0, 0, 0]) + 1) % 256)
    return Image.fromarray(arr, "RGB")


class DewatermarkRunner(QThread):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(bool, str)

    def __init__(
        self,
        file_list: list[str],
        output_dir: str,
        strength: str,
        output_format: str,
        parent=None,
    ):
        super().__init__(parent)
        self.file_list = list(file_list)
        self.output_dir = output_dir
        self.strength = strength
        self.output_format = output_format
        self._abort = False

    def abort(self):
        self._abort = True

    def run(self):
        try:
            os.makedirs(self.output_dir, exist_ok=True)
            total = len(self.file_list)
            if total == 0:
                self.finished.emit(False, "没有可处理的图片")
                return

            done = 0
            workers = max(1, min(6, (os.cpu_count() or 2) - 1))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(self._process_one, path): path
                    for path in self.file_list
                }
                for future in as_completed(futures):
                    if self._abort:
                        self.finished.emit(False, "已取消")
                        return

                    path = futures[future]
                    try:
                        out_path = future.result()
                        done += 1
                        self.progress.emit(done, total, os.path.basename(out_path))
                    except Exception as exc:
                        done += 1
                        self.progress.emit(done, total, f"跳过 {os.path.basename(path)}：{exc}")

            if self._abort:
                self.finished.emit(False, "已取消")
            else:
                self.finished.emit(True, f"完成！共处理 {done} 张图片")
        except Exception as exc:
            self.finished.emit(False, f"错误: {exc}\n{traceback.format_exc()}")

    def _process_one(self, path: str) -> str:
        if self._abort:
            raise RuntimeError("已取消")

        with Image.open(path) as img:
            result = dewatermark_image(img, self.strength)

        if self._abort:
            raise RuntimeError("已取消")

        out_path, save_format, save_kwargs = self._output_target(path)
        if save_format == "JPEG":
            result = result.convert("RGB")
        result.save(out_path, save_format, **save_kwargs)
        return out_path

    def _output_target(self, path: str) -> tuple[str, str, dict]:
        src = Path(path)
        fmt = self.output_format
        if fmt in _IMAGE_SAVE_EXTS:
            ext = _IMAGE_SAVE_EXTS[fmt]
            save_format = fmt
        else:
            ext = src.suffix.lower()
            save_format = _format_for_suffix(ext)

        out_path = Path(self.output_dir) / f"{src.stem}{ext}"
        # 防止覆盖原图：输出路径与输入路径指向同一文件时，自动加 _clean 后缀
        try:
            if out_path.resolve() == src.resolve():
                out_path = Path(self.output_dir) / f"{src.stem}_clean{ext}"
        except (OSError, RuntimeError):
            pass
        save_kwargs = _save_kwargs(save_format)
        return str(out_path), save_format, save_kwargs


def _format_for_suffix(ext: str) -> str:
    if ext in {".jpg", ".jpeg"}:
        return "JPEG"
    if ext == ".png":
        return "PNG"
    if ext == ".webp":
        return "WEBP"
    if ext in {".tif", ".tiff"}:
        return "TIFF"
    if ext == ".bmp":
        return "BMP"
    return "PNG"


def _save_kwargs(save_format: str) -> dict:
    if save_format == "JPEG":
        return {"quality": 95}
    if save_format == "WEBP":
        return {"quality": 95}
    return {}
