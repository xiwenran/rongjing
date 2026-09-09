"""PowerPoint 固定授权中转目录与精确副本清理。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import time
from dataclasses import dataclass
from pathlib import Path

STAGING_DIRECTORY_NAME = "融景Office中转"
STAGING_FILE_PREFIX = "rongjing-office-"
STAGING_MAX_AGE_SECONDS = 24 * 60 * 60
_MANIFEST_VERSION = 2
_RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_MANIFEST_RE = re.compile(rf"^{re.escape(STAGING_FILE_PREFIX)}(?P<run_id>[0-9a-f]{{32}})\.manifest\.json$")
_MAX_MANIFEST_BYTES = 64 * 1024


@dataclass(frozen=True)
class OfficeStagingRun:
    root: Path
    run_id: str
    source_copy: Path
    pdf_path: Path
    manifest_path: Path


def default_office_staging_root() -> Path:
    """返回唯一允许的生产中转根；测试通过 monkeypatch 此函数隔离。"""
    return Path.home() / "Documents" / STAGING_DIRECTORY_NAME


def _validated_root(root: str | os.PathLike[str]) -> Path:
    raw_root = os.fspath(root)
    if not raw_root:
        raise ValueError("Office 中转根目录不能为空")
    staging_root = Path(raw_root).expanduser()
    expected = default_office_staging_root().expanduser()
    if not staging_root.is_absolute() or not expected.is_absolute():
        raise ValueError("Office 中转根目录必须是绝对路径")
    if staging_root.name != STAGING_DIRECTORY_NAME:
        raise ValueError("Office 中转根目录缺少专用目录标识")
    if staging_root.resolve(strict=False) != expected.resolve(strict=False):
        raise ValueError("Office 中转根目录不是固定授权目录")
    try:
        if stat.S_ISLNK(os.lstat(staging_root).st_mode):
            raise ValueError("Office 中转根目录不能是符号链接")
    except FileNotFoundError:
        pass
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


def _lstat(name: str, root_fd: int) -> os.stat_result:
    return os.stat(name, dir_fd=root_fd, follow_symlinks=False)


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
        and stat.S_IMODE(fd_stat.st_mode) == 0o700
    )


def _open_root(root: Path, *, create: bool) -> int:
    root = _validated_root(root)
    if not _safe_fd_capabilities():
        raise RuntimeError("当前平台不支持安全的 Office 中转目录操作")
    if create:
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fchmod(root_fd, 0o700)
        if not _root_matches_fd(root, root_fd):
            raise RuntimeError("Office 中转根目录已被替换或权限不安全，已拒绝操作")
        return root_fd
    except Exception:
        os.close(root_fd)
        raise


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
    temporary_name = f".{manifest_name}.{secrets.token_hex(16)}.tmp"
    file_fd: int | None = None
    try:
        file_fd = os.open(temporary_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=root_fd)
        offset = 0
        while offset < len(encoded):
            offset += os.write(file_fd, encoded[offset:])
        os.fsync(file_fd)
        os.close(file_fd)
        file_fd = None
        os.rename(temporary_name, manifest_name, src_dir_fd=root_fd, dst_dir_fd=root_fd)
    finally:
        if file_fd is not None:
            os.close(file_fd)


def _stream_sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _file_record(name: str, item_stat: os.stat_result) -> dict[str, int | str]:
    return {"name": name, "dev": item_stat.st_dev, "ino": item_stat.st_ino, "size": item_stat.st_size}


def create_powerpoint_staging_run(
    source_file: str | os.PathLike[str], *, sequence: int = 1, run_id: str | None = None
) -> OfficeStagingRun:
    """复制 PowerPoint 原件到唯一固定根，并记录副本身份。"""
    source = Path(source_file).expanduser()
    try:
        source_stat = source.stat(follow_symlinks=False)
    except OSError as exc:
        raise FileNotFoundError(f"PowerPoint 原件不可读：{source.name}") from exc
    if not stat.S_ISREG(source_stat.st_mode):
        raise ValueError("PowerPoint 原件必须是普通文件")

    staging_root = _validated_root(default_office_staging_root())
    current_run_id = _require_run_id(run_id) if run_id is not None else secrets.token_hex(16)
    stem = _safe_stem(source)
    source_name = _run_file_name(current_run_id, sequence, stem, source.suffix.lower())
    pdf_name = _run_file_name(current_run_id, sequence, stem, ".pdf")
    manifest_name = _manifest_name(current_run_id)
    run = OfficeStagingRun(
        root=staging_root,
        run_id=current_run_id,
        source_copy=staging_root / source_name,
        pdf_path=staging_root / pdf_name,
        manifest_path=staging_root / manifest_name,
    )

    root_fd = _open_root(staging_root, create=True)
    try:
        destination_fd = os.open(source_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=root_fd)
        try:
            with source.open("rb") as source_stream:
                while True:
                    chunk = source_stream.read(1024 * 1024)
                    if not chunk:
                        break
                    offset = 0
                    while offset < len(chunk):
                        offset += os.write(destination_fd, chunk[offset:])
            os.fsync(destination_fd)
        finally:
            os.close(destination_fd)

        copied_stat = _lstat(source_name, root_fd)
        payload: dict[str, object] = {
            "version": _MANIFEST_VERSION,
            "run_id": current_run_id,
            "created_files": [_file_record(source_name, copied_stat)],
            "source_display": {"name": source.name},
            "expected_pdf_name": pdf_name,
        }
        _write_manifest_atomic(root_fd, manifest_name, payload)
        source_hash, source_size = _stream_sha256(source)
        copied_hash, copied_size = _stream_sha256(run.source_copy)
        if source_size != copied_size or source_hash != copied_hash:
            raise RuntimeError("PowerPoint 中转副本 SHA-256 校验失败")
        if not _root_matches_fd(staging_root, root_fd):
            raise RuntimeError("Office 中转根目录已被替换，已拒绝使用副本")
    except Exception:
        os.close(root_fd)
        root_fd = -1
        cleanup_powerpoint_staging_run(current_run_id)
        raise
    finally:
        if root_fd >= 0:
            os.close(root_fd)
    return run


def _read_manifest_fd(root_fd: int, manifest_name: str) -> tuple[dict[str, object], os.stat_result]:
    manifest_fd = os.open(manifest_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root_fd)
    try:
        manifest_stat = os.fstat(manifest_fd)
        if not stat.S_ISREG(manifest_stat.st_mode) or manifest_stat.st_size > _MAX_MANIFEST_BYTES:
            raise ValueError("manifest 不是有效普通文件")
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


def _validate_manifest(payload: dict[str, object], manifest_name: str, expected_run_id: str) -> list[dict[str, int | str]]:
    run_id = _require_run_id(expected_run_id)
    if manifest_name != _manifest_name(run_id):
        raise ValueError("manifest 名称与 run_id 不匹配")
    if payload.get("version") != _MANIFEST_VERSION or payload.get("run_id") != run_id:
        raise ValueError("manifest 归属信息或版本不匹配")
    records = payload.get("created_files")
    if not isinstance(records, list) or not records or len(records) > 16:
        raise ValueError("manifest 文件清单无效")
    run_prefix = f"{STAGING_FILE_PREFIX}{run_id}-"
    validated: list[dict[str, int | str]] = []
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("manifest 缺少文件身份元数据")
        name, dev, ino, size = record.get("name"), record.get("dev"), record.get("ino"), record.get("size")
        if (
            not isinstance(name, str) or not name or Path(name).name != name
            or os.path.isabs(name) or not name.startswith(run_prefix)
            or type(dev) is not int or type(ino) is not int or type(size) is not int
            or dev < 0 or ino <= 0 or size < 0 or name in seen
        ):
            raise ValueError("manifest 文件身份元数据无效")
        seen.add(name)
        validated.append({"name": name, "dev": dev, "ino": ino, "size": size})
    return validated


def refresh_powerpoint_staging_run(run: OfficeStagingRun) -> None:
    """PDF 出现后把其身份写入 manifest；路径仍须属于唯一固定根。"""
    root = _validated_root(default_office_staging_root())
    if run.root.resolve(strict=False) != root.resolve(strict=False):
        raise ValueError("Office 中转 run 不属于固定授权目录")
    root_fd = _open_root(root, create=False)
    try:
        manifest_name = _manifest_name(run.run_id)
        payload, _ = _read_manifest_fd(root_fd, manifest_name)
        records = _validate_manifest(payload, manifest_name, run.run_id)
        pdf_name = payload.get("expected_pdf_name")
        if pdf_name != run.pdf_path.name or not isinstance(pdf_name, str) or Path(pdf_name).name != pdf_name:
            raise ValueError("manifest 的 PDF 名称无效")
        try:
            pdf_stat = _lstat(pdf_name, root_fd)
        except FileNotFoundError:
            return
        if not stat.S_ISREG(pdf_stat.st_mode):
            raise RuntimeError("PowerPoint 输出 PDF 不是普通文件")
        records = [record for record in records if record["name"] != pdf_name]
        records.append(_file_record(pdf_name, pdf_stat))
        payload["created_files"] = records
        _write_manifest_atomic(root_fd, manifest_name, payload)
    finally:
        os.close(root_fd)


def _same_identity(item_stat: os.stat_result, record: dict[str, int | str]) -> bool:
    return stat.S_ISREG(item_stat.st_mode) and item_stat.st_dev == record["dev"] and item_stat.st_ino == record["ino"]


def _quarantine_and_unlink(root_fd: int, name: str, expected_dev: int, expected_ino: int) -> None:
    expected: dict[str, int | str] = {"dev": expected_dev, "ino": expected_ino}
    before = _lstat(name, root_fd)
    if not _same_identity(before, expected):
        raise RuntimeError(f"文件身份不匹配，已保留：{name}")
    quarantine = f".{STAGING_FILE_PREFIX}quarantine-{secrets.token_hex(16)}"
    os.rename(name, quarantine, src_dir_fd=root_fd, dst_dir_fd=root_fd)
    after = _lstat(quarantine, root_fd)
    if not _same_identity(after, expected):
        raise RuntimeError(f"隔离后文件身份变化，已保留：{quarantine}")
    os.unlink(quarantine, dir_fd=root_fd)


def _cleanup_manifest_fd(root: Path, root_fd: int, manifest_name: str, run_id: str) -> dict[str, int | bool | list[str]]:
    result: dict[str, int | bool | list[str]] = {"removed_files": 0, "removed_manifest": False, "missing_files": 0, "errors": []}
    try:
        if not _root_matches_fd(root, root_fd):
            raise RuntimeError("Office 中转根目录已被替换，已拒绝清理")
        payload, manifest_stat = _read_manifest_fd(root_fd, manifest_name)
        records = _validate_manifest(payload, manifest_name, run_id)
        for record in records:
            name = str(record["name"])
            try:
                _lstat(name, root_fd)
            except FileNotFoundError:
                result["missing_files"] += 1
                continue
            _quarantine_and_unlink(root_fd, name, int(record["dev"]), int(record["ino"]))
            result["removed_files"] += 1
        if not _root_matches_fd(root, root_fd):
            raise RuntimeError("Office 中转根目录已被替换，已拒绝清理 manifest")
        _quarantine_and_unlink(root_fd, manifest_name, manifest_stat.st_dev, manifest_stat.st_ino)
        result["removed_manifest"] = True
    except (OSError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        result["errors"].append(str(exc))
    return result


def cleanup_powerpoint_staging_run(run_id: str) -> dict[str, int | bool | list[str]]:
    """只在唯一固定根内按本 run 的强身份 manifest 清理。"""
    result: dict[str, int | bool | list[str]] = {"removed_files": 0, "removed_manifest": False, "missing_files": 0, "errors": []}
    try:
        staging_root = _validated_root(default_office_staging_root())
        current_run_id = _require_run_id(run_id)
        if not _safe_fd_capabilities(require_unlink=True):
            raise RuntimeError("当前平台不支持安全的目录描述符清理，已跳过")
        root_fd = _open_root(staging_root, create=False)
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        result["errors"].append(str(exc))
        return result
    try:
        return _cleanup_manifest_fd(staging_root, root_fd, _manifest_name(current_run_id), current_run_id)
    finally:
        os.close(root_fd)


def cleanup_expired_powerpoint_staging(*, now: float | None = None, max_age_seconds: float = STAGING_MAX_AGE_SECONDS) -> dict[str, int | list[str]]:
    """启动时仅清理固定根直属层中严格超过 24 小时的新版 manifest。"""
    stats: dict[str, int | list[str]] = {"removed_runs": 0, "removed_files": 0, "kept_manifests": 0, "skipped": 0, "errors": []}
    try:
        staging_root = _validated_root(default_office_staging_root())
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
                stats["skipped"] += 1
                continue
            if outcome["removed_manifest"]:
                stats["removed_runs"] += 1
                stats["removed_files"] += int(outcome["removed_files"])
    except OSError as exc:
        stats["errors"].append(str(exc))
    finally:
        os.close(root_fd)
    return stats
