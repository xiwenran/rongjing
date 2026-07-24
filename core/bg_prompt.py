from __future__ import annotations

"""AI 背景生成 prompt 单一规范源。

拼装逻辑与词表从 `ui/ai_generate_tab.py` 的
`AIGenerateTab._build_prompt()`（词表 `_TRANSLATIONS`）原样抽取而来，
供 GUI（`ui/ai_generate_tab.py` 委托调用）与 CLI（`cli.py` 的
`bg-prompt` 子命令）共用，两侧输出必须逐字一致。

改动这里的拼装逻辑或词表时，两侧行为会同时变化——这正是抽取的目的。
"""

# 与 ui/ai_generate_tab.py 的 AIGenerateTab._TRANSLATIONS 保持逐字一致。
_TRANSLATIONS = {
    "教室场景": "a realistic Chinese classroom or teacher office display template background",
    "文档纸张": "a realistic blank sheet of paper on a desk for document compositing",
    "台式机电脑": "a desktop computer screen template background",
    "笔记本室内": "a laptop computer indoor desk template background",
    "自定义场景": "a realistic custom template background for compositing",
    "笔记本电脑": "a laptop computer (MacBook Pro or Lenovo ThinkPad)",
    "笔记本外接屏": "a laptop with an external monitor on a desk",
    "台式显示器": "a desktop computer monitor on a desk",
    "一体机屏幕": "an all-in-one desktop computer on a desk",
    "双屏桌面": "a dual-monitor desktop computer setup",
    "希沃白板": "a large Seewo brand interactive whiteboard mounted on the classroom wall",
    "教室大屏": "a large classroom display mounted on the wall",
    "多媒体大屏": "a multimedia classroom display screen",
    "屏幕区域": "a clean blank screen area for compositing",
    "纸张区域": "a clean blank paper area for compositing",
    "A4 竖版纸": "a vertical A4 blank white sheet of paper",
    "A4 横版纸": "a horizontal A4 blank white sheet of paper",
    "教师办公桌": "on a teacher's office desk in a Chinese school",
    "家里书桌": "on a home study desk in a Chinese household",
    "校园办公室": "in a Chinese school campus office",
    "教研室": "in a Chinese teaching research office",
    "居家备课": "at a home lesson-preparation desk",
    "宿舍": "in a Chinese student dorm room",
    "小学教室": "in a Chinese elementary school classroom",
    "中学教室": "in a Chinese middle school classroom",
    "多媒体教室": "in a Chinese multimedia classroom",
    "教师办公室": "in a Chinese teacher office",
    "木质桌面": "on a clean wooden desk",
    "暖光书桌": "on a warm-lit study desk",
    "冷白办公桌": "on a cool white office desk",
    "浅色桌面": "on a light-colored clean desk",
    "暖色灯光": "warm amber ambient lighting",
    "自然光": "natural daylight from window",
    "冷白光": "cool white office lighting",
    "柔光": "soft diffused light",
    "偏暗氛围": "dim moody atmospheric lighting",
    "正面平视": "front eye-level camera angle",
    "略偏侧角": "slightly side camera angle",
    "略微仰视": "slightly low-angle camera angle",
    "有植物": "with a small potted plant on the desk",
    "有咖啡杯": "with a coffee cup or tea cup on the desk",
    "有书本": "with Chinese textbooks and exercise notebooks on the desk",
    "有小摆件": "with a small yellow rubber duck toy or cute figurine on the desk",
    "极简": "minimal clean desktop with nothing extra",
    "学生背影": "1-2 students visible from behind in the foreground, seated at classroom desks facing the screen, slightly out of focus",
    "粉笔槽板擦": "a chalk tray with a blackboard eraser visible at the bottom edge of the frame",
    "标语横幅": "a prominent red banner with bold Chinese calligraphy slogan directly above the screen",
    "国旗": "a small Chinese national flag clearly visible mounted above the screen",
}

# 与 ui/ai_generate_tab.py 的模块级 _CLASSROOM_SCENES 保持逐字一致；
# 只影响本文件内 is_classroom 判断，不是同一个对象（UI 侧另有一份用于
# 填充「环境位置」标签选项，两处含义不同但取值必须同步）。
_CLASSROOM_SCENES = ["小学教室", "中学教室", "多媒体教室"]

# 与 ui/ai_generate_tab.py 的模块级 _SCREEN_FILL_RANGE 保持逐字一致。
_SCREEN_FILL_RANGE = {
    "教室场景": (55, 75),
    "笔记本室内": (60, 75),
    "台式机电脑": (55, 70),
    "自定义场景": (60, 70),
}


def _is_document_target(target: str, device: str) -> bool:
    return target == "文档纸张" or device in ("纸张区域", "A4 竖版纸", "A4 横版纸")


def build_prompt(
    target: str,
    device: str,
    scene: str,
    greenscreen: bool,
    decor: str = "",
    light: str = "",
    angle: str = "",
    extra: str = "",
) -> str:
    """按 GUI 标签选择拼装 AI 背景生成 prompt。

    参数对应 GUI 侧的选择：
      target — 「背景场景」标签组选中值（如 教室场景/台式机电脑/...）
      device — 「主体类型」标签组选中值（如 希沃白板/台式显示器/...）
      scene  — 「环境位置」标签组选中值
      greenscreen — 「屏幕显示绿幕」勾选框状态（文档纸张场景下会被忽略）
      decor  — 「教室元素」或「桌面摆件」标签组选中值（同一个 UI 组件，
               按 target 是否教室场景切换选项列表，语义合一）
      light  — 「灯光」标签组选中值
      angle  — 「拍摄角度」标签组选中值
      extra  — 「额外描述」输入框文本

    与抽取前的 AIGenerateTab._build_prompt() 输出逐字一致。
    """
    is_document = _is_document_target(target, device)
    is_classroom = device in ("希沃白板", "教室大屏", "多媒体大屏") or scene in _CLASSROOM_SCENES
    use_greenscreen = (not is_document) and greenscreen

    parts = [
        "A candid realistic close-up photograph shot on a smartphone camera",
        "authentic everyday scene with natural imperfections",
        "subtle depth of field, natural lighting variations, slight film grain",
    ]

    if is_document:
        parts.extend([
            "realistic desktop background with a single blank white paper sheet as the main subject",
            "paper sheet fills 65-80% of the frame, four paper corners clearly visible and easy to mark",
            "paper surface mostly empty, no printed content, no handwriting, no text, no diagrams",
            "natural paper texture, slight shadows around the paper edge, readable blank page area",
        ])
    elif target:
        parts.append(_TRANSLATIONS.get(target, target))

    # Device & scene
    if device and not is_document:
        parts.append(_TRANSLATIONS.get(device, device))
    if scene:
        parts.append(_TRANSLATIONS.get(scene, scene))

    # 屏幕类场景：近景硬约束，屏幕占比按场景微调，参考实拍风格基准
    if not is_document and device not in ("纸张区域", "A4 竖版纸", "A4 横版纸"):
        lo, hi = _SCREEN_FILL_RANGE.get(target, (60, 70))
        parts.append(f"close-up smartphone shot, the screen fills {lo}-{hi}% of the frame")
        parts.append("vertical portrait framing, minimal surrounding environment beyond the immediate close-up context")

    # Chinese context — classroom vs personal desk
    if is_classroom and not is_document:
        parts.append("Chinese school classroom, red banner with bold Chinese calligraphy slogan and a small Chinese national flag visible in the corner above the screen")
        parts.append("dark forest-green chalkboard edges visible on both sides of the screen")
        if device == "希沃白板":
            parts.append("if chalkboard is visible it must be completely clean and blank, absolutely no chalk writing, no handwritten text, no diagrams on the board surface")
        # 教室元素（学生背影/粉笔槽板擦/标语横幅/国旗/极简）
        if decor:
            parts.append(_TRANSLATIONS.get(decor, decor))
    else:
        if not is_document:
            parts.append("Chinese domestic or office setting")
            if target == "笔记本室内":
                parts.append("laptop keyboard and the lower half of the laptop body visible in the lower part of the frame")
            elif target == "台式机电脑":
                parts.append("keyboard and mouse visible on the desk in front of the monitor")
        # 桌面摆件（有植物/有咖啡杯/有书本/有小摆件/极简），文档纸张场景沿用原有行为
        if decor:
            parts.append(_TRANSLATIONS.get(decor, decor))

    # Screen / paper surface — must be clean and detectable for compositing
    if is_document:
        parts.append("paper corners clearly visible, realistic perspective, clean composition")
    elif use_greenscreen:
        parts.append("the screen displays a solid pure chroma-key green color similar to #00FF00, perfectly flat and uniform across the entire screen surface")
        parts.append("no gradient, no reflection, no glare, no glossy highlight, no bezel glow on the green screen area")
        if is_classroom:
            parts.append("the classroom blackboard is a distinctly dark forest-green tone, clearly different from the bright pure green screen color, the two greens must not be confused")
    else:
        parts.append("screen displays completely solid matte black, absolutely no reflections, no glare, no ambient light on screen surface")

    # Lighting, angle
    for sel in (light, angle):
        if sel:
            parts.append(_TRANSLATIONS.get(sel, sel))

    # Extra user description
    extra = (extra or "").strip()
    if extra:
        parts.append(extra)

    # Constraints
    parts.append("absolutely no English text, signs, diplomas, or labels anywhere in the scene")
    parts.append("all visible text and signage must be in simplified Chinese only")
    parts.append("no text about grades, homework, class names, subjects, schedules, or any academic content visible anywhere")
    parts.append("no watermark, no logo on screen")
    if not is_document:
        parts.append("screen corners clearly visible, clean composition, realistic perspective")
    parts.append("NOT an AI-generated image, looks like a real phone photo, natural and authentic")
    return ", ".join(parts)
