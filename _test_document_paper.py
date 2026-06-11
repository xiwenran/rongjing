import numpy as np
from PIL import Image, ImageDraw

from core.image_processor import embed_document_paper_pil, embed_image_pil_fast, precompute_template_cache


def make_paper_image():
    img = Image.new("RGB", (80, 100), (245, 245, 238))
    draw = ImageDraw.Draw(img)
    for y in range(18, 82, 12):
        draw.line([(12, y), (68, y)], fill=(20, 20, 20), width=2)
    return img


def make_background():
    bg = Image.new("RGB", (160, 120), (150, 128, 105))
    draw = ImageDraw.Draw(bg)
    draw.rectangle([24, 12, 136, 110], fill=(218, 210, 190))
    return bg


def test_document_presets_keep_size_and_legacy_modes_are_normalized():
    paper = make_paper_image()
    bg = make_background()
    points = [[24, 12], [136, 18], [130, 110], [28, 104]]

    clear = embed_document_paper_pil(paper, bg, points, "clear")
    paper_preset = embed_document_paper_pil(paper, bg, points, "paper")
    warm = embed_document_paper_pil(paper, bg, points, "warm")

    assert clear.size == bg.size
    assert paper_preset.size == bg.size
    assert warm.size == bg.size
    assert np.array_equal(np.array(clear), np.array(paper_preset))
    assert np.array_equal(np.array(clear), np.array(warm))


def test_document_multiply_blends_white_and_keeps_color_content():
    paper = Image.new("RGB", (80, 100), "white")
    draw = ImageDraw.Draw(paper)
    draw.rectangle([20, 20, 60, 70], fill=(20, 120, 40))
    bg = make_background()
    points = [[24, 12], [136, 18], [130, 110], [28, 104]]

    result = embed_document_paper_pil(paper, bg, points, "paper")
    arr = np.array(result, dtype=np.float32)
    bg_arr = np.array(bg, dtype=np.float32)

    white_region = arr[25:32, 35:42].mean(axis=(0, 1))
    bg_region = bg_arr[25:32, 35:42].mean(axis=(0, 1))
    color_region = arr[45:55, 70:82].mean(axis=(0, 1))
    color_bg_region = bg_arr[45:55, 70:82].mean(axis=(0, 1))

    assert np.linalg.norm(white_region - bg_region) < 8
    assert np.linalg.norm(color_region - color_bg_region) > 35


def test_screen_fast_path_still_callable():
    ppt = Image.new("RGB", (40, 30), (180, 120, 40))
    bg = Image.new("RGB", (80, 60), (20, 40, 60))
    points = [[0, 0], [80, 0], [80, 60], [0, 60]]
    cache = precompute_template_cache(bg, points, ppt_size=ppt.size)
    result = embed_image_pil_fast(ppt, cache)
    assert result.size == bg.size


def run_tests():
    test_document_presets_keep_size_and_legacy_modes_are_normalized()
    test_document_multiply_blends_white_and_keeps_color_content()
    test_screen_fast_path_still_callable()
    print("document paper tests passed")


if __name__ == "__main__":
    run_tests()
