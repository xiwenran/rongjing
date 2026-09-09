"""Atomic, non-overwriting output-path allocation."""

from __future__ import annotations

import ctypes
import errno
import os
import sys
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
    safe_stem = sanitize_source_name(raw_name)

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


def _copy_file_exclusive(source_path: Path, destination_path: Path) -> None:
    descriptor = os.open(
        destination_path,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        0o666,
    )
    try:
        destination_file = os.fdopen(descriptor, "wb")
    except BaseException:
        os.close(descriptor)
        raise

    with source_path.open("rb") as source_file, destination_file:
        while chunk := source_file.read(1024 * 1024):
            destination_file.write(chunk)
        destination_file.flush()
        os.fsync(destination_file.fileno())

    source_path.unlink()


def move_file_noreplace(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
) -> Path:
    """Publish *source* without overwriting; move atomically when supported, else copy exclusively on ExFAT."""
    source_path = Path(source)
    destination_path = Path(destination)
    if sys.platform == "darwin":
        renamex_np = ctypes.CDLL(None, use_errno=True).renamex_np
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex_np.restype = ctypes.c_int
        if renamex_np(os.fsencode(source_path), os.fsencode(destination_path), 0x00000004):
            error = ctypes.get_errno()
            if error == errno.EEXIST:
                raise FileExistsError(error, os.strerror(error), str(destination_path))
            unsupported_errors = {
                errno.ENOTSUP,
                getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
            }
            if error in unsupported_errors:
                _copy_file_exclusive(source_path, destination_path)
                return destination_path
            raise OSError(error, os.strerror(error), str(source_path), None, str(destination_path))
    elif os.name == "nt":
        os.rename(source_path, destination_path)
    else:
        os.link(source_path, destination_path)
        os.unlink(source_path)
    return destination_path


def move_unique_file(
    source: str | os.PathLike[str],
    parent: str | os.PathLike[str],
    filename: str | os.PathLike[str],
    *,
    max_attempts: int = 10_000,
) -> Path:
    """Move *source* to the first available ``name``, ``name_2``, ... path."""
    output_root = _require_directory(parent)
    raw_name = Path(os.fspath(filename)).name
    suffix = Path(raw_name).suffix
    safe_stem = sanitize_source_name(raw_name)
    for attempt in range(1, max_attempts + 1):
        candidate = output_root / f"{_numbered_name(safe_stem, attempt)}{suffix}"
        try:
            return move_file_noreplace(source, candidate)
        except FileExistsError:
            continue
    raise OutputPathAllocationError(
        f"无法分配唯一输出文件：{output_root / raw_name}（已尝试 {max_attempts} 次）"
    )
