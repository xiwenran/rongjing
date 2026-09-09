#!/usr/bin/env python3
"""Focused tests for core.file_policy without filesystem cleanup side effects."""

from __future__ import annotations

import stat
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.file_policy import is_valid_input_file, natural_sort_key, scan_input_files


class FilePolicyTests(unittest.TestCase):
    def test_natural_sort(self):
        names = ["10.jpg", "1.jpg", "0.jpg"]
        self.assertEqual(sorted(names, key=natural_sort_key), ["0.jpg", "1.jpg", "10.jpg"])

    def test_input_boundaries_and_explicit_mode(self):
        kinds = {
            "0.jpg": stat.S_IFREG,
            "._0.jpg": stat.S_IFREG,
            ".hidden.jpg": stat.S_IFREG,
            "~$x.pptx": stat.S_IFREG,
            "folder.jpg": stat.S_IFDIR,
            "x.pptx": stat.S_IFREG,
        }

        def fake_lstat(path):
            name = Path(path).name
            if name not in kinds:
                raise FileNotFoundError(name)
            return SimpleNamespace(st_mode=kinds[name])

        with patch("pathlib.Path.lstat", fake_lstat):
            self.assertTrue(is_valid_input_file("0.jpg", "image"))
            self.assertFalse(is_valid_input_file("._0.jpg", "image"))
            self.assertFalse(is_valid_input_file(".hidden.jpg", "image"))
            self.assertFalse(is_valid_input_file("~$x.pptx", "ppt"))
            self.assertFalse(is_valid_input_file("folder.jpg", "image"))
            self.assertFalse(is_valid_input_file("x.pptx", "image"))
            self.assertTrue(is_valid_input_file("x.pptx", "ppt"))
            with self.assertRaises(ValueError):
                is_valid_input_file("0.jpg", "all")  # type: ignore[arg-type]

    def test_recursive_scan_prunes_hidden_directories_before_sorting(self):
        visited_hidden = False

        def fake_walk(root, *, topdown, followlinks):
            nonlocal visited_hidden
            dirs = [".hidden", "visible"]
            yield str(root), dirs, ["10.jpg", "._0.jpg", "1.jpg", "x.pptx"]
            if ".hidden" in dirs:
                visited_hidden = True
                yield str(Path(root) / ".hidden"), [], ["0.jpg"]
            if "visible" in dirs:
                yield str(Path(root) / "visible"), [], ["0.jpg"]

        def fake_valid(path, mode):
            name = Path(path).name
            return mode == "image" and not name.startswith(".") and name.endswith(".jpg")

        with (
            patch("pathlib.Path.is_dir", return_value=True),
            patch("core.file_policy.os.walk", fake_walk),
            patch("core.file_policy.is_valid_input_file", side_effect=fake_valid),
        ):
            files = scan_input_files("source", "image", recursive=True)

        self.assertFalse(visited_hidden)
        self.assertEqual(
            [str(path) for path in files],
            ["source/1.jpg", "source/10.jpg", "source/visible/0.jpg"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
