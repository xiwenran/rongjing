import json
import os
import re
from dataclasses import asdict, dataclass
from typing import List, Optional


def _natural_key(s: str):
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', s)]


@dataclass
class CollageTemplate:
    name: str
    layout: str
    rows: int
    cols: int
    gap: int
    padding: int
    background_color: str
    cell_aspect_ratio: float
    output_width: int
    output_height: int
    output_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CollageTemplate":
        return cls(
            name=d["name"],
            layout=d["layout"],
            rows=d["rows"],
            cols=d["cols"],
            gap=d["gap"],
            padding=d["padding"],
            background_color=d["background_color"],
            cell_aspect_ratio=d["cell_aspect_ratio"],
            output_width=d["output_width"],
            output_height=d["output_height"],
            output_count=d.get("output_count", 0),
        )

    @property
    def total_cells(self) -> int:
        if self.layout == "hero":
            return 1 + self.rows * self.cols
        return self.rows * self.cols


class CollageManager:
    def __init__(self, collages_dir: str):
        self.collages_dir = collages_dir
        os.makedirs(collages_dir, exist_ok=True)

    def save(self, template: CollageTemplate) -> None:
        path = os.path.join(self.collages_dir, f"{template.name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(template.to_dict(), f, ensure_ascii=False, indent=2)

    def load_all(self) -> List[CollageTemplate]:
        templates = []
        for fn in sorted(os.listdir(self.collages_dir), key=_natural_key):
            if fn.endswith(".json"):
                try:
                    with open(os.path.join(self.collages_dir, fn), "r", encoding="utf-8") as f:
                        templates.append(CollageTemplate.from_dict(json.load(f)))
                except Exception:
                    pass
        return templates

    def load(self, name: str) -> Optional[CollageTemplate]:
        path = os.path.join(self.collages_dir, f"{name}.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return CollageTemplate.from_dict(json.load(f))
        return None

    def delete(self, name: str) -> None:
        path = os.path.join(self.collages_dir, f"{name}.json")
        if os.path.exists(path):
            os.remove(path)

    def names(self) -> List[str]:
        return [t.name for t in self.load_all()]
