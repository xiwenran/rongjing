#!/usr/bin/env python3
"""Focused tests for atomic, non-overwriting output allocation."""

from __future__ import annotations

import ctypes
import errno
import os
import sys
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from core.output_paths import (
    allocate_unique_directory,
    allocate_unique_file,
    move_file_noreplace,
    move_unique_file,
    sanitize_source_name,
)


ROOT = Path(__file__).parent / "qa" / "logs" / f"output-paths-{uuid.uuid4().hex}"
ROOT.mkdir(parents=True)


class _AtomicRegistry:
    def __init__(self):
        self.paths: set[str] = set()
        self.lock = threading.Lock()
        self.next_descriptor = 10

    def mkdir(self, path, *args, **kwargs):
        key = os.fspath(path)
        with self.lock:
            if key in self.paths:
                raise FileExistsError(errno.EEXIST, "exists", key)
            self.paths.add(key)

    def open(self, path, flags, mode=0o777):
        key = os.fspath(path)
        with self.lock:
            if key in self.paths:
                raise FileExistsError(errno.EEXIST, "exists", key)
            self.paths.add(key)
            self.next_descriptor += 1
            return self.next_descriptor


class _FailingWriter:
    def __init__(self, wrapped):
        self.wrapped = wrapped

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return self.wrapped.__exit__(exc_type, exc_value, traceback)

    def write(self, _chunk):
        raise OSError(errno.EIO, "simulated write failure")

    def flush(self):
        self.wrapped.flush()

    def fileno(self):
        return self.wrapped.fileno()


class OutputPathTests(unittest.TestCase):
    def setUp(self):
        self.registry = _AtomicRegistry()
        self.root = Path("/virtual/output")
        self.is_dir_patch = patch("pathlib.Path.is_dir", return_value=True)
        self.is_dir_patch.start()

    def tearDown(self):
        self.is_dir_patch.stop()

    def test_source_name_is_cleaned(self):
        self.assertEqual(sanitize_source_name("课堂【版权】/第一课?.pptx"), "第一课")
        self.assertEqual(sanitize_source_name("CON.docx"), "CON_file")

    def test_three_directory_allocations_are_numbered(self):
        with patch("core.output_paths.os.mkdir", self.registry.mkdir):
            paths = [allocate_unique_directory(self.root, "第一课.pptx") for _ in range(3)]
        self.assertEqual(
            [path.name for path in paths],
            ["第一课", "第一课_2", "第一课_3"],
        )

    def test_existing_file_is_not_overwritten(self):
        self.registry.paths.add(str(self.root / "成品.png"))
        with (
            patch("core.output_paths.os.open", self.registry.open),
            patch("core.output_paths.os.close"),
        ):
            allocated = allocate_unique_file(self.root, "成品.png")
        self.assertEqual(allocated.name, "成品_2.png")
        self.assertIn(str(self.root / "成品.png"), self.registry.paths)

    def test_concurrent_directory_claims_are_unique(self):
        results: list[Path] = []
        results_lock = threading.Lock()

        def allocate():
            path = allocate_unique_directory(self.root, "并发来源")
            with results_lock:
                results.append(path)

        with patch("core.output_paths.os.mkdir", self.registry.mkdir):
            threads = [threading.Thread(target=allocate) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(len(results), 8)
        self.assertEqual(len(set(results)), 8)
        self.assertEqual(
            {path.name for path in results},
            {"并发来源", *(f"并发来源_{number}" for number in range(2, 9))},
        )

    def test_move_file_noreplace_to_absent_target(self):
        folder = ROOT / "move-success"
        folder.mkdir()
        source = folder / "attempt.mp4"
        destination = folder / "final.mp4"
        source.write_bytes(b"encoded")
        if sys.platform == "darwin":
            with patch("core.output_paths.os.link", side_effect=AssertionError("macOS 不应使用硬链接")):
                moved = move_file_noreplace(source, destination)
        else:
            moved = move_file_noreplace(source, destination)
        self.assertEqual(moved, destination)
        self.assertEqual(destination.read_bytes(), b"encoded")
        self.assertFalse(source.exists())

    def test_move_file_noreplace_preserves_existing_target_and_source(self):
        folder = ROOT / "move-conflict"
        folder.mkdir()
        source = folder / "attempt.mp4"
        destination = folder / "final.mp4"
        source.write_bytes(b"encoded")
        destination.write_bytes(b"external")
        with self.assertRaises(FileExistsError):
            move_file_noreplace(source, destination)
        self.assertEqual(source.read_bytes(), b"encoded")
        self.assertEqual(destination.read_bytes(), b"external")

    def _macos_enotsup_patches(self):
        renamex_np = Mock()

        def reject_rename(*_args):
            ctypes.set_errno(errno.ENOTSUP)
            return -1

        renamex_np.side_effect = reject_rename
        library = Mock(renamex_np=renamex_np)
        return (
            patch("core.output_paths.sys.platform", "darwin"),
            patch("core.output_paths.ctypes.CDLL", return_value=library),
        )

    def test_macos_enotsup_fallback_copies_and_removes_source(self):
        folder = ROOT / "move-enotsup-success"
        folder.mkdir()
        source = folder / "attempt.mp4"
        destination = folder / "final.mp4"
        source.write_bytes(b"encoded")
        platform_patch, library_patch = self._macos_enotsup_patches()

        with platform_patch, library_patch:
            moved = move_file_noreplace(source, destination)

        self.assertEqual(moved, destination)
        self.assertEqual(destination.read_bytes(), b"encoded")
        self.assertFalse(source.exists())

    def test_macos_enotsup_fallback_preserves_competing_target_and_source(self):
        folder = ROOT / "move-enotsup-conflict"
        folder.mkdir()
        source = folder / "attempt.mp4"
        destination = folder / "final.mp4"
        source.write_bytes(b"encoded")
        destination.write_bytes(b"external")
        platform_patch, library_patch = self._macos_enotsup_patches()

        with platform_patch, library_patch, self.assertRaises(FileExistsError):
            move_file_noreplace(source, destination)

        self.assertEqual(destination.read_bytes(), b"external")
        self.assertEqual(source.read_bytes(), b"encoded")

    def test_macos_enotsup_fallback_preserves_source_on_write_failure(self):
        folder = ROOT / "move-enotsup-write-failure"
        folder.mkdir()
        source = folder / "attempt.mp4"
        destination = folder / "final.mp4"
        source.write_bytes(b"encoded")
        platform_patch, library_patch = self._macos_enotsup_patches()
        real_fdopen = os.fdopen

        def failing_fdopen(descriptor, *args, **kwargs):
            return _FailingWriter(real_fdopen(descriptor, *args, **kwargs))

        with (
            platform_patch,
            library_patch,
            patch("core.output_paths.os.fdopen", side_effect=failing_fdopen),
            self.assertRaisesRegex(OSError, "simulated write failure"),
        ):
            move_file_noreplace(source, destination)

        self.assertEqual(source.read_bytes(), b"encoded")
        self.assertTrue(destination.exists())

    def test_move_unique_file_uses_next_number(self):
        folder = ROOT / "move-unique"
        folder.mkdir()
        source = folder / "attempt.mp4"
        existing = folder / "成品.mp4"
        source.write_bytes(b"encoded")
        existing.write_bytes(b"external")
        moved = move_unique_file(source, folder, "成品.mp4")
        self.assertEqual(moved.name, "成品_2.mp4")
        self.assertEqual(existing.read_bytes(), b"external")
        self.assertEqual(moved.read_bytes(), b"encoded")
        self.assertFalse(source.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
