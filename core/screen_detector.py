from __future__ import annotations

import os
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image


PointList = list[list[float]]

_WORK_MAX_DIM = 900          # 检测阶段工作分辨率上限（提速+降噪）
_REFINE_MARGIN_RATIO = 0.08  # 精修裁剪区域相对四边形尺寸外扩比例
_MIN_AREA_RATIO = 0.18
_MAX_AREA_RATIO = 0.62
_ASPECT_MIN = 1.05
_ASPECT_MAX = 2.2


def _log_detect_error(stage: str) -> None:
    """把识别失败的完整堆栈写入数据目录日志，打包 .app 里无控制台时这是唯一现场。"""
    import traceback
    try:
        from main import get_data_dir
        log_path = os.path.join(get_data_dir(), "detect_error.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"===== {stage} =====\n{traceback.format_exc()}\n")
    except Exception:
        pass


def _import_cv2():
    try:
        import cv2
        return cv2
    except Exception:
        _log_detect_error("import cv2")
        return None


def detect_screen_points(image) -> Optional[PointList]:
    """Detect screen quadrilateral points in TL, TR, BR, BL order.

    算法分三步：
    1) 在缩小的工作图上用多种互补方法（暗区/亮区/Canny/CLAHE/自适应阈值）生成大量四边形候选；
    2) 用统一的「边界梯度贴合度 + 内部纹理/亮度惩罚」打分，取分数最高的候选；
    3) 把该候选映射回原图分辨率，再用局部直线拟合精修四个角点（精修失败则保留粗定位结果）。
    """
    cv2 = _import_cv2()
    if cv2 is None:
        return None
    rgb = _load_rgb_array(image)
    if rgb is None:
        return None

    try:
        h, w = rgb.shape[:2]
        gray_full = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)

        scale = min(1.0, _WORK_MAX_DIM / float(max(w, h)))
        if scale < 1.0:
            small = cv2.resize(gray_full, (int(round(w * scale)), int(round(h * scale))),
                                interpolation=cv2.INTER_AREA)
        else:
            small = gray_full
        sh, sw = small.shape[:2]

        candidates = _gather_candidates(cv2, small)
        if not candidates:
            return None

        gx = cv2.Sobel(small, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(small, cv2.CV_32F, 0, 1, ksize=3)
        mag = cv2.magnitude(gx, gy)
        global_mean_mag = float(np.mean(mag)) + 1e-6

        best_quad = None
        best_score = -1.0
        for quad, prior in candidates:
            score = _score_quad(cv2, gx, gy, mag, global_mean_mag, small, quad, sw, sh) * prior
            if score > best_score:
                best_score = score
                best_quad = quad

        if best_quad is None or best_score <= 0:
            return None

        inv = 1.0 / scale if scale > 0 else 1.0
        quad_full = _clamp_points([[x * inv, y * inv] for x, y in best_quad], w, h)

        quad_full = _refine_quad_guarded(cv2, gray_full, quad_full, w, h, scale,
                                          gx, gy, mag, global_mean_mag, small, sw, sh)

        return _clamp_points(quad_full, w, h)
    except Exception:
        _log_detect_error("detect")
        return None


def _refine_quad_guarded(cv2, gray_full: np.ndarray, quad_full: PointList, w: int, h: int,
                          scale: float, gx: np.ndarray, gy: np.ndarray, mag: np.ndarray,
                          global_mean_mag: float, small: np.ndarray, sw: int, sh: int) -> PointList:
    """`_refine_quad` 精修「越修越歪」防护：精修后不无条件采用，而是用同一套
    `_score_quad` 边界梯度贴合度打分，在工作图坐标系下比较精修前后两个四边形，
    精修后分数没有更高就丢弃精修结果、保留精修前（粗定位）的四边形。

    （实测案例：10_bg.JPG 精修前约 115px 误差，精修后被带偏到约 141px——本防护
    正是为了拦住这类「精修反而变差」的情况。）
    """
    refined_quad = _refine_quad(cv2, gray_full, quad_full, w, h)
    if refined_quad is None:
        return quad_full

    pre_small = [[x * scale, y * scale] for x, y in quad_full]
    post_small = [[x * scale, y * scale] for x, y in refined_quad]

    try:
        score_pre = _score_quad(cv2, gx, gy, mag, global_mean_mag, small, pre_small, sw, sh)
        score_post = _score_quad(cv2, gx, gy, mag, global_mean_mag, small, post_small, sw, sh)
    except Exception:
        return quad_full

    return refined_quad if score_post >= score_pre else quad_full


# ---------------------------------------------------------------------------
# VLM 融合识别：经典候选 + ark-worker 粗框，IoU 作为强先验参与打分/兜底
# ---------------------------------------------------------------------------

_VLM_IOU_AGREE = 0.30   # 经典候选与 VLM 粗框的 IoU 达到此值才算「基本认可这个候选」
_VLM_IOU_WEIGHT = 2.0   # IoU 对打分的放大权重


def detect_screen_points_vlm(image, vlm_quad: Optional[PointList] = None,
                              vlm_timeout: int = 120) -> Optional[PointList]:
    """经典算法 + VLM 粗定位融合识别。

    vlm_quad 为 None 时会尝试调用本机 ark-worker 打杂端获取一次粗框（原图像素坐标，
    TL/TR/BR/BL 顺序）；也可以由调用方预先获取好传入，避免重复调用同一张图（批量/
    基准测试场景）。VLM 不可用（脚本缺失/超时/解析失败/无网）时行为退化为与
    detect_screen_points 完全一致——只用经典候选与打分，不报错。

    融合策略（VLM 粗框全程只是「候选 + 先验」，从不脱离统一打分被盲目信任）：
    1) VLM 粗框本身作为一个额外候选加入候选池（不受面积/宽高比过滤，因为它就是用来
       兜底经典候选整体跑偏的情况）；
    2) 其余经典候选里，与 VLM 粗框 IoU 越高的，打分被放大越多——纠正「候选生成阶段
       其实产生过正确候选，但打分阶段选错」的情况；
    3) 最终仍是同一套「边界梯度贴合度」打分选出最高分候选，VLM 粗框只有在它本身也
       贴合真实边界（打分高）或没有更好的经典候选时才会真正胜出，避免 VLM 定位偏差
       较大时把本来还凑合的经典结果替换成更差的结果（已用近黑关屏样本验证）。
    """
    cv2 = _import_cv2()
    if cv2 is None:
        return None
    rgb = _load_rgb_array(image)
    if rgb is None:
        return None

    try:
        h, w = rgb.shape[:2]
        gray_full = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)

        scale = min(1.0, _WORK_MAX_DIM / float(max(w, h)))
        if scale < 1.0:
            small = cv2.resize(gray_full, (int(round(w * scale)), int(round(h * scale))),
                                interpolation=cv2.INTER_AREA)
        else:
            small = gray_full
        sh, sw = small.shape[:2]

        candidates = _gather_candidates(cv2, small)

        if vlm_quad is None and isinstance(image, (str, os.PathLike)):
            from core.vlm_locator import locate_screen_quad
            vlm_quad = locate_screen_quad(str(image), (w, h), timeout=vlm_timeout)

        vlm_small = None
        if vlm_quad is not None:
            vlm_small = [[x * scale, y * scale] for x, y in vlm_quad]
            # VLM 候选本身也进入候选池：prior=1.0（中性），不因面积/宽高比被提前过滤——
            # 它存在的意义就是在经典候选整体跑偏时兜底。
            candidates = list(candidates) + [(vlm_small, 1.0)]

        if not candidates:
            return None

        gx = cv2.Sobel(small, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(small, cv2.CV_32F, 0, 1, ksize=3)
        mag = cv2.magnitude(gx, gy)
        global_mean_mag = float(np.mean(mag)) + 1e-6

        best_quad = None
        best_score = -1.0
        for quad, prior in candidates:
            try:
                score = _score_quad(cv2, gx, gy, mag, global_mean_mag, small, quad, sw, sh) * prior
            except Exception:
                continue
            iou = _quad_iou(cv2, quad, vlm_small) if vlm_small is not None else 0.0
            fused = score * (1.0 + _VLM_IOU_WEIGHT * iou) if vlm_small is not None else score
            if fused > best_score:
                best_score = fused
                best_quad = quad

        if best_quad is None or best_score <= 0:
            return None

        inv = 1.0 / scale if scale > 0 else 1.0
        quad_full = _clamp_points([[x * inv, y * inv] for x, y in best_quad], w, h)

        quad_full = _refine_quad_guarded(cv2, gray_full, quad_full, w, h, scale,
                                          gx, gy, mag, global_mean_mag, small, sw, sh)

        return _clamp_points(quad_full, w, h)
    except Exception:
        _log_detect_error("detect_vlm")
        return None


# ---------------------------------------------------------------------------
# 绿幕识别：AI 生成背景图按「屏幕为纯绿幕」约束时，直接找绿色矩形，比猜边界更可靠
# ---------------------------------------------------------------------------

_GREEN_MIN_AREA_RATIO = 0.03      # 绿区占画面比例低于此值判定为「不是绿幕图」
_GREEN_MIN_RECT_FILL = 0.85       # minAreaRect 兜底时轮廓面积/外接矩形面积的最低填充率
_GREEN_EXPAND_PX = 8.0            # 角点沿四边形中心向外扩张的像素数，贴住屏幕黑边框，避免合成羽化渗绿边
                                   # （实测：2-3px 不足以完全盖住渗色，样例图 + 4 张 PPT 截图合成验证 8px 时
                                   # 边界内外 5px 环带绿色像素数归零，见 docs/roadmap 交付回执）


def detect_green_screen_points(image) -> Optional[PointList]:
    """在「屏幕区域被约束为纯绿幕」的 AI 生成背景图上，直接用颜色分割找屏幕四角。

    流程：HSV 绿色阈值分割 → 形态学去噪 → 取最大连通区 → 四边形拟合（approxPolyDP，
    失败则退化到 minAreaRect 并要求填充率达标）→ 角点沿四边形中心向外扩张（贴到
    屏幕黑色边框上，实测需 8px 才能完全盖住渗色，见 _GREEN_EXPAND_PX 注释）。
    合成时 embed_image_pil 的 inward 羽化会让边界像素与背景混合，角点若恰好落在
    绿区边沿会把绿色渗进最终合成图，外扩后羽化混合的是黑边框而非绿幕。

    绿区面积占比过低，或既拟合不出凸四边形、minAreaRect 填充率也不达标时，判定为
    「不是绿幕图」，返回 None，交由调用方回退到经典/VLM 识别。
    """
    cv2 = _import_cv2()
    if cv2 is None:
        return None
    rgb = _load_rgb_array(image)
    if rgb is None:
        return None

    try:
        h, w = rgb.shape[:2]
        image_area = float(w * h)
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        lower = np.array([40, 120, 120], dtype=np.uint8)
        upper = np.array([80, 255, 255], dtype=np.uint8)
        mask = cv2.inRange(hsv, lower, upper)

        close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_kernel)
        open_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, open_kernel)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None

        contour = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(contour)
        if area < image_area * _GREEN_MIN_AREA_RATIO:
            return None

        quad = None
        perimeter = cv2.arcLength(contour, True)
        if perimeter > 0:
            for eps_factor in (0.02, 0.03, 0.04, 0.06):
                approx = cv2.approxPolyDP(contour, eps_factor * perimeter, True)
                if len(approx) == 4 and cv2.isContourConvex(approx):
                    quad = _order_points(approx.reshape(4, 2).astype(np.float32))
                    break

        if quad is None:
            rect = cv2.minAreaRect(contour)
            box = cv2.boxPoints(rect).astype(np.float32)
            box_area = cv2.contourArea(box)
            if box_area <= 0 or area / box_area < _GREEN_MIN_RECT_FILL:
                return None
            quad = _order_points(box)

        quad = _expand_quad_outward(quad, _GREEN_EXPAND_PX)
        return _clamp_points(quad, w, h)
    except Exception:
        _log_detect_error("detect_green_screen")
        return None


def _expand_quad_outward(quad: PointList, expand_px: float) -> PointList:
    """把四边形每个角点沿「中心→角点」方向向外平移 expand_px 像素。"""
    pts = np.array(quad, dtype=np.float64)
    center = pts.mean(axis=0)
    result = []
    for p in pts:
        direction = p - center
        norm = np.linalg.norm(direction)
        if norm < 1e-6:
            result.append([float(p[0]), float(p[1])])
            continue
        unit = direction / norm
        moved = p + unit * expand_px
        result.append([float(moved[0]), float(moved[1])])
    return result


def _quad_iou(cv2, quad_a: PointList, quad_b: Optional[PointList]) -> float:
    """两个凸四边形的 IoU（工作图坐标系下计算，尺度一致，不影响比值）。"""
    if quad_b is None:
        return 0.0
    try:
        a = np.array(quad_a, dtype=np.float32).reshape(-1, 1, 2)
        b = np.array(quad_b, dtype=np.float32).reshape(-1, 1, 2)
        area_a = cv2.contourArea(a)
        area_b = cv2.contourArea(b)
        if area_a <= 0 or area_b <= 0:
            return 0.0
        inter_area, _ = cv2.intersectConvexConvex(a, b)
        if inter_area <= 0:
            return 0.0
        union = area_a + area_b - inter_area
        if union <= 0:
            return 0.0
        return float(inter_area / union)
    except Exception:
        return 0.0


def _load_rgb_array(image) -> Optional[np.ndarray]:
    try:
        if isinstance(image, Image.Image):
            pil_image = image
        elif isinstance(image, (str, os.PathLike)):
            pil_image = Image.open(image)
        else:
            return None
        return np.asarray(pil_image.convert("RGB"))
    except Exception:
        _log_detect_error("load image")
        return None


# ---------------------------------------------------------------------------
# 候选四边形生成
# ---------------------------------------------------------------------------

def _gather_candidates(cv2, gray: np.ndarray) -> List[Tuple[PointList, float]]:
    candidates: List[Tuple[PointList, float]] = []
    candidates.extend(_candidates_from_threshold(cv2, gray, dark=True))
    candidates.extend(_candidates_from_threshold(cv2, gray, dark=False))
    candidates.extend(_candidates_from_canny(cv2, gray, use_clahe=False))
    candidates.extend(_candidates_from_canny(cv2, gray, use_clahe=True))
    candidates.extend(_candidates_from_adaptive(cv2, gray))
    return candidates


def _candidates_from_adaptive(cv2, gray: np.ndarray) -> List[Tuple[PointList, float]]:
    """自适应局部阈值：整张照片全局对比度很低（黑屏笔记本靠在同样暗的背景前）时，
    全局阈值/Canny 都找不到有效边界，但屏幕边框在局部范围内仍有细微对比，
    adaptiveThreshold 按局部均值分割能捕捉到这种弱边界。"""
    h, w = gray.shape[:2]
    image_area = float(w * h)
    out: List[Tuple[PointList, float]] = []
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    for block_size in (31, 61, 101):
        for c in (2, 5):
            mask = cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                          cv2.THRESH_BINARY_INV, block_size, c)
            close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
            open_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_kernel)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, open_kernel)

            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                area = cv2.contourArea(contour)
                if area < image_area * _MIN_AREA_RATIO or area > image_area * _MAX_AREA_RATIO:
                    continue
                for quad, prior in _quads_from_contour(cv2, contour):
                    if _aspect_ok(quad):
                        out.append((quad, prior))

    return out


def _candidates_from_threshold(cv2, gray: np.ndarray, dark: bool) -> List[Tuple[PointList, float]]:
    h, w = gray.shape[:2]
    image_area = float(w * h)
    out: List[Tuple[PointList, float]] = []

    thresholds = (5, 10, 15, 20, 30, 40, 50) if dark else (140, 160, 180, 200, 220)
    # 多个 open kernel 尺寸：偏暗实拍照片里，屏幕常与旁边同样偏暗的家具/背景连成一片，
    # 单一 kernel 很难恰好切断这类「细颈」粘连；用不同尺寸各生成一套候选，交给统一打分挑选。
    open_sizes = (5, 9, 15)

    for threshold in thresholds:
        if dark:
            base_mask = np.where(gray < threshold, 255, 0).astype(np.uint8)
        else:
            base_mask = np.where(gray > threshold, 255, 0).astype(np.uint8)

        close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
        closed = cv2.morphologyEx(base_mask, cv2.MORPH_CLOSE, close_kernel)

        for open_size in open_sizes:
            open_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (open_size, open_size))
            mask = cv2.morphologyEx(closed, cv2.MORPH_OPEN, open_kernel)

            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                area = cv2.contourArea(contour)
                if area < image_area * _MIN_AREA_RATIO or area > image_area * _MAX_AREA_RATIO:
                    continue
                # 亮区阈值找的是屏幕内容区域，内容色块贴近某一角时轮廓会被截断，
                # 额外补一个 minAreaRect / 截角重建候选兜底该角被截断的情况。
                for quad, prior in _quads_from_contour(cv2, contour, include_min_rect=not dark):
                    if _aspect_ok(quad):
                        out.append((quad, prior))

    return out


def _candidates_from_canny(cv2, gray: np.ndarray, use_clahe: bool) -> List[Tuple[PointList, float]]:
    h, w = gray.shape[:2]
    image_area = float(w * h)
    out: List[Tuple[PointList, float]] = []

    if use_clahe:
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        src = clahe.apply(gray)
    else:
        src = gray

    blurred = cv2.GaussianBlur(src, (5, 5), 0)

    for low, high in ((30, 100), (50, 150), (80, 200)):
        edges = cv2.Canny(blurred, low, high)
        edge_ratio = float(np.mean(edges > 0))
        if edge_ratio > 0.35:
            continue

        dilate_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
        edges = cv2.dilate(edges, dilate_kernel, iterations=2 if use_clahe else 1)
        edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, close_kernel)
        if use_clahe:
            edges = cv2.erode(edges, dilate_kernel, iterations=1)

        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < image_area * _MIN_AREA_RATIO or area > image_area * _MAX_AREA_RATIO:
                continue
            for quad, prior in _quads_from_contour(cv2, contour):
                if _aspect_ok(quad):
                    out.append((quad, prior))

    return out


def _quads_from_contour(cv2, contour, include_min_rect: bool = False) -> List[Tuple[PointList, float]]:
    """一个轮廓可能产出多个候选四边形（quad, prior 打分权重）：approxPolyDP 的角点
    更贴合真实边界，但偶尔会被局部噪声带偏一个角（例如亮区阈值的轮廓被内容色块
    提前截断一角）；minAreaRect 对这种「三边准一角被截断」更稳健，但刚性旋转矩形
    可能把误差转嫁到相邻角，此时截角重建（见 _quad_from_cut_corner）更精确，给
    更高的 prior。默认只在 approx 失败时才补充 minAreaRect（避免候选池噪声过多），
    只有 include_min_rect=True 的调用方（亮区阈值，已知易被内容色块截断角点）
    才总是额外补 minAreaRect 与截角重建两种候选。"""
    results: List[Tuple[PointList, float]] = []
    perimeter = cv2.arcLength(contour, True)
    if perimeter <= 0:
        return results

    found_approx = False
    approx_quad = None
    for eps_factor in (0.02, 0.03, 0.04, 0.06):
        approx = cv2.approxPolyDP(contour, eps_factor * perimeter, True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            approx_quad = _order_points(approx.reshape(4, 2).astype(np.float32))
            results.append((approx_quad, 1.0))
            found_approx = True
            break

    if include_min_rect or not found_approx:
        rect = cv2.minAreaRect(contour)
        box = cv2.boxPoints(rect).astype(np.float32)
        box_area = cv2.contourArea(box)
        contour_area = cv2.contourArea(contour)
        if box_area > 0 and contour_area / box_area >= 0.80:
            results.append((_order_points(box), 1.0))

    if include_min_rect:
        # 「截角五边形」修复：内容色块贴近某一角时，凸包会在真实矩形的那个角上
        # 多切出一条短边（4 边形变成 5 边形），minAreaRect 的刚性旋转矩形又可能
        # 把误差转嫁到相邻角。用「延长截角两侧的长边求交点」直接还原被切掉的角。
        # 这类重建候选几何上更可信（3 个角来自实测边界、1 个角是两条长边的精确
        # 交点），但重建出的角附近采样点落在原本被截断的模糊过渡区，边界打分会
        # 偏低，所以给它一点先验加成，避免被刚性矩形候选比下去。
        reconstructed = _quad_from_cut_corner(cv2, contour, approx_quad)
        if reconstructed is not None:
            results.append((reconstructed, 1.35))

    return results


def _quad_from_cut_corner(cv2, contour, approx_quad: Optional[PointList]) -> Optional[PointList]:
    hull = cv2.convexHull(contour)
    peri = cv2.arcLength(hull, True)
    if peri <= 0:
        return None
    approx = cv2.approxPolyDP(hull, 0.01 * peri, True)
    if len(approx) != 5:
        return None

    pts = approx.reshape(5, 2).astype(np.float64)
    edge_lens = [np.linalg.norm(pts[(i + 1) % 5] - pts[i]) for i in range(5)]
    cut_idx = int(np.argmin(edge_lens))
    # 截角边必须明显短于其余边，否则这只是个普通五边形（非「切角」），不做重建
    other_lens = [edge_lens[i] for i in range(5) if i != cut_idx]
    if edge_lens[cut_idx] > 0.35 * (sum(other_lens) / len(other_lens)):
        return None

    prev_pt = pts[(cut_idx - 1) % 5]
    p0 = pts[cut_idx]
    p1 = pts[(cut_idx + 1) % 5]
    next_pt = pts[(cut_idx + 2) % 5]

    corner = _line_intersection(_line_through(prev_pt, p0), _line_through(p1, next_pt))
    if corner is None:
        return None

    remaining = [pts[(cut_idx + 2 + i) % 5] for i in range(3)]  # 保留另外 3 个未受影响的角
    quad = np.array([corner, *remaining], dtype=np.float32)
    if not cv2.isContourConvex(quad.reshape(-1, 1, 2).astype(np.float32)):
        return None

    # 一致性校验：五边形有时并非「一个角被截断」，而是反映了另一处完全不同的
    # 缺口（比如某一侧有条线缆/反光、或轮廓边缘本身有轻微凹凸），此时重建会把
    # 一个本来定位良好的角搬到错误的位置。用同一轮廓的 approxPolyDP 四边形做
    # 参照：重建结果里「未受影响」的 3 个角必须能在参照四边形中找到很接近的
    # 对应角。若这条轮廓根本没有一个干净的 4 边形近似可做参照，说明轮廓形状本身
    # 不规则，无法可靠判断截角重建是否安全，直接放弃这个候选（宁可不修，不可能错）。
    if approx_quad is None:
        return None
    ref = np.array(approx_quad, dtype=np.float64)
    max_dim = max(np.ptp(ref[:, 0]), np.ptp(ref[:, 1]))
    tolerance = max(15.0, max_dim * 0.03)
    for pt in remaining:
        nearest = np.min(np.linalg.norm(ref - pt, axis=1))
        if nearest > tolerance:
            return None

    return _order_points(quad)


def _line_through(p1: np.ndarray, p2: np.ndarray) -> Tuple[float, float, float, float]:
    d = p2 - p1
    return (float(p1[0]), float(p1[1]), float(d[0]), float(d[1]))


def _aspect_ok(points: PointList) -> bool:
    ratio = _quad_aspect_ratio(points)
    return _ASPECT_MIN < ratio < _ASPECT_MAX


# ---------------------------------------------------------------------------
# 统一打分：候选四边形边界与图像梯度的贴合度
# ---------------------------------------------------------------------------

def _score_quad(cv2, gx: np.ndarray, gy: np.ndarray, mag_map: np.ndarray, global_mean_mag: float,
                 gray: np.ndarray, quad: PointList, w: int, h: int) -> float:
    pts = [np.array(p, dtype=np.float64) for p in quad]
    edges = [(pts[i], pts[(i + 1) % 4]) for i in range(4)]

    sample_xs = []
    sample_ys = []
    normals = []
    n_per_edge = 24

    for a, b in edges:
        d = b - a
        length = np.linalg.norm(d)
        if length < 1e-6:
            return -1.0
        d = d / length
        normal = np.array([-d[1], d[0]])
        for i in range(n_per_edge):
            t = 0.12 + 0.76 * (i / (n_per_edge - 1))
            p = a + t * (b - a)
            sample_xs.append(p[0])
            sample_ys.append(p[1])
            normals.append(normal)

    xs = np.array(sample_xs, dtype=np.float32).reshape(-1, 1)
    ys = np.array(sample_ys, dtype=np.float32).reshape(-1, 1)
    valid = (xs[:, 0] >= 0) & (xs[:, 0] <= w - 1) & (ys[:, 0] >= 0) & (ys[:, 0] <= h - 1)
    if not np.any(valid):
        return -1.0

    gx_s = cv2.remap(gx, xs, ys, interpolation=cv2.INTER_LINEAR).reshape(-1)
    gy_s = cv2.remap(gy, xs, ys, interpolation=cv2.INTER_LINEAR).reshape(-1)
    mag = np.sqrt(gx_s ** 2 + gy_s ** 2)

    normals_arr = np.array(normals)
    with np.errstate(divide="ignore", invalid="ignore"):
        cos_align = np.abs(gx_s * normals_arr[:, 0] + gy_s * normals_arr[:, 1]) / np.maximum(mag, 1e-6)
    cos_align = np.nan_to_num(cos_align)

    raw = mag * cos_align
    score_samples = raw[valid]
    if score_samples.size == 0:
        return -1.0

    # 面积先验：实拍场景里屏幕占画面比例集中在 28%~51%（对 28 个标注模板统计），
    # 用高斯先验强烈偏好该范围，压制「画面里凑巧一小块强对比区域」这类误检。
    area = cv2.contourArea(np.array(quad, dtype=np.float32))
    area_ratio = area / float(w * h)
    area_prior = float(np.exp(-((area_ratio - 0.37) / 0.14) ** 2))

    # 内部纹理惩罚：屏幕内容（哪怕是文字幻灯片）的局部梯度密度远低于键盘/书架等
    # 强纹理干扰物；用「内部平均梯度 / 全图平均梯度」的比值做惩罚，压制误检。
    interior_ratio = _interior_texture_ratio(cv2, mag_map, quad, w, h, global_mean_mag)
    texture_penalty = 1.0 / (1.0 + max(0.0, interior_ratio - 1.0) * 0.6)

    # 内部亮度标准差惩罚：真实屏幕内部（无论纯黑还是内容画面）亮度分布相对连续，
    # 若候选把屏幕和旁边书架/键盘等完全不同色调的区域圈在一起，标准差会明显偏高。
    interior_std = _interior_intensity_std(cv2, gray, quad, w, h)
    std_penalty = 1.0 / (1.0 + max(0.0, interior_std - 45.0) / 35.0)

    return float(np.mean(score_samples)) * max(area_prior, 0.08) * texture_penalty * std_penalty


def _interior_intensity_std(cv2, gray: np.ndarray, quad: PointList, w: int, h: int) -> float:
    pts = np.array(quad, dtype=np.float64)
    center = pts.mean(axis=0)
    shrunk = center + (pts - center) * 0.85
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.fillConvexPoly(mask, shrunk.astype(np.int32), 255)
    interior = gray[mask > 0]
    if interior.size == 0:
        return 0.0
    return float(np.std(interior))


def _interior_texture_ratio(cv2, mag: np.ndarray, quad: PointList, w: int, h: int,
                             global_mean_mag: float) -> float:
    pts = np.array(quad, dtype=np.float64)
    center = pts.mean(axis=0)
    shrunk = center + (pts - center) * 0.85  # 内缩 15%，避开边界本身的梯度
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.fillConvexPoly(mask, shrunk.astype(np.int32), 255)
    interior = mag[mask > 0]
    if interior.size == 0:
        return 1.0
    return float(np.mean(interior)) / global_mean_mag


# ---------------------------------------------------------------------------
# 角点精修：在原图分辨率上，沿每条边做局部梯度极值搜索 + 直线拟合
# ---------------------------------------------------------------------------

def _refine_quad(cv2, gray_full: np.ndarray, quad: PointList, w: int, h: int) -> Optional[PointList]:
    """在原图分辨率上沿每条边做局部梯度极值搜索 + 直线拟合，修正粗定位（在缩小
    工作图上找到）的角点误差。任何一步不可靠就返回 None，调用方回退到粗定位结果。"""
    try:
        xs = [p[0] for p in quad]
        ys = [p[1] for p in quad]
        qw = max(xs) - min(xs)
        qh = max(ys) - min(ys)
        margin = int(round(max(qw, qh) * _REFINE_MARGIN_RATIO)) + 5

        x0 = max(0, int(min(xs)) - margin)
        y0 = max(0, int(min(ys)) - margin)
        x1 = min(w, int(max(xs)) + margin)
        y1 = min(h, int(max(ys)) + margin)
        if x1 - x0 < 10 or y1 - y0 < 10:
            return None

        crop = gray_full[y0:y1, x0:x1]
        crop_gx = cv2.Sobel(crop, cv2.CV_32F, 1, 0, ksize=3)
        crop_gy = cv2.Sobel(crop, cv2.CV_32F, 0, 1, ksize=3)
        ch, cw = crop.shape[:2]
        crop_mag_mean = float(np.mean(cv2.magnitude(crop_gx, crop_gy))) + 1e-6
        # 边缘点必须显著强于裁剪区域的平均梯度水平，否则近乎无纹理的画面里任何
        # 噪声起伏都会被当成「找到了边缘」。
        mag_threshold = max(6.0, crop_mag_mean * 3.0)

        local_pts = [np.array([p[0] - x0, p[1] - y0], dtype=np.float64) for p in quad]
        search_range = max(6.0, max(qw, qh) * 0.012)
        search_range = min(search_range, 25.0)

        refined_lines = []
        for i in range(4):
            a = local_pts[i]
            b = local_pts[(i + 1) % 4]
            line = _refine_edge_line(cv2, crop_gx, crop_gy, a, b, cw, ch, search_range, mag_threshold)
            if line is None:
                return None
            refined_lines.append(line)

        new_corners = []
        for i in range(4):
            prev_line = refined_lines[(i - 1) % 4]
            curr_line = refined_lines[i]
            inter = _line_intersection(prev_line, curr_line)
            if inter is None:
                return None
            new_corners.append(inter)

        # 精修结果离粗定位太远则视为失败，回退到原始角点
        for orig, new in zip(local_pts, new_corners):
            if np.linalg.norm(np.array(new) - orig) > search_range * 3:
                return None

        result = [[float(p[0] + x0), float(p[1] + y0)] for p in new_corners]
        return _order_points(np.array(result, dtype=np.float32))
    except Exception:
        return None


def _refine_edge_line(cv2, gx: np.ndarray, gy: np.ndarray, a: np.ndarray, b: np.ndarray,
                       w: int, h: int, search_range: float, mag_threshold: float):
    d = b - a
    length = np.linalg.norm(d)
    if length < 1e-6:
        return None
    d = d / length
    normal = np.array([-d[1], d[0]])

    n_samples = 30
    edge_points = []
    steps = np.linspace(-search_range, search_range, int(search_range * 2) + 1)

    for i in range(n_samples):
        t = 0.1 + 0.8 * (i / (n_samples - 1))
        p = a + t * (b - a)
        probe_x = p[0] + steps * normal[0]
        probe_y = p[1] + steps * normal[1]
        valid = (probe_x >= 0) & (probe_x <= w - 1) & (probe_y >= 0) & (probe_y <= h - 1)
        if not np.any(valid):
            continue
        px = probe_x[valid].astype(np.float32).reshape(-1, 1)
        py = probe_y[valid].astype(np.float32).reshape(-1, 1)
        gxv = cv2.remap(gx, px, py, interpolation=cv2.INTER_LINEAR).reshape(-1)
        gyv = cv2.remap(gy, px, py, interpolation=cv2.INTER_LINEAR).reshape(-1)
        mag = np.sqrt(gxv ** 2 + gyv ** 2)
        if mag.size == 0:
            continue
        best_idx = int(np.argmax(mag))
        if mag[best_idx] < mag_threshold:
            continue
        edge_points.append((px[best_idx, 0], py[best_idx, 0]))

    if len(edge_points) < max(6, n_samples // 3):
        return None

    pts = np.array(edge_points, dtype=np.float32)
    vx, vy, x0, y0 = cv2.fitLine(pts, cv2.DIST_HUBER, 0, 0.01, 0.01).reshape(-1)
    return (float(x0), float(y0), float(vx), float(vy))


def _line_intersection(l1, l2) -> Optional[Tuple[float, float]]:
    x1, y1, dx1, dy1 = l1
    x2, y2, dx2, dy2 = l2
    denom = dx1 * dy2 - dy1 * dx2
    if abs(denom) < 1e-9:
        return None
    t = ((x2 - x1) * dy2 - (y2 - y1) * dx2) / denom
    return (x1 + t * dx1, y1 + t * dy1)


# ---------------------------------------------------------------------------
# 纸张四角识别：亮色空白纸张放在深/中色桌面上的实拍照片（文档纸张模板场景）
# ---------------------------------------------------------------------------

_PAPER_MIN_AREA_RATIO = 0.15
_PAPER_MAX_AREA_RATIO = 0.85
_PAPER_OPEN_RATIOS = (0.01, 0.02, 0.035, 0.05, 0.07, 0.10, 0.15)
_PAPER_APPROX_EPS = (0.01, 0.02, 0.03, 0.04, 0.06, 0.08)
_PAPER_MIN_RECT_FILL = 0.75


def detect_paper_points(image, inset_ratio: float = 0.03) -> Optional[PointList]:
    """在「亮色空白纸张放在深/中色桌面」的实拍照片上识别纸张四角。

    与 detect_screen_points（针对屏幕设备，暗色边框/内部有强纹理）思路不同，纸张
    场景是「亮区分割」：纸张整体明显亮于周围桌面。但实测发现单一灰度阈值不够
    稳——浅色木桌在灰度图上可能跟白纸一样亮，纯亮度分割会把桌面也圈进来；于是
    改用 HSV「去饱和度亮度」（V-S，纸张接近纯白=低饱和高亮度，木桌/桌面通常
    饱和度更高）作为主分割通道，Otsu 阈值 + 灰度自适应阈值兜底，在多档形态学
    开运算核尺寸下各生成一批候选四边形（同一套「候选池 + 统一打分」架构见
    detect_screen_points 顶部注释），再用「边界梯度贴合度 × 内部亮度均匀度 ×
    四边内侧窄带均匀度」打分选出最佳候选——内部/内侧窄带均匀度是纸张专属信号：
    真纸面内部和紧贴四边内侧几乎是纯色，候选四边形一旦把桌面/其他物体圈了进来，
    这两个均匀度会明显下降，从而压制误检。

    识别到的四角再朝四边形质心方向按 inset_ratio 内缩，避免合成内容压在纸张
    物理边缘（纸边容易有阴影、轻微卷边，内容贴边会显得穿帮）。

    竖版拍摄是本函数的主场景，不套用 detect_screen_points 面向横屏设备的宽高比
    过滤（_ASPECT_MIN/_ASPECT_MAX）；只用面积占比 [0.15, 0.85] 过滤噪声轮廓。
    识别失败（无 cv2、图片加载失败、找不到合规候选）一律返回 None，不抛异常。

    已知局限：当纸张与背景（如白墙/浅色窗台）在亮度和饱和度上都非常接近、且
    过渡区域没有明显阴影/纹理断层时，分割候选可能包含少量背景，角点定位会有
    偏差（仍会返回一个合规四边形，不会误判为识别失败）；建议调用方在 preview
    图上做一次人工确认。
    """
    cv2 = _import_cv2()
    if cv2 is None:
        return None
    rgb = _load_rgb_array(image)
    if rgb is None:
        return None

    try:
        h, w = rgb.shape[:2]
        gray_full = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)

        scale = min(1.0, _WORK_MAX_DIM / float(max(w, h)))
        if scale < 1.0:
            small_rgb = cv2.resize(rgb, (int(round(w * scale)), int(round(h * scale))),
                                    interpolation=cv2.INTER_AREA)
        else:
            small_rgb = rgb
        small_gray = cv2.cvtColor(small_rgb, cv2.COLOR_RGB2GRAY)
        sh, sw = small_gray.shape[:2]

        candidates = _gather_paper_candidates(cv2, small_rgb, small_gray)
        if not candidates:
            return None

        gx = cv2.Sobel(small_gray, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(small_gray, cv2.CV_32F, 0, 1, ksize=3)
        mag = cv2.magnitude(gx, gy)
        global_mean_mag = float(np.mean(mag)) + 1e-6

        best_quad = None
        best_score = -1.0
        for quad in candidates:
            score = _score_paper_quad(cv2, gx, gy, mag, global_mean_mag, small_gray, quad, sw, sh)
            if score > best_score:
                best_score = score
                best_quad = quad

        if best_quad is None or best_score <= 0:
            return None

        inv = 1.0 / scale if scale > 0 else 1.0
        quad_full = _clamp_points([[x * inv, y * inv] for x, y in best_quad], w, h)
        quad_full = _inset_quad_toward_center(quad_full, inset_ratio)
        return _clamp_points(quad_full, w, h)
    except Exception:
        _log_detect_error("detect_paper")
        return None


def _gather_paper_candidates(cv2, small_rgb: np.ndarray, small_gray: np.ndarray) -> List[PointList]:
    """生成纸张候选四边形：HSV「去饱和度亮度」Otsu 分割为主，灰度自适应阈值兜底，
    多档开运算核尺寸各出一批候选（核越大越能断开纸张与背景之间的弱连接）。"""
    h, w = small_gray.shape[:2]
    image_area = float(w * h)
    out: List[PointList] = []

    hsv = cv2.cvtColor(small_rgb, cv2.COLOR_RGB2HSV)
    s_ch = hsv[:, :, 1].astype(np.int16)
    v_ch = hsv[:, :, 2].astype(np.int16)
    whiteness = np.clip(v_ch - s_ch, 0, 255).astype(np.uint8)
    whiteness = cv2.GaussianBlur(whiteness, (5, 5), 0)
    _, whiteness_mask = cv2.threshold(whiteness, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    blurred_gray = cv2.GaussianBlur(small_gray, (5, 5), 0)
    adaptive_mask = cv2.adaptiveThreshold(
        blurred_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 51, -10
    )

    close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    open_sizes = []
    for ratio in _PAPER_OPEN_RATIOS:
        size = max(5, int(round(min(w, h) * ratio)))
        if size % 2 == 0:
            size += 1
        open_sizes.append(size)

    for base_mask in (whiteness_mask, adaptive_mask):
        base_mask = cv2.morphologyEx(base_mask, cv2.MORPH_CLOSE, close_kernel)
        for open_size in open_sizes:
            open_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (open_size, open_size))
            mask = cv2.morphologyEx(base_mask, cv2.MORPH_OPEN, open_kernel)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                area = cv2.contourArea(contour)
                if area < image_area * _PAPER_MIN_AREA_RATIO or area > image_area * _PAPER_MAX_AREA_RATIO:
                    continue
                out.extend(_paper_quads_from_contour(cv2, contour))

    return out


def _paper_quads_from_contour(cv2, contour) -> List[PointList]:
    results: List[PointList] = []
    perimeter = cv2.arcLength(contour, True)
    if perimeter <= 0:
        return results

    for eps_factor in _PAPER_APPROX_EPS:
        approx = cv2.approxPolyDP(contour, eps_factor * perimeter, True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            results.append(_order_points(approx.reshape(4, 2).astype(np.float32)))

    rect = cv2.minAreaRect(contour)
    box = cv2.boxPoints(rect).astype(np.float32)
    box_area = cv2.contourArea(box)
    contour_area = cv2.contourArea(contour)
    if box_area > 0 and contour_area / box_area >= _PAPER_MIN_RECT_FILL:
        results.append(_order_points(box))

    return results


def _score_paper_quad(cv2, gx: np.ndarray, gy: np.ndarray, mag_map: np.ndarray, global_mean_mag: float,
                       gray: np.ndarray, quad: PointList, w: int, h: int) -> float:
    """纸张候选打分 = 边界梯度贴合度 × 内部亮度均匀度 × 四边内侧窄带均匀度。

    与 _score_quad（屏幕打分）的关键差异：屏幕内部允许有内容（图文/黑屏），只
    惩罚「纹理过多」；纸张假定为空白，内部和紧贴四边内侧的窄带都应该是均匀纯色，
    候选一旦把背景圈了进来，这两处的标准差会明显升高，直接压低分数。
    """
    pts = [np.array(p, dtype=np.float64) for p in quad]
    edges = [(pts[i], pts[(i + 1) % 4]) for i in range(4)]

    sample_xs, sample_ys, normals = [], [], []
    n_per_edge = 24
    for a, b in edges:
        d = b - a
        length = np.linalg.norm(d)
        if length < 1e-6:
            return -1.0
        d = d / length
        normal = np.array([-d[1], d[0]])
        for i in range(n_per_edge):
            t = 0.12 + 0.76 * (i / (n_per_edge - 1))
            p = a + t * (b - a)
            sample_xs.append(p[0])
            sample_ys.append(p[1])
            normals.append(normal)

    xs = np.array(sample_xs, dtype=np.float32).reshape(-1, 1)
    ys = np.array(sample_ys, dtype=np.float32).reshape(-1, 1)
    valid = (xs[:, 0] >= 0) & (xs[:, 0] <= w - 1) & (ys[:, 0] >= 0) & (ys[:, 0] <= h - 1)
    if not np.any(valid):
        return -1.0

    gx_s = cv2.remap(gx, xs, ys, interpolation=cv2.INTER_LINEAR).reshape(-1)
    gy_s = cv2.remap(gy, xs, ys, interpolation=cv2.INTER_LINEAR).reshape(-1)
    mag = np.sqrt(gx_s ** 2 + gy_s ** 2)

    normals_arr = np.array(normals)
    with np.errstate(divide="ignore", invalid="ignore"):
        cos_align = np.abs(gx_s * normals_arr[:, 0] + gy_s * normals_arr[:, 1]) / np.maximum(mag, 1e-6)
    cos_align = np.nan_to_num(cos_align)

    raw = mag * cos_align
    score_samples = raw[valid]
    if score_samples.size == 0:
        return -1.0
    boundary_score = float(np.mean(score_samples))

    interior_std = _interior_intensity_std(cv2, gray, quad, w, h)
    interior_uniformity = 1.0 / (1.0 + (interior_std / 10.0) ** 2)

    band_std = _paper_edge_band_std(gray, quad, w, h, band_px=max(6, int(round(0.02 * min(w, h)))))
    band_uniformity = 1.0 / (1.0 + (band_std / 10.0) ** 2)

    return boundary_score * interior_uniformity * band_uniformity


def _paper_edge_band_std(gray: np.ndarray, quad: PointList, w: int, h: int, band_px: int) -> float:
    """沿四边形每条边的内侧取一条窄带采样灰度，返回四条边窄带标准差的均值。

    真纸边内侧应始终是均匀纸面（低标准差）；候选四边形若有一条边切过了背景
    物体（桌面纹理、其他物件），那条边内侧窄带会混入差异很大的像素，标准差
    明显升高，从而在 _score_paper_quad 中拖累整体打分。
    """
    pts = np.array(quad, dtype=np.float64)
    center = pts.mean(axis=0)
    stds = []
    n_per_edge = 40
    offsets = np.linspace(2, band_px, 5)

    for i in range(4):
        a = pts[i]
        b = pts[(i + 1) % 4]
        d = b - a
        length = np.linalg.norm(d)
        if length < 1e-6:
            continue
        d_unit = d / length
        normal = np.array([-d_unit[1], d_unit[0]])
        mid = (a + b) / 2.0
        if np.dot(center - mid, normal) < 0:
            normal = -normal

        samples = []
        for t in np.linspace(0.1, 0.9, n_per_edge):
            p = a + t * (b - a)
            for off in offsets:
                q = p + normal * off
                x, y = int(round(q[0])), int(round(q[1]))
                if 0 <= x < w and 0 <= y < h:
                    samples.append(gray[y, x])
        if len(samples) > 5:
            stds.append(float(np.std(samples)))

    if not stds:
        return 999.0
    return float(np.mean(stds))


def _inset_quad_toward_center(quad: PointList, inset_ratio: float) -> PointList:
    """把四边形四个角点朝质心方向按 inset_ratio 内缩（0.03 = 缩 3%）。"""
    pts = np.array(quad, dtype=np.float64)
    center = pts.mean(axis=0)
    ratio = max(0.0, min(0.49, float(inset_ratio)))
    shrunk = center + (pts - center) * (1.0 - ratio)
    return [[float(x), float(y)] for x, y in shrunk]


# ---------------------------------------------------------------------------
# 通用几何工具
# ---------------------------------------------------------------------------

def _order_points(points: np.ndarray) -> PointList:
    pts = points.astype(np.float32)
    ordered = np.zeros((4, 2), dtype=np.float32)

    sums = pts.sum(axis=1)
    diffs = np.diff(pts, axis=1).reshape(-1)
    ordered[0] = pts[np.argmin(sums)]
    ordered[2] = pts[np.argmax(sums)]
    ordered[1] = pts[np.argmin(diffs)]
    ordered[3] = pts[np.argmax(diffs)]

    return [[float(x), float(y)] for x, y in ordered]


def _quad_aspect_ratio(points: PointList) -> float:
    """Width/height ratio of the quadrilateral."""
    tl, tr, br, bl = [np.array(p) for p in points]
    w = (np.linalg.norm(tr - tl) + np.linalg.norm(br - bl)) / 2
    h = (np.linalg.norm(bl - tl) + np.linalg.norm(br - tr)) / 2
    return w / max(h, 1.0)


def _clamp_points(points: PointList, width: int, height: int) -> PointList:
    return [
        [
            max(0.0, min(float(width - 1), float(x))),
            max(0.0, min(float(height - 1), float(y))),
        ]
        for x, y in points
    ]
