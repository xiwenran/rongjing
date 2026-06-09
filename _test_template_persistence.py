import hashlib
import json
import os
import shutil
import tempfile
from unittest.mock import patch

from models.template_model import Template, TemplateManager


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_file(path, data):
    with open(path, "wb") as f:
        f.write(data)


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def make_template(name, background_path):
    return Template(
        name=name,
        background_path=background_path,
        screen_points=[[0, 0], [10, 0], [10, 10], [0, 10]],
    )


def with_tmp(fn):
    root = tempfile.mkdtemp(prefix="rongjing_template_test_")
    try:
        templates_dir = os.path.join(root, "templates")
        backgrounds_dir = os.path.join(root, "backgrounds")
        sources_dir = os.path.join(root, "sources")
        settings_dir = os.path.join(root, "settings")
        os.makedirs(templates_dir)
        os.makedirs(backgrounds_dir)
        os.makedirs(sources_dir)
        os.makedirs(settings_dir)
        try:
            from PyQt6.QtCore import QSettings

            QSettings.setDefaultFormat(QSettings.Format.IniFormat)
            QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, settings_dir)
        except Exception:
            pass
        fn(root, templates_dir, backgrounds_dir, sources_dir)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_save_external_background_copies_and_existing_background_is_direct():
    def run(root, templates_dir, backgrounds_dir, sources_dir):
        src = os.path.join(sources_dir, "教室.jpg")
        write_file(src, b"image-bytes")

        manager = TemplateManager(templates_dir, backgrounds_dir=backgrounds_dir)
        manager.save(make_template("教室1", src))

        data = read_json(os.path.join(templates_dir, "教室1.json"))
        dst = os.path.join(backgrounds_dir, "教室1_bg.jpg")
        assert data["background_path"] == dst
        assert data["category"] == "教师场景"
        assert data["template_type"] == "screen"
        assert data["render_preset"] == "clear"
        assert os.path.exists(dst)
        assert sha256(src) == sha256(dst)
        assert "is_broken" not in data

        loaded = manager.load("教室1")
        assert loaded.background_path == dst
        assert loaded.is_broken is False
        assert read_json(os.path.join(templates_dir, "教室1.json"))["background_path"] == dst
        assert sorted(os.listdir(backgrounds_dir)) == ["教室1_bg.jpg"]

    with_tmp(run)


def test_new_template_fields_persist_and_load():
    def run(root, templates_dir, backgrounds_dir, sources_dir):
        src = os.path.join(sources_dir, "paper.png")
        write_file(src, b"paper-image")
        manager = TemplateManager(templates_dir, backgrounds_dir=backgrounds_dir)
        manager.save(Template(
            name="文档模板1",
            background_path=src,
            screen_points=[[0, 0], [10, 0], [10, 10], [0, 10]],
            category="文档模板",
            template_type="document_paper",
            render_preset="warm",
        ))

        loaded = manager.load("文档模板1")
        assert loaded.category == "文档纸张"
        assert loaded.template_type == "document_paper"
        assert loaded.render_preset == "warm"

    with_tmp(run)


def test_old_json_defaults_to_screen_template_fields():
    def run(root, templates_dir, backgrounds_dir, sources_dir):
        src = os.path.join(sources_dir, "old.jpg")
        write_file(src, b"old-image")
        old_data = {
            "name": "旧字段模板",
            "background_path": src,
            "screen_points": [[0, 0], [10, 0], [10, 10], [0, 10]],
            "output_width": 0,
            "output_height": 0,
        }
        json_path = os.path.join(templates_dir, "旧字段模板.json")
        write_json(json_path, old_data)

        manager = TemplateManager(templates_dir, backgrounds_dir=backgrounds_dir)
        loaded = manager.load("旧字段模板")
        assert loaded.category == "教师场景"
        assert loaded.template_type == "screen"
        assert loaded.render_preset == "clear"

    with_tmp(run)


def test_load_old_existing_background_migrates_once():
    def run(root, templates_dir, backgrounds_dir, sources_dir):
        src = os.path.join(sources_dir, "old.jpeg")
        write_file(src, b"old-image")
        json_path = os.path.join(templates_dir, "旧模板.json")
        write_json(json_path, make_template("旧模板", src).to_dict())

        manager = TemplateManager(templates_dir, backgrounds_dir=backgrounds_dir)
        loaded = manager.load("旧模板")

        dst = os.path.join(backgrounds_dir, "旧模板_bg.jpeg")
        assert loaded.background_path == dst
        assert os.path.exists(dst)
        assert sha256(src) == sha256(dst)
        assert read_json(json_path)["background_path"] == dst

        mtime = os.path.getmtime(dst)
        loaded_again = manager.load("旧模板")
        assert loaded_again.background_path == dst
        assert os.path.getmtime(dst) == mtime

    with_tmp(run)


def test_load_migration_copy_failure_keeps_original_json():
    def run(root, templates_dir, backgrounds_dir, sources_dir):
        src = os.path.join(sources_dir, "old.jpg")
        write_file(src, b"old-image")
        original = make_template("失败模板", src).to_dict()
        json_path = os.path.join(templates_dir, "失败模板.json")
        write_json(json_path, original)

        manager = TemplateManager(templates_dir, backgrounds_dir=backgrounds_dir)
        with patch("models.template_model.shutil.copy2", side_effect=PermissionError("no")):
            loaded = manager.load("失败模板")

        assert loaded.background_path == src
        assert loaded.is_broken is False
        assert read_json(json_path) == original
        assert os.listdir(backgrounds_dir) == []
        try:
            from PyQt6.QtCore import QSettings

            pending = QSettings("融景", "RongJing").value("migration/pending_failed", [])
            assert "失败模板" in pending
        except Exception:
            pass

    with_tmp(run)


def test_load_missing_old_background_marks_broken_without_json_change():
    def run(root, templates_dir, backgrounds_dir, sources_dir):
        missing = os.path.join(sources_dir, "missing.jpg")
        original = make_template("缺失模板", missing).to_dict()
        json_path = os.path.join(templates_dir, "缺失模板.json")
        write_json(json_path, original)

        manager = TemplateManager(templates_dir, backgrounds_dir=backgrounds_dir)
        loaded = manager.load("缺失模板")

        assert loaded.background_path == missing
        assert loaded.is_broken is True
        assert read_json(json_path) == original

    with_tmp(run)


def test_load_hash_mismatch_deletes_copy_and_keeps_original_json():
    def run(root, templates_dir, backgrounds_dir, sources_dir):
        src = os.path.join(sources_dir, "old.png")
        write_file(src, b"old-image")
        original = make_template("校验失败", src).to_dict()
        json_path = os.path.join(templates_dir, "校验失败.json")
        write_json(json_path, original)

        manager = TemplateManager(templates_dir, backgrounds_dir=backgrounds_dir)
        original_hash = TemplateManager._sha256

        def fake_hash(path):
            if path.startswith(backgrounds_dir):
                return "bad-hash"
            return original_hash(path)

        with patch.object(TemplateManager, "_sha256", side_effect=fake_hash):
            loaded = manager.load("校验失败")

        assert loaded.background_path == src
        assert loaded.is_broken is False
        assert read_json(json_path) == original
        assert os.listdir(backgrounds_dir) == []

    with_tmp(run)


def run_tests():
    test_save_external_background_copies_and_existing_background_is_direct()
    test_new_template_fields_persist_and_load()
    test_old_json_defaults_to_screen_template_fields()
    test_load_old_existing_background_migrates_once()
    test_load_migration_copy_failure_keeps_original_json()
    test_load_missing_old_background_marks_broken_without_json_change()
    test_load_hash_mismatch_deletes_copy_and_keeps_original_json()
    print("all template persistence tests passed")


if __name__ == "__main__":
    run_tests()
