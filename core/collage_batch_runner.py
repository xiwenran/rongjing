import math
import os
from typing import List, Optional, Set

from PyQt6.QtCore import QThread, pyqtSignal

from PIL import Image

from models.collage_model import CollageTemplate
from core.collage_processor import calculate_auto_split, create_collage
from core.diversifier import DiversifyConfig, diversify_image


class CollageBatchRunner(QThread):
    progress = pyqtSignal(int, int, str)   # done, total, status_msg
    finished = pyqtSignal(bool, str)       # success, message

    def __init__(
        self,
        image_files: List[str],
        collage_template: CollageTemplate,
        output_dir: str,
        output_format: str = "JPEG",
        total_output_images: int = 0,
        excluded_indices: Optional[Set[int]] = None,
        diversify_config: Optional[DiversifyConfig] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.image_files = image_files
        self.collage_template = collage_template
        self.output_dir = output_dir
        self.output_format = output_format
        self.total_output_images = total_output_images
        self.excluded_indices = excluded_indices or set()
        self.diversify_config = diversify_config
        self._abort = False

    def abort(self):
        self._abort = True

    def run(self):
        try:
            os.makedirs(self.output_dir, exist_ok=True)

            template = self.collage_template
            total_cells = template.total_cells
            filtered_files = [
                path for idx, path in enumerate(self.image_files)
                if idx not in self.excluded_indices
            ]

            if not filtered_files:
                self.finished.emit(True, "完成！没有可处理的图片")
                return

            output_count = self.total_output_images
            if output_count <= 0:
                output_count = math.ceil(len(filtered_files) / total_cells)

            ranges = calculate_auto_split(len(filtered_files), output_count, total_cells)
            total = len(ranges)
            done = 0

            output_format = self.output_format.upper()
            ext = ".jpg" if output_format == "JPEG" else ".png"
            save_format = "JPEG" if output_format == "JPEG" else "PNG"

            for collage_idx, (start, end) in enumerate(ranges, 1):
                if self._abort:
                    self.finished.emit(False, "已取消"); return

                images = []
                for img_path in filtered_files[start:end]:
                    if self._abort:
                        self.finished.emit(False, "已取消"); return

                    try:
                        with Image.open(img_path) as img:
                            images.append(img.copy())
                    except Exception as e:
                        self.progress.emit(
                            done, total,
                            f"⚠ 跳过 {os.path.basename(img_path)}：{str(e)}"
                        )

                result = create_collage(
                    images,
                    template.layout,
                    template.rows,
                    template.cols,
                    gap=template.gap,
                    padding=template.padding,
                    background_color=template.background_color,
                    cell_aspect_ratio=template.cell_aspect_ratio,
                    output_width=template.output_width,
                    output_height=template.output_height,
                )

                if self.diversify_config is not None and self.diversify_config.enabled:
                    import time
                    seed = hash((collage_idx, time.time_ns()))
                    result = diversify_image(result, self.diversify_config, seed=seed)

                out_path = os.path.join(self.output_dir, f"拼图_{collage_idx}{ext}")
                if save_format == "JPEG":
                    result = result.convert("RGB")
                    result.save(out_path, "JPEG", quality=95)
                else:
                    result.save(out_path, "PNG")

                done += 1
                self.progress.emit(done, total, f"拼图_{collage_idx}{ext}")

            self.finished.emit(True, f"完成！共生成 {done} 张拼图")

        except Exception as e:
            import traceback
            self.finished.emit(False, f"错误: {str(e)}\n{traceback.format_exc()}")
