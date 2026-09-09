"""页面翻页预览的独立缓存目录与安全过期清理。"""

from __future__ import annotations

import os
import stat
import time
import uuid
from pathlib import Path


PREVIEW_CACHE_MAX_AGE_SECONDS = 24 * 60 * 60


def _scandir_fd(root_fd: int):
    return os.scandir(root_fd)


def preview_cache_root(app_data_dir: str | os.PathLike[str]) -> Path:
    return Path(app_data_dir).expanduser() / "page_preview_cache"


def _validated_preview_root(root: str | os.PathLike[str]) -> Path:
    raw_root = os.fspath(root)
    if not raw_root:
        raise ValueError("预览缓存根目录不能为空")
    cache_root = Path(raw_root).expanduser()
    if not cache_root.is_absolute():
        raise ValueError("预览缓存根目录必须是绝对路径")
    if cache_root.name != "page_preview_cache":
        raise ValueError("预览缓存根目录缺少专用目录标识")
    return cache_root


def allocate_preview_file(root: str | os.PathLike[str]) -> Path:
    cache_root = _validated_preview_root(root)
    cache_root.mkdir(parents=True, exist_ok=True)
    while True:
        destination = cache_root / f"page-turn-preview-{uuid.uuid4().hex}.mp4"
        if not destination.exists():
            return destination


def cleanup_expired_preview_cache(
    root: str | os.PathLike[str],
    *,
    now: float | None = None,
    max_age_seconds: float = PREVIEW_CACHE_MAX_AGE_SECONDS,
) -> dict[str, int | list[str]]:
    """只清理专用根目录内超过保留期的顶层缓存项。"""
    stats: dict[str, int | list[str]] = {
        "removed": 0,
        "kept": 0,
        "skipped_symlinks": 0,
        "skipped": 0,
        "errors": [],
    }
    try:
        cache_root = _validated_preview_root(root)
    except (TypeError, ValueError, OSError) as exc:
        stats["errors"].append(str(exc))
        return stats

    if (
        os.name != "posix"
        or not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
        or os.scandir not in os.supports_fd
        or os.unlink not in os.supports_dir_fd
    ):
        stats["skipped"] += 1
        stats["errors"].append("当前平台不支持安全的目录描述符清理，已跳过")
        return stats

    cutoff = (time.time() if now is None else float(now)) - float(max_age_seconds)
    root_fd = None
    try:
        root_fd = os.open(
            cache_root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        with _scandir_fd(root_fd) as entries:
            for entry in entries:
                try:
                    if entry.is_symlink():
                        stats["skipped_symlinks"] += 1
                        continue
                    entry_stat = entry.stat(follow_symlinks=False)
                    if not stat.S_ISREG(entry_stat.st_mode) or not entry.name.endswith(".mp4"):
                        stats["skipped"] += 1
                        continue
                    if entry_stat.st_mtime >= cutoff:
                        stats["kept"] += 1
                        continue
                    os.unlink(entry.name, dir_fd=root_fd)
                    stats["removed"] += 1
                except OSError as exc:
                    stats["errors"].append(f"{entry.name}: {exc}")
    except OSError as exc:
        stats["errors"].append(str(exc))
    finally:
        if root_fd is not None:
            os.close(root_fd)
    return stats
