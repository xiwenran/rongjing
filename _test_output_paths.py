#!/usr/bin/env python3
"""Focused tests for atomic, non-overwriting output allocation."""

from __future__ import annotations

import errno
import os
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from core.output_paths import (
    allocate_unique_directory,
    allocate_unique_file,
    sanitize_source_name,
)


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
