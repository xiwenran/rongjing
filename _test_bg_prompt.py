"""core.bg_prompt.build_prompt 回归测试（抽取 AIGenerateTab._build_prompt 后的行为锁定）。

三条黄金串不是从源码推导手工拼出来的，是抽取前用真实 GUI 代码跑出来的：
用 QT_QPA_PLATFORM=offscreen（无需真实显示器）启动 QApplication，实例化
抽取前的 AIGenerateTab，设置标签组选择后直接调用原始 _build_prompt()，
把返回值原样固化为下面的字符串常量。build_prompt() 的输出必须与其逐字
一致——一个字符都不能差。

覆盖的三组组合：
  1. 教室场景 + 希沃白板 + 小学教室 + 绿幕开
  2. 教室场景 + 希沃白板 + 小学教室 + 绿幕关
  3. 非教室场景（台式机电脑 + 台式显示器 + 教师办公桌）+ 绿幕开
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.bg_prompt import build_prompt


GOLDEN_CLASSROOM_GREENSCREEN_ON = (
    "A candid realistic close-up photograph shot on a smartphone camera, authentic everyday scene with natural imperfections, subtle depth of field, natural lighting variations, slight film grain, a realistic Chinese classroom or teacher office display template background, a large Seewo brand interactive whiteboard mounted on the classroom wall, in a Chinese elementary school classroom, close-up smartphone shot, the screen fills 55-75% of the frame, vertical portrait framing, minimal surrounding environment beyond the immediate close-up context, Chinese school classroom, red banner with bold Chinese calligraphy slogan and a small Chinese national flag visible in the corner above the screen, dark forest-green chalkboard edges visible on both sides of the screen, if chalkboard is visible it must be completely clean and blank, absolutely no chalk writing, no handwritten text, no diagrams on the board surface, 1-2 students visible from behind in the foreground, seated at classroom desks facing the screen, slightly out of focus, the screen displays a solid pure chroma-key green color similar to #00FF00, perfectly flat and uniform across the entire screen surface, no gradient, no reflection, no glare, no glossy highlight, no bezel glow on the green screen area, the classroom blackboard is a distinctly dark forest-green tone, clearly different from the bright pure green screen color, the two greens must not be confused, natural daylight from window, front eye-level camera angle, absolutely no English text, signs, diplomas, or labels anywhere in the scene, all visible text and signage must be in simplified Chinese only, no text about grades, homework, class names, subjects, schedules, or any academic content visible anywhere, no watermark, no logo on screen, screen corners clearly visible, clean composition, realistic perspective, NOT an AI-generated image, looks like a real phone photo, natural and authentic"
)

GOLDEN_CLASSROOM_GREENSCREEN_OFF = (
    "A candid realistic close-up photograph shot on a smartphone camera, authentic everyday scene with natural imperfections, subtle depth of field, natural lighting variations, slight film grain, a realistic Chinese classroom or teacher office display template background, a large Seewo brand interactive whiteboard mounted on the classroom wall, in a Chinese elementary school classroom, close-up smartphone shot, the screen fills 55-75% of the frame, vertical portrait framing, minimal surrounding environment beyond the immediate close-up context, Chinese school classroom, red banner with bold Chinese calligraphy slogan and a small Chinese national flag visible in the corner above the screen, dark forest-green chalkboard edges visible on both sides of the screen, if chalkboard is visible it must be completely clean and blank, absolutely no chalk writing, no handwritten text, no diagrams on the board surface, 1-2 students visible from behind in the foreground, seated at classroom desks facing the screen, slightly out of focus, screen displays completely solid matte black, absolutely no reflections, no glare, no ambient light on screen surface, natural daylight from window, front eye-level camera angle, absolutely no English text, signs, diplomas, or labels anywhere in the scene, all visible text and signage must be in simplified Chinese only, no text about grades, homework, class names, subjects, schedules, or any academic content visible anywhere, no watermark, no logo on screen, screen corners clearly visible, clean composition, realistic perspective, NOT an AI-generated image, looks like a real phone photo, natural and authentic"
)

GOLDEN_NON_CLASSROOM_GREENSCREEN_ON = (
    "A candid realistic close-up photograph shot on a smartphone camera, authentic everyday scene with natural imperfections, subtle depth of field, natural lighting variations, slight film grain, a desktop computer screen template background, a desktop computer monitor on a desk, on a teacher's office desk in a Chinese school, close-up smartphone shot, the screen fills 55-70% of the frame, vertical portrait framing, minimal surrounding environment beyond the immediate close-up context, Chinese domestic or office setting, keyboard and mouse visible on the desk in front of the monitor, with a small potted plant on the desk, the screen displays a solid pure chroma-key green color similar to #00FF00, perfectly flat and uniform across the entire screen surface, no gradient, no reflection, no glare, no glossy highlight, no bezel glow on the green screen area, warm amber ambient lighting, slightly side camera angle, absolutely no English text, signs, diplomas, or labels anywhere in the scene, all visible text and signage must be in simplified Chinese only, no text about grades, homework, class names, subjects, schedules, or any academic content visible anywhere, no watermark, no logo on screen, screen corners clearly visible, clean composition, realistic perspective, NOT an AI-generated image, looks like a real phone photo, natural and authentic"
)


def test_classroom_seewo_greenscreen_on_matches_golden():
    prompt = build_prompt(
        target="教室场景",
        device="希沃白板",
        scene="小学教室",
        greenscreen=True,
        decor="学生背影",
        light="自然光",
        angle="正面平视",
        extra="",
    )
    assert prompt == GOLDEN_CLASSROOM_GREENSCREEN_ON


def test_classroom_seewo_greenscreen_off_matches_golden():
    prompt = build_prompt(
        target="教室场景",
        device="希沃白板",
        scene="小学教室",
        greenscreen=False,
        decor="学生背影",
        light="自然光",
        angle="正面平视",
        extra="",
    )
    assert prompt == GOLDEN_CLASSROOM_GREENSCREEN_OFF


def test_non_classroom_desktop_greenscreen_on_matches_golden():
    prompt = build_prompt(
        target="台式机电脑",
        device="台式显示器",
        scene="教师办公桌",
        greenscreen=True,
        decor="有植物",
        light="暖色灯光",
        angle="略偏侧角",
        extra="",
    )
    assert prompt == GOLDEN_NON_CLASSROOM_GREENSCREEN_ON


def test_gui_delegate_matches_pure_function():
    """回归验证：ui/ai_generate_tab.py 的 AIGenerateTab._build_prompt() 委托调用
    core.bg_prompt.build_prompt 后，真实 GUI 路径的输出与纯函数输出仍逐字一致
    （证明委托改动没有破坏 GUI 行为）。用 QT_QPA_PLATFORM=offscreen 无需真实显示器。
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    from ui.ai_generate_tab import AIGenerateTab

    tab = AIGenerateTab(backgrounds_dir="/tmp")
    try:
        tab._target_group.set_selection("教室场景")
        tab._on_target_changed("教室场景")
        tab._device_group.set_selection("希沃白板")
        tab._on_device_changed("希沃白板")
        tab._scene_group.set_selection("小学教室")
        tab._decor_group.set_selection("学生背影")
        tab._light_group.set_selection("自然光")
        tab._angle_group.set_selection("正面平视")
        tab._greenscreen_check.setChecked(True)

        gui_prompt = tab._build_prompt()
        direct_prompt = build_prompt(
            target="教室场景", device="希沃白板", scene="小学教室",
            greenscreen=True, decor="学生背影", light="自然光", angle="正面平视", extra="",
        )
        assert gui_prompt == direct_prompt == GOLDEN_CLASSROOM_GREENSCREEN_ON
    finally:
        tab.deleteLater()


if __name__ == "__main__":
    test_classroom_seewo_greenscreen_on_matches_golden()
    test_classroom_seewo_greenscreen_off_matches_golden()
    test_non_classroom_desktop_greenscreen_on_matches_golden()
    test_gui_delegate_matches_pure_function()
    print("全部通过")
