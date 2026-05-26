from __future__ import annotations

import os
import traceback
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageEnhance
from PyQt6.QtCore import QThread, pyqtSignal

from core.diversifier import strip_metadata


_IMAGE_SAVE_EXTS = {
    "JPEG": ".jpg",
    "PNG": ".png",
}


def dewatermark_image(
    img: Image.Image,
    strength: str,
    note: Callable[[str], None] | None = None,
) -> Image.Image:
    if strength not in {"low", "medium", "high"}:
        raise ValueError("strength must be 'low', 'medium', or 'high'")

    if strength == "low":
        return _strip_metadata_preserve_mode(img)

    result, alpha = _prepare_processing_image(img, note)

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
        if alpha is not None:
            result = _merge_alpha(result, alpha)
        return result

    arr = np.array(result, dtype=np.int16)
    noise = np.random.randint(-2, 3, arr.shape, dtype=np.int8).astype(np.int16)
    arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
    result = Image.fromarray(arr, "RGB")

    brightness = 1.01 if np.random.random() >= 0.5 else 0.99
    contrast = 1.01 if np.random.random() >= 0.5 else 0.99
    result = ImageEnhance.Brightness(result).enhance(brightness)
    result = ImageEnhance.Contrast(result).enhance(contrast)
    if alpha is not None:
        result = _merge_alpha(result, alpha)
    return result


def _strip_metadata_preserve_mode(img: Image.Image) -> Image.Image:
    clean = Image.new(img.mode, img.size)
    clean.paste(img)
    return clean


def _prepare_processing_image(
    img: Image.Image,
    note: Callable[[str], None] | None,
) -> tuple[Image.Image, Image.Image | None]:
    clean = _strip_metadata_preserve_mode(img)
    if clean.mode == "RGB":
        return clean, None
    if clean.mode == "L":
        return clean.convert("RGB"), None
    if clean.mode in {"RGBA", "LA", "PA"}:
        rgba = clean.convert("RGBA")
        return rgba.convert("RGB"), rgba.getchannel("A")

    if note is not None:
        note(f"mode {clean.mode} -> RGB")
    return strip_metadata(clean.convert("RGB")).convert("RGB"), None


def _merge_alpha(rgb_img: Image.Image, alpha: Image.Image) -> Image.Image:
    result = rgb_img.convert("RGBA")
    result.putalpha(alpha)
    return result


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


def _prepare_for_save(img: Image.Image, save_format: str) -> Image.Image:
    if save_format != "JPEG":
        return img
    return _flatten_to_white(img)


def _flatten_to_white(img: Image.Image) -> Image.Image:
    rgba = img.convert("RGBA")
    background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    flattened = Image.alpha_composite(background, rgba)
    return flattened.convert("RGB")


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

            output_plan = self._build_output_plan()
            done = 0
            success = 0
            failed = 0
            skipped = 0
            failed_names: list[str] = []
            workers = max(1, min(6, (os.cpu_count() or 2) - 1))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(self._process_one, path, target): path
                    for path, target in zip(self.file_list, output_plan, strict=False)
                }
                for future in as_completed(futures):
                    if self._abort:
                        skipped = total - done
                        self.finished.emit(False, self._cancel_message(success, failed, skipped))
                        return

                    path = futures[future]
                    try:
                        out_path, note_msg = future.result()
                        done += 1
                        success += 1
                        msg = os.path.basename(out_path)
                        if note_msg:
                            msg = f"{msg}（{note_msg}）"
                        self.progress.emit(done, total, msg)
                    except Exception as exc:
                        done += 1
                        if self._abort and str(exc) == "已取消":
                            skipped += 1
                            self.progress.emit(done, total, f"已取消 {os.path.basename(path)}")
                        else:
                            failed += 1
                            failed_names.append(os.path.basename(path))
                            self.progress.emit(done, total, f"失败 {os.path.basename(path)}：{exc}")

            if self._abort:
                skipped += max(0, total - done)
                self.finished.emit(False, self._cancel_message(success, failed, skipped))
            elif failed == 0:
                self.finished.emit(True, f"完成！{success}/{total} 张全部成功")
            elif success > 0:
                self.finished.emit(
                    True,
                    f"完成 {success}/{total}，{failed} 张失败：{_summarize_failed_names(failed_names)}",
                )
            else:
                self.finished.emit(
                    False,
                    f"失败！全部 {failed} 张处理失败：{_summarize_failed_names(failed_names)}",
                )
        except Exception as exc:
            self.finished.emit(False, f"错误: {exc}\n{traceback.format_exc()}")

    def _process_one(self, path: str, target: tuple[str, str, dict]) -> tuple[str, str]:
        if self._abort:
            raise RuntimeError("已取消")

        notes: list[str] = []
        with Image.open(path) as img:
            result = dewatermark_image(img, self.strength, note=notes.append)

        if self._abort:
            raise RuntimeError("已取消")

        out_path, save_format, save_kwargs = target
        result = _prepare_for_save(result, save_format)
        result.save(out_path, save_format, **save_kwargs)
        return out_path, "；".join(notes)

    def _build_output_plan(self) -> list[tuple[str, str, dict]]:
        allocated: set[Path] = set()
        source_paths = self._source_path_set()
        plan: list[tuple[str, str, dict]] = []
        total = len(self.file_list)
        for path in self.file_list:
            try:
                candidate, save_format, save_kwargs = self._base_output_target(path)
                reserved = source_paths - {_safe_resolve(Path(path))}
                final_path = self._dedupe_output_path(candidate, allocated, reserved)
            except (OSError, RuntimeError) as exc:
                final_path, save_format, save_kwargs = self._legacy_output_target(path, clean_fallback=True)
                self.progress.emit(0, total, f"预检警告 {os.path.basename(path)}：{exc}")
            allocated.add(_safe_resolve(final_path))
            plan.append((str(final_path), save_format, save_kwargs))
        return plan

    def _source_path_set(self) -> set[Path]:
        resolved: set[Path] = set()
        for path in self.file_list:
            resolved.add(_safe_resolve(Path(path)))
        return resolved

    def _base_output_target(self, path: str) -> tuple[Path, str, dict]:
        src = Path(path)
        fmt = self.output_format
        if fmt in _IMAGE_SAVE_EXTS:
            ext = _IMAGE_SAVE_EXTS[fmt]
            save_format = fmt
        else:
            ext = src.suffix.lower()
            save_format = _format_for_suffix(ext)

        out_path = Path(self.output_dir) / f"{src.stem}{ext}"
        if _safe_resolve(out_path) == _safe_resolve(src):
            out_path = Path(self.output_dir) / f"{src.stem}_clean{ext}"
        save_kwargs = _save_kwargs(save_format)
        return out_path, save_format, save_kwargs

    def _legacy_output_target(self, path: str, clean_fallback: bool = False) -> tuple[Path, str, dict]:
        src = Path(path)
        fmt = self.output_format
        if fmt in _IMAGE_SAVE_EXTS:
            ext = _IMAGE_SAVE_EXTS[fmt]
            save_format = fmt
        else:
            ext = src.suffix.lower()
            save_format = _format_for_suffix(ext)
        out_path = Path(self.output_dir) / f"{src.stem}{ext}"
        if clean_fallback and "_clean" not in out_path.stem:
            out_path = out_path.with_name(f"{out_path.stem}_clean{out_path.suffix}")
        return out_path, save_format, _save_kwargs(save_format)

    def _dedupe_output_path(
        self,
        candidate: Path,
        allocated: set[Path],
        reserved: set[Path],
    ) -> Path:
        final_path = candidate
        serial = 1
        while True:
            resolved = _safe_resolve(final_path)
            if resolved not in allocated and resolved not in reserved:
                return final_path
            final_path = candidate.with_name(f"{candidate.stem}_{serial}{candidate.suffix}")
            serial += 1

    def _cancel_message(self, success: int, failed: int, skipped: int) -> str:
        return f"已取消（成功 {success}，失败 {failed}，跳过 {skipped}）"


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


def _safe_resolve(path: Path) -> Path:
    return path.resolve(strict=False)


def _summarize_failed_names(names: list[str], limit: int = 5) -> str:
    if not names:
        return "无"
    if len(names) <= limit:
        return "、".join(names)
    visible = "、".join(names[:limit])
    return f"{visible} 等共 {len(names)} 张"
