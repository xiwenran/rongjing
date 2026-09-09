import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import office_staging as staging

RUN_ID = "a" * 32


class OfficeStagingTest(unittest.TestCase):
    def _fixed_root(self, folder: str) -> Path:
        return Path(folder) / staging.STAGING_DIRECTORY_NAME

    def test_production_signatures_have_no_root_override(self):
        self.assertNotIn("root", inspect.signature(staging.create_powerpoint_staging_run).parameters)
        self.assertNotIn("root", inspect.signature(staging.cleanup_powerpoint_staging_run).parameters)
        self.assertNotIn("root", inspect.signature(staging.cleanup_expired_powerpoint_staging).parameters)

    def test_copy_sha_identity_and_original_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "课堂讲义.pptx"
            source.write_bytes(b"original-ppt")
            root = self._fixed_root(folder)
            with mock.patch.object(staging, "default_office_staging_root", return_value=root):
                run = staging.create_powerpoint_staging_run(source, run_id=RUN_ID)
            payload = json.loads(run.manifest_path.read_text(encoding="utf-8"))
            record = payload["created_files"][0]
            copied_stat = run.source_copy.stat()
            self.assertEqual(payload["run_id"], RUN_ID)
            self.assertEqual((record["dev"], record["ino"], record["size"]), (copied_stat.st_dev, copied_stat.st_ino, copied_stat.st_size))
            self.assertEqual(staging._stream_sha256(source), staging._stream_sha256(run.source_copy))
            self.assertEqual(source.read_bytes(), b"original-ppt")
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)

    def test_fixed_root_rejects_other_absolute_root(self):
        with tempfile.TemporaryDirectory() as folder:
            fixed = self._fixed_root(folder)
            foreign = Path(folder) / "other" / staging.STAGING_DIRECTORY_NAME
            with mock.patch.object(staging, "default_office_staging_root", return_value=fixed):
                with self.assertRaisesRegex(ValueError, "固定授权目录"):
                    staging._validated_root(foreign)

    def test_quarantine_identity_change_never_unlinks(self):
        before = mock.Mock(st_mode=0o100600, st_dev=1, st_ino=2)
        replaced = mock.Mock(st_mode=0o100600, st_dev=1, st_ino=3)
        with mock.patch.object(staging, "_lstat", side_effect=[before, replaced]), mock.patch.object(staging.os, "rename") as rename, mock.patch.object(staging.os, "unlink") as unlink:
            with self.assertRaisesRegex(RuntimeError, "隔离后文件身份变化"):
                staging._quarantine_and_unlink(9, "source.pptx", 1, 2)
        rename.assert_called_once()
        unlink.assert_not_called()

    def test_quarantine_exact_identity_unlinks_only_random_direct_child(self):
        same = mock.Mock(st_mode=0o100600, st_dev=1, st_ino=2)
        with mock.patch.object(staging, "_lstat", side_effect=[same, same]), mock.patch.object(staging.os, "rename") as rename, mock.patch.object(staging.os, "unlink") as unlink, mock.patch.object(staging.secrets, "token_hex", return_value="b" * 32):
            staging._quarantine_and_unlink(9, "source.pptx", 1, 2)
        quarantine = f".{staging.STAGING_FILE_PREFIX}quarantine-{'b' * 32}"
        rename.assert_called_once_with("source.pptx", quarantine, src_dir_fd=9, dst_dir_fd=9)
        unlink.assert_called_once_with(quarantine, dir_fd=9)

    def test_old_manifest_without_identity_is_rejected(self):
        payload = {"version": 1, "run_id": RUN_ID, "created_files": ["old.pptx"]}
        with self.assertRaisesRegex(ValueError, "版本不匹配"):
            staging._validate_manifest(payload, staging._manifest_name(RUN_ID), RUN_ID)


if __name__ == "__main__":
    unittest.main()
