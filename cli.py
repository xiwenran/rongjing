#!/usr/bin/env python3
"""
融景 CLI — 命令行接口，供 Claude Code Skill 调用。
不依赖 PyQt6，直接调用 core/ 的纯 Python 处理函数。

用法：
  python cli.py list-templates
  python cli.py process --input <文件夹或图片路径...> --templates <模板名...> --output <输出目录> [--format PNG|JPEG]
  python cli.py process ... --cover-source <封面源路径>
  python cli.py create-template --bg <背景图路径> [--name <模板名>] [--category <分类>] \
      [--preview-out <预览图路径>] [--json-result] [--force]
"""

import argparse
import json
import math
import os
import re
import shutil
import sys

TEMPLATES_DIR = os.path.expanduser("~/Library/Application Support/融景/templates")
COLLAGES_DIR = os.path.expanduser("~/Library/Application Support/融景/collages")

sys.path.insert(0, os.path.dirname(__file__))
from models.template_model import normalize_render_preset, normalize_template_category


def natural_sort_key(s: str):
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", s)]


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tiff"}


def load_template(name: str):
    path = os.path.join(TEMPLATES_DIR, f"{name}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        d["_storage_key"] = name
        return d

    matches = []
    if os.path.isdir(TEMPLATES_DIR):
        for fn in sorted(os.listdir(TEMPLATES_DIR), key=natural_sort_key):
            if not fn.endswith(".json"):
                continue
            key = fn[:-5]
            try:
                with open(os.path.join(TEMPLATES_DIR, fn), encoding="utf-8") as f:
                    d = json.load(f)
            except Exception:
                continue
            if d.get("name", key) == name:
                d["_storage_key"] = key
                matches.append(d)

    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        options = [
            f"{m['_storage_key']}（{m.get('name', m['_storage_key'])} · {normalize_template_category(m.get('category', '未分类'))}）"
            for m in matches
        ]
        raise FileNotFoundError(
            f"模板名重复：{name}。请改用模板 key：{', '.join(options)}"
        )
    raise FileNotFoundError(f"模板不存在：{name}（{path}）")


def list_templates():
    if not os.path.isdir(TEMPLATES_DIR):
        print(json.dumps([], ensure_ascii=False))
        return
    templates = []
    for fn in sorted(os.listdir(TEMPLATES_DIR), key=natural_sort_key):
        if fn.endswith(".json"):
            try:
                with open(os.path.join(TEMPLATES_DIR, fn), encoding="utf-8") as f:
                    d = json.load(f)
                bg = d.get("background_path", "")
                key = fn[:-5]
                name = d.get("name", key)
                category = normalize_template_category(d.get("category", "教室场景"))
                d["category"] = category
                d["render_preset"] = normalize_render_preset(category, d.get("render_preset"))
                templates.append({
                    "key": key,
                    "name": name,
                    "category": category,
                    "label": f"{name} · {category}",
                    "background": os.path.basename(bg),
                    "background_exists": os.path.exists(bg),
                })
            except Exception as e:
                templates.append({"key": fn[:-5], "name": fn[:-5], "error": str(e)})
    print(json.dumps(templates, ensure_ascii=False, indent=2))


def collect_images(inputs: list[str]) -> list[str]:
    """从文件夹或文件路径列表收集图片，保持自然排序。"""
    images = []
    for inp in inputs:
        inp = os.path.expanduser(inp)
        if os.path.isdir(inp):
            for fn in sorted(os.listdir(inp), key=natural_sort_key):
                ext = os.path.splitext(fn)[1].lower()
                if ext in IMAGE_EXTS:
                    images.append(os.path.join(inp, fn))
        elif os.path.isfile(inp):
            images.append(inp)
        else:
            print(f"[警告] 路径不存在，跳过：{inp}", file=sys.stderr)
    return images


# 封面文件名映射：源文件名 → 目标文件名
_COVER_RENAME_MAP = [
    ("0(1).jpg", "0.jpg"),
    ("0(2).jpg", "0(1).jpg"),
    ("0(3).jpg", "0(2).jpg"),
]


def _place_covers(output_root: str, cover_source: str):
    """
    将封面图复制到融景输出目录的每个模板子目录。

    支持两种模式（自动识别）：
    - 模式 A：cover_source 下直接有 0(1).jpg 等文件 → 复制到所有模板子目录
    - 模式 B：cover_source 下有多个主题子目录 → 按主题名前 6 字符匹配后复制
    """
    cover_source = os.path.expanduser(cover_source)
    if not os.path.isdir(cover_source):
        print(f"[封面] 警告：封面源路径不存在，跳过：{cover_source}", file=sys.stderr)
        return

    has_covers_directly = any(
        os.path.isfile(os.path.join(cover_source, src_n))
        for src_n, _ in _COVER_RENAME_MAP
    )

    total_folders = 0
    total_copied = 0
    failed_topics = []

    if has_covers_directly:
        # 模式 A：output_root 下每个子目录都是模板目录
        for tmpl_name in sorted(os.listdir(output_root), key=natural_sort_key):
            tmpl_path = os.path.join(output_root, tmpl_name)
            if not os.path.isdir(tmpl_path):
                continue
            total_folders += 1
            for src_n, dst_n in _COVER_RENAME_MAP:
                src = os.path.join(cover_source, src_n)
                if os.path.isfile(src):
                    shutil.copy2(src, os.path.join(tmpl_path, dst_n))
                    total_copied += 1
    else:
        # 模式 B：cover_source 下有主题子目录，按名称前 6 字符匹配 output_root 下主题目录
        cover_topics = [
            d for d in os.listdir(cover_source)
            if os.path.isdir(os.path.join(cover_source, d))
        ]
        output_topics = [
            d for d in os.listdir(output_root)
            if os.path.isdir(os.path.join(output_root, d))
        ]
        for ct in cover_topics:
            matched = next(
                (ot for ot in output_topics if ot[:6] == ct[:6]),
                None
            )
            if not matched:
                failed_topics.append(ct)
                continue
            topic_out = os.path.join(output_root, matched)
            # topic_out 下每个子目录是模板目录
            for tmpl_name in sorted(os.listdir(topic_out), key=natural_sort_key):
                tmpl_path = os.path.join(topic_out, tmpl_name)
                if not os.path.isdir(tmpl_path):
                    continue
                total_folders += 1
                for src_n, dst_n in _COVER_RENAME_MAP:
                    src = os.path.join(cover_source, ct, src_n)
                    if os.path.isfile(src):
                        shutil.copy2(src, os.path.join(tmpl_path, dst_n))
                        total_copied += 1

    print(f"\n封面放置完成：")
    print(f"  处理 {total_folders} 个模板文件夹")
    print(f"  复制 {total_copied} 张封面")
    if failed_topics:
        print(f"  匹配失败 {len(failed_topics)} 个主题：{', '.join(failed_topics)}")


def process(inputs: list[str], template_names: list[str], output_dir: str, fmt: str,
            cover_source: str | None = None):
    # 延迟导入，避免系统没装 Pillow 时 list-templates 也报错
    sys.path.insert(0, os.path.dirname(__file__))
    from PIL import Image
    from core.image_processor import (
        embed_document_paper_pil,
        embed_image_pil_fast,
        precompute_template_cache,
    )

    output_dir = os.path.expanduser(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    images = collect_images(inputs)
    if not images:
        print("[错误] 没有找到任何图片文件", file=sys.stderr)
        sys.exit(1)

    ext = ".png" if fmt.upper() == "PNG" else ".jpg"
    save_kwargs = {} if fmt.upper() == "PNG" else {"quality": 95}

    total = len(images) * len(template_names)
    done = 0

    for tpl_name in template_names:
        try:
            tpl = load_template(tpl_name)
        except FileNotFoundError as exc:
            print(f"[错误] {exc}", file=sys.stderr)
            sys.exit(1)
        tpl_key = tpl.get("_storage_key", tpl_name)
        category = normalize_template_category(tpl.get("category", "未分类"))
        tpl["category"] = category
        tpl["render_preset"] = normalize_render_preset(category, tpl.get("render_preset"))
        tpl_label = f"{tpl.get('name', tpl_key)} · {category}"
        bg_path = tpl["background_path"]
        if not os.path.exists(bg_path):
            print(f"[错误] 模板 {tpl_label} 的背景图不存在：{bg_path}", file=sys.stderr)
            sys.exit(1)

        bg_img = Image.open(bg_path)
        is_document = tpl.get("template_type") == "document_paper"
        cache = None if is_document else precompute_template_cache(bg_img, tpl["screen_points"])

        out_sub = os.path.join(output_dir, tpl_key)
        os.makedirs(out_sub, exist_ok=True)

        for i, img_path in enumerate(images, 1):
            ppt_img = Image.open(img_path)
            if is_document:
                result = embed_document_paper_pil(
                    ppt_img,
                    bg_img,
                    tpl["screen_points"],
                    tpl.get("render_preset", "paper"),
                )
            else:
                result = embed_image_pil_fast(ppt_img, cache)

            out_path = os.path.join(out_sub, f"{i}{ext}")
            result.save(out_path, **save_kwargs)
            done += 1
            print(f"[{done}/{total}] 模板={tpl_label} 图={i} → {out_path}")

    print(f"\n完成！共处理 {done} 张，输出目录：{output_dir}")

    if cover_source:
        _place_covers(output_dir, cover_source)


def _parse_pages(pages_arg: str | None, total: int) -> list[int]:
    """解析 --pages 逗号分隔的 1-based 页序号，返回 0-based 索引列表。"""
    if not pages_arg:
        return list(range(total))
    indices = []
    for part in pages_arg.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            n = int(part)
        except ValueError:
            print(f"[错误] --pages 包含非法页序号：{part}", file=sys.stderr)
            sys.exit(1)
        if n < 1 or n > total:
            print(f"[错误] --pages 页序号 {n} 超出范围（共 {total} 张图）", file=sys.stderr)
            sys.exit(1)
        indices.append(n - 1)
    return indices


def collage(input_dir: str, output: str, template_name: str | None,
            rows: int | None, cols: int | None, pages: str | None,
            json_result: bool = False):
    import hashlib

    sys.path.insert(0, os.path.dirname(__file__))
    from PIL import Image
    from core.collage_processor import create_collage
    from models.collage_model import CollageManager

    if template_name and (rows or cols):
        print("[错误] --template 与 --rows/--cols 二选一，不能同时给出", file=sys.stderr)
        sys.exit(1)
    if not template_name and not (rows and cols):
        print("[错误] 必须指定 --template，或同时指定 --rows 和 --cols", file=sys.stderr)
        sys.exit(1)

    manager = CollageManager(COLLAGES_DIR)

    if template_name:
        tpl = manager.load(template_name)
        if tpl is None:
            available = manager.names()
            print(f"[错误] 拼图预设不存在：{template_name}", file=sys.stderr)
            if available:
                print(f"  可用预设：{', '.join(available)}", file=sys.stderr)
            else:
                print(f"  预设目录为空：{COLLAGES_DIR}", file=sys.stderr)
            sys.exit(1)
        layout = tpl.layout
        use_rows = tpl.rows
        use_cols = tpl.cols
        gap = tpl.gap
        padding = tpl.padding
        background_color = tpl.background_color
        cell_aspect_ratio = tpl.cell_aspect_ratio
        output_width = tpl.output_width
        output_height = tpl.output_height
    else:
        layout = "grid"
        use_rows = rows
        use_cols = cols
        gap = 4
        padding = 0
        background_color = "#FFFFFF"
        cell_aspect_ratio = 0
        output_width = 1920
        output_height = 0

    input_dir = os.path.expanduser(input_dir)
    if not os.path.isdir(input_dir):
        print(f"[错误] 输入目录不存在：{input_dir}", file=sys.stderr)
        sys.exit(1)

    image_paths = collect_images([input_dir])
    if not image_paths:
        print(f"[错误] 输入目录下没有找到任何图片：{input_dir}", file=sys.stderr)
        sys.exit(1)

    page_indices = _parse_pages(pages, len(image_paths))
    selected_paths = [image_paths[i] for i in page_indices]
    images = [Image.open(p) for p in selected_paths]

    result = create_collage(
        images,
        layout=layout,
        rows=use_rows,
        cols=use_cols,
        gap=gap,
        padding=padding,
        background_color=background_color,
        cell_aspect_ratio=cell_aspect_ratio,
        output_width=output_width,
        output_height=output_height,
    )

    output = os.path.expanduser(output)
    out_parent = os.path.dirname(output)
    if out_parent:
        os.makedirs(out_parent, exist_ok=True)
    result.save(output)

    sha256 = hashlib.sha256()
    with open(output, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)

    info = {
        "output": output,
        "size": list(result.size),
        "sha256": sha256.hexdigest(),
        "template": {
            "name": template_name,
            "layout": layout,
            "rows": use_rows,
            "cols": use_cols,
            "gap": gap,
            "padding": padding,
            "background_color": background_color,
            "cell_aspect_ratio": cell_aspect_ratio,
            "output_width": output_width,
            "output_height": output_height,
        },
        "input_images": len(image_paths),
        "used_images": len(selected_paths),
    }

    if json_result:
        print(json.dumps(info, ensure_ascii=False))
    else:
        print(f"完成！拼图已生成：{output}")
        print(f"  尺寸：{result.size[0]}x{result.size[1]}")
        print(f"  sha256：{sha256.hexdigest()}")


def _default_template_name(manager, category: str, bg_path: str) -> str:
    """从背景图文件名派生一个不与现有「同名同分类」模板冲突的默认模板名。"""
    stub = os.path.splitext(os.path.basename(bg_path))[0].strip()
    stub = re.sub(r'[\\/:\*\?"<>\|]+', "_", stub)[:24] or "模板"
    candidate = f"{stub}_自动识别"
    idx = 2
    while manager.key_for_name_category(candidate, category) is not None:
        candidate = f"{stub}_自动识别_{idx}"
        idx += 1
    return candidate


def _quad_quality(points: list, img_w: int, img_h: int) -> dict:
    """计算四边形质量指标：面积占比、宽高比、是否触及图像边缘。"""
    n = len(points)
    area = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    area = abs(area) / 2.0
    image_area = float(img_w * img_h) or 1.0

    tl, tr, br, bl = points
    top_w = math.hypot(tr[0] - tl[0], tr[1] - tl[1])
    bottom_w = math.hypot(br[0] - bl[0], br[1] - bl[1])
    left_h = math.hypot(bl[0] - tl[0], bl[1] - tl[1])
    right_h = math.hypot(br[0] - tr[0], br[1] - tr[1])
    avg_w = (top_w + bottom_w) / 2.0
    avg_h = (left_h + right_h) / 2.0

    margin = max(2.0, min(img_w, img_h) * 0.01)
    touches_edge = any(
        x <= margin or x >= img_w - margin or y <= margin or y >= img_h - margin
        for x, y in points
    )

    return {
        "area_ratio": round(area / image_area, 4),
        "aspect_ratio": round(avg_w / avg_h, 3) if avg_h > 0 else 0.0,
        "touches_edge": touches_edge,
    }


def _draw_quad_preview(bg_path: str, points: list, out_path: str) -> None:
    from PIL import Image, ImageDraw

    with Image.open(bg_path) as src:
        im = src.convert("RGB")
    draw = ImageDraw.Draw(im)
    pts = [tuple(p) for p in points]
    line_width = max(3, int(min(im.size) * 0.004))
    draw.line(pts + [pts[0]], fill=(255, 32, 32), width=line_width)
    r = max(6, int(min(im.size) * 0.01))
    for x, y in pts:
        draw.ellipse([x - r, y - r, x + r, y + r], fill=(32, 220, 32), outline=(255, 32, 32), width=2)

    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    save_kwargs = {"quality": 95} if os.path.splitext(out_path)[1].lower() in (".jpg", ".jpeg") else {}
    im.save(out_path, **save_kwargs)


def create_template(bg: str, name: str | None, category: str, preview_out: str | None,
                     json_result: bool, force: bool, use_vlm: bool = True,
                     min_screen_width: int = 1600):
    sys.path.insert(0, os.path.dirname(__file__))
    import math
    import tempfile
    from PIL import Image
    from core.screen_detector import (
        detect_screen_points,
        detect_screen_points_vlm,
        detect_green_screen_points,
    )
    from models.template_model import Template, TemplateManager

    bg = os.path.expanduser(bg)
    if not os.path.isfile(bg):
        print(f"[错误] 背景图不存在：{bg}", file=sys.stderr)
        sys.exit(1)

    category = normalize_template_category(category or "教师场景")
    manager = TemplateManager(TEMPLATES_DIR)

    detect_method = None
    points = detect_green_screen_points(bg)
    if points:
        detect_method = "greenscreen"
    elif use_vlm:
        points = detect_screen_points_vlm(bg)
        detect_method = "vlm_fusion"
    else:
        points = detect_screen_points(bg)
        detect_method = "classic"
    if not points:
        print(f"[错误] 未能从背景图中识别出屏幕四角，请检查图片内容或改用 GUI 手工标注：{bg}", file=sys.stderr)
        sys.exit(1)

    # 屏幕区域宽度（TL-TR 距离）过窄时，放大整张背景图以降低 PPT 内容压缩比，避免嵌入后文字模糊
    naming_bg = bg  # 用于默认模板命名，放大后仍取原始文件名
    screen_width = math.hypot(points[1][0] - points[0][0], points[1][1] - points[0][1])
    upscale_factor = 1.0
    if screen_width > 0 and screen_width < min_screen_width:
        upscale_factor = min(min_screen_width / screen_width, 3.0)

    if upscale_factor > 1.0:
        with Image.open(bg) as src_im:
            src_im = src_im.convert("RGB")
            new_w = round(src_im.width * upscale_factor)
            new_h = round(src_im.height * upscale_factor)
            upscaled = src_im.resize((new_w, new_h), Image.LANCZOS)
        ext = os.path.splitext(bg)[1] or ".png"
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=f"_upscaled{ext}")
        os.close(tmp_fd)
        save_kwargs = {"quality": 95} if ext.lower() in (".jpg", ".jpeg") else {}
        upscaled.save(tmp_path, **save_kwargs)
        points = [[x * upscale_factor, y * upscale_factor] for x, y in points]
        screen_width *= upscale_factor
        bg = tmp_path

    if name:
        existing_key = manager.key_for_name_category(name, category)
        if existing_key and not force:
            print(
                f"[错误] 模板名「{name}」（分类：{category}）已存在，冲突项：{existing_key}.json。"
                f"如需覆盖请加 --force",
                file=sys.stderr,
            )
            sys.exit(1)
        final_name = name
        storage_key_hint = existing_key if (existing_key and force) else None
    else:
        final_name = _default_template_name(manager, category, naming_bg)
        storage_key_hint = None

    with Image.open(bg) as im:
        bg_w, bg_h = im.size

    tpl = Template(
        name=final_name,
        background_path=bg,
        screen_points=points,
        category=category,
        template_type="screen",
    )
    storage_key = manager.save(tpl, storage_key=storage_key_hint)

    if upscale_factor > 1.0 and os.path.exists(bg):
        os.remove(bg)  # 已被 manager.save 复制进 backgrounds_dir，清理放大产生的临时文件

    template_path = os.path.join(TEMPLATES_DIR, f"{storage_key}.json")
    with open(template_path, encoding="utf-8") as f:
        saved_data = json.load(f)
    persisted_bg_path = saved_data["background_path"]

    quality = _quad_quality(points, bg_w, bg_h)
    quality["method"] = detect_method
    quality["upscale_factor"] = round(upscale_factor, 3)
    quality["screen_width"] = round(screen_width, 1)

    if preview_out:
        preview_out = os.path.expanduser(preview_out)
    else:
        preview_out = os.path.join(os.path.dirname(bg) or ".", f"{final_name}_preview.jpg")
    _draw_quad_preview(persisted_bg_path, points, preview_out)

    result = {
        "name": final_name,
        "key": storage_key,
        "category": category,
        "template_path": template_path,
        "background_path": persisted_bg_path,
        "preview_path": preview_out,
        "screen_points": points,
        "quality": quality,
    }

    if json_result:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"模板已创建：{final_name}（key={storage_key}，分类={category}）")
        print(f"  模板文件：{template_path}")
        print(f"  背景图：{persisted_bg_path}")
        print(f"  预览图：{preview_out}")
        print(f"  四角坐标：{points}")
        print(f"  质量指标：面积占比={quality['area_ratio']}，宽高比={quality['aspect_ratio']}，"
              f"触及边缘={quality['touches_edge']}，识别方式={quality['method']}，"
              f"放大倍数={quality['upscale_factor']}，屏幕宽度={quality['screen_width']}px")


def bg_prompt_cmd(
    target: str,
    device: str,
    scene: str,
    greenscreen: bool,
    decor: str,
    light: str,
    angle: str,
    extra: str,
    json_result: bool,
):
    """拼装 AI 背景生成 prompt，与 GUI（AIGenerateTab._build_prompt 委托调用）共用同一份规则。"""
    from core.bg_prompt import build_prompt

    prompt = build_prompt(
        target, device, scene, greenscreen,
        decor=decor, light=light, angle=angle, extra=extra,
    )
    if json_result:
        print(json.dumps({"prompt": prompt}, ensure_ascii=False))
    else:
        print(prompt)


def dewatermark_cmd(input_path: str, output_path: str, strength: str, json_result: bool):
    """对单张图片做去水印处理，纯函数调用 core.dewatermark.dewatermark_image，无需 GUI 显示环境。"""
    from PIL import Image

    from core.dewatermark import dewatermark_image, _format_for_suffix, _prepare_for_save, _save_kwargs

    input_path = os.path.expanduser(input_path)
    output_path = os.path.expanduser(output_path)

    if not os.path.isfile(input_path):
        print(f"[错误] 输入图片不存在：{input_path}", file=sys.stderr)
        sys.exit(1)

    ext = os.path.splitext(output_path)[1].lower()
    save_format = _format_for_suffix(ext)
    save_kwargs = _save_kwargs(save_format)

    with Image.open(input_path) as img:
        result = dewatermark_image(img, strength)
    result = _prepare_for_save(result, save_format)

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    result.save(output_path, save_format, **save_kwargs)

    payload = {
        "input": input_path,
        "output": output_path,
        "strength": strength,
        "metadata_stripped": True,
    }
    if json_result:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"已去水印：{output_path}（强度={strength}）")


def main():
    parser = argparse.ArgumentParser(description="融景命令行工具")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("list-templates", help="列出所有可用模板")

    p = sub.add_parser("process", help="批量合成图片")
    p.add_argument("--input", nargs="+", required=True, help="输入：文件夹或图片路径（可多个）")
    p.add_argument("--templates", nargs="+", required=True, help="模板 key 或唯一模板名称（可多个）")
    p.add_argument("--output", required=True, help="输出目录")
    p.add_argument("--format", default="JPEG", choices=["PNG", "JPEG"], help="输出格式（默认 JPEG）")
    p.add_argument("--cover-source", default=None,
                   help="封面源目录：单一目录（含 0(1).jpg/0(2).jpg/0(3).jpg）或多主题父目录（按名称前 6 字符匹配）")

    c = sub.add_parser("collage", help="将一批图片拼接成单张拼图")
    c.add_argument("--input-dir", required=True, help="输入图片目录")
    c.add_argument("--output", required=True, help="输出文件路径")
    c.add_argument("--template", default=None, help="拼图预设名（如 1 / 2 / 逐字稿），与 --rows/--cols 二选一")
    c.add_argument("--rows", type=int, default=None, help="行数（与 --cols 搭配使用，不与 --template 同时给出）")
    c.add_argument("--cols", type=int, default=None, help="列数（与 --rows 搭配使用，不与 --template 同时给出）")
    c.add_argument("--pages", default=None, help="逗号分隔的 1-based 页序号，默认使用全部图片")
    c.add_argument("--json-result", action="store_true", help="以 JSON 格式输出结果到 stdout")

    ct = sub.add_parser("create-template", help="从背景图自动识别屏幕四角并创建模板")
    ct.add_argument("--bg", required=True, help="背景图路径")
    ct.add_argument("--name", default=None, help="模板名称（缺省自动从背景图文件名生成不冲突的名字）")
    ct.add_argument("--category", default="教师场景", help="模板分类（默认：教师场景）")
    ct.add_argument("--preview-out", default=None,
                     help="识别结果预览图输出路径（缺省为背景图同目录 <名字>_preview.jpg）")
    ct.add_argument("--json-result", action="store_true", help="以 JSON 格式输出结果到 stdout")
    ct.add_argument("--force", action="store_true", help="与现有同名同分类模板冲突时覆盖，不加则报错退出")
    ct.add_argument("--no-vlm", action="store_true",
                     help="禁用 VLM 粗定位融合，只用纯经典算法识别（默认走 VLM 融合，VLM 不可用时自动降级为纯经典）")
    ct.add_argument("--min-screen-width", type=int, default=1600,
                     help="屏幕区域最小宽度（像素，默认 1600）；识别出的屏幕宽度低于此值时放大整张背景图（最多 3 倍）以降低 PPT 内容压缩比")

    bp = sub.add_parser("bg-prompt", help="拼装 AI 背景生成 prompt（与 GUI 的 AIGenerateTab._build_prompt 共用同一份规则）")
    bp.add_argument("--target", required=True, help="背景场景（如：教室场景/台式机电脑/笔记本室内/文档纸张/自定义场景）")
    bp.add_argument("--device", default="", help="主体类型（如：希沃白板/台式显示器/笔记本电脑...）")
    bp.add_argument("--scene", default="", help="环境位置（如：小学教室/教师办公桌...）")
    bp.add_argument("--greenscreen", action="store_true", help="屏幕显示绿幕（默认关闭；文档纸张场景下会被自动忽略）")
    bp.add_argument("--decor", default="", help="教室元素或桌面摆件（如：学生背影/有植物...）")
    bp.add_argument("--light", default="", help="灯光（如：自然光/暖色灯光...）")
    bp.add_argument("--angle", default="", help="拍摄角度（如：正面平视/略偏侧角...）")
    bp.add_argument("--extra", default="", help="额外描述（可选）")
    bp.add_argument("--json-result", action="store_true", help="以 JSON 格式输出结果到 stdout")

    dw = sub.add_parser("dewatermark", help="对单张图片做去水印处理（core.dewatermark.dewatermark_image，无需 GUI 显示环境）")
    dw.add_argument("--input", required=True, help="输入图片路径")
    dw.add_argument("--output", required=True, help="输出图片路径")
    dw.add_argument("--strength", required=True, choices=["low", "medium", "high"], help="去水印强度")
    dw.add_argument("--json-result", action="store_true", help="以 JSON 格式输出结果到 stdout")

    args = parser.parse_args()

    if args.cmd == "list-templates":
        list_templates()
    elif args.cmd == "process":
        process(args.input, args.templates, args.output, args.format,
                cover_source=args.cover_source)
    elif args.cmd == "collage":
        collage(args.input_dir, args.output, args.template, args.rows, args.cols,
                args.pages, json_result=args.json_result)
    elif args.cmd == "create-template":
        create_template(args.bg, args.name, args.category, args.preview_out,
                         args.json_result, args.force, use_vlm=not args.no_vlm,
                         min_screen_width=args.min_screen_width)
    elif args.cmd == "bg-prompt":
        bg_prompt_cmd(args.target, args.device, args.scene, args.greenscreen,
                      args.decor, args.light, args.angle, args.extra, args.json_result)
    elif args.cmd == "dewatermark":
        dewatermark_cmd(args.input, args.output, args.strength, args.json_result)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
