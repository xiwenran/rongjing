import os
import random
import tempfile
import json

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PyQt6.QtWidgets import QApplication

import ui.ai_generate_tab as ai_generate_tab
from ui.ai_generate_tab import AIGenerateTab, TagButton, TagGroup, _HistoryDialog


def main():
    app = QApplication.instance() or QApplication([])

    btn = TagButton("自然光")
    states = []
    btn.state_changed.connect(states.append)
    btn.click()
    assert btn.isChecked() is True
    btn.click()
    assert btn.isChecked() is False
    assert states == [True, False]

    group = TagGroup("灯光", ["暖色灯光", "自然光", "冷白光"])
    seen = []
    group.selection_changed.connect(seen.append)
    group.set_selection("暖色灯光")
    assert group.get_selection() == "暖色灯光"
    group.set_selection("自然光")
    assert group.get_selection() == "自然光"
    checked = [b.text() for b in group._buttons if b.isChecked()]
    assert checked == ["自然光"], checked
    group._buttons[1].click()
    assert group.get_selection() == ""
    assert seen[-1] == ""

    with tempfile.TemporaryDirectory() as backgrounds_dir:
        tab = AIGenerateTab(backgrounds_dir=backgrounds_dir)
        assert tab._count_spin.value() == 4
        assert tab._aspect_combo.currentText() == "3:4"

        tab._target_group.set_selection("教师场景")
        tab._device_group.set_selection("希沃白板")
        classroom = tab._scene_group.options()
        assert "小学教室" in classroom
        assert "教师办公桌" not in classroom

        tab._target_group.set_selection("笔记本室内")
        tab._device_group.set_selection("笔记本电脑")
        personal = tab._scene_group.options()
        assert "教师办公桌" in personal
        assert "小学教室" not in personal
        assert tab._template_context()["category"] == "笔记本室内"

        tab._target_group.set_selection("自定义场景")
        assert tab._template_context()["category"] == "自定义场景"
        tab._device_group.set_selection("纸张区域")
        custom_paper_context = tab._template_context()
        assert custom_paper_context["category"] == "文档纸张"
        assert custom_paper_context["template_type"] == "document_paper"
        assert "paper sheet" in tab._build_prompt()
        assert "solid matte black" not in tab._build_prompt()

        for tag_group in tab._tag_groups:
            tag_group.set_selection("")
        tab._random_select_unset(random.Random(1))
        assert any(tag_group.get_selection() for tag_group in tab._tag_groups)

        tab._target_group.set_selection("教师场景")
        prompt = tab._build_prompt()
        lowered = prompt.lower()
        assert "screen" in lowered
        assert "black" in lowered
        tab._device_group.set_selection("")
        tab._scene_group.set_selection("教师办公室")
        office_prompt = tab._build_prompt()
        assert "national flag" not in office_prompt
        assert "chalkboard" not in office_prompt

        document_context = {
            "category": "文档纸张",
            "template_type": "document_paper",
            "render_preset": "paper",
        }
        image = Image.new("RGB", (8, 8), "white")
        tab._load_history_images([image], document_context)
        tab._target_group.set_selection("教师场景")
        emitted = []
        tab.save_finished.connect(lambda paths, context: emitted.append((paths, context)))
        tab._save_selected()
        assert emitted
        assert emitted[-1][1] == document_context

        tab._clear_results()
        assert tab._loaded_template_context is None

    with tempfile.TemporaryDirectory() as cache_dir:
        old_cache_dir = ai_generate_tab._CACHE_DIR
        ai_generate_tab._CACHE_DIR = cache_dir
        try:
            batch_dir = os.path.join(cache_dir, "20260607_120000")
            os.makedirs(batch_dir)
            Image.new("RGB", (4, 4), "white").save(os.path.join(batch_dir, "1.png"))
            with open(os.path.join(batch_dir, "meta.json"), "w", encoding="utf-8") as f:
                json.dump({"template_context": document_context}, f, ensure_ascii=False)

            dialog = _HistoryDialog()
            loaded = []
            dialog.load_requested.connect(lambda images, context: loaded.append((images, context)))
            dialog._load_batch(batch_dir)
            assert len(loaded) == 1
            assert len(loaded[0][0]) == 1
            assert loaded[0][1] == document_context

            broken_dir = os.path.join(cache_dir, "20260607_120001")
            os.makedirs(broken_dir)
            Image.new("RGB", (4, 4), "white").save(os.path.join(broken_dir, "1.png"))
            with open(os.path.join(broken_dir, "meta.json"), "w", encoding="utf-8") as f:
                f.write("{broken")
            loaded.clear()
            dialog._load_batch(broken_dir)
            assert loaded[0][1] == {}
        finally:
            ai_generate_tab._CACHE_DIR = old_cache_dir

    print("All AI generate tab tests passed.")


if __name__ == "__main__":
    main()
