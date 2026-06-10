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

    preset = (render_preset or "clear").lower()
    if preset == "paper":
        paper_img = ImageEnhance.Contrast(paper_img).enhance(1.06)
        paper_img = ImageEnhance.Sharpness(paper_img).enhance(1.08)
    elif preset == "warm":
        paper_img = ImageEnhance.Contrast(paper_img).enhance(1.04)
        arr = np.array(paper_img, dtype=np.float32)
        arr[:, :, 0] *= 1.035
        arr[:, :, 1] *= 1.012
        arr[:, :, 2] *= 0.955
        paper_img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")
    else:
        preset = "clear"
        paper_img = ImageEnhance.Contrast(paper_img).enhance(1.10)
        paper_img = ImageEnhance.Sharpness(paper_img).enhance(1.12)

    src_pts = np.float64([[0, 0], [paper_w, 0], [paper_w, paper_h], [0, paper_h]])
    dst_pts = order_points(points).astype(np.float64)
    coeffs = _perspective_coeffs(src_pts, dst_pts)
    warped = paper_img.transform(
        (bg_w, bg_h), Image.PERSPECTIVE, coeffs, Image.BILINEAR
    )
    edge_fade = _document_edge_fade_mask(paper_w, paper_h, bg_w, bg_h, coeffs)

    mask = Image.new("L", (bg_w, bg_h), 0)
    draw = ImageDraw.Draw(mask)
    draw.polygon([(float(p[0]), float(p[1])) for p in dst_pts], fill=255)
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

    bg_luma = (
        0.299 * bg_arr[:, :, 0]
        + 0.587 * bg_arr[:, :, 1]
        + 0.114 * bg_arr[:, :, 2]
    )[:, :, np.newaxis]
    paper_luma = bg_luma[mask_f[:, :, :1] > 0.35]
    paper_white = float(np.percentile(paper_luma, 82)) if paper_luma.size else 220.0
    white_scale = np.clip(238.0 / max(paper_white, 1.0), 0.88, 1.42)
    paper_base = np.clip(bg_arr * white_scale, 0, 255)

    multiply = paper_base * warped_arr / 255.0
    content = np.clip((1.0 - src_luma) * 2.2 + src_sat * 1.25, 0.0, 1.0)
    content = np.power(content, 0.72)

    if preset == "paper":
        restore = np.clip(0.28 + src_sat * 0.42 + (1.0 - src_luma) * 0.18, 0.0, 0.64)
        page = multiply * (1.0 - restore * content) + warped_arr * (restore * content)
        alpha = 0.997
        noise_sigma = 0.75
        blur_radius = 0.12
        cast = np.array([0.0, -1.0, -2.0], dtype=np.float32)
    elif preset == "warm":
        restore = np.clip(0.30 + src_sat * 0.38 + (1.0 - src_luma) * 0.16, 0.0, 0.60)
        page = multiply * (1.0 - restore * content) + warped_arr * (restore * content)
        alpha = 0.996
        noise_sigma = 0.65
        blur_radius = 0.14
        cast = np.array([5.0, 2.0, -4.0], dtype=np.float32)
    else:
        restore = np.clip(0.34 + src_sat * 0.36 + (1.0 - src_luma) * 0.14, 0.0, 0.62)
        page = multiply * (1.0 - restore * content) + warped_arr * (restore * content)
        alpha = 0.998
        noise_sigma = 0.45
        blur_radius = 0.08
        cast = np.array([0.0, 0.0, 0.0], dtype=np.float32)

    page = page + cast
    rng = np.random.default_rng(17 if preset == "paper" else 23 if preset == "warm" else 11)
    page = page + rng.normal(0, noise_sigma, (bg_h, bg_w, 1)).astype(np.float32)
    page = (page - 128.0) * 0.985 + 128.0
    page_img = Image.fromarray(np.clip(page, 0, 255).astype(np.uint8), "RGB")
    page = np.array(page_img.filter(ImageFilter.GaussianBlur(blur_radius)), dtype=np.float32)
    blend_f = mask_f * edge_fade * alpha
    result = (1.0 - blend_f) * bg_arr + blend_f * page
    return Image.fromarray(np.clip(result, 0, 255).astype(np.uint8), "RGB")


def _document_edge_fade_mask(
    src_w: int,
    src_h: int,
    bg_w: int,
    bg_h: int,
    coeffs: tuple,
    fade_ratio: float = 0.015,
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
    band_ratio: float = 0.035,
    strength: float = 0.62,
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
