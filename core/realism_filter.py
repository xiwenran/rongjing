"""实拍质感滤镜：让合成结果看起来像「拿手机随手拍下来的」，而不是数字贴图。

两层结构，都只作用于嵌入区域（屏幕/纸面），背景本身已经是实拍或 AI 实拍风，
再叠一遍等于二次劣化。

第一层｜光照适配 adapt_light
    合成前的嵌入内容是「正面平铺、亮度均匀」的，而真实场景里的屏幕/纸面一定
    带着环境光——一侧受光、四周暗角、整体偏某个色温。这一层给内容套上一张
    符合本背景图的光照层。两条路自动判定（见 _build_light）：

      路 A｜原屏有纹理（实拍背景，屏幕上原本有画面/反光/关屏灰阶）
        直接从原屏区域取大核低频当光照层。信息是真的，永远协调。

      路 B｜原屏是纯色（AI 绿幕背景，本项目主力路径）
        绿幕区域是均匀纯色，零光照信息可继承（实测 63 个模板：绿幕区亮度
        p5-p95 仅差 2-3 级）。改从屏幕外圈环带采样，线性拟合出环境光的方向
        与色温，程序化重建一张限幅的光照层。

第二层｜拍摄损耗
    动态范围收窄到 [_BLACK, _WHITE]（黑被环境光抬成灰、白被相机压住不到顶，
    这两件事一起发生才是「暗淡」）、轻失焦、含彩色分量的暗部加权噪点、暗角。

所有强度由 strength(0-100) 统一缩放，默认 _DEFAULT_STRENGTH。
"""

from typing import List, Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from core.image_processor import order_points

# ── 参数基准（strength=100 时的取值，实际按 strength/100 线性缩放）──────────
_TILT_MAX = 0.20      # 方向光在屏幕跨度上的最大亮度起伏（±10%）
_VIGNETTE = 0.24      # 暗角深度
_CAST_MAX = 0.035     # 环境色温偏移上限。大面积白底最容易暴露色偏——同样的
                      # 偏移量在彩色内容上看不出来，在白底上直接显成一片黄绿。
# 动态范围收窄：把源图的 [0,255] 映射到 [_BLACK, _WHITE]。
# 「拍屏显得暗淡」的本质是两端一起收——黑被环境光散射抬成灰，白被相机压住不到顶。
# 早期版本用「整体曝光下压 + 黑位抬升 + 对比压缩」三个系数，前两者方向相反、
# 实测互相抵消（中心区只压暗 3 级），所以用户看着仍不够暗。改为直接给黑白位。
_BLACK = 22.0         # 黑位：纯黑抬到多少
_WHITE = 232.0        # 白位：纯白压到多少。压太狠（实测 216）会让大面积白底的
                      # 页面整片发灰显脏，参考实拍里的白底仍是干净的浅灰白。
_EXPOSURE = 0.94      # 收窄后再整体轻压，让中间调一起降下来
_BLUR = 0.9           # 失焦半径（px）
_NOISE = 7.0          # 噪点 sigma（暗部加权后的峰值）
_CHROMA = 0.55        # 噪点里彩色分量占比：真实高 ISO 是亮度噪点为主、彩噪为辅，
                      # 纯亮度噪点看起来像加了颗粒滤镜，不像相机
_FLAT_STD = 6.0       # 原屏低频标准差低于此值 → 判为纯色，走路 B

# 环境亮度自适应（仅路 B）：屏幕的明暗必须跟着它所处环境走，否则背景一暗，
# 屏幕就像自己在发光、贴上去的。实测：把环境压暗 60% 而不做自适应时，合成后
# 屏幕亮度纹丝不动，屏幕/环境亮度比从 1.66 一路飙到 4.14。
# 用次幂曲线软化——屏幕本来就该比环境亮（亮屏物理如此），要跟随的是「亮多少」
# 而不是压到跟环境一样暗；再设下限，避免极暗背景把内容压到看不清（笔记图的
# 首要任务仍是内容可读）。
_AMBIENT_REF = 105.0  # 参考环境亮度，对应「正常室内灯光」
_AMBIENT_GAMMA = 0.45 # 跟随强度，<1 表示不完全跟随
_AMBIENT_MIN = 0.68   # 压暗下限
_AMBIENT_MAX = 1.06   # 环境格外亮时允许略微提亮

# 这里曾有一层程序化「屏幕表面脏污」（多尺度低频云斑模拟指纹/擦拭痕/浮尘）。
# 已移除：合成出来的脏很难控制轻重，稍重就糊成一片灰、稍轻又看不出来，
# 收益不稳。改为在 AI 生成背景时就让模型把实拍质感画进去（见 core/bg_prompt.py
# 的实拍观感基调），AI 画出来的暗部、噪点、光照是真实自洽的。

_NOISE_TILES = 12     # 预生成噪点块数量（视频逐帧循环取用，避免每帧 randn）
_NOISE_TILE_PX = 512  # 噪点块边长，用时平铺到屏幕外接框
_PAD = 8              # 屏幕外接框的外扩像素，给羽化和模糊留余量

_DEFAULT_STRENGTH = 70  # 默认强度。改这里要同步 cli.py / batch_runner.py / main_window.py 的默认值


def _quad_mask(size, pts, feather: int = 0) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).polygon([(float(p[0]), float(p[1])) for p in pts], fill=255)
    if feather > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(feather))
    return mask


def _quad_bbox(pts, size):
    """屏幕四边形的整数外接框，钳制在画布内。"""
    w, h = size
    x0 = max(0, int(np.floor(pts[:, 0].min())))
    y0 = max(0, int(np.floor(pts[:, 1].min())))
    x1 = min(w, int(np.ceil(pts[:, 0].max())))
    y1 = min(h, int(np.ceil(pts[:, 1].max())))
    return x0, y0, max(x0 + 1, x1), max(y0 + 1, y1)


def _ring_stats(bg_arr: np.ndarray, pts: np.ndarray, size):
    """采样屏幕外圈环带，拟合环境光方向与色温。

    环带取四边形以中心为原点缩放 1.02→1.25 倍之间的部分——贴着屏幕外沿，
    取到的是打在屏幕边框/墙面上的光，正是屏幕本身该受到的那束光。
    返回 (gx, gy, cast)：gx/gy 是归一化到屏幕跨度的亮度梯度（已限幅），
    cast 是相对灰的 RGB 比例。采样点不足时返回 None。

    注意这里刻意不返回环带的绝对亮度：环带贴着屏幕外沿，常整圈落在黑色边框上，
    不能代表环境亮度。环境亮度自适应在 `_build_light` 里另取整个屏幕外区域。
    """
    c = pts.mean(axis=0)
    outer = c + (pts - c) * 1.25
    inner = c + (pts - c) * 1.02
    mk = (np.array(_quad_mask(size, outer)) > 128) & (np.array(_quad_mask(size, inner)) < 128)
    if mk.sum() < 500:
        return None

    ys, xs = np.nonzero(mk)
    px = bg_arr[ys, xs]
    luma = 0.299 * px[:, 0] + 0.587 * px[:, 1] + 0.114 * px[:, 2]

    # 以屏幕外接框为归一化基准，拟合 luma ≈ a*u + b*v + c（u,v ∈ 屏幕跨度）
    x0, y0, x1, y1 = _quad_bbox(pts, size)
    u = (xs - x0) / max(x1 - x0, 1)
    v = (ys - y0) / max(y1 - y0, 1)
    A = np.c_[u, v, np.ones(len(u))]
    try:
        sol, *_ = np.linalg.lstsq(A, luma, rcond=None)
    except np.linalg.LinAlgError:
        return None

    base = max(float(luma.mean()), 12.0)
    # 梯度换算成「跨过整个屏幕的相对亮度变化」，再限幅——环带落在窗框、黑边等
    # 高反差物体上时原始梯度可达 100+ 级，照搬会把屏幕一侧压成黑块。
    gx = float(np.clip(sol[0] / base, -1.0, 1.0))
    gy = float(np.clip(sol[1] / base, -1.0, 1.0))

    rgb = px.mean(axis=0)
    gray = max(float(rgb.mean()), 1.0)
    return gx, gy, (rgb / gray).astype(np.float32)


def _build_light(bg_arr, pts, size, strength: float, screen_mask: np.ndarray):
    """构建光照层：HxWx3，均值≈1.0，用乘法作用于嵌入内容。"""
    h, w = bg_arr.shape[:2]
    x0, y0, x1, y1 = _quad_bbox(pts, size)
    bw, bh = x1 - x0, y1 - y0
    k = strength / 100.0

    # ── 判路 ────────────────────────────────────────────────────────────
    # 内缩取样，避开屏幕边框；判据只看屏幕内部的原始像素，不看任何模糊结果——
    # 早期版本用「全图大核模糊后在屏内取标准差」判路，模糊核（屏幕跨度的 6%）
    # 会把屏幕外的物体糊进屏内，绿幕模板因此被误判成「原屏有光照」而走了路 A，
    # 再从被污染的低频里取 RGB 比值，直接在白底上拉出粉/青彩色渐变。
    erode = max(3, int(min(bw, bh) * 0.02) | 1)
    inner = np.array(_quad_mask(size, pts).filter(ImageFilter.MinFilter(erode))) > 128

    use_ring = True
    if inner.sum() > 1000:
        px = bg_arr[inner]
        luma = 0.299 * px[:, 0] + 0.587 * px[:, 1] + 0.114 * px[:, 2]
        is_green = float(((px[:, 1] > px[:, 0] * 1.3) & (px[:, 1] > px[:, 2] * 1.3)).mean())
        # 绿幕一票判为纯色；其余看屏内原始亮度起伏够不够构成真实光照
        use_ring = is_green > 0.6 or float(luma.std()) < _FLAT_STD

    light = np.ones((h, w, 3), dtype=np.float32)

    if not use_ring:
        # 路 A：原屏有真实光照，继承它的低频。取低频前先把屏幕外区域整体填成
        # 屏内均值，否则大核模糊会把窗框、黑板等外部颜色带进屏幕边沿。
        ref = bg_arr[inner].mean(axis=0)
        ref[ref < 8.0] = 8.0
        filled = np.where(inner[:, :, np.newaxis], bg_arr, ref[np.newaxis, np.newaxis, :])
        low_rgb = np.array(
            Image.fromarray(np.clip(filled, 0, 255).astype(np.uint8), "RGB")
            .filter(ImageFilter.GaussianBlur(max(bw, bh) * 0.08)),
            dtype=np.float32,
        )
        ratio = low_rgb / ref
        # 继承强度限幅：低频比值可能极端（原屏有高光/纯黑），钳到 ±35%
        light = 1.0 + (np.clip(ratio, 0.65, 1.35) - 1.0) * k
    else:
        # 路 B：绿幕/纯色屏，从环带反推
        stats = _ring_stats(bg_arr, pts, size)
        u = ((np.arange(w) - x0) / max(bw, 1)).astype(np.float32)
        v = ((np.arange(h) - y0) / max(bh, 1)).astype(np.float32)
        uu = u[np.newaxis, :]
        vv = v[:, np.newaxis]

        plane = np.ones((h, w), dtype=np.float32)
        cast = np.ones(3, dtype=np.float32)
        if stats is not None:
            gx, gy, cast = stats
            plane = 1.0 + (gx * (uu - 0.5) + gy * (vv - 0.5)) * _TILT_MAX * k

        # 环境亮度自适应：环境暗则屏幕跟着压暗，避免屏幕「自己发光」。
        # 基准取整个屏幕外区域而不是环带——环带贴着屏幕，常整圈落在黑色边框上，
        # 比真实环境暗得多（实测同一模板环带 60 vs 非屏幕区 105），拿它当基准会
        # 让正常亮度的场景也被误判成暗环境而压暗。
        outside = screen_mask[:, :, 0] < 0.5
        if outside.sum() > 1000:
            env = float((0.299 * bg_arr[:, :, 0] + 0.587 * bg_arr[:, :, 1]
                         + 0.114 * bg_arr[:, :, 2])[outside].mean())
            amb = float(np.clip(
                (max(env, 1.0) / _AMBIENT_REF) ** _AMBIENT_GAMMA,
                _AMBIENT_MIN, _AMBIENT_MAX,
            ))
            plane = plane * (1.0 + (amb - 1.0) * k)

        # 径向暗角：以屏幕中心为原点，按屏幕跨度归一
        r2 = np.clip((uu - 0.5) ** 2 + (vv - 0.5) ** 2, 0.0, 1.0) * 4.0
        plane = plane * (1.0 - _VIGNETTE * k * np.clip(r2, 0.0, 1.0))

        # 色温偏移必须严格限幅：环带常落在红色横幅、绿黑板这类高饱和物体上，
        # 原始 RGB 比值可达 ±40%，照搬会把屏幕白底整片染色。
        cast = np.clip(1.0 + (cast - 1.0) * k, 1.0 - _CAST_MAX * k, 1.0 + _CAST_MAX * k)
        light = plane[:, :, np.newaxis] * cast[np.newaxis, np.newaxis, :]

    # 只在屏幕区域生效，区域外保持 1.0（背景不动）
    m = screen_mask
    return 1.0 + (light - 1.0) * m


def precompute_realism(
    bg_img: Image.Image,
    points: List[List[float]],
    strength: int = _DEFAULT_STRENGTH,
    feather: int = 2,
    seed: int = 0,
) -> Optional[dict]:
    """按模板预计算实拍滤镜所需的全部静态层。每个模板算一次，逐图/逐帧复用。

    strength<=0 时返回 None，调用方据此跳过整条滤镜路径（零开销）。
    """
    if strength <= 0:
        return None

    bg_img = bg_img.convert("RGB")
    size = bg_img.size
    w, h = size
    bg_arr = np.array(bg_img, dtype=np.float32)
    pts = order_points(points).astype(np.float64)

    # 滤镜作用范围的软掩膜：比合成掩膜多羽化一点，避免滤镜在屏幕边沿形成硬接缝
    mask = _quad_mask(size, pts, feather=max(feather, 3))
    m = (np.array(mask, dtype=np.float32) / 255.0)[:, :, np.newaxis]

    k = strength / 100.0
    light = _build_light(bg_arr, pts, size, float(strength), m)

    # ── 只保留屏幕外接框内的层 ───────────────────────────────────────────
    # 滤镜在框外恒等于「不改变」，整图存储纯属浪费：2155×2873 的模板整图存法
    # 实测占 396 MB/模板，批量跑多模板会直接吃光内存；逐帧运算也全在整图上做，
    # 单帧 144 ms，视频导出会被拖垮。裁到外接框后内存与耗时都按屏幕占画面的
    # 比例下降。外扩 _PAD 像素给羽化和模糊留余量，避免框沿出现处理断层。
    x0, y0, x1, y1 = _quad_bbox(pts, size)
    x0 = max(0, x0 - _PAD)
    y0 = max(0, y0 - _PAD)
    x1 = min(w, x1 + _PAD)
    y1 = min(h, y1 + _PAD)

    rng = np.random.default_rng(seed or 20260728)
    mask_box = np.ascontiguousarray(m[y0:y1, x0:x1])
    light_box = np.ascontiguousarray(light[y0:y1, x0:x1])

    # 噪点只存小块，用时平铺——整图存 12 张噪点是内存大头（单模板 297 MB）。
    # 噪点无结构，平铺周期在视觉上不可分辨。
    # 三通道相关噪点：亮度分量（三通道同相）为主 + 彩色分量（三通道独立）为辅，
    # 复现高 ISO 的实际噪点构成。
    rgb = rng.normal(0.0, 1.0, (_NOISE_TILES, _NOISE_TILE_PX, _NOISE_TILE_PX, 3)).astype(np.float32)
    lum = rgb.mean(axis=3, keepdims=True)
    noise = lum * (1.0 - _CHROMA) * 1.7 + rgb * _CHROMA

    return {
        "size": size,
        "box": (x0, y0, x1, y1),
        "strength": float(strength),
        "k": float(k),
        "mask": mask_box,
        "light": light_box,
        "noise": noise,
        "black": _BLACK * k,
        "white": 255.0 - (255.0 - _WHITE) * k,
        "exposure": 1.0 - (1.0 - _EXPOSURE) * k,
        "blur": _BLUR * k,
        "noise_sigma": _NOISE * k,
    }


def apply_realism(img: Image.Image, cache: Optional[dict], frame_index: int = 0) -> Image.Image:
    """把实拍滤镜应用到已合成的图上。cache 为 None 时原样返回。

    frame_index 只影响噪点选取——视频必须逐帧换噪点，否则固定噪点看起来像
    镜头脏了而不是高 ISO；光照/暗角等静态层则必须逐帧不变。
    """
    if cache is None:
        return img

    img = img.convert("RGB")
    if img.size != cache["size"]:
        # 尺寸不符（例如输出被 resize 过）时不硬套预计算层，跳过滤镜而不是产出错位结果
        return img

    # 所有运算只在屏幕外接框内做，框外像素原样保留（滤镜在那里本就是恒等变换）
    x0, y0, x1, y1 = cache["box"]
    m = cache["mask"]
    full = np.array(img, dtype=np.float32)
    arr = full[y0:y1, x0:x1]

    # 框内按「整块算一遍滤镜、最后用 mask 混回原块」处理：中间步骤含高斯模糊，
    # 若提前按 mask 混合会让模糊在屏幕边沿吃到未处理像素，形成一圈接缝。
    # 光照层 + 曝光下压
    out = arr * cache["light"] * cache["exposure"]

    # 动态范围收窄到 [black, white]：黑被环境光抬成灰，白被相机压住不到顶
    black, white = cache["black"], cache["white"]
    out = black + out * ((white - black) / 255.0)

    # 轻失焦：手持近拍景深浅，屏幕不会像素级锐利
    if cache["blur"] > 0.05:
        out = np.array(
            Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")
            .filter(ImageFilter.GaussianBlur(cache["blur"])),
            dtype=np.float32,
        )

    # 噪点：暗部权重更高（高 ISO 噪点集中在暗部）。小块噪点平铺到框尺寸，
    # 并按 frame_index 滚动错位，避免视频里出现固定不动的重复图案。
    sigma = cache["noise_sigma"]
    if sigma > 0.05:
        bh, bw = out.shape[:2]
        tile = cache["noise"][frame_index % _NOISE_TILES]
        reps = (bh // _NOISE_TILE_PX + 2, bw // _NOISE_TILE_PX + 2, 1)
        off_y = (frame_index * 37) % _NOISE_TILE_PX
        off_x = (frame_index * 53) % _NOISE_TILE_PX
        n = np.tile(tile, reps)[off_y:off_y + bh, off_x:off_x + bw]
        luma = (0.299 * out[:, :, 0] + 0.587 * out[:, :, 1] + 0.114 * out[:, :, 2])[:, :, np.newaxis]
        weight = 1.0 - np.clip(luma / 255.0, 0.0, 1.0) * 0.6
        out = out + n * sigma * weight

    full[y0:y1, x0:x1] = arr * (1.0 - m) + out * m
    return Image.fromarray(np.clip(full, 0, 255).astype(np.uint8), "RGB")
