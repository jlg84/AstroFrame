from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .equipment_catalog import CAMERAS, TELESCOPES


@dataclass(frozen=True)
class CameraEntry:
    key: str
    manufacturer: str
    model: str
    sensor_width_mm: float
    sensor_height_mm: float

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


class EquipmentLibrary:
    """Searchable, UI-agnostic equipment catalogue.

    The first implementation wraps AstroFrame's existing built-ins.  External
    metadata can later be mapped into the same CameraEntry/OpticalEntry model
    without changing the rig or framing code.
    """

    def __init__(
        self,
        cameras: Sequence[CameraEntry] | None = None,
        optics: Sequence[OpticalEntry] | None = None,
    ) -> None:
        self._cameras = tuple(cameras if cameras is not None else builtin_camera_entries())
        self._optics = tuple(optics if optics is not None else builtin_optical_entries())

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
