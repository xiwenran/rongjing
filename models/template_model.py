import json
import os
from dataclasses import dataclass, asdict
from typing import List, Optional


@dataclass
class Template:
    name: str
    background_path: str
    screen_points: List[List[float]]  # 4 points [[x,y],...] TL→TR→BR→BL in bg image coords
    output_width: int = 0   # 0 = auto (use bg image size)
    output_height: int = 0  # 0 = auto

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Template":
        return cls(
            name=d["name"],
            background_path=d["background_path"],
            screen_points=d["screen_points"],
            output_width=d.get("output_width", 0),
            output_height=d.get("output_height", 0),
        )

    @property
    def output_size(self) -> Optional[tuple]:
        if self.output_width > 0 and self.output_height > 0:
            return (self.output_width, self.output_height)
        return None


class TemplateManager:
    def __init__(self, templates_dir: str):
        self.templates_dir = templates_dir
        os.makedirs(templates_dir, exist_ok=True)

    def save(self, template: Template) -> None:
        path = os.path.join(self.templates_dir, f"{template.name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(template.to_dict(), f, ensure_ascii=False, indent=2)

    def load_all(self) -> List[Template]:
        templates = []
        for fn in sorted(os.listdir(self.templates_dir)):
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
                return Template.from_dict(json.load(f))
        return None

    def delete(self, name: str) -> None:
        path = os.path.join(self.templates_dir, f"{name}.json")
        if os.path.exists(path):
            os.remove(path)

    def names(self) -> List[str]:
        return [t.name for t in self.load_all()]
