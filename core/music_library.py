"""Persistent music-library storage and audio extraction for Rongjing."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import threading
import uuid
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Callable, Iterable, Iterator, Literal

import av

from core.file_policy import VIDEO_EXTENSIONS, is_valid_input_file, natural_sort_key, scan_input_files


SCHEMA_VERSION = 1
AUDIO_EXTENSIONS = frozenset({".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav"})
SourceKind = Literal["audio", "video"]


class MusicLibraryError(RuntimeError):
    """Raised when a music-library operation cannot be completed safely."""


class InvalidLibraryError(MusicLibraryError):
    """Raised when library.json is corrupt or has an unsupported shape."""


_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}


def default_music_root() -> Path:
    """Return the platform-specific persistent music-library root."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "融景" / "music"
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise MusicLibraryError("Windows 环境缺少 APPDATA，无法确定音乐库目录")
        return Path(appdata) / "融景" / "music"
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data_home).expanduser() if xdg_data_home else Path.home() / ".local" / "share"
    return base / "融景" / "music"


def _lock_for(root: Path) -> threading.RLock:
    key = os.path.normcase(os.path.abspath(os.fspath(root)))
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _is_visible_regular_file(path: Path) -> bool:
    if path.name.startswith(".") or path.name.startswith("~$"):
        return False
    try:
        return path.is_file() and not path.is_symlink()
    except OSError:
        return False


def is_audio_input_file(path: str | os.PathLike[str]) -> bool:
    """Return whether *path* is a supported visible regular audio file."""
    file_path = Path(path)
    return _is_visible_regular_file(file_path) and file_path.suffix.casefold() in AUDIO_EXTENSIONS


def scan_audio_files(directory: str | os.PathLike[str], *, recursive: bool = False) -> list[Path]:
    """Scan a folder for supported audio, pruning hidden directories."""
    root = Path(directory)
    if not root.is_dir():
        raise NotADirectoryError(f"输入目录不存在或不是目录：{root}")
    files: list[Path] = []
    if recursive:
        for current, dirs, names in os.walk(root, topdown=True, followlinks=False):
            dirs[:] = [name for name in dirs if not name.startswith(".")]
            current_path = Path(current)
            files.extend(
                path for name in names if is_audio_input_file(path := current_path / name)
            )
    else:
        with os.scandir(root) as entries:
            files.extend(Path(entry.path) for entry in entries if is_audio_input_file(entry.path))
    return sorted(files, key=natural_sort_key)


def scan_music_inputs(
    source: str | os.PathLike[str],
    source_kind: SourceKind,
    *,
    recursive: bool = False,
) -> list[Path]:
    """Normalize a supported single file or folder into an import list."""
    if source_kind not in ("audio", "video"):
        raise ValueError(f"不支持的音乐来源类型：{source_kind!r}")
    path = Path(source)
    if path.is_dir():
        return (
            scan_audio_files(path, recursive=recursive)
            if source_kind == "audio"
            else scan_input_files(path, "video", recursive=recursive)
        )
    valid = is_audio_input_file(path) if source_kind == "audio" else is_valid_input_file(path, "video")
    if not valid:
        allowed = AUDIO_EXTENSIONS if source_kind == "audio" else VIDEO_EXTENSIONS
        raise MusicLibraryError(
            f"不是可导入的{ '音频' if source_kind == 'audio' else '视频' }文件：{path}；"
            f"支持：{', '.join(sorted(allowed))}"
        )
    return [path]


def classify_music_input(path: str | os.PathLike[str]) -> SourceKind | None:
    """Return the supported music source kind for one visible regular file."""
    file_path = Path(path)
    if is_audio_input_file(file_path):
        return "audio"
    if is_valid_input_file(file_path, "video"):
        return "video"
    return None


def _scan_mixed_folder(directory: Path, *, recursive: bool) -> tuple[list[tuple[Path, SourceKind]], list[dict]]:
    if not directory.is_dir():
        raise NotADirectoryError(f"输入目录不存在或不是目录：{directory}")
    accepted: list[tuple[Path, SourceKind]] = []
    skipped: list[dict] = []

    def inspect(path: Path) -> None:
        kind = classify_music_input(path)
        if kind is not None:
            accepted.append((path, kind))
        else:
            skipped.append({
                "status": "skipped",
                "name": path.name,
                "source_kind": None,
                "message": "已跳过不支持、隐藏或临时文件",
            })

    if recursive:
        for current, dirs, names in os.walk(directory, topdown=True, followlinks=False):
            dirs[:] = [name for name in dirs if not name.startswith(".")]
            current_path = Path(current)
            for name in names:
                inspect(current_path / name)
    else:
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_file(follow_symlinks=False):
                    inspect(Path(entry.path))
    accepted.sort(key=lambda item: natural_sort_key(item[0]))
    skipped.sort(key=lambda item: natural_sort_key(item["name"]))
    return accepted, skipped


def _short_import_error(exc: Exception) -> str:
    message = str(exc)
    if isinstance(exc, InvalidLibraryError):
        return "音乐库索引损坏，已停止导入"
    for marker in ("没有音轨", "无法解码", "时长无效", "无法读取音频", "音轨提取失败"):
        if marker in message:
            return marker
    return "文件损坏或无法读取"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_basename(name: str, *, fallback: str) -> str:
    cleaned = re.sub(r"[^\w.\-]+", "_", Path(name).name, flags=re.UNICODE).strip("._")
    return (cleaned or fallback)[:120]


def _duration_seconds(container: av.container.InputContainer, stream: av.audio.stream.AudioStream) -> float:
    if stream.duration is not None and stream.time_base is not None:
        duration = float(stream.duration * stream.time_base)
        if duration > 0:
            return duration
    if container.duration is not None:
        duration = float(container.duration / av.time_base)
        if duration > 0:
            return duration
    return 0.0


def _probe_audio(path: Path, *, require_audio_only: bool = False) -> tuple[float, str]:
    try:
        with av.open(os.fspath(path), mode="r") as container:
            audio_streams = list(container.streams.audio)
            if not audio_streams:
                raise MusicLibraryError(f"文件没有音轨：{path.name}")
            if require_audio_only and list(container.streams.video):
                raise MusicLibraryError(f"提取结果意外包含视频轨：{path.name}")
            stream = audio_streams[0]
            duration = _duration_seconds(container, stream)
            decoded_duration = 0.0
            decoded = False
            for frame in container.decode(stream):
                decoded = True
                if frame.sample_rate and frame.samples:
                    decoded_duration += frame.samples / frame.sample_rate
            if not decoded:
                raise MusicLibraryError(f"音轨无法解码：{path.name}")
            duration = max(duration, decoded_duration)
            if duration <= 0:
                raise MusicLibraryError(f"音轨时长无效：{path.name}")
            codec = stream.codec_context.name or "unknown"
            return duration, codec
    except MusicLibraryError:
        raise
    except (av.error.FFmpegError, OSError, ValueError) as exc:
        raise MusicLibraryError(f"无法读取音频：{path.name}（{exc}）") from exc


class MusicLibrary:
    """A small persistent music library with atomic metadata updates."""

    def __init__(self, root: str | os.PathLike[str] | None = None):
        self.root = Path(root).expanduser() if root is not None else default_music_root()
        self.incoming = self.root / "incoming"
        self.index_path = self.root / "library.json"
        self._lock = _lock_for(self.root)
        self.incoming.mkdir(parents=True, exist_ok=True)

    def _read_index(self) -> dict:
        if not self.index_path.exists():
            return {"schema_version": SCHEMA_VERSION, "tracks": []}
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvalidLibraryError(f"音乐库索引损坏，已停止且不会覆盖：{self.index_path}（{exc}）") from exc
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            raise InvalidLibraryError(f"音乐库索引 schema 不受支持：{self.index_path}")
        if not isinstance(data.get("tracks"), list):
            raise InvalidLibraryError(f"音乐库索引 tracks 字段无效：{self.index_path}")
        return data

    def _write_index(self, data: dict) -> None:
        temporary = self.incoming / f"library-{uuid.uuid4().hex}.json.tmp"
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.index_path)
        except Exception:
            if temporary.exists():
                temporary.unlink()
            raise

    def list(self) -> list[dict]:
        """Return metadata for all tracks in import order."""
        with self._lock:
            return [dict(track) for track in self._read_index()["tracks"]]

    def get(self, track_id: str) -> dict | None:
        """Return one track by id, or None when it is absent."""
        with self._lock:
            for track in self._read_index()["tracks"]:
                if track.get("id") == track_id:
                    return dict(track)
        return None

    def resolve_tracks(self, track_ids: Iterable[str]) -> list[dict]:
        """Resolve stable ids to playable files without accepting paths outside the library."""
        requested = list(track_ids)
        with self._lock:
            tracks_by_id = {
                track.get("id"): dict(track)
                for track in self._read_index()["tracks"]
                if track.get("id")
            }
        resolved = []
        root = self.root.resolve()
        for track_id in requested:
            track = tracks_by_id.get(track_id)
            if track is None:
                raise MusicLibraryError(f"配乐 ID 已失效：{track_id}")
            stored = track.get("stored")
            if not isinstance(stored, str) or not stored:
                raise InvalidLibraryError(f"配乐文件字段无效：{track_id}")
            try:
                path = (root / stored).resolve(strict=True)
                path.relative_to(root)
            except (OSError, ValueError) as exc:
                raise InvalidLibraryError(f"配乐文件不可用：{track_id}") from exc
            if not path.is_file() or path.is_symlink():
                raise InvalidLibraryError(f"配乐文件不可用：{track_id}")
            try:
                duration = float(track.get("duration", 0))
            except (TypeError, ValueError) as exc:
                raise InvalidLibraryError(f"配乐时长无效：{track_id}") from exc
            if duration <= 0:
                raise MusicLibraryError(f"配乐时长无效：{track.get('display_name') or track_id}")
            track["path"] = str(path)
            resolved.append(track)
        return resolved

    def scan(
        self,
        source: str | os.PathLike[str],
        source_kind: SourceKind,
        *,
        recursive: bool = False,
    ) -> list[Path]:
        return scan_music_inputs(source, source_kind, recursive=recursive)

    def import_path(
        self,
        source: str | os.PathLike[str],
        source_kind: SourceKind,
        *,
        recursive: bool = False,
    ) -> list[dict]:
        """Import one supported file or every matching file in a folder."""
        return [
            self.import_source(path, source_kind)
            for path in self.scan(source, source_kind, recursive=recursive)
        ]

    def import_many(
        self,
        sources: Iterable[str | os.PathLike[str]],
        *,
        recursive: bool = True,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> dict:
        """Import mixed audio/video selections and return per-item UI-safe results."""
        pending: list[tuple[Path, SourceKind]] = []
        results: list[dict] = []
        for raw_source in sources:
            source = Path(raw_source)
            if source.is_dir():
                accepted, skipped = _scan_mixed_folder(source, recursive=recursive)
                pending.extend(accepted)
                results.extend(skipped)
                continue
            kind = classify_music_input(source)
            if kind is None:
                results.append({
                    "status": "skipped",
                    "name": source.name or "未命名文件",
                    "source_kind": None,
                    "message": "已跳过不支持、隐藏或临时文件",
                })
            else:
                pending.append((source, kind))

        total = len(pending)
        for done, (source, source_kind) in enumerate(pending, start=1):
            if progress is not None:
                progress(done - 1, total, f"正在导入：{source.name}")
            try:
                imported = self.import_source(source, source_kind)
                results.append({
                    "status": imported["status"],
                    "name": source.name,
                    "source_kind": source_kind,
                    "message": "已导入" if imported["status"] == "imported" else "库中已存在",
                    "track": imported["track"],
                })
            except MusicLibraryError as exc:
                results.append({
                    "status": "failed",
                    "name": source.name,
                    "source_kind": source_kind,
                    "message": _short_import_error(exc),
                })
            if progress is not None:
                progress(done, total, f"已处理：{source.name}")

        counts = {
            status: sum(item["status"] == status for item in results)
            for status in ("imported", "existing", "failed", "skipped")
        }
        return {"items": results, "counts": counts}

    def import_source(
        self,
        source: str | os.PathLike[str],
        source_kind: SourceKind,
    ) -> dict:
        """Import one supported audio file or one video's complete first audio track."""
        paths = scan_music_inputs(source, source_kind)
        if len(paths) != 1:
            raise MusicLibraryError("import_source 只接受单个文件；文件夹请先调用 scan")
        source_path = paths[0]
        with self._lock:
            index = self._read_index()
            if source_kind == "audio":
                duration, codec = _probe_audio(source_path)
                sha256 = _sha256(source_path)
                incoming = self._copy_to_incoming(source_path)
                extension = source_path.suffix.casefold()
            else:
                incoming, duration, codec = self._extract_video_audio(source_path)
                sha256 = _sha256(incoming)
                extension = ".m4a"

            existing = next((track for track in index["tracks"] if track.get("sha256") == sha256), None)
            if existing is not None:
                self._discard_incoming(incoming)
                stored = self.root / existing.get("stored", "")
                if not stored.is_file():
                    raise InvalidLibraryError(f"音乐库索引指向不存在的文件：{existing.get('stored')}")
                return {"status": "existing", "track": dict(existing)}

            display_name = source_path.stem
            safe_name = _safe_basename(
                f"{source_path.stem}{extension}",
                fallback=f"track{extension}",
            )
            relative = f"{sha256[:16]}-{safe_name}"
            destination = self.root / relative
            self._publish_noreplace(incoming, destination, sha256)
            track = {
                "id": sha256,
                "display_name": display_name,
                "stored": relative,
                "sha256": sha256,
                "duration": round(float(duration), 6),
                "codec": codec,
                "source_kind": source_kind,
                "imported_at": datetime.now(timezone.utc).isoformat(),
            }
            index["tracks"].append(track)
            self._write_index(index)
            return {"status": "imported", "track": dict(track)}

    def import_audio(self, source: str | os.PathLike[str]) -> dict:
        return self.import_source(source, "audio")

    def import_video(self, source: str | os.PathLike[str]) -> dict:
        return self.import_source(source, "video")

    def _copy_to_incoming(self, source: Path) -> Path:
        destination = self.incoming / f"audio-{uuid.uuid4().hex}{source.suffix.casefold()}"
        try:
            with source.open("rb") as src, destination.open("xb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
                dst.flush()
                os.fsync(dst.fileno())
            if _sha256(source) != _sha256(destination):
                raise MusicLibraryError(f"音频复制校验失败：{source.name}")
            return destination
        except Exception:
            if destination.exists():
                destination.unlink()
            raise

    def _extract_video_audio(self, source: Path) -> tuple[Path, float, str]:
        destination = self.incoming / f"video-{uuid.uuid4().hex}.m4a"
        try:
            with av.open(os.fspath(source), mode="r") as input_container:
                audio_streams = list(input_container.streams.audio)
                if not audio_streams:
                    raise MusicLibraryError(f"视频没有音轨：{source.name}")
                input_stream = audio_streams[0]
                sample_rate = input_stream.codec_context.sample_rate or 44100
                input_layout = input_stream.codec_context.layout
                layout_name = input_layout.name if input_layout is not None else "stereo"
                with av.open(os.fspath(destination), mode="w", format="mp4") as output_container:
                    output_stream = output_container.add_stream("aac", rate=sample_rate)
                    output_stream.layout = layout_name
                    resampler = av.AudioResampler(format="fltp", layout=layout_name, rate=sample_rate)
                    audio_time_base = Fraction(1, sample_rate)
                    audio_pts = 0
                    for frame in input_container.decode(input_stream):
                        for converted in _resampled_frames(resampler.resample(frame)):
                            converted.pts = audio_pts
                            converted.time_base = audio_time_base
                            audio_pts += converted.samples
                            for packet in output_stream.encode(converted):
                                output_container.mux(packet)
                    for converted in _resampled_frames(resampler.resample(None)):
                        converted.pts = audio_pts
                        converted.time_base = audio_time_base
                        audio_pts += converted.samples
                        for packet in output_stream.encode(converted):
                            output_container.mux(packet)
                    for packet in output_stream.encode(None):
                        output_container.mux(packet)
            duration, codec = _probe_audio(destination, require_audio_only=True)
            return destination, duration, codec
        except MusicLibraryError:
            if destination.exists():
                destination.unlink()
            raise
        except (av.error.FFmpegError, OSError, ValueError) as exc:
            if destination.exists():
                destination.unlink()
            raise MusicLibraryError(f"视频音轨提取失败：{source.name}（{exc}）") from exc

    def _publish_noreplace(self, incoming: Path, destination: Path, expected_sha256: str) -> None:
        try:
            try:
                os.link(incoming, destination)
            except FileExistsError:
                if _sha256(destination) != expected_sha256:
                    raise MusicLibraryError(f"音乐库目标文件冲突，未覆盖：{destination.name}")
            except OSError as exc:
                raise MusicLibraryError(f"音乐库无法原子发布且未覆盖目标：{destination.name}（{exc}）") from exc
            if _sha256(destination) != expected_sha256:
                raise MusicLibraryError(f"音乐库发布校验失败：{destination.name}")
        finally:
            self._discard_incoming(incoming)

    @staticmethod
    def _discard_incoming(path: Path) -> None:
        if path.exists():
            path.unlink()


def _resampled_frames(value) -> Iterator[av.AudioFrame]:
    if value is None:
        return iter(())
    if isinstance(value, list):
        return iter(value)
    return iter((value,))
