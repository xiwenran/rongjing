#!/usr/bin/env python3
"""
融景 CLI — 命令行接口，供 Claude Code Skill 调用。
不依赖 PyQt6，直接调用 core/ 的纯 Python 处理函数。

用法：
  python cli.py list-templates
  python cli.py process --input <文件夹或图片路径...> --templates <模板名...> --output <输出目录> [--format PNG|JPEG]
"""

import argparse
import json
import os
import re
import sys

TEMPLATES_DIR = os.path.expanduser("~/Library/Application Support/融景/templates")


def natural_sort_key(s: str):
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", s)]


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tiff"}


def load_template(name: str):
    path = os.path.join(TEMPLATES_DIR, f"{name}.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"模板不存在：{name}（{path}）")
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return d


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
                templates.append({
                    "name": d.get("name", fn[:-5]),
                    "background": os.path.basename(bg),
                    "background_exists": os.path.exists(bg),
                })
            except Exception as e:
                templates.append({"name": fn[:-5], "error": str(e)})
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


def process(inputs: list[str], template_names: list[str], output_dir: str, fmt: str):
    # 延迟导入，避免系统没装 Pillow 时 list-templates 也报错
    sys.path.insert(0, os.path.dirname(__file__))
    from PIL import Image
    from core.image_processor import precompute_template_cache, embed_image_pil_fast

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
        tpl = load_template(tpl_name)
        bg_path = tpl["background_path"]
        if not os.path.exists(bg_path):
            print(f"[错误] 模板 {tpl_name} 的背景图不存在：{bg_path}", file=sys.stderr)
            sys.exit(1)

        bg_img = Image.open(bg_path)
        cache = precompute_template_cache(bg_img, tpl["screen_points"])

        out_sub = os.path.join(output_dir, tpl_name)
        os.makedirs(out_sub, exist_ok=True)

        for i, img_path in enumerate(images, 1):
            ppt_img = Image.open(img_path)
            result = embed_image_pil_fast(ppt_img, cache)

            out_path = os.path.join(out_sub, f"{i}{ext}")
            result.save(out_path, **save_kwargs)
            done += 1
            print(f"[{done}/{total}] 模板={tpl_name} 图={i} → {out_path}")

    print(f"\n完成！共处理 {done} 张，输出目录：{output_dir}")


def main():
    parser = argparse.ArgumentParser(description="融景命令行工具")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("list-templates", help="列出所有可用模板")

    p = sub.add_parser("process", help="批量合成图片")
    p.add_argument("--input", nargs="+", required=True, help="输入：文件夹或图片路径（可多个）")
    p.add_argument("--templates", nargs="+", required=True, help="模板名称（可多个）")
    p.add_argument("--output", required=True, help="输出目录")
    p.add_argument("--format", default="JPEG", choices=["PNG", "JPEG"], help="输出格式（默认 JPEG）")

    args = parser.parse_args()

    if args.cmd == "list-templates":
        list_templates()
    elif args.cmd == "process":
        process(args.input, args.templates, args.output, args.format)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
