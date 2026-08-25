import math
from typing import List, Tuple

from PIL import Image, ImageColor


def _validate_collage_args(layout: str, rows: int, cols: int, gap: int, padding: int) -> None:
    if layout not in ("grid", "horizontal", "vertical", "hero"):
        raise ValueError("layout must be 'grid', 'horizontal', 'vertical', or 'hero'")
    if rows < 1 or rows > 4:
        raise ValueError("rows must be between 1 and 4")
    if cols < 1 or cols > 6:
        raise ValueError("cols must be between 1 and 6")
    if gap < 0 or gap > 20:
        raise ValueError("gap must be between 0 and 20")
    if padding < 0 or padding > 40:
        raise ValueError("padding must be between 0 and 40")


def _image_aspect_ratio(images: List[Image.Image], cell_aspect_ratio: float) -> float:
    if cell_aspect_ratio > 0:
        return cell_aspect_ratio
    if images:
        width, height = images[0].size
        if height > 0:
            return width / height
    return 1.0


def _resize_center_crop(img: Image.Image, size: Tuple[int, int]) -> Image.Image:
    cell_w, cell_h = size
    src_w, src_h = img.size
    scale = max(cell_w / src_w, cell_h / src_h)
    resized_w = max(1, int(round(src_w * scale)))
    resized_h = max(1, int(round(src_h * scale)))

    resized = img.convert("RGB").resize((resized_w, resized_h), Image.LANCZOS)
    left = max(0, (resized_w - cell_w) // 2)
    top = max(0, (resized_h - cell_h) // 2)
    return resized.crop((left, top, left + cell_w, top + cell_h))


def create_collage(
    images: List[Image.Image],
    layout: str,
    rows: int,
    cols: int,
    gap: int = 4,
    padding: int = 0,
    background_color: str = "#FFFFFF",
    cell_aspect_ratio: float = 0,
    output_width: int = 1920,
    output_height: int = 0,
) -> Image.Image:
    _validate_collage_args(layout, rows, cols, gap, padding)
    if output_width <= padding * 2 + gap * (cols - 1):
        raise ValueError("output_width is too small for the requested collage")
    if output_height < 0:
        raise ValueError("output_height must be >= 0")

    bg = ImageColor.getrgb(background_color)
    aspect_ratio = _image_aspect_ratio(images, cell_aspect_ratio)
    if aspect_ratio <= 0:
        raise ValueError("cell_aspect_ratio must be > 0 when provided")

    inner_w = output_width - padding * 2
    if layout == "hero":
        thumb_grid_w = inner_w - gap * (cols - 1)
        cell_w = thumb_grid_w // cols
        hero_w = inner_w

        if output_height == 0:
            hero_h = max(1, int(round(hero_w / aspect_ratio)))
            cell_h = max(1, int(round(cell_w / aspect_ratio)))
            output_height = padding * 2 + hero_h + gap + rows * cell_h + gap * (rows - 1)
        else:
            if output_height <= padding * 2 + gap * rows:
                raise ValueError("output_height is too small for the requested collage")
            available_h = output_height - padding * 2 - gap - gap * (rows - 1)
            hero_h = max(1, int(round(available_h * 0.58)))
            cell_h = max(1, (available_h - hero_h) // rows)

        result = Image.new("RGB", (output_width, output_height), bg)
        if images:
            result.paste(_resize_center_crop(images[0], (hero_w, hero_h)), (padding, padding))

        thumb_y = padding + hero_h + gap
        total_thumbs = rows * cols
        for idx, img in enumerate(images[1:1 + total_thumbs]):
            row = idx // cols
            col = idx % cols
            x = padding + col * (cell_w + gap)
            y = thumb_y + row * (cell_h + gap)
            result.paste(_resize_center_crop(img, (cell_w, cell_h)), (x, y))

        return result

    inner_w -= gap * (cols - 1)
    cell_w = inner_w // cols

    if output_height == 0:
        cell_h = max(1, int(round(cell_w / aspect_ratio)))
        output_height = padding * 2 + rows * cell_h + gap * (rows - 1)
    else:
        if output_height <= padding * 2 + gap * (rows - 1):
            raise ValueError("output_height is too small for the requested collage")
        inner_h = output_height - padding * 2 - gap * (rows - 1)
        cell_h = inner_h // rows

    result = Image.new("RGB", (output_width, output_height), bg)
    total_cells = rows * cols

    for idx, img in enumerate(images[:total_cells]):
        row = idx // cols
        col = idx % cols
        x = padding + col * (cell_w + gap)
        y = padding + row * (cell_h + gap)
        cell_img = _resize_center_crop(img, (cell_w, cell_h))
        result.paste(cell_img, (x, y))

    return result


def calculate_auto_split(
    total_pages: int,
    total_output_images: int,
    cells_per_collage: int,
) -> List[Tuple[int, int]]:
    if total_pages <= 0 or cells_per_collage <= 0:
        return []

    if total_output_images <= 0:
        n = math.ceil(total_pages / cells_per_collage)
        ranges = []
        for i in range(n):
            start = i * cells_per_collage
            end = min(start + cells_per_collage, total_pages)
            ranges.append((start, end))
        return ranges

    max_capacity = total_output_images * cells_per_collage
    used_pages = min(total_pages, max_capacity)

    # 前满后补：每张先填满 cells_per_collage，仅最后一张放剩余
    ranges = []
    cursor = 0
    for _ in range(total_output_images):
        if cursor >= used_pages:
            break
        end = min(cursor + cells_per_collage, used_pages)
        ranges.append((cursor, end))
        cursor = end
    return ranges


def calculate_auto_layout(num_images: int) -> tuple[int, int]:
    """根据图片数量自动选最佳 (rows, cols)，优先竖版 3:4 比例。

    偏好行多列少，拼出接近 3:4 竖版比例。
    列数优先 1-2 列，3 行以内。
    """
    if num_images <= 0:
        return (1, 1)
    if num_images == 1:
        return (1, 1)

    best: tuple[tuple[float, ...], tuple[int, int]] | None = None
    for rows in range(1, 5):          # 行数上限 4（与 UI _row_spin 一致）
        for cols in range(1, 7):      # 列数上限 6（与 UI _col_spin 一致）
            cells = rows * cols
            if cells < num_images:
                continue
            waste = cells - num_images
            # 综合评分：竖版优先，但不能为了竖版浪费太多格子
            ratio_distance = abs((rows / cols) - 1.333)  # 接近 4:3 竖版比例
            # 极端比例惩罚：单行或单列的超长条不实用
            extreme_penalty = 0.6 if (rows == 1 and cols >= 3) or (cols == 1 and rows >= 3) else 0
            # 方向得分 + waste 得分 合并为主排序键
            # 竖版(rows>cols): orientation_cost=0
            # 正方(rows==cols): orientation_cost=0.5
            # 横版(rows<cols): orientation_cost=2
            orientation_cost = 0 if rows > cols else (0.5 if rows == cols else 2)
            # waste 代价：每个空格 0.4 分
            waste_cost = waste * 0.4
            primary = orientation_cost + waste_cost + extreme_penalty
            score = (primary, ratio_distance)
            if best is None or score < best[0]:
                best = (score, (rows, cols))

    if best is None:
        return (4, 6)
    return best[1]


def calculate_dropped_pages(
    total_pages: int,
    total_output_images: int,
    cells_per_collage: int,
) -> int:
    if total_output_images <= 0 or total_pages <= 0:
        return 0
    max_capacity = total_output_images * cells_per_collage
    return max(0, total_pages - max_capacity)
