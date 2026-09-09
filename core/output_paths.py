"""Atomic, non-overwriting output-path allocation."""

from __future__ import annotations

import os
from pathlib import Path

from core.filename_cleaner import clean_filename


class OutputPathAllocationError(RuntimeError):
    """Raised when a unique output path cannot be reserved."""


def sanitize_source_name(source: str | os.PathLike[str]) -> str:
    """Turn a source path or display name into a safe output stem."""
    raw_name = Path(os.fspath(source)).name
    stem = Path(raw_name).stem if Path(raw_name).suffix else raw_name
    return clean_filename(stem)


def _require_directory(directory: str | os.PathLike[str]) -> Path:
    parent = Path(directory)
    if not parent.is_dir():
        raise OutputPathAllocationError(f"输出根目录不存在或不是目录：{parent}")
    return parent


def _numbered_name(name: str, attempt: int) -> str:
    return name if attempt == 1 else f"{name}_{attempt}"


def allocate_unique_directory(
    parent: str | os.PathLike[str],
    source_name: str | os.PathLike[str],
    *,
    max_attempts: int = 10_000,
) -> Path:
    """Atomically create and return ``name``, ``name_2``, ... under *parent*."""
    output_root = _require_directory(parent)
    safe_name = sanitize_source_name(source_name)
    for attempt in range(1, max_attempts + 1):
        candidate = output_root / _numbered_name(safe_name, attempt)
        try:
            os.mkdir(candidate)
            return candidate
        except FileExistsError:
            continue
        except OSError as exc:
            raise OutputPathAllocationError(f"无法创建输出目录：{candidate}：{exc}") from exc
    raise OutputPathAllocationError(
        f"无法分配唯一输出目录：{output_root / safe_name}（已尝试 {max_attempts} 次）"
    )


def allocate_unique_file(
    parent: str | os.PathLike[str],
    filename: str | os.PathLike[str],
    *,
    max_attempts: int = 10_000,
) -> Path:
    """Atomically reserve a new empty file without overwriting an existing path."""
    output_root = _require_directory(parent)
    raw_name = Path(os.fspath(filename)).name
    suffix = Path(raw_name).suffix
    safe_stem = clean_filename(Path(raw_name).stem if suffix else raw_name)

    for attempt in range(1, max_attempts + 1):
        stem = _numbered_name(safe_stem, attempt)
        candidate = output_root / f"{stem}{suffix}"
        try:
            descriptor = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
        except FileExistsError:
            continue
        except OSError as exc:
            raise OutputPathAllocationError(f"无法创建输出文件：{candidate}：{exc}") from exc
        os.close(descriptor)
        return candidate

    raise OutputPathAllocationError(
        f"无法分配唯一输出文件：{output_root / raw_name}（已尝试 {max_attempts} 次）"
    )
