import random
from dataclasses import asdict, dataclass
from typing import Optional

import numpy as np
from PIL import Image, ImageEnhance


@dataclass
class DiversifyConfig:
    enabled: bool = False
    corner_jitter_px: int = 3
    brightness_range: float = 0.03
    contrast_range: float = 0.03
    saturation_range: float = 0.04
    rotation_range: float = 0.3
    scale_range: float = 0.01
    noise_intensity: float = 2.0
    jpeg_quality_range: int = 3
    randomize_metadata: bool = True

    @classmethod
    def preset(cls, level: str) -> "DiversifyConfig":
        if level not in {"low", "medium", "high"}:
            raise ValueError("level must be 'low', 'medium', or 'high'")

        config = cls(enabled=True)
        if level == "medium":
            return config

        multiplier = 0.5 if level == "low" else 2.0
        config.corner_jitter_px = max(1, int(round(config.corner_jitter_px * multiplier)))
        config.brightness_range *= multiplier
        config.contrast_range *= multiplier
        config.saturation_range *= multiplier
        config.rotation_range = min(0.6, config.rotation_range * multiplier)
        config.scale_range *= multiplier
        config.noise_intensity = min(4.0, config.noise_intensity * multiplier)
        config.jpeg_quality_range = max(1, int(round(config.jpeg_quality_range * multiplier)))
        return config

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict):
        allowed = cls().__dataclass_fields__.keys()
        values = {key: value for key, value in d.items() if key in allowed}
        return cls(**values)


def diversify_image(
    img: Image.Image,
    config: DiversifyConfig,
    seed: Optional[int] = None,
) -> Image.Image:
    """Apply subtle batch variation without mutating the input image."""
    if not config.enabled:
        return img.copy()

    rng = random.Random(seed)
    result = img.copy()

    brightness = 1 + rng.uniform(-config.brightness_range, config.brightness_range)
    result = ImageEnhance.Brightness(result).enhance(brightness)

    contrast = 1 + rng.uniform(-config.contrast_range, config.contrast_range)
    result = ImageEnhance.Contrast(result).enhance(contrast)

    saturation = 1 + rng.uniform(-config.saturation_range, config.saturation_range)
    result = ImageEnhance.Color(result).enhance(saturation)

    result = _scale_center_crop(result, rng.uniform(-config.scale_range, config.scale_range))

    angle = rng.uniform(-config.rotation_range, config.rotation_range)
    result = result.rotate(angle, resample=Image.BILINEAR, expand=False)

    result = _add_noise(result, config.noise_intensity, rng)

    if config.randomize_metadata:
        result = strip_metadata(result)

    return result


def jitter_points(
    points: list,
    jitter_px: int,
    rng: random.Random,
) -> list:
    """Jitter 4 corner points by a random offset within ±jitter_px."""
    return [
        [
            point[0] + rng.randint(-jitter_px, jitter_px),
            point[1] + rng.randint(-jitter_px, jitter_px),
        ]
        for point in points
    ]


def randomize_jpeg_quality(
    base_quality: int,
    quality_range: int,
    rng: random.Random,
) -> int:
    """Return a randomized JPEG quality clamped to [85, 100]."""
    quality = base_quality + rng.randint(-quality_range, quality_range)
    return max(85, min(100, quality))


def strip_metadata(img: Image.Image) -> Image.Image:
    """Remove EXIF and image metadata from a copy of the image."""
    clean = img.copy()
    clean.info.clear()
    if hasattr(clean, "encoderinfo"):
        clean.encoderinfo = {}
    return clean


def _scale_center_crop(img: Image.Image, scale: float) -> Image.Image:
    w, h = img.size
    factor = 1 + scale
    if factor == 1:
        return img

    new_w = max(1, int(round(w * factor)))
    new_h = max(1, int(round(h * factor)))
    resized = img.resize((new_w, new_h), Image.BILINEAR)

    if factor >= 1:
        left = (new_w - w) // 2
        top = (new_h - h) // 2
        return resized.crop((left, top, left + w, top + h))

    crop_w = max(1, int(round(w * factor)))
    crop_h = max(1, int(round(h * factor)))
    left = (w - crop_w) // 2
    top = (h - crop_h) // 2
    cropped = img.crop((left, top, left + crop_w, top + crop_h))
    return cropped.resize((w, h), Image.BILINEAR)


def _add_noise(img: Image.Image, sigma: float, rng: random.Random) -> Image.Image:
    if sigma <= 0:
        return img

    arr = np.asarray(img)
    np_rng = np.random.default_rng(rng.randint(0, 2**31))
    try:
        noise = np_rng.standard_normal(arr.shape, dtype=np.float32)
    except TypeError:
        noise = np_rng.standard_normal(arr.shape).astype(np.float32)

    noisy = arr.astype(np.float32, copy=False)
    noisy += noise * np.float32(sigma)
    np.clip(noisy, 0, 255, out=noisy)
    noisy = noisy.astype(np.uint8)
    return Image.fromarray(noisy, img.mode)
