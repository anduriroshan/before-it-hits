"""Staged regions: the data a drawn box is answered from.

Nothing here is fetched from a third-party API at request time. Terrain is a cloud-optimised
GeoTIFF read through a windowed range request; vectors are FlatGeobuf, which carries a packed
R-tree so a bounding-box read also only fetches the bytes it needs. Both work the same against a
local path or a gs:// URL, so the staging job is the only thing that ever talks to Overpass or a
data portal.

Live conditions (rainfall, places, news) are a different matter and do come from APIs at request
time: they change by the hour and are small. Geography does not change by the hour.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pyogrio
import shapely

from .osm import OsmData, Road

LAYERS = ("water", "waterways", "roads", "buildings")


@dataclass
class Region:
    """One staged region: a manifest, a terrain raster and some vector layers."""

    root: Path
    manifest: dict

    @classmethod
    def load(cls, root: str | Path) -> "Region":
        root = Path(root)
        return cls(root=root, manifest=json.loads((root / "region.json").read_text(encoding="utf-8")))

    @property
    def name(self) -> str:
        return self.manifest["name"]

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return tuple(self.manifest["bbox_wsen"])

    @property
    def terrain(self) -> Path:
        return self.root / self.manifest["terrain"]["file"]

    @property
    def terrain_cap(self) -> float | None:
        """The source's ceiling value, if it has one (DeltaDTM caps at 30 m)."""
        return self.manifest["terrain"].get("cap_value")

    @property
    def presets(self) -> list[dict]:
        return self.manifest.get("presets", [])

    @property
    def official_return_periods(self) -> list[int]:
        return (self.manifest.get("official") or {}).get("return_periods", [])

    @property
    def preparations(self) -> dict | None:
        """The documented preparedness budget and the measures it pays for, if staged for this
        region. Everything in it is a figure from a city document, never one the engine invents."""
        return self.manifest.get("preparations")

    def layer_path(self, layer: str) -> Path:
        return self.root / (layer + ".fgb")

    def covers(self, bbox: tuple[float, float, float, float]) -> bool:
        w, s, e, n = bbox
        rw, rs, re_, rn = self.bounds
        return rw <= w and rs <= s and e <= re_ and n <= rn

    def read_osm(self, bbox: tuple[float, float, float, float]) -> OsmData:
        """Read only the features whose extent meets `bbox`, using each file's spatial index."""
        out = OsmData()
        counts = {}
        for layer in LAYERS:
            path = self.layer_path(layer)
            if not path.exists():
                counts[layer] = None  # not staged for this region
                continue
            geoms, fields = _read_bbox(path, bbox)
            counts[layer] = len(geoms)
            if layer == "water":
                out.water = geoms
            elif layer == "waterways":
                out.waterways = geoms
            elif layer == "buildings":
                out.buildings = geoms
            else:
                out.roads = [
                    Road(geom=g, highway=h or "residential", name=nm,
                         elevated=bool(ev), tunnel=bool(tn), osm_id=int(i) if i is not None else None)
                    for g, h, nm, ev, tn, i in zip(
                        geoms, fields["highway"], fields["name"], fields["elevated"],
                        fields["tunnel"], fields["osm_id"])
                ]
        out.stats = {"source": "staged region", "region": self.name, "read": counts}
        return out


def _read_bbox(path: Path, bbox):
    meta, _index, geometry, field_data = pyogrio.raw.read(str(path), bbox=tuple(bbox))
    geoms = list(shapely.from_wkb(geometry)) if geometry is not None else []
    fields = {name: list(col) for name, col in zip(meta["fields"], field_data)}
    for name in ("highway", "name", "elevated", "tunnel", "osm_id"):
        fields.setdefault(name, [None] * len(geoms))
    return geoms, fields
