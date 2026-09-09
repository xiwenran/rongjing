"""Shared input-file boundaries for Rongjing workflows."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Literal


InputMode = Literal["ppt", "word", "image", "video"]

PPT_EXTENSIONS = frozenset({".ppt", ".pptx"})
WORD_EXTENSIONS = frozenset({".doc", ".docx"})
IMAGE_EXTENSIONS = frozenset(
    {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
)
VIDEO_EXTENSIONS = frozenset({".avi", ".m4v", ".mkv", ".mov", ".mp4", ".wmv"})

EXTENSIONS_BY_MODE: dict[InputMode, frozenset[str]] = {
    "ppt": PPT_EXTENSIONS,
    "word": WORD_EXTENSIONS,
    "image": IMAGE_EXTENSIONS,
    "video": VIDEO_EXTENSIONS,
}

_NATURAL_PART = re.compile(r"(\d+)")


def natural_sort_key(value: str | os.PathLike[str]) -> tuple[tuple[int, object], ...]:
    """Return a case-insensitive key that compares digit runs numerically."""
    parts = _NATURAL_PART.split(os.fspath(value))
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in parts
        if part
    )


def _extensions_for(mode: InputMode) -> frozenset[str]:
    try:
        return EXTENSIONS_BY_MODE[mode]
    except (KeyError, TypeError) as exc:
        choices = ", ".join(EXTENSIONS_BY_MODE)
        raise ValueError(f"不支持的输入模式：{mode!r}；可用模式：{choices}") from exc


def _is_ignored_name(name: str) -> bool:
    return name.startswith(".") or name.startswith("~$")


def is_valid_input_file(path: str | os.PathLike[str], mode: InputMode) -> bool:
    """Check that *path* is a regular, visible file allowed by *mode*."""
    extensions = _extensions_for(mode)
    file_path = Path(path)
    if _is_ignored_name(file_path.name):
        return False
    if file_path.suffix.casefold() not in extensions:
        return False
    try:
        return stat.S_ISREG(file_path.lstat().st_mode)
    except (OSError, ValueError):
        return False


def scan_input_files(
    directory: str | os.PathLike[str],
    mode: InputMode,
    *,
    recursive: bool = False,
) -> list[Path]:
    """Scan one directory for valid inputs, pruning hidden directories."""
    _extensions_for(mode)
    root = Path(directory)
    if not root.is_dir():
        raise NotADirectoryError(f"输入目录不存在或不是目录：{root}")

    files: list[Path] = []
    if recursive:
        for current, dirs, names in os.walk(root, topdown=True, followlinks=False):
            dirs[:] = [name for name in dirs if not name.startswith(".")]
            current_path = Path(current)
            files.extend(
                path
                for name in names
                if is_valid_input_file(path := current_path / name, mode)
            )
    else:
        with os.scandir(root) as entries:
            files.extend(
                Path(entry.path)
                for entry in entries
                if is_valid_input_file(entry.path, mode)
            )

    return sorted(files, key=natural_sort_key)
