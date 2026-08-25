from PIL import Image

from core.collage_processor import create_collage, calculate_auto_split, calculate_dropped_pages
from models.collage_model import CollageManager, CollageTemplate


COLORS = [
    (255, 0, 0),
    (0, 255, 0),
    (0, 0, 255),
    (255, 255, 0),
    (255, 0, 255),
    (0, 255, 255),
    (128, 0, 0),
    (0, 128, 0),
    (0, 0, 128),
    (128, 128, 0),
    (128, 0, 128),
    (0, 128, 128),
]


def assert_equal(actual, expected, message):
    if actual != expected:
        raise AssertionError(f"{message}: expected {expected}, got {actual}")


def make_images(count):
    return [Image.new("RGB", (100, 100), COLORS[i]) for i in range(count)]


def test_grid_collage_size_and_order():
    result = create_collage(
        make_images(12),
        layout="grid",
        rows=3,
        cols=4,
        gap=4,
        padding=0,
        output_width=1280,
    )

    cell_w = (1280 - 3 * 4) // 4
    cell_h = int(round(cell_w / 1.0))
    expected_height = 3 * cell_h + 2 * 4
    assert_equal(result.size, (1280, expected_height), "collage size")

    for idx, color in enumerate(COLORS):
        row = idx // 4
        col = idx % 4
        x = col * (cell_w + 4)
        y = row * (cell_h + 4)
        assert_equal(result.getpixel((x, y))[:3], color, f"cell {idx + 1} color")


def test_missing_images_fill_background():
    result = create_collage(
        make_images(8),
        layout="grid",
        rows=3,
        cols=4,
        gap=4,
        padding=0,
        background_color="#112233",
        output_width=1280,
    )

    cell_w = (1280 - 3 * 4) // 4
    cell_h = int(round(cell_w / 1.0))
    bg = (17, 34, 51)

    for idx in range(8, 12):
        row = idx // 4
        col = idx % 4
        left = col * (cell_w + 4)
        top = row * (cell_h + 4)
        points = [
            (left, top),
            (left + cell_w - 1, top),
            (left, top + cell_h - 1),
            (left + cell_w - 1, top + cell_h - 1),
        ]
        for point in points:
            assert_equal(result.getpixel(point)[:3], bg, f"empty cell {idx + 1} background")


def test_hero_collage_places_large_first_image_then_thumbnails():
    result = create_collage(
        make_images(5),
        layout="hero",
        rows=2,
        cols=2,
        gap=4,
        padding=0,
        output_width=1000,
    )

    hero_h = int(round(1000 / 1.0))
    cell_w = (1000 - 4) // 2
    thumb_y = hero_h + 4
    assert_equal(result.getpixel((10, 10))[:3], COLORS[0], "hero image color")
    assert_equal(result.getpixel((10, thumb_y + 10))[:3], COLORS[1], "first thumbnail color")
    assert_equal(result.getpixel((cell_w + 4 + 10, thumb_y + 10))[:3], COLORS[2], "second thumbnail color")


def test_auto_split_examples():
    assert_equal(
        calculate_auto_split(48, 0, 12),
        [(0, 12), (12, 24), (24, 36), (36, 48)],
        "48 pages auto split",
    )
    assert_equal(
        calculate_auto_split(50, 0, 12),
        [(0, 12), (12, 24), (24, 36), (36, 48), (48, 50)],
        "50 pages auto split",
    )
    assert_equal(
        calculate_auto_split(48, 4, 12),
        [(0, 12), (12, 24), (24, 36), (36, 48)],
        "48 pages requested split",
    )
    assert_equal(
        calculate_auto_split(30, 2, 4),
        [(0, 4), (4, 8)],
        "30 pages requested split drops overflow",
    )
    assert_equal(
        calculate_auto_split(6, 2, 4),
        [(0, 4), (4, 6)],
        "6 pages requested split uses front-filled groups",
    )
    assert_equal(
        calculate_auto_split(7, 2, 4),
        [(0, 4), (4, 7)],
        "7 pages requested split uses front-filled groups",
    )
    assert_equal(
        calculate_auto_split(50, 4, 12),
        [(0, 12), (12, 24), (24, 36), (36, 48)],
        "50 pages requested split drops overflow",
    )
    assert_equal(
        calculate_dropped_pages(30, 2, 4),
        22,
        "30 pages dropped count",
    )
    assert_equal(
        calculate_dropped_pages(6, 2, 4),
        0,
        "6 pages dropped count",
    )
    assert_equal(
        calculate_dropped_pages(48, 4, 12),
        0,
        "48 pages dropped count",
    )


def test_collage_template_round_trip_and_manager(tmp_dir="_tmp_collages"):
    import os
    import shutil

    if os.path.exists(tmp_dir):
        shutil.rmtree(tmp_dir)

    template = CollageTemplate(
        name="三行四列",
        layout="grid",
        rows=3,
        cols=4,
        gap=4,
        padding=8,
        background_color="#FFFFFF",
        cell_aspect_ratio=1.0,
        output_width=1280,
        output_height=0,
    )

    loaded = CollageTemplate.from_dict(template.to_dict())
    assert_equal(loaded.total_cells, 12, "template total cells")
    assert_equal(loaded, template, "template dict round trip")

    manager = CollageManager(tmp_dir)
    manager.save(template)
    assert_equal(manager.names(), ["三行四列"], "manager names")
    assert_equal(manager.load("三行四列"), template, "manager load")
    manager.delete("三行四列")
    assert_equal(manager.names(), [], "manager delete")

    hero_template = CollageTemplate(
        name="上大图四小图",
        layout="hero",
        rows=2,
        cols=2,
        gap=4,
        padding=8,
        background_color="#FFFFFF",
        cell_aspect_ratio=1.0,
        output_width=1280,
        output_height=0,
    )
    assert_equal(hero_template.total_cells, 5, "hero template total cells")
    shutil.rmtree(tmp_dir)


if __name__ == "__main__":
    test_grid_collage_size_and_order()
    test_missing_images_fill_background()
    test_hero_collage_places_large_first_image_then_thumbnails()
    test_auto_split_examples()
    test_collage_template_round_trip_and_manager()
    print("all collage tests passed")
