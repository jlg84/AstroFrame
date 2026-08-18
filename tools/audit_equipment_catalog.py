from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.request import Request, urlopen

from astroframe.equipment_library import EquipmentLibrary

DEFAULT_URL = (
    "https://raw.githubusercontent.com/tophrchris/astroguide-metadata/"
    "main/v1/packages/equipment/astrophotography_equipment_catalog_v1.json"
)


def _load(source: str) -> Mapping[str, Any]:
    path = Path(source).expanduser()
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    else:
        request = Request(source, headers={"User-Agent": "AstroFrame-equipment-audit/1.1"})
        with urlopen(request, timeout=30) as response:  # nosec B310 - explicit dev audit URL
            payload = json.load(response)
    if not isinstance(payload, Mapping):
        raise ValueError("Equipment catalogue must contain a JSON object")
    return payload


def _rows(payload: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    catalog = payload.get("catalog", payload)
    if not isinstance(catalog, Mapping):
        return
    for value in catalog.values():
        if isinstance(value, list):
            for row in value:
                if isinstance(row, Mapping) and row.get("component_type"):
                    yield row


def _missing(value: Any) -> bool:
    return value is None or value == ""


def _duplicate_names(rows: Iterable[Mapping[str, Any]]) -> list[tuple[str, int]]:
    counts: Counter[str] = Counter()
    for row in rows:
        name = f"{row.get('manufacturer', '')} {row.get('model', '')}".strip().casefold()
        if name:
            counts[name] += 1
    return sorted(((name, count) for name, count in counts.items() if count > 1), key=lambda x: (-x[1], x[0]))


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit AstroGuide equipment metadata for AstroFrame 1.1")
    parser.add_argument("source", nargs="?", default=DEFAULT_URL, help="JSON file path or URL")
    args = parser.parse_args()

    print(f"Loading: {args.source}")
    payload = _load(args.source)
    raw_rows = list(_rows(payload))
    by_type = Counter(str(row.get("component_type", "")).strip() for row in raw_rows)
    by_status = Counter(str(row.get("curation_status", "")).strip() or "(blank)" for row in raw_rows)

    library = EquipmentLibrary.from_astroguide_dict(payload, include_needs_review=True)

    print("\n=== RAW CATALOGUE ===")
    print(f"Rows: {len(raw_rows)}")
    for kind, count in sorted(by_type.items()):
        print(f"  {kind}: {count}")
    print("Curation status:")
    for status, count in sorted(by_status.items()):
        print(f"  {status}: {count}")

    print("\n=== ASTROFRAME-LOADABLE ===")
    print(f"Cameras: {len(library.cameras)}")
    print(f"Optics: {len(library.optics)}")
    optic_types = Counter(item.component_type for item in library.optics)
    for kind, count in sorted(optic_types.items()):
        print(f"  {kind}: {count}")
    print(f"Camera manufacturers: {len(library.camera_manufacturers())}")
    print(f"Optical manufacturers: {len(library.optical_manufacturers())}")

    print("\n=== DATA QUALITY ===")
    cameras = [row for row in raw_rows if str(row.get("component_type", "")).casefold() == "camera"]
    optics = [row for row in raw_rows if str(row.get("component_type", "")).casefold() in {"optical_tube", "lens", "lens_candidate"}]
    missing_sensor = sum(_missing(row.get("sensor_width_mm")) or _missing(row.get("sensor_height_mm")) for row in cameras)
    missing_focal = sum(_missing(row.get("native_focal_length_mm")) for row in optics)
    missing_aperture = sum(_missing(row.get("aperture_mm")) for row in optics)
    missing_attribution = sum(_missing(row.get("source_attribution")) for row in raw_rows)
    duplicates = _duplicate_names(raw_rows)
    print(f"Cameras missing sensor dimensions: {missing_sensor}")
    print(f"Optics missing focal length: {missing_focal}")
    print(f"Optics missing aperture: {missing_aperture}")
    print(f"Rows missing source attribution: {missing_attribution}")
    print(f"Duplicate manufacturer/model names: {len(duplicates)}")
    for name, count in duplicates[:15]:
        print(f"  {count}x {name}")

    print("\n=== LARGEST MANUFACTURERS ===")
    camera_makers = Counter(item.manufacturer for item in library.cameras)
    optic_makers = Counter(item.manufacturer for item in library.optics)
    print("Cameras:")
    for maker, count in camera_makers.most_common(15):
        print(f"  {maker}: {count}")
    print("Optics:")
    for maker, count in optic_makers.most_common(15):
        print(f"  {maker}: {count}")

    print("\n=== SPOT CHECKS ===")
    for query in ("EdgeHD 8", "ASI533", "Seestar", "Rokinon", "Sigma 14"):
        matches = (*library.search_cameras(query), *library.search_optics(query))
        print(f"{query}: {len(matches)} match(es)")
        for item in matches[:8]:
            print(f"  {item.display_name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
