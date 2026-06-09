import json
import hashlib
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from typing import List, Optional


def _natural_key(s: str):
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', s)]


TEMPLATE_CATEGORY_ALIASES = {
    "教室大屏模板": "教师场景",
    "希沃白板模板": "教师场景",
    "电脑背景模板": "台式机电脑",
    "文档模板": "文档纸张",
}


def normalize_template_category(category: str | None) -> str:
    category = (category or "教师场景").strip()
    return TEMPLATE_CATEGORY_ALIASES.get(category, category)


@dataclass
class Template:
    name: str
    background_path: str
    screen_points: List[List[float]]  # 4 points [[x,y],...] TL→TR→BR→BL in bg image coords
    output_width: int = 0   # 0 = auto (use bg image size)
    output_height: int = 0  # 0 = auto
    category: str = "教师场景"
    template_type: str = "screen"
    render_preset: str = "clear"
    is_broken: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "background_path": self.background_path,
            "screen_points": self.screen_points,
            "output_width": self.output_width,
            "output_height": self.output_height,
            "category": normalize_template_category(self.category),
            "template_type": self.template_type or "screen",
            "render_preset": self.render_preset or "clear",
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Template":
        category = normalize_template_category(d.get("category", "教师场景"))
        default_type = "document_paper" if category == "文档纸张" else "screen"
        template_type = d.get("template_type", d.get("render_type", default_type))
        return cls(
            name=d["name"],
            background_path=d["background_path"],
            screen_points=d["screen_points"],
            output_width=d.get("output_width", 0),
            output_height=d.get("output_height", 0),
            category=category,
            template_type=template_type,
            render_preset=d.get("render_preset", d.get("blend_mode", "clear")),
            is_broken=d.get("is_broken", False),
        )

    @property
    def output_size(self) -> Optional[tuple]:
        if self.output_width > 0 and self.output_height > 0:
            return (self.output_width, self.output_height)
        return None


class TemplateManager:
    def __init__(self, templates_dir: str, backgrounds_dir: Optional[str] = None):
        self.templates_dir = templates_dir
        self.backgrounds_dir = backgrounds_dir or os.path.join(os.path.dirname(templates_dir), "backgrounds")
        os.makedirs(templates_dir, exist_ok=True)
        os.makedirs(self.backgrounds_dir, exist_ok=True)

    def save(self, template: Template) -> None:
        path = os.path.join(self.templates_dir, f"{template.name}.json")
        data = template.to_dict()
        data["background_path"] = self._persistent_background_path(template.name, template.background_path)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load_all(self) -> List[Template]:
        templates = []
        for fn in sorted(os.listdir(self.templates_dir), key=_natural_key):
            if fn.endswith(".json"):
                try:
                    with open(os.path.join(self.templates_dir, fn), "r", encoding="utf-8") as f:
                        templates.append(Template.from_dict(json.load(f)))
                except Exception:
                    pass
        return templates

    def load(self, name: str) -> Optional[Template]:
        path = os.path.join(self.templates_dir, f"{name}.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return self._load_from_data(path, data)
        return None

    def delete(self, name: str) -> None:
        path = os.path.join(self.templates_dir, f"{name}.json")
        if os.path.exists(path):
            os.remove(path)

    def names(self) -> List[str]:
        return [t.name for t in self.load_all()]

    def _load_from_data(self, json_path: str, data: dict) -> Template:
        template = Template.from_dict(data)
        if self._is_in_backgrounds(template.background_path):
            return template

        if not os.path.exists(template.background_path):
            template.is_broken = True
            return template

        try:
            new_background_path = self._copy_background(template.name, template.background_path)
            new_data = dict(data)
            new_data["background_path"] = new_background_path
            self._atomic_write_json(json_path, new_data)
            template.background_path = new_background_path
        except Exception:
            self._record_pending_failed(template.name)
        return template

    def _persistent_background_path(self, template_name: str, background_path: str) -> str:
        if self._is_in_backgrounds(background_path) or not os.path.exists(background_path):
            return background_path
        return self._copy_background(template_name, background_path)

    def _copy_background(self, template_name: str, source_path: str) -> str:
        _, ext = os.path.splitext(source_path)
        dest_path = os.path.join(self.backgrounds_dir, f"{template_name}_bg{ext}")
        if os.path.abspath(source_path) == os.path.abspath(dest_path):
            return dest_path

        shutil.copy2(source_path, dest_path)
        if self._sha256(source_path) != self._sha256(dest_path):
            if os.path.exists(dest_path):
                os.remove(dest_path)
            raise ValueError("background copy hash mismatch")
        return dest_path

    def _is_in_backgrounds(self, path: str) -> bool:
        try:
            backgrounds_dir = os.path.abspath(self.backgrounds_dir)
            return os.path.commonpath([os.path.abspath(path), backgrounds_dir]) == backgrounds_dir
        except ValueError:
            return False

    @staticmethod
    def _sha256(path: str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _atomic_write_json(path: str, data: dict) -> None:
        fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=os.path.dirname(path))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

    @staticmethod
    def _record_pending_failed(template_name: str) -> None:
        try:
            from PyQt6.QtCore import QSettings

            settings = QSettings("融景", "RongJing")
            pending = settings.value("migration/pending_failed", [])
            if isinstance(pending, str):
                pending = [pending] if pending else []
            pending = list(pending)
            if template_name not in pending:
                pending.append(template_name)
            settings.setValue("migration/pending_failed", pending)
        except Exception:
            pass
