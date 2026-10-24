"""Read galaxy positions and dimensions from ASTAP's HyperLEDA catalogue."""

from __future__ import annotations

from dataclasses import dataclass
import os
import sys
from pathlib import Path


@dataclass(frozen=True)
class Galaxy:
    name: str
    ra_deg: float
    dec_deg: float
    major_arcmin: float
    minor_arcmin: float
    position_angle_deg: float


def read_galaxies(catalogue_path: str | Path) -> list[Galaxy]:
    """Read galaxy records with usable ellipse dimensions."""
    galaxies = []

    with Path(catalogue_path).open(encoding="utf-8-sig") as catalogue:
        for line in catalogue:
            fields = line.strip().split(",")

            if len(fields) != 6:
                continue

            ra, dec, name, major, minor, angle = fields

            try:
                major_value = float(major) / 10.0
                minor_value = float(minor) / 10.0

                if major_value <= 0 or minor_value <= 0:
                    continue

                galaxy = Galaxy(
                    name=name,
                    ra_deg=float(ra) / 2400.0,
                    dec_deg=float(dec) / 3600.0,
                    major_arcmin=major_value,
                    minor_arcmin=minor_value,
                    position_angle_deg=float(angle),
                )
            except ValueError:
                continue

            galaxies.append(galaxy)

    return galaxies


def galaxies_in_image(
    galaxies: list[Galaxy],
    wcs_path: str | Path,
    image_width: int,
    image_height: int,
) -> list[Galaxy]:
    """Find catalogue galaxies whose centres fall within an image."""
    from astroframe.annotations import sky_to_pixel

    visible = []

    for galaxy in galaxies:
        try:
            x, y = sky_to_pixel(
                wcs_path,
                galaxy.ra_deg,
                galaxy.dec_deg,
                image_height=image_height,
            )
        except (ValueError, ZeroDivisionError):
            continue

        if 0 <= x < image_width and 0 <= y < image_height:
            visible.append(galaxy)

    return visible


def galaxies_near_field(
    galaxies: list[Galaxy],
    centre_ra_deg: float,
    centre_dec_deg: float,
    radius_deg: float,
) -> list[Galaxy]:
    """Quickly select galaxies within an angular radius of the field centre."""
    import math

    ra0 = math.radians(centre_ra_deg)
    dec0 = math.radians(centre_dec_deg)
    cos_limit = math.cos(math.radians(radius_deg))

    nearby = []

    for galaxy in galaxies:
        ra = math.radians(galaxy.ra_deg)
        dec = math.radians(galaxy.dec_deg)

        cos_separation = (
            math.sin(dec0) * math.sin(dec)
            + math.cos(dec0) * math.cos(dec) * math.cos(ra - ra0)
        )

        if cos_separation >= cos_limit:
            nearby.append(galaxy)

    return nearby


def find_galaxy_catalogue() -> Path | None:
    """Find HyperLEDA in bundled resources or installed ASTAP directories."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    candidates = [
        base / "data" / "hyperleda.csv",
        Path.home() / "Library/Application Support/ASTAP/hyperleda.csv",
        Path("/usr/local/opt/astap/hyperleda.csv"),
        Path("/opt/homebrew/opt/astap/hyperleda.csv"),
        Path("/usr/share/astap/hyperleda.csv"),
    ]

    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA", "APPDATA"):
        root = os.environ.get(variable)
        if root:
            candidates.append(Path(root) / "astap" / "hyperleda.csv")

    candidates.append(Path("C:/astap/hyperleda.csv"))

    return next((path for path in candidates if path.is_file()), None)
