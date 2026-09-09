"""PowerPoint 固定授权中转目录与精确副本清理。"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import stat
import time
from dataclasses import dataclass
from pathlib import Path


STAGING_DIRECTORY_NAME = "融景Office中转"
STAGING_FILE_PREFIX = "rongjing-office-"
STAGING_MAX_AGE_SECONDS = 24 * 60 * 60
_MANIFEST_VERSION = 1
_RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_MANIFEST_RE = re.compile(
    rf"^{re.escape(STAGING_FILE_PREFIX)}(?P<run_id>[0-9a-f]{{32}})\.manifest\.json$"
)
_MAX_MANIFEST_BYTES = 64 * 1024


@dataclass(frozen=True)
class OfficeStagingRun:
    root: Path
    run_id: str
    source_copy: Path
    pdf_path: Path
    manifest_path: Path


def office_staging_root(home: str | os.PathLike[str] | None = None) -> Path:
    """返回固定授权根；home 参数仅用于测试或显式环境覆盖。"""
    base = Path.home() if home is None else Path(home).expanduser()
    return base / "Documents" / STAGING_DIRECTORY_NAME


def _validated_root(root: str | os.PathLike[str]) -> Path:
    raw_root = os.fspath(root)
    if not raw_root:
        raise ValueError("Office 中转根目录不能为空")
    staging_root = Path(raw_root).expanduser()
    if not staging_root.is_absolute():
        raise ValueError("Office 中转根目录必须是绝对路径")
    if staging_root.name != STAGING_DIRECTORY_NAME:
        raise ValueError("Office 中转根目录缺少专用目录标识")
    return staging_root


def _safe_fd_capabilities(*, require_scan: bool = False, require_unlink: bool = False) -> bool:
    if (
        os.name != "posix"
        or not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
        or os.open not in os.supports_dir_fd
        or os.stat not in os.supports_dir_fd
        or os.rename not in os.supports_dir_fd
    ):
        return False
    if require_scan and os.scandir not in os.supports_fd:
        return False
    if require_unlink and os.unlink not in os.supports_dir_fd:
        return False
    return True


def _root_matches_fd(root: Path, root_fd: int) -> bool:
    try:
        path_stat = os.stat(root, follow_symlinks=False)
        fd_stat = os.fstat(root_fd)
    except OSError:
        return False
    return (
        stat.S_ISDIR(path_stat.st_mode)
        and path_stat.st_dev == fd_stat.st_dev
        and path_stat.st_ino == fd_stat.st_ino
    )


def _open_root(root: Path, *, create: bool) -> int:
    if not _safe_fd_capabilities():
        raise RuntimeError("当前平台不支持安全的 Office 中转目录操作")
    if create:
        root.mkdir(parents=True, exist_ok=True)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    if not _root_matches_fd(root, root_fd):
        os.close(root_fd)
        raise RuntimeError("Office 中转根目录已被替换，已拒绝操作")
    return root_fd


def _safe_stem(source: Path) -> str:
    value = re.sub(r"[^\w.-]+", "-", source.stem, flags=re.UNICODE).strip("-._")
    return (value or "document")[:64]


def _require_run_id(run_id: str) -> str:
    if not _RUN_ID_RE.fullmatch(run_id):
        raise ValueError("无效的 Office 中转 run_id")
    return run_id


def _manifest_name(run_id: str) -> str:
    return f"{STAGING_FILE_PREFIX}{_require_run_id(run_id)}.manifest.json"


def _run_file_name(run_id: str, sequence: int, stem: str, suffix: str) -> str:
    if sequence < 1:
        raise ValueError("sequence 必须大于 0")
    return f"{STAGING_FILE_PREFIX}{_require_run_id(run_id)}-{sequence:04d}-{stem}{suffix}"


def _write_manifest_atomic(root_fd: int, manifest_name: str, payload: dict[str, object]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    temporary_name = f".{manifest_name}.{secrets.token_hex(8)}.tmp"
    file_fd: int | None = None
    try:
        file_fd = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=root_fd,
        )
        offset = 0
        while offset < len(encoded):
            offset += os.write(file_fd, encoded[offset:])
        os.fsync(file_fd)
        os.close(file_fd)
        file_fd = None
        os.rename(
            temporary_name,
            manifest_name,
            src_dir_fd=root_fd,
            dst_dir_fd=root_fd,
        )
    finally:
        if file_fd is not None:
            os.close(file_fd)


def create_powerpoint_staging_run(
    source_file: str | os.PathLike[str],
    *,
    root: str | os.PathLike[str] | None = None,
    sequence: int = 1,
    run_id: str | None = None,
) -> OfficeStagingRun:
    """复制 PowerPoint 原件，并在固定根内预登记本次 PDF。"""
    source = Path(source_file).expanduser()
    try:
        source_stat = source.stat(follow_symlinks=False)
    except OSError as exc:
        raise FileNotFoundError(f"PowerPoint 原件不可读：{source.name}") from exc
    if not stat.S_ISREG(source_stat.st_mode):
        raise ValueError("PowerPoint 原件必须是普通文件")

    staging_root = _validated_root(root if root is not None else office_staging_root())
    current_run_id = _require_run_id(run_id) if run_id is not None else secrets.token_hex(16)
    stem = _safe_stem(source)
    source_name = _run_file_name(current_run_id, sequence, stem, source.suffix.lower())
    pdf_name = _run_file_name(current_run_id, sequence, stem, ".pdf")
    manifest_name = _manifest_name(current_run_id)
    source_copy = staging_root / source_name
    pdf_path = staging_root / pdf_name
    manifest_path = staging_root / manifest_name

    root_fd = _open_root(staging_root, create=True)
    try:
        payload: dict[str, object] = {
            "version": _MANIFEST_VERSION,
            "run_id": current_run_id,
            "created_files": [source_name, pdf_name],
            "source_display": {"name": source.name},
        }
        _write_manifest_atomic(root_fd, manifest_name, payload)
        if not _root_matches_fd(staging_root, root_fd):
            raise RuntimeError("Office 中转根目录已被替换，已拒绝复制")
        shutil.copy2(source, source_copy, follow_symlinks=False)
        copied_stat = source_copy.stat(follow_symlinks=False)
        if not stat.S_ISREG(copied_stat.st_mode) or copied_stat.st_size != source_stat.st_size:
            raise RuntimeError("PowerPoint 中转副本校验失败")
        if not _root_matches_fd(staging_root, root_fd):
            raise RuntimeError("Office 中转根目录已被替换，已拒绝使用副本")
    except Exception:
        os.close(root_fd)
        root_fd = -1
        cleanup_powerpoint_staging_run(staging_root, current_run_id)
        raise
    finally:
        if root_fd >= 0:
            os.close(root_fd)

    return OfficeStagingRun(
        root=staging_root,
        run_id=current_run_id,
        source_copy=source_copy,
        pdf_path=pdf_path,
        manifest_path=manifest_path,
    )


def _read_manifest_fd(root_fd: int, manifest_name: str) -> tuple[dict[str, object], os.stat_result]:
    manifest_fd = os.open(manifest_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root_fd)
    try:
        manifest_stat = os.fstat(manifest_fd)
        if not stat.S_ISREG(manifest_stat.st_mode):
            raise ValueError("manifest 不是普通文件")
        if manifest_stat.st_size > _MAX_MANIFEST_BYTES:
            raise ValueError("manifest 超出大小限制")
        chunks: list[bytes] = []
        remaining = _MAX_MANIFEST_BYTES + 1
        while remaining > 0:
            chunk = os.read(manifest_fd, min(8192, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        if remaining == 0:
            raise ValueError("manifest 超出大小限制")
        payload = json.loads(b"".join(chunks).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("manifest 内容无效")
        return payload, manifest_stat
    finally:
        os.close(manifest_fd)


def _validate_manifest(
    payload: dict[str, object], manifest_name: str, expected_run_id: str
) -> list[str]:
    run_id = _require_run_id(expected_run_id)
    if manifest_name != _manifest_name(run_id):
        raise ValueError("manifest 名称与 run_id 不匹配")
    if payload.get("version") != _MANIFEST_VERSION or payload.get("run_id") != run_id:
        raise ValueError("manifest 归属信息不匹配")
    names = payload.get("created_files")
    if not isinstance(names, list) or not names or len(names) > 16:
        raise ValueError("manifest 文件清单无效")
    run_prefix = f"{STAGING_FILE_PREFIX}{run_id}-"
    validated: list[str] = []
    for name in names:
        if (
            not isinstance(name, str)
            or not name
            or name in {".", ".."}
            or Path(name).name != name
            or os.path.isabs(name)
            or not name.startswith(run_prefix)
        ):
            raise ValueError("manifest 含越界或不属于本 run 的文件名")
        validated.append(name)
    if len(set(validated)) != len(validated):
        raise ValueError("manifest 含重复文件名")
    return validated


def _cleanup_manifest_fd(
    root: Path,
    root_fd: int,
    manifest_name: str,
    run_id: str,
) -> dict[str, int | bool | list[str]]:
    result: dict[str, int | bool | list[str]] = {
        "removed_files": 0,
        "removed_manifest": False,
        "missing_files": 0,
        "errors": [],
    }
    try:
        if not _root_matches_fd(root, root_fd):
            raise RuntimeError("Office 中转根目录已被替换，已拒绝清理")
        payload, opened_manifest_stat = _read_manifest_fd(root_fd, manifest_name)
        names = _validate_manifest(payload, manifest_name, run_id)

        existing_names: list[str] = []
        for name in names:
            try:
                item_stat = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            except FileNotFoundError:
                result["missing_files"] += 1
                continue
            if not stat.S_ISREG(item_stat.st_mode):
                raise RuntimeError(f"拒绝清理非普通文件：{name}")
            existing_names.append(name)

        current_manifest_stat = os.stat(
            manifest_name, dir_fd=root_fd, follow_symlinks=False
        )
        if (
            not stat.S_ISREG(current_manifest_stat.st_mode)
            or current_manifest_stat.st_dev != opened_manifest_stat.st_dev
            or current_manifest_stat.st_ino != opened_manifest_stat.st_ino
            or not _root_matches_fd(root, root_fd)
        ):
            raise RuntimeError("manifest 或 Office 中转根已被替换，已拒绝清理")

        for name in existing_names:
            os.unlink(name, dir_fd=root_fd)
            result["removed_files"] += 1
        os.unlink(manifest_name, dir_fd=root_fd)
        result["removed_manifest"] = True
    except (OSError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        result["errors"].append(str(exc))
    return result


def cleanup_powerpoint_staging_run(
    root: str | os.PathLike[str], run_id: str
) -> dict[str, int | bool | list[str]]:
    """只按本 run 的 manifest 清理直属普通文件。"""
    result: dict[str, int | bool | list[str]] = {
        "removed_files": 0,
        "removed_manifest": False,
        "missing_files": 0,
        "errors": [],
    }
    try:
        staging_root = _validated_root(root)
        current_run_id = _require_run_id(run_id)
        if not _safe_fd_capabilities(require_unlink=True):
            raise RuntimeError("当前平台不支持安全的目录描述符清理，已跳过")
        root_fd = _open_root(staging_root, create=False)
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        result["errors"].append(str(exc))
        return result
    try:
        return _cleanup_manifest_fd(
            staging_root, root_fd, _manifest_name(current_run_id), current_run_id
        )
    finally:
        os.close(root_fd)


def cleanup_expired_powerpoint_staging(
    root: str | os.PathLike[str] | None = None,
    *,
    now: float | None = None,
    max_age_seconds: float = STAGING_MAX_AGE_SECONDS,
) -> dict[str, int | list[str]]:
    """启动时仅清理直属层中严格超过 24 小时的有效 manifest 所属文件。"""
    stats: dict[str, int | list[str]] = {
        "removed_runs": 0,
        "removed_files": 0,
        "kept_manifests": 0,
        "skipped": 0,
        "errors": [],
    }
    try:
        staging_root = _validated_root(root if root is not None else office_staging_root())
        if not staging_root.exists():
            return stats
        if not _safe_fd_capabilities(require_scan=True, require_unlink=True):
            raise RuntimeError("当前平台不支持安全的目录描述符清理，已跳过")
        root_fd = _open_root(staging_root, create=False)
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        stats["errors"].append(str(exc))
        stats["skipped"] += 1
        return stats

    current_time = time.time() if now is None else float(now)
    try:
        with os.scandir(root_fd) as entries:
            candidates: list[tuple[str, str]] = []
            for entry in entries:
                match = _MANIFEST_RE.fullmatch(entry.name)
                if not match:
                    stats["skipped"] += 1
                    continue
                try:
                    entry_stat = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    stats["errors"].append(f"{entry.name}: {exc}")
                    continue
                if entry.is_symlink() or not stat.S_ISREG(entry_stat.st_mode):
                    stats["skipped"] += 1
                    continue
                if current_time - entry_stat.st_mtime <= float(max_age_seconds):
                    stats["kept_manifests"] += 1
                    continue
                candidates.append((entry.name, match.group("run_id")))

        for manifest_name, run_id in candidates:
            outcome = _cleanup_manifest_fd(staging_root, root_fd, manifest_name, run_id)
            if outcome["errors"]:
                stats["errors"].extend(outcome["errors"])
                continue
            if outcome["removed_manifest"]:
                stats["removed_runs"] += 1
                stats["removed_files"] += int(outcome["removed_files"])
    except OSError as exc:
        stats["errors"].append(str(exc))
    finally:
        os.close(root_fd)
    return stats
