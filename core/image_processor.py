from typing import List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter


def order_points(pts: List[List[float]]) -> np.ndarray:
    """Reorder 4 points as [TL, TR, BR, BL] regardless of input order."""
    pts = np.array(pts, dtype=np.float32)
    s = pts.sum(axis=1)
    tl = pts[np.argmin(s)]
    br = pts[np.argmax(s)]
    d = np.diff(pts, axis=1).flatten()
    tr = pts[np.argmin(d)]
    bl = pts[np.argmax(d)]
    return np.array([tl, tr, br, bl], dtype=np.float32)


def _perspective_coeffs(src_pts: np.ndarray, dst_pts: np.ndarray) -> tuple:
    """
    Compute PIL PERSPECTIVE 8 coefficients mapping dst→src.
    PIL formula:  x_in = (a*x + b*y + c) / (g*x + h*y + 1)
                  y_in = (d*x + e*y + f) / (g*x + h*y + 1)
    (x, y)       = destination (bg) coordinate
    (x_in, y_in) = source (ppt) coordinate
    """
    matrix = []
    rhs = []
    for (xd, yd), (xs, ys) in zip(dst_pts, src_pts):
        matrix.append([xd, yd, 1, 0,  0,  0, -xs * xd, -xs * yd])
        matrix.append([0,  0,  0, xd, yd,  1, -ys * xd, -ys * yd])
        rhs.extend([xs, ys])
    A = np.array(matrix, dtype=np.float64)
    b = np.array(rhs, dtype=np.float64)
    return tuple(np.linalg.solve(A, b))


def embed_image_pil(
    ppt_img: Image.Image,
    bg_img: Image.Image,
    points: List[List[float]],
    feather: int = 2,
) -> Image.Image:
    """
    Perspective-warp ppt_img into the quadrilateral defined by points on bg_img.
    feather: Gaussian blur radius applied to the mask edge for smooth blending.
    Pure PIL/numpy implementation — no cv2 dependency.
    """
    ppt_img = ppt_img.convert("RGBA")
    bg_img  = bg_img.convert("RGBA")

    bg_w, bg_h = bg_img.size
    ppt_w, ppt_h = ppt_img.size

    src_pts = np.float64([[0, 0], [ppt_w, 0], [ppt_w, ppt_h], [0, ppt_h]])
    dst_pts = order_points(points).astype(np.float64)

    # PIL PERSPECTIVE maps OUTPUT(bg) → INPUT(ppt), so dst→src coefficients
    coeffs = _perspective_coeffs(src_pts, dst_pts)
    warped = ppt_img.transform(
        (bg_w, bg_h), Image.PERSPECTIVE, coeffs, Image.BICUBIC
    )

    # Build mask for the quadrilateral region
    mask = Image.new("L", (bg_w, bg_h), 0)
    draw = ImageDraw.Draw(mask)
    poly = [(float(p[0]), float(p[1])) for p in dst_pts]
    draw.polygon(poly, fill=255)

    # Inward feathering: erode (MinFilter≈morphological erosion) then blur,
    # clip blurred result to the original hard quad boundary.
    if feather > 0:
        mask_orig = np.array(mask)
        mask = mask.filter(ImageFilter.MinFilter(3))          # 3×3 erosion
        mask = mask.filter(ImageFilter.GaussianBlur(feather)) # soften edge
        mask = Image.fromarray(
            np.where(mask_orig >= 128, np.array(mask), 0).astype(np.uint8)
        )

    # Alpha blend: result = (1 - mask) * bg + mask * warped
    bg_arr     = np.array(bg_img,  dtype=np.float32)
    warped_arr = np.array(warped,  dtype=np.float32)
    mask_f     = np.array(mask,    dtype=np.float32)[:, :, np.newaxis] / 255.0

    result = (1.0 - mask_f) * bg_arr + mask_f * warped_arr
    return Image.fromarray(result.astype(np.uint8), "RGBA")


def precompute_template_cache(
    bg_img: Image.Image,
    points: List[List[float]],
    feather: int = 2,
    ppt_size: Optional[Tuple[int, int]] = None,
) -> dict:
    """Precompute mask and background array for a template.

    Call once per template, then pass the returned cache dict to
    embed_image_pil_fast() for each image/frame.  Avoids redundant mask
    computation when processing many images or video frames with the same
    template.

    ppt_size: if provided, also pre-compute perspective coefficients for that
    source resolution (useful for video where all frames are the same size,
    enabling safe multi-threaded use of the cache).
    """
    bg_img = bg_img.convert("RGB")   # 3-channel: 25% less memory/compute than RGBA
    bg_w, bg_h = bg_img.size
    dst_pts = order_points(points).astype(np.float64)

    mask = Image.new("L", (bg_w, bg_h), 0)
    draw = ImageDraw.Draw(mask)
    poly = [(float(p[0]), float(p[1])) for p in dst_pts]
    draw.polygon(poly, fill=255)
    if feather > 0:
        mask_orig = np.array(mask)
        mask = mask.filter(ImageFilter.MinFilter(3))
        mask = mask.filter(ImageFilter.GaussianBlur(feather))
        mask = Image.fromarray(
            np.where(mask_orig >= 128, np.array(mask), 0).astype(np.uint8)
        )

    cache: dict = {
        "dst_pts": dst_pts,
        "bg_size": (bg_w, bg_h),
        "mask_f":  np.array(mask, dtype=np.float32)[:, :, np.newaxis] / 255.0,
        "bg_arr":  np.array(bg_img, dtype=np.float32),
    }

    # Pre-compute perspective coefficients if source size is known (e.g. video).
    # This makes the cache fully read-only during parallel use.
    if ppt_size is not None:
        ppt_w, ppt_h = ppt_size
        src_pts = np.float64([[0, 0], [ppt_w, 0], [ppt_w, ppt_h], [0, ppt_h]])
        cache["_coeffs"]     = _perspective_coeffs(src_pts, dst_pts)
        cache["_coeffs_key"] = ppt_size

    return cache


def embed_image_pil_fast(ppt_img: Image.Image, cache: dict) -> Image.Image:
    """Embed using a precomputed template cache (see precompute_template_cache).

    Uses BILINEAR interpolation and RGB processing (3 channels) for maximum
    speed. Returns an RGB image.

    Uses precomputed coefficients when they match the current source size.
    Mismatched sizes compute local coefficients without writing to `cache`,
    so a shared cache remains safe for concurrent use in a thread pool.
    """
    ppt_img = ppt_img.convert("RGB")   # 3 channels — faster transform & blend
    ppt_w, ppt_h = ppt_img.size

    size_key = (ppt_w, ppt_h)
    coeffs = cache.get("_coeffs") if cache.get("_coeffs_key") == size_key else None
    if coeffs is None:
        src_pts = np.float64([[0, 0], [ppt_w, 0], [ppt_w, ppt_h], [0, ppt_h]])
        coeffs = _perspective_coeffs(src_pts, cache["dst_pts"])

    bg_w, bg_h = cache["bg_size"]
    # BILINEAR is ~2-3× faster than BICUBIC; for screen content the quality
    # difference is imperceptible after perspective distortion.
    warped = ppt_img.transform(
        (bg_w, bg_h), Image.PERSPECTIVE, coeffs, Image.BILINEAR
    )

    warped_arr = np.array(warped, dtype=np.float32)
    result = (1.0 - cache["mask_f"]) * cache["bg_arr"] + cache["mask_f"] * warped_arr
    return Image.fromarray(result.astype(np.uint8), "RGB")


def embed_document_paper_pil(
    paper_img: Image.Image,
    bg_img: Image.Image,
    points: List[List[float]],
    render_preset: str = "clear",
    feather: int = 3,
) -> Image.Image:
    """Place a document/page image onto a real paper or desktop background.

    Unlike screen compositing, the page should inherit some background light and
    paper texture. The source remains dominant so text stays readable.
    """
    paper_img = paper_img.convert("RGB")
    bg_img = bg_img.convert("RGB")
    paper_w, paper_h = paper_img.size

    trim_x = max(2, int(paper_w * 0.006))
    trim_y = max(1, int(paper_h * 0.003))
    if paper_w > trim_x * 2 and paper_h > trim_y * 2:
        paper_img = paper_img.crop((trim_x, trim_y, paper_w - trim_x, paper_h - trim_y))
        paper_w, paper_h = paper_img.size
    paper_img = _suppress_document_edge_lines(paper_img)

    bg_w, bg_h = bg_img.size

    preset = "paper"
    paper_img = ImageEnhance.Contrast(paper_img).enhance(1.08)
    paper_img = ImageEnhance.Sharpness(paper_img).enhance(1.10)

    src_pts = np.float64([[0, 0], [paper_w, 0], [paper_w, paper_h], [0, paper_h]])
    dst_pts = order_points(points).astype(np.float64)
    coeffs = _perspective_coeffs(src_pts, dst_pts)
    warped = paper_img.transform(
        (bg_w, bg_h), Image.PERSPECTIVE, coeffs, Image.BILINEAR
    )
    edge_fade = _document_edge_fade_mask(paper_w, paper_h, bg_w, bg_h, coeffs)

    mask = _polygon_mask((bg_w, bg_h), dst_pts)
    if feather > 0:
        mask_orig = np.array(mask)
        mask = mask.filter(ImageFilter.MinFilter(3))
        mask = mask.filter(ImageFilter.GaussianBlur(feather))
        mask = Image.fromarray(
            np.where(mask_orig >= 128, np.array(mask), 0).astype(np.uint8)
        )

    bg_arr = np.array(bg_img, dtype=np.float32)
    warped_arr = np.array(warped, dtype=np.float32)
    mask_f = np.array(mask, dtype=np.float32)[:, :, np.newaxis] / 255.0

    src_luma = (
        0.299 * warped_arr[:, :, 0]
        + 0.587 * warped_arr[:, :, 1]
        + 0.114 * warped_arr[:, :, 2]
    )[:, :, np.newaxis] / 255.0
    src_sat = (
        warped_arr.max(axis=2, keepdims=True)
        - warped_arr.min(axis=2, keepdims=True)
    ) / 255.0

    ink = np.clip(warped_arr / 255.0, 0.0, 1.0)
    multiply = bg_arr * np.clip(0.08 + ink * 0.92, 0.0, 1.0)
    content = np.clip((1.0 - src_luma) * 2.2 + src_sat * 1.25, 0.0, 1.0)
    content = np.power(content, 0.72)

    restore = np.clip(0.08 + src_sat * 0.16 + (1.0 - src_luma) * 0.08, 0.0, 0.28)
    page = multiply * (1.0 - restore * content) + warped_arr * (restore * content)
    alpha = 0.96
    noise_sigma = 0.75
    blur_radius = 0.12
    cast = np.array([0.0, -1.0, -2.0], dtype=np.float32)

    page = page + cast
    rng = np.random.default_rng(17)
    page = page + rng.normal(0, noise_sigma, (bg_h, bg_w, 1)).astype(np.float32)
    page = (page - 128.0) * 0.985 + 128.0
    page_img = Image.fromarray(np.clip(page, 0, 255).astype(np.uint8), "RGB")
    page = np.array(page_img.filter(ImageFilter.GaussianBlur(blur_radius)), dtype=np.float32)
    blend_f = mask_f * edge_fade * alpha
    result = (1.0 - blend_f) * bg_arr + blend_f * page
    return Image.fromarray(np.clip(result, 0, 255).astype(np.uint8), "RGB")


def _polygon_mask(size: tuple, pts: np.ndarray) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.polygon([(float(p[0]), float(p[1])) for p in pts], fill=255)
    return mask


def _document_edge_fade_mask(
    src_w: int,
    src_h: int,
    bg_w: int,
    bg_h: int,
    coeffs: tuple,
    fade_ratio: float = 0.008,
) -> np.ndarray:
    fade_x = max(2, int(src_w * fade_ratio))
    fade_y = max(2, int(src_h * fade_ratio))
    mask = Image.new("L", (src_w, src_h), 255)
    arr = np.array(mask, dtype=np.float32)
    x = np.minimum(np.arange(src_w), np.arange(src_w)[::-1])
    y = np.minimum(np.arange(src_h), np.arange(src_h)[::-1])
    edge = np.minimum(x[np.newaxis, :] / fade_x, y[:, np.newaxis] / fade_y)
    arr *= np.clip(edge, 0.0, 1.0)
    mask = Image.fromarray(arr.astype(np.uint8), "L")
    warped = mask.transform((bg_w, bg_h), Image.PERSPECTIVE, coeffs, Image.BILINEAR)
    return np.array(warped, dtype=np.float32)[:, :, np.newaxis] / 255.0


def _suppress_document_edge_lines(
    image: Image.Image,
    band_ratio: float = 0.022,
    strength: float = 0.42,
) -> Image.Image:
    """Fade low-saturation gray guide lines near document edges only."""
    arr = np.array(image.convert("RGB"), dtype=np.float32)
    h, w = arr.shape[:2]
    band_x = max(4, int(w * band_ratio))
    band_y = max(4, int(h * band_ratio))

    x_dist = np.minimum(np.arange(w), np.arange(w)[::-1])
    y_dist = np.minimum(np.arange(h), np.arange(h)[::-1])
    edge_band = np.maximum(
        np.clip(1.0 - x_dist[np.newaxis, :] / band_x, 0.0, 1.0),
        np.clip(1.0 - y_dist[:, np.newaxis] / band_y, 0.0, 1.0),
    )[:, :, np.newaxis]

    luma = (
        0.299 * arr[:, :, 0]
        + 0.587 * arr[:, :, 1]
        + 0.114 * arr[:, :, 2]
    )[:, :, np.newaxis]
    sat = (arr.max(axis=2, keepdims=True) - arr.min(axis=2, keepdims=True)) / 255.0
    gray_line = np.clip((1.0 - sat * 7.0) * (1.0 - np.abs(luma - 188.0) / 78.0), 0.0, 1.0)
    fade = edge_band * gray_line * strength
    arr = arr * (1.0 - fade) + 255.0 * fade
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def _quad_aspect_ratio(points: List[List[float]]) -> float:
    """候选四边形的等效宽高比：上下两条边长度均值 / 左右两条边长度均值。"""
    tl, tr, br, bl = order_points(points).astype(np.float64)
    top = np.linalg.norm(tr - tl)
    bottom = np.linalg.norm(br - bl)
    left = np.linalg.norm(bl - tl)
    right = np.linalg.norm(br - tr)
    avg_w = (top + bottom) / 2.0
    avg_h = (left + right) / 2.0
    return float(avg_w / max(avg_h, 1e-6))


def _edge_median_color(img: Image.Image) -> tuple:
    """取图片四条边缘像素的中位数颜色，作为等比适配补白色（贴近源图纸面/背景色，
    比固定纯白更不容易在合成后露出突兀的白边）。"""
    arr = np.array(img.convert("RGB"))
    h, w = arr.shape[:2]
    border = np.concatenate([
        arr[0, :, :], arr[-1, :, :], arr[:, 0, :], arr[:, -1, :],
    ], axis=0)
    med = np.median(border, axis=0)
    return tuple(int(round(v)) for v in med)


def fit_source_to_quad(
    img: Image.Image,
    points: List[List[float]],
    mode: str = "stretch",
    fill_color: Optional[tuple] = None,
) -> Image.Image:
    """按目标四边形的等效宽高比，对源图做等比适配预处理。

    mode="stretch"（默认）：原样返回 img，后续透视变换会把 img 的四角直接映射到
    目标四边形四角，源图与目标宽高比不一致时内容会被拉伸——这是合成器一直以来的
    行为，零变化。

    mode="contain"：先按目标四边形的等效宽高比（上下边均值 / 左右边均值，见
    _quad_aspect_ratio）对源图做 letterbox——等比缩放后居中，用 fill_color 补白
    两侧留白，让补白后画布的宽高比恰好等于目标四边形——避免源图内容在透视映射
    时被非均匀拉伸变形。补白色默认取源图四条边缘像素的中位数（贴近源图纸面/
    背景色，通常比固定纯白更不突兀）；调用方可传 fill_color 覆盖（如 (250, 250,
    250) 对应纯白 #FAFAFA）。

    mode="cover"：等比缩放铺满目标宽高比后居中裁掉溢出——内容满版直达纸边，
    比例不合的部分「延伸出纸面」被裁切，不留任何补白边（2026-07-25 用户看
    contain 首版合成后裁决：参考笔记图都是内容铺满整张纸，留白边显假）。适合
    封面/版式饱满的页面；正文密排页顶底可能被裁，须按页型选择。
    """
    if mode not in ("contain", "cover"):
        return img

    img = img.convert("RGB")
    w, h = img.size
    if w <= 0 or h <= 0:
        return img
    src_aspect = w / float(h)
    target_aspect = _quad_aspect_ratio(points)

    if abs(src_aspect - target_aspect) < 1e-3:
        return img

    if mode == "cover":
        # 等比铺满：从源图中裁出目标比例的最大内接矩形（居中），溢出部分裁掉
        if src_aspect > target_aspect:
            crop_w = max(1, round(h * target_aspect))
            crop_h = h
        else:
            crop_w = w
            crop_h = max(1, round(w / target_aspect))
        off_x = (w - crop_w) // 2
        off_y = (h - crop_h) // 2
        return img.crop((off_x, off_y, off_x + crop_w, off_y + crop_h))

    if fill_color is None:
        fill_color = _edge_median_color(img)

    if src_aspect > target_aspect:
        # 源图比目标更「宽」：以源图宽度为基准，画布拉高，上下补白
        canvas_w = w
        canvas_h = max(1, round(w / target_aspect))
    else:
        # 源图比目标更「窄/高」：以源图高度为基准，画布拉宽，左右补白
        canvas_h = h
        canvas_w = max(1, round(h * target_aspect))

    canvas = Image.new("RGB", (canvas_w, canvas_h), fill_color)
    off_x = (canvas_w - w) // 2
    off_y = (canvas_h - h) // 2
    canvas.paste(img, (off_x, off_y))
    return canvas


def embed_image(
    ppt_path: str,
    bg_path: str,
    points: List[List[float]],
    output_size: Optional[Tuple[int, int]] = None,
    feather: int = 2,
) -> Image.Image:
    """Load from paths, embed, and optionally resize output."""
    ppt_img = Image.open(ppt_path)
    bg_img  = Image.open(bg_path)
    result  = embed_image_pil(ppt_img, bg_img, points, feather=feather)
    if output_size:
        result = result.resize(output_size, Image.LANCZOS)
    return result
