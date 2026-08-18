from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .equipment_catalog import CAMERAS, TELESCOPES


@dataclass(frozen=True)
class CameraEntry:
    key: str
    manufacturer: str
    model: str
    sensor_width_mm: float
    sensor_height_mm: float
    pixel_size_width_um: float | None = None
    pixel_size_height_um: float | None = None
    horizontal_resolution_px: int | None = None
    vertical_resolution_px: int | None = None
    source_url: str | None = None
    source_attribution: str | None = None

    @property
    def display_name(self) -> str:
        return f"{self.manufacturer} {self.model}".strip()


@dataclass(frozen=True)
class OpticalEntry:
    key: str
    manufacturer: str
    model: str
    focal_length_mm: float
    aperture_mm: float | None = None
    focal_ratio: float | None = None
    component_type: str = "optical_tube"
    source_url: str | None = None
    source_attribution: str | None = None

    @property
    def display_name(self) -> str:
        return f"{self.manufacturer} {self.model}".strip()


def _split_manufacturer(name: str) -> tuple[str, str]:
    """Split the current built-in display name into manufacturer and model.

    This is deliberately conservative. It exists only to let the new library
    layer wrap AstroFrame's existing presets while the larger curated external
    catalogue is developed separately.
    """
    known_manufacturers = (
        "William Optics",
        "Sky-Watcher",
        "Takahashi",
        "Celestron",
        "Askar",
        "Canon",
        "Nikon",
        "Sony",
        "ZWO",
    )
    for manufacturer in known_manufacturers:
        prefix = f"{manufacturer} "
        if name.startswith(prefix):
            return manufacturer, name[len(prefix) :]
    first, sep, rest = name.partition(" ")
    return (first, rest) if sep else ("Other", name)


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def builtin_camera_entries() -> tuple[CameraEntry, ...]:
    entries: list[CameraEntry] = []
    for item in CAMERAS:
        manufacturer, model = _split_manufacturer(item.name)
        entries.append(
            CameraEntry(
                key=item.key,
                manufacturer=manufacturer,
                model=model,
                sensor_width_mm=float(item.sensor_width_mm),
                sensor_height_mm=float(item.sensor_height_mm),
            )
        )
    return tuple(entries)


def builtin_optical_entries() -> tuple[OpticalEntry, ...]:
    entries: list[OpticalEntry] = []
    for item in TELESCOPES:
        manufacturer, model = _split_manufacturer(item.name)
        entries.append(
            OpticalEntry(
                key=item.key,
                manufacturer=manufacturer,
                model=model,
                focal_length_mm=float(item.focal_length_mm),
            )
        )
    return tuple(entries)


def _catalog_component_rows(payload: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    """Yield component dictionaries from an AstroGuide metadata envelope.

    AstroGuide currently groups optics and cameras into separate arrays.  We
    deliberately discover component arrays by content instead of hard-coding
    every top-level array name so the adapter survives harmless package layout
    changes while still requiring an explicit ``component_type`` per row.
    """
    catalog = payload.get("catalog", payload)
    if not isinstance(catalog, Mapping):
        return

    for value in catalog.values():
        if not isinstance(value, list):
            continue
        for row in value:
            if isinstance(row, Mapping) and row.get("component_type"):
                yield row


def astroguide_entries(
    payload: Mapping[str, Any],
    *,
    include_needs_review: bool = True,
) -> tuple[tuple[CameraEntry, ...], tuple[OpticalEntry, ...]]:
    """Map AstroGuide equipment metadata into AstroFrame library entries.

    The source catalogue currently marks rows ``needs_review``.  During 1.1
    development those rows are included by default so we can audit the full
    catalogue.  A release build can switch this flag off once curated status
    is being used as a shipping gate.
    """
    cameras: list[CameraEntry] = []
    optics: list[OpticalEntry] = []

    for row in _catalog_component_rows(payload):
        if not include_needs_review and row.get("curation_status") == "needs_review":
            continue

        component_type = str(row.get("component_type", "")).strip().casefold()
        key = str(row.get("component_id") or row.get("source_id") or "").strip()
        manufacturer = str(row.get("manufacturer") or "Other").strip() or "Other"
        model = str(row.get("model") or "").strip()
        if not key or not model:
            continue

        source_url = str(row.get("source_url") or "").strip() or None
        source_attribution = str(row.get("source_attribution") or "").strip() or None

        if component_type == "camera":
            sensor_width = _optional_float(row.get("sensor_width_mm"))
            sensor_height = _optional_float(row.get("sensor_height_mm"))
            if not sensor_width or not sensor_height:
                continue
            cameras.append(
                CameraEntry(
                    key=key,
                    manufacturer=manufacturer,
                    model=model,
                    sensor_width_mm=sensor_width,
                    sensor_height_mm=sensor_height,
                    pixel_size_width_um=_optional_float(row.get("pixel_size_width_um")),
                    pixel_size_height_um=_optional_float(row.get("pixel_size_height_um")),
                    horizontal_resolution_px=_optional_int(row.get("horizontal_resolution_px")),
                    vertical_resolution_px=_optional_int(row.get("vertical_resolution_px")),
                    source_url=source_url,
                    source_attribution=source_attribution,
                )
            )
            continue

        if component_type in {"optical_tube", "lens", "lens_candidate"}:
            focal_length = _optional_float(row.get("native_focal_length_mm"))
            if not focal_length:
                continue
            optics.append(
                OpticalEntry(
                    key=key,
                    manufacturer=manufacturer,
                    model=model,
                    focal_length_mm=focal_length,
                    aperture_mm=_optional_float(row.get("aperture_mm")),
                    focal_ratio=_optional_float(row.get("native_focal_ratio")),
                    component_type=component_type,
                    source_url=source_url,
                    source_attribution=source_attribution,
                )
            )

    return tuple(cameras), tuple(optics)


class EquipmentLibrary:
    """Searchable, UI-agnostic equipment catalogue."""

    def __init__(
        self,
        cameras: Sequence[CameraEntry] | None = None,
        optics: Sequence[OpticalEntry] | None = None,
    ) -> None:
        self._cameras = tuple(cameras if cameras is not None else builtin_camera_entries())
        self._optics = tuple(optics if optics is not None else builtin_optical_entries())

    @classmethod
    def from_astroguide_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        include_needs_review: bool = True,
    ) -> "EquipmentLibrary":
        cameras, optics = astroguide_entries(
            payload,
            include_needs_review=include_needs_review,
        )
        return cls(cameras=cameras, optics=optics)

    @classmethod
    def from_astroguide_file(
        cls,
        path: str | Path,
        *,
        include_needs_review: bool = True,
    ) -> "EquipmentLibrary":
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, Mapping):
            raise ValueError("AstroGuide equipment package must contain a JSON object")
        return cls.from_astroguide_dict(
            payload,
            include_needs_review=include_needs_review,
        )

    @property
    def cameras(self) -> tuple[CameraEntry, ...]:
        return self._cameras

    @property
    def optics(self) -> tuple[OpticalEntry, ...]:
        return self._optics

    def camera_manufacturers(self) -> tuple[str, ...]:
        return tuple(sorted({item.manufacturer for item in self._cameras}, key=str.casefold))

    def optical_manufacturers(self) -> tuple[str, ...]:
        return tuple(sorted({item.manufacturer for item in self._optics}, key=str.casefold))

    def cameras_for_manufacturer(self, manufacturer: str) -> tuple[CameraEntry, ...]:
        return self._sorted(
            item for item in self._cameras if item.manufacturer.casefold() == manufacturer.casefold()
        )

    def optics_for_manufacturer(self, manufacturer: str) -> tuple[OpticalEntry, ...]:
        return self._sorted(
            item for item in self._optics if item.manufacturer.casefold() == manufacturer.casefold()
        )

    def search_cameras(self, query: str) -> tuple[CameraEntry, ...]:
        return self._search(self._cameras, query)

    def search_optics(self, query: str) -> tuple[OpticalEntry, ...]:
        return self._search(self._optics, query)

    @staticmethod
    def _search(entries: Iterable[CameraEntry | OpticalEntry], query: str):
        needle = query.strip().casefold()
        if not needle:
            return EquipmentLibrary._sorted(entries)
        return EquipmentLibrary._sorted(
            item for item in entries if needle in item.display_name.casefold()
        )

    @staticmethod
    def _sorted(entries):
        return tuple(sorted(entries, key=lambda item: item.display_name.casefold()))
