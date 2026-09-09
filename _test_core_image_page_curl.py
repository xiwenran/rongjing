from __future__ import annotations

import json
import os
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

from PIL import Image, ImageChops, ImageDraw, ImageStat

from core.core_image_page_curl import PageCurlUnavailable, availability, render_batch


ROOT = Path(__file__).resolve().parent
HELPER = ROOT / "build" / "page_curl" / "PageCurlRenderer"


def make_page(path: Path, background: str, title: str, marker: str) -> None:
    image = Image.new("RGB", (640, 360), background)
    draw = ImageDraw.Draw(image)
    draw.rectangle((18, 18, 622, 342), outline="black", width=8)
    draw.text((52, 58), title, fill="black", stroke_width=1)
    draw.polygon([(90, 140), (250, 180), (90, 220)], fill=marker)
    draw.text((70, 285), "TOP -> RIGHT / BOTTOM", fill="black")
    image.save(path)


def mean_difference(left: Image.Image, right: Image.Image) -> float:
    return sum(ImageStat.Stat(ImageChops.difference(left, right)).mean) / 3.0


def verify_frame_set(evidence: Path, filter_name: str) -> dict:
    frames = [Image.open(evidence / f"frame_{index:04}.png").convert("RGB") for index in range(3)]
    source_image = Image.open(evidence / "source.png").convert("RGB")
    target_image = Image.open(evidence / "target.png").convert("RGB")
    metrics = {
        "filter": filter_name,
        "source_to_first_mean_abs": mean_difference(source_image, frames[0]),
        "target_to_last_mean_abs": mean_difference(target_image, frames[2]),
        "middle_to_first_mean_abs": mean_difference(frames[1], frames[0]),
        "middle_to_last_mean_abs": mean_difference(frames[1], frames[2]),
        "middle_unique_colors": len(frames[1].getcolors(maxcolors=640 * 360) or []),
        "size": list(frames[1].size),
    }
    if metrics["source_to_first_mean_abs"] >= 1.0:
        raise AssertionError("首帧与 source 差异过大")
    if metrics["target_to_last_mean_abs"] >= 1.0:
        raise AssertionError("尾帧与 target 差异过大")
    if metrics["middle_to_first_mean_abs"] <= 8.0 or metrics["middle_to_last_mean_abs"] <= 8.0:
        raise AssertionError("中间帧疑似简单切换")
    if metrics["middle_unique_colors"] <= 100:
        raise AssertionError("中间帧缺少曲面明暗层次")
    return metrics


class CoreImagePageCurlTests(unittest.TestCase):
    def test_non_darwin_is_explicitly_unavailable(self) -> None:
        with mock.patch.object(sys, "platform", "win32"):
            ok, reason = availability(HELPER)
            self.assertFalse(ok)
            self.assertIn("仅支持 macOS", reason)
            with self.assertRaises(PageCurlUnavailable):
                render_batch("a", "b", "out", [0.0], 10, 10, helper_path=HELPER)

    def test_existing_three_frame_evidence(self) -> None:
        raw_path = os.environ.get("RONGJING_CI_EVIDENCE")
        if not raw_path:
            self.skipTest("未指定既有三帧证据目录")
        metrics = verify_frame_set(Path(raw_path), "CIPageCurlWithShadowTransition")
        self.assertEqual(metrics["size"], [640, 360])

    @unittest.skipUnless(
        sys.platform == "darwin" and HELPER.is_file() and os.environ.get("RONGJING_CI_RUN_REAL") == "1",
        "仅在显式启用时调用真实 macOS helper",
    )
    def test_real_three_frame_batch(self) -> None:
        run_id = os.environ.get("RONGJING_CI_RUN_ID", uuid.uuid4().hex)
        evidence = ROOT / "qa" / "logs" / f"coreimage-swift-p1-{run_id}"
        evidence.mkdir(parents=True, exist_ok=True)
        source = evidence / "source.png"
        target = evidence / "target.png"
        make_page(source, "#fff1ce", "SOURCE PAGE 1", "#dc2626")
        make_page(target, "#d7f1ff", "TARGET PAGE 2", "#16a34a")

        result = render_batch(
            source,
            target,
            evidence,
            [0.0, 0.5, 1.0],
            640,
            360,
            helper_path=HELPER,
            manifest_path=evidence / "manifest.json",
        )
        metrics = verify_frame_set(evidence, result["filter"])
        (evidence / "report.json").write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
