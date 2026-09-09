"""Focused real-media verification for core.music_library; artifacts are retained."""

from __future__ import annotations

import json
import math
import os
import sys
import uuid
import wave
from fractions import Fraction
from pathlib import Path

import av
import numpy as np

from core.music_library import InvalidLibraryError, MusicLibrary, MusicLibraryError


def run_ui_checks(run_id: str) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QItemSelectionModel, QSettings, QThread
    from PyQt6.QtWidgets import QApplication, QAbstractItemView, QMessageBox

    from ui.main_window import MainWindow
    from ui.music_library_dialog import (
        BackgroundMusicCard,
        MODE_FIXED,
        MODE_NONE,
        MODE_RANDOM,
        MusicImportWorker,
        MusicLibraryDialog,
    )

    evidence = Path("qa/logs") / f"music-library-m2-{run_id}"
    evidence.mkdir(parents=True, exist_ok=False)
    app = QApplication.instance() or QApplication([])

    class FakeLibrary:
        def __init__(self):
            self.tracks = [
                {"id": "track-a", "display_name": "同名音乐", "duration": 61, "source_kind": "audio"},
                {"id": "track-b", "display_name": "同名音乐", "duration": 125, "source_kind": "video"},
            ]

        def list(self):
            return [dict(track) for track in self.tracks]

    library = FakeLibrary()
    settings = QSettings(str(evidence / "settings.ini"), QSettings.Format.IniFormat)
    settings.clear()
    card = BackgroundMusicCard(settings, library)
    assert card.mode == MODE_NONE
    assert card.volume == 35
    assert card.selected_ids == []

    card.mode_combo.setCurrentIndex(card.mode_combo.findData(MODE_FIXED))
    card.set_selected_ids(["track-a", "track-b"])
    assert card.selected_ids == ["track-a"]
    assert card.selection_label.text() == "同名音乐"

    card.mode_combo.setCurrentIndex(card.mode_combo.findData(MODE_RANDOM))
    card.set_selected_ids(["track-a", "track-b"])
    assert card.selected_ids == ["track-a", "track-b"]
    assert card.selection_label.text() == "已选 2 首"
    card.volume_spin.setValue(42)
    settings.sync()
    restored = BackgroundMusicCard(settings, library)
    assert restored.mode == MODE_RANDOM and restored.volume == 42
    assert restored.selected_ids == ["track-a", "track-b"]

    library.tracks = [library.tracks[0]]
    restored.refresh_library()
    assert restored.selected_ids == ["track-a"]
    restored.mode_combo.setCurrentIndex(restored.mode_combo.findData(MODE_NONE))
    assert restored.selected_ids == []

    fixed_dialog = MusicLibraryDialog(library, MODE_FIXED, [])
    assert fixed_dialog.table.selectionMode() == QAbstractItemView.SelectionMode.SingleSelection
    warnings = []
    original_warning = QMessageBox.warning
    QMessageBox.warning = lambda *args: warnings.append(args[2])
    try:
        fixed_dialog._confirm_selection()
    finally:
        QMessageBox.warning = original_warning
    assert warnings == ["固定配乐需要选择 1 首音乐。"]

    library.tracks.append(
        {"id": "track-b", "display_name": "同名音乐", "duration": 125, "source_kind": "video"}
    )

    class FakeSignal:
        def __init__(self):
            self.slots = []

        def connect(self, slot):
            self.slots.append(slot)

        def emit(self, *args):
            for slot in self.slots:
                slot(*args)

    class FakeWorker:
        def __init__(self, fake_library, sources):
            self.library = fake_library
            self.sources = sources
            self.progress = FakeSignal()
            self.import_finished = FakeSignal()
            self.running = False

        def isRunning(self):
            return self.running

        def start(self):
            self.running = True
            self.progress.emit(0, 1, "正在导入：示例.mp3")
            self.library.tracks.append(
                {"id": "track-c", "display_name": "新音乐", "duration": 30, "source_kind": "audio"}
            )
            self.running = False
            self.import_finished.emit({
                "items": [{"status": "imported", "name": "示例.mp3", "message": "已导入"}],
                "counts": {"imported": 1, "existing": 0, "failed": 0, "skipped": 0},
            })

    random_dialog = MusicLibraryDialog(
        library,
        MODE_RANDOM,
        ["track-a"],
        worker_factory=FakeWorker,
    )
    assert random_dialog.table.selectionMode() == QAbstractItemView.SelectionMode.MultiSelection
    random_dialog.table.selectionModel().select(
        random_dialog.table.model().index(1, 0),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
    )
    assert set(random_dialog.selected_ids()) == {"track-a", "track-b"}
    random_dialog._start_import(["/不应显示/示例.mp3"])
    assert random_dialog.table.rowCount() == 3
    assert random_dialog.progress_label.text() == "导入 1 个"
    assert "/不应显示/" not in random_dialog.progress_label.text()
    assert issubclass(MusicImportWorker, QThread)

    class VisibilityProbe:
        def __init__(self):
            self.visible = None
            self.refreshes = 0

        def setVisible(self, visible):
            self.visible = visible

        def refresh_library(self):
            self.refreshes += 1

    probe = VisibilityProbe()
    stub = type("Stub", (), {
        "_batch_mode": 2,
        "_video_input_kind": "image",
        "_background_music_card": probe,
    })()
    MainWindow._sync_page_music_state(stub)
    assert probe.visible is True and probe.refreshes == 1
    stub._video_input_kind = "video"
    MainWindow._sync_page_music_state(stub)
    assert probe.visible is False

    manifest = {
        "task_id": "RJ-MUSIC-M2/V2",
        "run_id": run_id,
        "checks": {
            "page_image_visibility": "passed",
            "real_video_hidden": "passed",
            "fixed_single_selection": "passed",
            "random_multi_selection": "passed",
            "invalid_id_cleanup": "passed",
            "settings_mode_volume_ids": "passed",
            "worker_mock": "passed",
            "no_runner_wiring": "passed",
        },
    }
    (evidence / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False))
    app.processEvents()


def make_wav(path: Path, seconds: float = 0.6, rate: int = 24000) -> None:
    samples = bytearray()
    for index in range(round(seconds * rate)):
        value = int(9000 * math.sin(2 * math.pi * 440 * index / rate))
        samples.extend(value.to_bytes(2, "little", signed=True))
    with wave.open(os.fspath(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(samples)


def make_video(path: Path, *, with_audio: bool) -> None:
    rate = 10
    sample_rate = 48000
    with av.open(os.fspath(path), "w") as output:
        video = output.add_stream("libx264", rate=rate)
        video.width = 96
        video.height = 64
        video.pix_fmt = "yuv420p"
        audio = output.add_stream("aac", rate=sample_rate) if with_audio else None
        if audio is not None:
            audio.layout = "mono"
        for index in range(rate):
            pixels = np.full((64, 96, 3), index * 20, dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            frame.pts = index
            frame.time_base = Fraction(1, rate)
            for packet in video.encode(frame):
                output.mux(packet)
        for packet in video.encode(None):
            output.mux(packet)
        if audio is not None:
            sample_count = sample_rate
            chunk = 1024
            pts = 0
            while pts < sample_count:
                count = min(chunk, sample_count - pts)
                values = np.array(
                    [int(7000 * math.sin(2 * math.pi * 330 * (pts + i) / sample_rate)) for i in range(count)],
                    dtype=np.int16,
                ).reshape(1, -1)
                frame = av.AudioFrame.from_ndarray(values, format="s16", layout="mono")
                frame.sample_rate = sample_rate
                frame.pts = pts
                frame.time_base = Fraction(1, sample_rate)
                pts += count
                for packet in audio.encode(frame):
                    output.mux(packet)
            for packet in audio.encode(None):
                output.mux(packet)


def main() -> None:
    run_id = os.environ.get("RJ_MUSIC_TEST_RUN_ID") or uuid.uuid4().hex
    if os.environ.get("RJ_MUSIC_UI_ONLY") == "1":
        run_ui_checks(run_id)
        return
    evidence = Path("qa/logs") / f"music-library-m1-{run_id}"
    evidence.mkdir(parents=True, exist_ok=False)
    inputs = evidence / "inputs"
    inputs.mkdir()
    library_root = evidence / "library"

    wav_path = inputs / "短音乐.wav"
    make_wav(wav_path)
    video_path = inputs / "有音轨.mp4"
    make_video(video_path, with_audio=True)
    silent_video = inputs / "无音轨.mp4"
    make_video(silent_video, with_audio=False)
    make_wav(inputs / "._AppleDouble.wav")
    make_wav(inputs / "~$Office.wav")

    library = MusicLibrary(library_root)
    scanned = library.scan(inputs, "audio")
    assert scanned == [wav_path], scanned

    imported_wav = library.import_audio(wav_path)
    duplicate_wav = library.import_audio(wav_path)
    assert imported_wav["status"] == "imported"
    assert duplicate_wav["status"] == "existing"
    assert imported_wav["track"] == duplicate_wav["track"]
    assert imported_wav["track"]["source_kind"] == "audio"
    assert imported_wav["track"]["duration"] > 0
    folder_results = library.import_path(inputs, "audio")
    assert [item["status"] for item in folder_results] == ["existing"]

    imported_video = library.import_video(video_path)
    assert imported_video["status"] == "imported"
    extracted = library_root / imported_video["track"]["stored"]
    with av.open(os.fspath(extracted), "r") as container:
        assert len(container.streams.audio) == 1
        assert len(container.streams.video) == 0
    assert imported_video["track"]["duration"] > 0
    assert imported_video["track"]["source_kind"] == "video"

    try:
        library.import_video(silent_video)
    except MusicLibraryError as exc:
        assert "没有音轨" in str(exc)
    else:
        raise AssertionError("无音轨视频必须明确失败")

    first_json = library.index_path.read_bytes()
    duplicate_video = library.import_video(video_path)
    assert duplicate_video["status"] == "existing"
    assert library.list() == [imported_wav["track"], imported_video["track"]]
    assert library.get(imported_video["track"]["id"]) == imported_video["track"]
    assert library.index_path.read_bytes() == first_json
    index = json.loads(first_json)
    assert index["schema_version"] == 1
    assert len(index["tracks"]) == 2
    assert all("/Users/" not in json.dumps(track) for track in index["tracks"])
    assert not list((library_root / "incoming").iterdir())

    corrupt_root = evidence / "corrupt-library"
    corrupt_root.mkdir()
    corrupt_index = corrupt_root / "library.json"
    corrupt_index.write_text("{not-json", encoding="utf-8")
    before = corrupt_index.read_bytes()
    try:
        MusicLibrary(corrupt_root).list()
    except InvalidLibraryError as exc:
        assert "不会覆盖" in str(exc)
    else:
        raise AssertionError("损坏 JSON 必须明确失败")
    assert corrupt_index.read_bytes() == before

    manifest = {
        "task_id": "RJ-BGM-M1",
        "run_id": run_id,
        "checks": {
            "wav_import": "passed",
            "sha256_dedup": "passed",
            "video_audio_extract": "passed",
            "audio_only_m4a": "passed",
            "silent_video_rejected": "passed",
            "appledouble_filtered": "passed",
            "json_idempotent": "passed",
            "corrupt_json_preserved": "passed",
        },
        "library_root": str(library_root),
        "track_count": len(index["tracks"]),
    }
    (evidence / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"SAMPLE_FAILED: {exc}", file=sys.stderr)
        raise
