"""Non-destructive image annotation rendering for AstroFrame."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from astropy.io import fits
from astropy.wcs import WCS

from PySide6.QtCore import QPointF, Qt, QRectF
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen


@dataclass(frozen=True)
class Annotation:
    """An identified astronomical object at source-image pixel coordinates."""

    name: str
    x: float
    y: float
    major_arcmin: float = 0.0
    minor_arcmin: float = 0.0
    position_angle_deg: float = 0.0
    major_radius_px: float = 0.0
    minor_radius_px: float = 0.0
    image_angle_deg: float = 0.0
    colour: str | None = None
    prefer_marker: bool = False


def render_annotated_image(
    image_path: str | Path,
    annotations: list[Annotation],
    output_path: str | Path,
    *,
    colour: str = "#83CBE8",
    annotation_style: str = "automatic",
    show_outlines: bool = True,
) -> None:
    """Export an annotated copy without modifying the source photograph."""

    image = QImage(str(image_path))
    if image.isNull():
        raise ValueError(f"Cannot open image: {image_path}")

    image = image.convertToFormat(QImage.Format.Format_ARGB32)

    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        scale = max(1.0, image.width() / 2000.0)

        pen = QPen(QColor(colour))
        pen.setWidthF(1.5 * scale)
        painter.setPen(pen)

        font = QFont("Helvetica")
        font.setPixelSize(round(15 * scale))
        painter.setFont(font)

        # Track occupied label rectangles to prevent text collisions.
        placed_label_rects: list[QRectF] = []

        for annotation in annotations:
            annotation_colour = annotation.colour or colour
            pen.setColor(QColor(annotation_colour))
            painter.setPen(pen)
            centre = QPointF(annotation.x, annotation.y)

            ellipse_is_oversized = (
                annotation.major_radius_px > 0
                and annotation.minor_radius_px > 0
                and (
                    2 * max(annotation.major_radius_px, annotation.minor_radius_px)
                    > 0.8 * min(image.width(), image.height())
                )
            )

            if (
                show_outlines
                and annotation.major_radius_px > 0
                and annotation.minor_radius_px > 0
                and (annotation_style == "outlines" or not ellipse_is_oversized)
                and annotation_style != "markers"
                and not annotation.prefer_marker
            ):
                # Draw the catalogue ellipse in image coordinates.
                painter.save()
                painter.translate(centre)
                painter.rotate(annotation.image_angle_deg)
                painter.drawEllipse(
                    QPointF(0, 0),
                    annotation.major_radius_px,
                    annotation.minor_radius_px,
                )
                painter.restore()

                # Horizontal extent of a rotated ellipse.
                import math
                angle = math.radians(annotation.image_angle_deg)
                right_extent = math.hypot(
                    annotation.major_radius_px * math.cos(angle),
                    annotation.minor_radius_px * math.sin(angle),
                )
                left_extent = right_extent
                label_x = annotation.x + right_extent + 12 * scale
            else:
                # Use discreet markers for tiny catalogue galaxies.
                # Other object types retain their original marker size.
                tiny_galaxy = (
                    annotation.prefer_marker
                    and annotation.major_radius_px > 0
                    and annotation.major_radius_px < 4.0
                    and annotation.minor_radius_px > 0
                )
                radius = (3.5 if tiny_galaxy else 12.0) * scale
                painter.drawEllipse(centre, radius, radius)
                left_extent = radius
                right_extent = radius
                label_x = annotation.x + radius + 6 * scale

            # Prefer a label outside the ellipse, but avoid pushing it
            # against the image edge when a large object fills the frame.
            metrics = painter.fontMetrics()
            label_width = metrics.horizontalAdvance(annotation.name)
            margin = 12 * scale
            gap = 12 * scale

            right_x = annotation.x + right_extent + gap
            left_x = annotation.x - left_extent - label_width - gap

            if right_x + label_width <= image.width() - margin:
                label_x = right_x
                label_y = annotation.y
            elif left_x >= margin:
                label_x = left_x
                label_y = annotation.y
            else:
                # Large nebulae may fill the entire frame. In that case,
                # place the label near the object rather than at an edge.
                label_x = annotation.x + 14 * scale
                label_y = annotation.y - 14 * scale

            label_x = max(
                margin,
                min(label_x, image.width() - label_width - margin),
            )
            label_y = max(
                metrics.ascent() + margin,
                min(
                    label_y,
                    image.height() - metrics.descent() - margin,
                ),
            )

            # Remember the preferred position so displaced labels
            # can be connected back to their astronomical objects.
            preferred_label_x = label_x
            preferred_label_y = label_y

            # Keep the original position unless it collides with
            # a label already placed.
            def label_rectangle(x, y):
                return QRectF(
                    x - 4 * scale,
                    y - metrics.ascent() - 3 * scale,
                    label_width + 8 * scale,
                    metrics.height() + 6 * scale,
                )

            def position_is_clear(x, y):
                rect = label_rectangle(x, y)
                if (
                    rect.left() < 0
                    or rect.top() < 0
                    or rect.right() > image.width()
                    or rect.bottom() > image.height()
                ):
                    return False
                return not any(
                    rect.adjusted(
                        -4 * scale, -4 * scale,
                        4 * scale, 4 * scale
                    ).intersects(other)
                    for other in placed_label_rects
                )

            if not position_is_clear(label_x, label_y):
                # Search progressively farther from the object,
                # preferring nearby positions before distant ones.
                candidates = []

                for distance in (1, 2, 3, 4):
                    offset = distance * (metrics.height() + 6 * scale)

                    candidates.extend([
                        (right_x, annotation.y - offset),
                        (right_x, annotation.y + offset),
                        (left_x, annotation.y - offset),
                        (left_x, annotation.y + offset),
                        (annotation.x - label_width / 2,
                         annotation.y - offset),
                        (annotation.x - label_width / 2,
                         annotation.y + offset),
                    ])

                for candidate_x, candidate_y in candidates:
                    if position_is_clear(candidate_x, candidate_y):
                        label_x, label_y = candidate_x, candidate_y
                        break

            # Keep the object marker even if no readable label
            # position can be found.
            if not position_is_clear(label_x, label_y):
                continue

            # Connect substantially displaced labels to their objects.
            # Draw before the backing so the line never obscures text.
            displacement = (
                (label_x - preferred_label_x) ** 2
                + (label_y - preferred_label_y) ** 2
            ) ** 0.5

            if displacement > 20 * scale:
                text_rect = label_rectangle(label_x, label_y)

                target_x = max(
                    text_rect.left(),
                    min(annotation.x, text_rect.right()),
                )
                target_y = max(
                    text_rect.top(),
                    min(annotation.y, text_rect.bottom()),
                )

                dx = target_x - annotation.x
                dy = target_y - annotation.y
                distance = (dx * dx + dy * dy) ** 0.5

                if distance > 1:
                    marker_radius = (
                        max(left_extent, right_extent)
                        if annotation.major_radius_px <= 0
                        else 0
                    )
                    start_offset = min(marker_radius, distance * 0.5)

                    start_x = annotation.x + dx * start_offset / distance
                    start_y = annotation.y + dy * start_offset / distance

                    painter.save()
                    connector_colour = QColor(annotation_colour)
                    connector_colour.setAlpha(110)
                    connector_pen = QPen(connector_colour)
                    connector_pen.setWidthF(0.8 * scale)
                    painter.setPen(connector_pen)
                    painter.drawLine(
                        QPointF(start_x, start_y),
                        QPointF(target_x, target_y),
                    )
                    painter.restore()

            # A translucent dark backing keeps labels readable.
            text_rect = label_rectangle(label_x, label_y)
            placed_label_rects.append(text_rect)
            painter.fillRect(text_rect, QColor(0, 0, 0, 180))
            painter.setPen(QColor(annotation_colour))
            painter.drawText(
                QPointF(label_x, label_y),
                annotation.name,
            )

    finally:
        painter.end()

    if not image.save(str(output_path)):
        raise OSError(f"Cannot save annotated image: {output_path}")



@lru_cache(maxsize=8)
def _load_astropy_wcs(path_text: str, modified_ns: int, file_size: int) -> WCS:
    """Load either a FITS WCS or an ASTAP header-only WCS."""
    path = Path(path_text)
    with path.open("rb") as handle:
        first_card = handle.read(80)
    if first_card.startswith(b"SIMPLE  ="):
        with fits.open(path) as hdul:
            header = hdul[0].header.copy()
    else:
        data = path.read_bytes()
        end = next(
            (i + 80 for i in range(0, len(data), 80)
             if data[i:i + 8].strip() == b"END"),
            len(data),
        )
        header = fits.Header.fromstring(
            data[:end].decode("ascii"), sep=""
        )
    return WCS(header).celestial


@lru_cache(maxsize=8)
def _read_wcs_header_cached(
    path_text: str, modified_ns: int, file_size: int
) -> dict[str, str]:
    """Read and parse a WCS header once per file version."""
    data = Path(path_text).read_bytes()
    header: dict[str, str] = {}

    for offset in range(0, len(data), 80):
        card = data[offset:offset + 80].decode("ascii", errors="replace")
        key = card[:8].strip()
        if key == "END":
            break
        if card[8:10] == "= ":
            value = card[10:80].split("/")[0].strip()
            header[key] = value.strip("' ")

    return header


def sky_to_pixel(
    wcs_path: str | Path,
    ra_deg: float,
    dec_deg: float,
    *,
    image_height: int,
) -> tuple[float, float]:
    """Convert celestial coordinates to top-left-origin image pixels."""
    path = Path(wcs_path).resolve()
    stat = path.stat()
    wcs = _load_astropy_wcs(str(path), stat.st_mtime_ns, stat.st_size)
    x, fits_y = wcs.all_world2pix([[ra_deg, dec_deg]], 0)[0]
    return float(x), float(image_height - 1 - fits_y)


def galaxy_ellipse_pixels(
    wcs_path: str | Path,
    ra_deg: float,
    dec_deg: float,
    major_arcmin: float,
    minor_arcmin: float,
    position_angle_deg: float,
    *,
    image_height: int,
) -> tuple[float, float, float, float, float]:
    """Return centre, semiaxes and image angle for a galaxy ellipse."""
    import math

    x, y = sky_to_pixel(
        wcs_path, ra_deg, dec_deg, image_height=image_height
    )

    pa = math.radians(position_angle_deg)
    dec = math.radians(dec_deg)

    # Position angle is measured eastward from celestial north.
    # Convert the major-axis direction into a small sky displacement.
    half_major_deg = major_arcmin / 120.0
    half_minor_deg = minor_arcmin / 120.0

    def projected_offset(east_deg: float, north_deg: float):
        offset_ra = east_deg / math.cos(dec)
        return sky_to_pixel(
            wcs_path,
            ra_deg + offset_ra,
            dec_deg + north_deg,
            image_height=image_height,
        )

    major_x, major_y = projected_offset(
        half_major_deg * math.sin(pa),
        half_major_deg * math.cos(pa),
    )
    minor_x, minor_y = projected_offset(
        half_minor_deg * math.cos(pa),
        -half_minor_deg * math.sin(pa),
    )

    major_radius = math.hypot(major_x - x, major_y - y)
    minor_radius = math.hypot(minor_x - x, minor_y - y)
    image_angle = math.degrees(math.atan2(major_y - y, major_x - x))

    return x, y, major_radius, minor_radius, image_angle


def image_dimensions(image_path: str | Path) -> tuple[int, int]:
    """Return the actual pixel dimensions of an image."""
    image = QImage(str(image_path))
    if image.isNull():
        raise ValueError(f"Cannot open image: {image_path}")
    return image.width(), image.height()


def pixel_to_sky(
    wcs_path: str | Path,
    x: float,
    y: float,
    *,
    image_height: int,
) -> tuple[float, float]:
    """Convert top-left-origin image pixels to celestial coordinates."""
    path = Path(wcs_path).resolve()
    stat = path.stat()
    wcs = _load_astropy_wcs(str(path), stat.st_mtime_ns, stat.st_size)
    fits_y = image_height - 1 - y
    ra, dec = wcs.all_pix2world([[x, fits_y]], 0)[0]
    return float(ra) % 360.0, float(dec)


def image_sky_coverage(
    wcs_path: str | Path,
    image_width: int,
    image_height: int,
) -> tuple[float, float, float]:
    """Return field-centre RA, Dec and corner search radius in degrees."""
    import math

    centre_ra, centre_dec = pixel_to_sky(
        wcs_path,
        (image_width - 1) / 2,
        (image_height - 1) / 2,
        image_height=image_height,
    )

    ra0 = math.radians(centre_ra)
    dec0 = math.radians(centre_dec)

    corners = [
        (0, 0),
        (image_width - 1, 0),
        (0, image_height - 1),
        (image_width - 1, image_height - 1),
    ]

    max_separation = 0.0

    for x, y in corners:
        ra, dec = pixel_to_sky(
            wcs_path, x, y, image_height=image_height
        )

        ra = math.radians(ra)
        dec = math.radians(dec)

        cos_separation = (
            math.sin(dec0) * math.sin(dec)
            + math.cos(dec0) * math.cos(dec)
            * math.cos(ra - ra0)
        )

        separation = math.degrees(
            math.acos(max(-1.0, min(1.0, cos_separation)))
        )

        max_separation = max(max_separation, separation)

    # Small margin for catalogue objects close to the image boundary.
    return centre_ra, centre_dec, max_separation * 1.05



def annotate_openngc_objects(
    image_path: str | Path,
    wcs_path: str | Path,
    output_path: str | Path,
    *,
    mode: str,
    min_major_arcmin: float = 0.5,
    colour: str = "#83CBE8",
    progress=None,
    collect_only: bool = False,
) -> int | list[Annotation]:
    """Annotate OpenNGC nebulae or star clusters on a plate-solved image."""
    import csv
    import requests
    from astropy.coordinates import SkyCoord
    import astropy.units as u

    supported_modes = {
        "nebulae": {"Neb", "HII", "EmN", "RfN", "SNR"},
        "clusters": {"Cl+N"},
        "planetary_nebulae": {"PN"},
        "open_clusters": {"OCl"},
        "globular_clusters": {"GCl"},
    }
    if mode not in supported_modes:
        raise ValueError(f"Unsupported OpenNGC annotation mode: {mode}")

    cache_dir = Path.home() / ".astroframe" / "reference_catalogs"
    cache_dir.mkdir(parents=True, exist_ok=True)
    catalogue_path = cache_dir / "openngc.csv"

    if not catalogue_path.is_file():
        if progress:
            progress("Downloading OpenNGC catalogue…")
        response = requests.get(
            "https://raw.githubusercontent.com/mattiaverga/OpenNGC/master/database_files/NGC.csv",
            timeout=60,
        )
        response.raise_for_status()
        temporary_path = catalogue_path.with_suffix(".tmp")
        temporary_path.write_bytes(response.content)
        temporary_path.replace(catalogue_path)

    if progress:
        progress("Preparing image and WCS…")

    width, height = image_dimensions(image_path)
    centre_ra, centre_dec, radius = image_sky_coverage(
        wcs_path, width, height
    )



    if progress:
        progress(f"Reading OpenNGC {mode} catalogue…")

    annotations = []
    image_centre = SkyCoord(
        ra=centre_ra * u.deg,
        dec=centre_dec * u.deg,
        frame="icrs",
    )

    with catalogue_path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        for row in reader:
            object_type = (row.get("Type") or "").strip()
            if object_type not in supported_modes[mode]:
                continue

            try:
                coord = SkyCoord(
                    row["RA"], row["Dec"],
                    unit=(u.hourangle, u.deg), frame="icrs",
                )
                ra_deg = float(coord.ra.deg)
                dec_deg = float(coord.dec.deg)
                major = float(row.get("MajAx") or 0)
                minor = float(row.get("MinAx") or major)
                position_angle = float(row.get("PosAng") or 0)
            except (ValueError, TypeError, KeyError):
                continue

            if major < min_major_arcmin or minor <= 0:
                continue

            # Reject objects well outside the image before doing the
            # more expensive WCS-to-pixel ellipse calculations.
            angular_distance = coord.separation(image_centre).deg
            object_radius_deg = max(major, minor) / 120.0
            if angular_distance > radius + object_radius_deg + 0.02:
                continue

            try:
                x, y, major_px, minor_px, angle = galaxy_ellipse_pixels(
                    wcs_path, ra_deg, dec_deg, major, minor,
                    position_angle, image_height=height,
                )
            except (ValueError, KeyError, ZeroDivisionError):
                continue

            if not (0 <= x < width and 0 <= y < height):
                continue

            catalogue_name = (row.get("Name") or "").strip()
            common_name = (row.get("Common names") or "").split(",", 1)[0].strip()
            messier = (row.get("M") or "").strip()

            # Format catalogue identifiers for readability.
            for prefix in ("NGC", "IC"):
                if catalogue_name.startswith(prefix):
                    suffix = catalogue_name[len(prefix):]
                    if suffix.isdigit():
                        catalogue_name = f"{prefix} {suffix}"
                    break

            if messier:
                messier_number = messier.upper().removeprefix("M").strip()
                if messier_number.isdigit():
                    messier = f"M{int(messier_number)}"
                else:
                    messier = f"M{messier_number}"
                if common_name:
                    name = f"{messier} — {common_name}"
                    if catalogue_name:
                        name += f" ({catalogue_name})"
                else:
                    name = f"{messier} ({catalogue_name})" if catalogue_name else messier
            elif common_name and catalogue_name:
                name = f"{common_name} ({catalogue_name})"
            else:
                name = common_name or catalogue_name

            if not name:
                continue

            annotations.append(
                Annotation(
                    name=name,
                    x=x,
                    y=y,
                    major_arcmin=major,
                    minor_arcmin=minor,
                    position_angle_deg=position_angle,
                    major_radius_px=major_px,
                    minor_radius_px=minor_px,
                    image_angle_deg=angle,
                )
            )

    if collect_only:
        return [Annotation(**{**vars(a), "colour": colour}) for a in annotations]

    if progress:
        progress(f"Rendering {len(annotations)} OpenNGC annotations…")

    render_annotated_image(
        image_path, annotations, output_path, colour=colour
    )
    return len(annotations)


def annotate_galaxies(
    image_path: str | Path,
    wcs_path: str | Path,
    catalogue_path: str | Path,
    output_path: str | Path,
    *,
    min_major_arcmin: float = 1.0,
    colour: str = "#00FFFF",
    progress=None,
    collect_only: bool = False,
) -> int | list[Annotation]:
    """Automatically annotate catalogue galaxies in a plate-solved image.

    Returns the number of galaxies annotated.
    """
    from astroframe.galaxy_catalogue import (
        read_galaxies,
        galaxies_near_field,
        galaxies_in_image,
    )

    if progress:
        progress("Preparing image and WCS…")

    width, height = image_dimensions(image_path)

    centre_ra, centre_dec, radius = image_sky_coverage(
        wcs_path, width, height
    )

    if progress:
        progress("Reading galaxy catalogue…")

    galaxies = read_galaxies(catalogue_path)

    if progress:
        progress("Identifying galaxies in the image…")

    nearby = galaxies_near_field(
        galaxies,
        centre_ra,
        centre_dec,
        radius,
    )

    visible = galaxies_in_image(
        nearby,
        wcs_path,
        width,
        height,
    )

    annotations = []

    for galaxy in visible:
        if galaxy.major_arcmin < min_major_arcmin:
            continue

        x, y, major_radius, minor_radius, angle = galaxy_ellipse_pixels(
            wcs_path,
            galaxy.ra_deg,
            galaxy.dec_deg,
            galaxy.major_arcmin,
            galaxy.minor_arcmin,
            galaxy.position_angle_deg,
            image_height=height,
        )

        annotations.append(
            Annotation(
                name=galaxy.name,
                x=x,
                y=y,
                major_arcmin=galaxy.major_arcmin,
                minor_arcmin=galaxy.minor_arcmin,
                position_angle_deg=galaxy.position_angle_deg,
                major_radius_px=major_radius,
                minor_radius_px=minor_radius,
                image_angle_deg=angle,
            )
        )

    if collect_only:
        return [Annotation(**{**vars(a), "colour": colour}) for a in annotations]

    if progress:
        progress("Rendering annotated image…")

    render_annotated_image(
        image_path,
        annotations,
        output_path,
        colour=colour,
    )

    return len(annotations)


def annotate_barnard_objects(
    image_path: str | Path,
    wcs_path: str | Path,
    output_path: str | Path,
    *,
    colour: str = "#D9B878",
    progress=None,
    collect_only: bool = False,
) -> int | list[Annotation]:
    """Annotate Barnard dark nebulae using the VizieR VII/220A catalogue."""
    import csv
    import io
    import requests
    from astropy.coordinates import SkyCoord
    import astropy.units as u

    cache_dir = Path.home() / ".astroframe" / "reference_catalogs"
    cache_dir.mkdir(parents=True, exist_ok=True)
    catalogue_path = cache_dir / "barnard.tsv"

    if not catalogue_path.is_file():
        if progress:
            progress("Downloading Barnard dark-nebula catalogue…")
        response = requests.get(
            "https://vizier.cds.unistra.fr/viz-bin/asu-tsv"
            "?-source=VII/220A/barnard&-out.max=unlimited",
            timeout=60,
        )
        response.raise_for_status()
        data = response.text
        assert "Barn" in data and "_RA.icrs" in data, "Invalid Barnard catalogue"
        temporary_path = catalogue_path.with_suffix(".tmp")
        temporary_path.write_text(data, encoding="utf-8")
        temporary_path.replace(catalogue_path)

    width, height = image_dimensions(image_path)
    centre_ra, centre_dec, radius = image_sky_coverage(
        wcs_path, width, height
    )
    image_centre = SkyCoord(
        ra=centre_ra * u.deg, dec=centre_dec * u.deg, frame="icrs"
    )

    if progress:
        progress("Reading Barnard dark nebulae…")

    lines = [
        line for line in catalogue_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    rows = list(csv.DictReader(io.StringIO("\n".join(lines)), delimiter="\t"))
    annotations = []

    for row in rows:
        number = (row.get("Barn") or "").strip()
        if not number.isdigit():
            continue
        try:
            coord = SkyCoord(
                row["_RA.icrs"], row["_DE.icrs"],
                unit=(u.hourangle, u.deg), frame="icrs",
            )
            diameter = float(row.get("Diam") or 0)
        except (ValueError, TypeError, KeyError):
            continue

        if coord.separation(image_centre).deg > radius + diameter / 120 + 0.02:
            continue

        try:
            if diameter > 0:
                x, y, major_px, minor_px, angle = galaxy_ellipse_pixels(
                    wcs_path, float(coord.ra.deg), float(coord.dec.deg),
                    diameter, diameter, 0, image_height=height,
                )
            else:
                x, y = sky_to_pixel(
                    wcs_path, float(coord.ra.deg), float(coord.dec.deg),
                    image_height=height,
                )
                major_px = minor_px = angle = 0.0
        except (ValueError, KeyError, ZeroDivisionError):
            continue

        if not (0 <= x < width and 0 <= y < height):
            continue

        annotations.append(Annotation(
            name=f"B {int(number)}",
            prefer_marker=True,
            x=x, y=y,
            major_arcmin=diameter,
            minor_arcmin=diameter,
            major_radius_px=major_px,
            minor_radius_px=minor_px,
            image_angle_deg=angle,
            colour=colour,
        ))

    if collect_only:
        return annotations

    render_annotated_image(image_path, annotations, output_path, colour=colour)
    return len(annotations)


def annotate_bright_stars(
    image_path: str | Path,
    wcs_path: str | Path,
    output_path: str | Path,
    *,
    magnitude_limit: float = 6.0,
    colour: str = "#FFD166",
    progress=None,
    collect_only: bool = False,
) -> int | list[Annotation]:
    """Annotate bright stars from the Yale Bright Star Catalogue."""
    import csv
    import io
    import requests
    from astropy.coordinates import SkyCoord
    import astropy.units as u

    cache_dir = Path.home() / ".astroframe" / "reference_catalogs"
    cache_dir.mkdir(parents=True, exist_ok=True)
    catalogue_path = cache_dir / "yale_bright_stars.tsv"

    if not catalogue_path.is_file():
        if progress:
            progress("Downloading Yale Bright Star Catalogue…")
        response = requests.get(
            "https://vizier.cds.unistra.fr/viz-bin/asu-tsv",
            params={
                "-source": "V/50/catalog",
                "-out": "HR,Name,RAJ2000,DEJ2000,Vmag",
                "-out.max": "unlimited",
            },
            timeout=60,
            headers={"User-Agent": "AstroFrame"},
        )
        response.raise_for_status()
        data = response.text
        if "RAJ2000" not in data or "Vmag" not in data:
            raise ValueError("VizieR returned an invalid bright-star catalogue.")
        temporary_path = catalogue_path.with_suffix(".tmp")
        temporary_path.write_text(data, encoding="utf-8")
        temporary_path.replace(catalogue_path)

    if progress:
        progress("Preparing image and WCS…")

    width, height = image_dimensions(image_path)
    centre_ra, centre_dec, radius = image_sky_coverage(
        wcs_path, width, height
    )
    image_centre = SkyCoord(
        ra=centre_ra * u.deg,
        dec=centre_dec * u.deg,
        frame="icrs",
    )

    if progress:
        progress(f"Reading bright stars (magnitude {magnitude_limit:g} or brighter)…")

    lines = [
        line for line in catalogue_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    header_index = next(
        (i for i, line in enumerate(lines)
         if {"HR", "RAJ2000", "DEJ2000", "Vmag"}.issubset(
             {column.strip() for column in line.split("\t")}
         )),
        None,
    )
    if header_index is None:
        raise ValueError("Could not find the expected bright-star catalogue columns.")

    rows = csv.DictReader(
        io.StringIO("\n".join(lines[header_index:])),
        delimiter="\t",
    )
    annotations = []

    for row in rows:
        hr = (row.get("HR") or "").strip()
        try:
            if not hr.isdigit():
                continue
            magnitude = float(row["Vmag"])
            if magnitude > magnitude_limit:
                continue
            coord = SkyCoord(
                (row.get("RAJ2000") or "").strip(),
                (row.get("DEJ2000") or "").strip(),
                unit=(u.hourangle, u.deg),
                frame="icrs",
            )
        except (ValueError, TypeError, KeyError):
            continue

        if coord.separation(image_centre).deg > radius + 0.02:
            continue

        try:
            x, y = sky_to_pixel(
                wcs_path,
                float(coord.ra.deg),
                float(coord.dec.deg),
                image_height=height,
            )
        except (ValueError, KeyError, ZeroDivisionError):
            continue

        if not (0 <= x < width and 0 <= y < height):
            continue

        star_name = (row.get("Name") or "").strip()
        star_name = " ".join(star_name.split())
        label = star_name if star_name else f"HR {int(hr)}"

        annotations.append(Annotation(
            name=label,
            x=x,
            y=y,
            colour=colour,
            prefer_marker=True,
        ))

    if collect_only:
        return annotations

    render_annotated_image(
        image_path,
        annotations,
        output_path,
        colour=colour,
    )
    return len(annotations)


def render_primary_subject_title(
    image_path: str | Path,
    subject: dict,
    output_path: str | Path,
) -> None:
    """Draw a discreet primary-subject title on an annotated image."""
    image = QImage(str(image_path))
    if image.isNull():
        raise ValueError(f"Cannot open image: {image_path}")

    image = image.convertToFormat(QImage.Format.Format_ARGB32)
    name = str(subject.get("name") or "").strip()
    if not name:
        if not image.save(str(output_path)):
            raise OSError(f"Cannot save image: {output_path}")
        return

    # Use familiar astronomical names for presentation titles.
    friendly_names = {
        "NGC 292": "Small Magellanic Cloud",
        "NGC 0292": "Small Magellanic Cloud",
        "NGC 224": "Andromeda Galaxy",
        "NGC 0224": "Andromeda Galaxy",
        "NGC 3372": "Carina Nebula",
    }

    catalogue_name = name
    display_name = friendly_names.get(name.upper(), name)

    details = []
    if display_name != catalogue_name:
        details.append(catalogue_name)

    constellation = str(subject.get("constellation") or "").strip()
    if constellation:
        details.append(constellation)

    subtitle = " · ".join(details)
    name = display_name

    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        scale = max(0.6, image.width() / 2000.0)
        margin = 65 * scale
        padding = 17 * scale
        line_gap = 9 * scale

        title_font = QFont("Helvetica")
        title_font.setPixelSize(round(30 * scale))
        title_font.setBold(True)

        subtitle_font = QFont("Helvetica")
        subtitle_font.setPixelSize(round(18 * scale))

        painter.setFont(title_font)
        title_metrics = painter.fontMetrics()
        title_width = title_metrics.horizontalAdvance(name)

        painter.setFont(subtitle_font)
        subtitle_metrics = painter.fontMetrics()
        subtitle_width = subtitle_metrics.horizontalAdvance(subtitle)

        box_width = min(
            image.width() - 2 * margin,
            max(title_width, subtitle_width) + 2 * padding,
        )
        box_height = (
            title_metrics.height()
            + (subtitle_metrics.height() + line_gap if subtitle else 0)
            + 2 * padding
        )

        x = image.width() - margin - box_width
        y = margin

        painter.fillRect(
            QRectF(x, y, box_width, box_height),
            QColor(0, 0, 0, 125),
        )

        text_x = x + padding
        painter.setPen(QColor("#F2F5F7"))
        painter.setFont(title_font)
        painter.drawText(
            QPointF(text_x, y + padding + title_metrics.ascent()),
            name,
        )

        if subtitle:
            painter.setPen(QColor("#C5D5DD"))
            painter.setFont(subtitle_font)
            painter.drawText(
                QPointF(
                    text_x,
                    y + padding + title_metrics.height()
                    + line_gap + subtitle_metrics.ascent(),
                ),
                subtitle,
            )
    finally:
        painter.end()

    if not image.save(str(output_path)):
        raise OSError(f"Cannot save titled image: {output_path}")
