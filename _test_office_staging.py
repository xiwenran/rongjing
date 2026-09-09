import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import office_staging as staging


RUN_ID = "a" * 32


class OfficeStagingTest(unittest.TestCase):
    def _root(self, folder: str) -> Path:
        root = Path(folder) / staging.STAGING_DIRECTORY_NAME
        root.mkdir()
        return root

    def _write_manifest(self, root: Path, run_id: str, names: list[str]) -> Path:
        manifest = root / f"{staging.STAGING_FILE_PREFIX}{run_id}.manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "version": 1,
                    "run_id": run_id,
                    "created_files": names,
                    "source_display": {"name": "lesson.pptx"},
                }
            ),
            encoding="utf-8",
        )
        return manifest

    def test_copy_naming_manifest_and_original_are_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "课堂 讲义.pptx"
            source.write_bytes(b"original-ppt")
            root = self._root(folder)

            run = staging.create_powerpoint_staging_run(
                source, root=root, run_id=RUN_ID, sequence=7
            )

            self.assertEqual(run.root, root)
            self.assertEqual(run.source_copy.parent, root)
            self.assertEqual(run.pdf_path.parent, root)
            self.assertTrue(run.source_copy.name.startswith(f"rongjing-office-{RUN_ID}-0007-"))
            self.assertEqual(run.source_copy.read_bytes(), b"original-ppt")
            self.assertEqual(source.read_bytes(), b"original-ppt")
            manifest = json.loads(run.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["created_files"], [run.source_copy.name, run.pdf_path.name])
            self.assertEqual(manifest["source_display"], {"name": source.name})
            self.assertNotIn(str(source.parent), run.manifest_path.read_text(encoding="utf-8"))

    def test_cleanup_only_unlinks_exact_manifest_regular_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self._root(folder)
            copy_name = f"rongjing-office-{RUN_ID}-0001-lesson.pptx"
            pdf_name = f"rongjing-office-{RUN_ID}-0001-lesson.pdf"
            (root / copy_name).write_bytes(b"ppt")
            (root / pdf_name).write_bytes(b"pdf")
            manifest = self._write_manifest(root, RUN_ID, [copy_name, pdf_name])

            with mock.patch.object(staging, "_safe_fd_capabilities", return_value=True), \
                 mock.patch.object(staging.os, "unlink") as unlink:
                result = staging.cleanup_powerpoint_staging_run(root, RUN_ID)

            self.assertEqual(result["errors"], [])
            self.assertEqual(result["removed_files"], 2)
            self.assertTrue(result["removed_manifest"])
            self.assertEqual(
                [call.args[0] for call in unlink.call_args_list],
                [copy_name, pdf_name, manifest.name],
            )
            self.assertTrue(all("dir_fd" in call.kwargs for call in unlink.call_args_list))

    def test_cleanup_refuses_symlink_directory_foreign_and_empty_names(self):
        bad_names = [
            f"rongjing-office-{RUN_ID}-0001-link.pptx",
            f"rongjing-office-{RUN_ID}-0001-dir.pptx",
            "foreign.pptx",
            "",
            "../outside.pptx",
        ]
        for index, bad_name in enumerate(bad_names):
            with self.subTest(name=bad_name), tempfile.TemporaryDirectory() as folder:
                root = self._root(folder)
                if index == 0:
                    target = Path(folder) / "outside.pptx"
                    target.write_bytes(b"outside")
                    (root / bad_name).symlink_to(target)
                elif index == 1:
                    (root / bad_name).mkdir()
                self._write_manifest(root, RUN_ID, [bad_name])

                with mock.patch.object(staging, "_safe_fd_capabilities", return_value=True), \
                     mock.patch.object(staging.os, "unlink") as unlink:
                    result = staging.cleanup_powerpoint_staging_run(root, RUN_ID)

                self.assertTrue(result["errors"])
                unlink.assert_not_called()

    def test_cleanup_refuses_replaced_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self._root(folder)
            name = f"rongjing-office-{RUN_ID}-0001-lesson.pptx"
            (root / name).write_bytes(b"ppt")
            self._write_manifest(root, RUN_ID, [name])

            with mock.patch.object(staging, "_safe_fd_capabilities", return_value=True), \
                 mock.patch.object(staging, "_root_matches_fd", return_value=False), \
                 mock.patch.object(staging.os, "unlink") as unlink:
                result = staging.cleanup_powerpoint_staging_run(root, RUN_ID)

            self.assertTrue(result["errors"])
            unlink.assert_not_called()

    def test_startup_cleanup_only_plans_manifests_strictly_older_than_24h(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self._root(folder)
            now = 2_000_000_000.0
            old_run = "b" * 32
            boundary_run = "c" * 32
            old_name = f"rongjing-office-{old_run}-0001-old.pptx"
            boundary_name = f"rongjing-office-{boundary_run}-0001-boundary.pptx"
            (root / old_name).write_bytes(b"old")
            (root / boundary_name).write_bytes(b"boundary")
            old_manifest = self._write_manifest(root, old_run, [old_name])
            boundary_manifest = self._write_manifest(root, boundary_run, [boundary_name])
            os.utime(old_manifest, (now - 24 * 60 * 60 - 1, now - 24 * 60 * 60 - 1))
            os.utime(boundary_manifest, (now - 24 * 60 * 60, now - 24 * 60 * 60))
            (root / "orphan.pptx").write_bytes(b"orphan")

            with mock.patch.object(staging, "_safe_fd_capabilities", return_value=True), \
                 mock.patch.object(staging.os, "unlink") as unlink:
                stats = staging.cleanup_expired_powerpoint_staging(root, now=now)

            self.assertEqual(stats["removed_runs"], 1)
            self.assertEqual(stats["removed_files"], 1)
            self.assertEqual(stats["kept_manifests"], 1)
            self.assertEqual(
                [call.args[0] for call in unlink.call_args_list],
                [old_name, old_manifest.name],
            )

    def test_invalid_roots_are_rejected_without_unlink(self):
        for invalid_root in ("", ".", "relative/融景Office中转", "/tmp/not-office-staging"):
            with self.subTest(root=invalid_root), mock.patch.object(
                staging.os, "unlink"
            ) as unlink:
                result = staging.cleanup_powerpoint_staging_run(invalid_root, RUN_ID)
            self.assertTrue(result["errors"])
            unlink.assert_not_called()


if __name__ == "__main__":
    unittest.main()
