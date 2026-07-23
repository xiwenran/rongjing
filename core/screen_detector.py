from __future__ import annotations

import os
from typing import Optional

import numpy as np
from PIL import Image


PointList = list[list[float]]


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
    """Detect screen quadrilateral points in TL, TR, BR, BL order."""
    cv2 = _import_cv2()
    if cv2 is None:
        return None
    rgb = _load_rgb_array(image)
    if rgb is None:
        return None

    try:
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        dark_ratio = float(np.mean(gray < 30))

        if dark_ratio > 0.02:
            points = _detect_solid_black_screen(cv2, gray)
            if points is not None:
                return points

        points = _detect_screen_edges(cv2, gray)
        if points is not None:
            return points

        return _detect_with_clahe(cv2, gray)
    except Exception:
        _log_detect_error("detect")
        return None


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


def _interior_std(cv2, gray: np.ndarray, points: PointList) -> float:
    h, w = gray.shape[:2]
    pts = np.array(points, dtype=np.int32)
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.fillConvexPoly(mask, pts, 255)
    interior = gray[mask > 0]
    if len(interior) == 0:
        return 999.0
    return float(interior.std())


def _detect_solid_black_screen(cv2, gray: np.ndarray) -> Optional[PointList]:
    h, w = gray.shape[:2]
    image_area = float(w * h)

    for threshold in (5, 10, 20, 30, 50, 80):
        mask = np.where(gray < threshold, 255, 0).astype(np.uint8)

        close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
        open_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, open_kernel)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue

        candidates = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < image_area * 0.03 or area > image_area * 0.95:
                continue
            points = _quad_from_contour(cv2, contour)
            if points is not None:
                aspect = _quad_aspect_ratio(points)
                if 0.3 < aspect < 3.5:
                    std = _interior_std(cv2, gray, points)
                    candidates.append((std, area, points))

        if not candidates:
            continue

        # Prefer low interior variance (uniform = screen), break ties by area
        candidates.sort(key=lambda item: (item[0], -item[1]))
        best_std, best_area, best_points = candidates[0]
        if best_std < 40:
            return _clamp_points(best_points, w, h)

    return None


def _detect_screen_edges(cv2, gray: np.ndarray) -> Optional[PointList]:
    h, w = gray.shape[:2]
    image_area = float(w * h)

    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    best_candidates = []

    for low, high in ((30, 100), (50, 150), (80, 200)):
        edges = cv2.Canny(blurred, low, high)
        edge_ratio = float(np.mean(edges > 0))
        if edge_ratio > 0.30:
            continue

        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        edges = cv2.dilate(edges, kernel, iterations=1)
        edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for contour in contours:
            area = cv2.contourArea(contour)
            if area < image_area * 0.03 or area > image_area * 0.90:
                continue

            perimeter = cv2.arcLength(contour, True)
            if perimeter <= 0:
                continue
            for eps_factor in (0.02, 0.04, 0.06):
                approx = cv2.approxPolyDP(contour, eps_factor * perimeter, True)
                if len(approx) == 4 and cv2.isContourConvex(approx):
                    points = approx.reshape(4, 2).astype(np.float32)
                    quad_area = cv2.contourArea(points)
                    if quad_area < image_area * 0.03:
                        continue
                    ordered = _order_points(points)
                    aspect = _quad_aspect_ratio(ordered)
                    if 0.3 < aspect < 3.5:
                        std = _interior_std(cv2, gray, ordered)
                        best_candidates.append((std, quad_area, ordered))
                    break

    if not best_candidates:
        return None
    best_candidates.sort(key=lambda item: (item[0], -item[1]))
    return _clamp_points(best_candidates[0][2], w, h)


def _detect_with_clahe(cv2, gray: np.ndarray) -> Optional[PointList]:
    """CLAHE contrast enhancement + aggressive edge connection for dark scenes."""
    h, w = gray.shape[:2]
    image_area = float(w * h)

    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    blurred = cv2.GaussianBlur(enhanced, (5, 5), 0)

    best_candidates = []

    for low, high in ((30, 100), (50, 150)):
        edges = cv2.Canny(blurred, low, high)

        dilate_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
        close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
        edges = cv2.dilate(edges, dilate_kernel, iterations=2)
        edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, close_kernel)
        edges = cv2.erode(edges, dilate_kernel, iterations=1)

        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for contour in contours:
            area = cv2.contourArea(contour)
            if area < image_area * 0.03 or area > image_area * 0.85:
                continue

            perimeter = cv2.arcLength(contour, True)
            if perimeter <= 0:
                continue
            for eps_factor in (0.02, 0.04, 0.06, 0.08):
                approx = cv2.approxPolyDP(contour, eps_factor * perimeter, True)
                if len(approx) == 4 and cv2.isContourConvex(approx):
                    points = approx.reshape(4, 2).astype(np.float32)
                    quad_area = cv2.contourArea(points)
                    if quad_area < image_area * 0.03:
                        continue
                    ordered = _order_points(points)
                    aspect = _quad_aspect_ratio(ordered)
                    if 0.3 < aspect < 3.5:
                        std = _interior_std(cv2, gray, ordered)
                        best_candidates.append((std, quad_area, ordered))
                    break

    if not best_candidates:
        return None
    best_candidates.sort(key=lambda item: (item[0], -item[1]))
    return _clamp_points(best_candidates[0][2], w, h)


def _quad_from_contour(cv2, contour) -> Optional[PointList]:
    perimeter = cv2.arcLength(contour, True)
    if perimeter <= 0:
        return None

    for eps_factor in (0.02, 0.04, 0.06):
        approx = cv2.approxPolyDP(contour, eps_factor * perimeter, True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            return _order_points(approx.reshape(4, 2).astype(np.float32))

    rect = cv2.minAreaRect(contour)
    box = cv2.boxPoints(rect).astype(np.float32)
    box_area = cv2.contourArea(box)
    contour_area = cv2.contourArea(contour)
    if box_area <= 0 or contour_area / box_area < 0.80:
        return None
    return _order_points(box)


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
