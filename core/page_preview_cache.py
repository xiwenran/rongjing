"""页面翻页预览的独立缓存目录与安全过期清理。"""

from __future__ import annotations

import os
import shutil
import time
import uuid
from pathlib import Path


PREVIEW_CACHE_MAX_AGE_SECONDS = 24 * 60 * 60


def preview_cache_root(app_data_dir: str | os.PathLike[str]) -> Path:
    return Path(app_data_dir).expanduser() / "page_preview_cache"


def allocate_preview_dir(root: str | os.PathLike[str]) -> Path:
    cache_root = Path(root).expanduser()
    cache_root.mkdir(parents=True, exist_ok=True)
    destination = cache_root / f"preview-{uuid.uuid4().hex}"
    destination.mkdir(exist_ok=False)
    return destination


def _contains_symlink(path: Path) -> bool:
    if path.is_symlink():
        return True
    if not path.is_dir():
        return False
    for current, dirs, files in os.walk(path, followlinks=False):
        current_path = Path(current)
        if any((current_path / name).is_symlink() for name in (*dirs, *files)):
            return True
    return False


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
        "errors": [],
    }
    cache_root = Path(root).expanduser()
    try:
        if cache_root.is_symlink():
            stats["errors"].append("预览缓存根目录是符号链接，已跳过")
            return stats
        cache_root.mkdir(parents=True, exist_ok=True)
        resolved_root = cache_root.resolve(strict=True)
    except OSError as exc:
        stats["errors"].append(str(exc))
        return stats

    cutoff = (time.time() if now is None else float(now)) - float(max_age_seconds)
    try:
        children = list(cache_root.iterdir())
    except OSError as exc:
        stats["errors"].append(str(exc))
        return stats

    for child in children:
        try:
            if _contains_symlink(child):
                stats["skipped_symlinks"] += 1
                continue
            resolved_child = child.resolve(strict=True)
            if resolved_child.parent != resolved_root:
                stats["errors"].append(f"缓存项越出专用根目录，已跳过：{child.name}")
                continue
            if child.stat().st_mtime >= cutoff:
                stats["kept"] += 1
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
            stats["removed"] += 1
        except OSError as exc:
            stats["errors"].append(f"{child.name}: {exc}")
    return stats
