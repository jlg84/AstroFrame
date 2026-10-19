"""WCS-based celestial coordinate grid for AstroFrame."""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtGui import QPainterPath

from .annotations import pixel_to_sky, sky_to_pixel

def coordinate_grid_paths(
    wcs_path: str | Path,
    width: int,
    height: int,
    *,
    ra_step_deg: float = 0.5,
    dec_step_deg: float = 1 / 6,
) -> list[tuple[str, float, QPainterPath]]:
    """Calculate celestial grid lines projected onto an image."""

    if width < 2 or height < 2:
        raise ValueError("Image dimensions must be at least 2 pixels")

    if ra_step_deg <= 0 or dec_step_deg <= 0:
        raise ValueError("Grid spacing must be positive")

    centre_ra, _ = pixel_to_sky(
        wcs_path,
        (width - 1) / 2,
        (height - 1) / 2,
        image_height=height,
    )

    samples = []

    for i in range(11):
        for j in range(11):
            x = (width - 1) * i / 10
            y = (height - 1) * j / 10

            samples.append(
                pixel_to_sky(
                    wcs_path, x, y, image_height=height
                )
            )

    # Unwrap RA around the image centre.
    ra_values = [
        centre_ra + ((ra - centre_ra + 180) % 360 - 180)
        for ra, dec in samples
    ]

    dec_values = [dec for ra, dec in samples]

    ra_min, ra_max = min(ra_values), max(ra_values)
    dec_min, dec_max = min(dec_values), max(dec_values)

    def make_path(axis: str, value: float) -> QPainterPath:
        path = QPainterPath()
        drawing = False

        if axis == "RA":
            start = dec_min - dec_step_deg
            stop = dec_max + dec_step_deg
        else:
            start = ra_min - ra_step_deg
            stop = ra_max + ra_step_deg

        for i in range(301):
            other = start + (stop - start) * i / 300

            if axis == "RA":
                ra, dec = value % 360, other
            else:
                ra, dec = other % 360, value

            try:
                x, y = sky_to_pixel(
                    wcs_path, ra, dec, image_height=height
                )
            except ValueError:
                drawing = False
                continue

            if not (math.isfinite(x) and math.isfinite(y)):
                drawing = False
                continue

            if -width <= x <= 2 * width and -height <= y <= 2 * height:
                if not drawing:
                    path.moveTo(x, y)
                    drawing = True
                else:
                    path.lineTo(x, y)
            else:
                drawing = False

        return path

    paths = []

    for axis, low, high, step in (
        ("RA", ra_min, ra_max, ra_step_deg),
        ("Dec", dec_min, dec_max, dec_step_deg),
    ):
        first = math.ceil((low - 1e-10) / step)
        last = math.floor((high + 1e-10) / step)

        for index in range(first, last + 1):
            value = index * step
            paths.append(
                (axis, value, make_path(axis, value))
            )

    return paths


def format_grid_coordinate(axis: str, value: float) -> str:
    """Format celestial coordinates for grid-edge labels."""
    if axis == "RA":
        total_minutes = round((value % 360) * 4)
        hours, minutes = divmod(total_minutes, 60)
        return f"{hours % 24:02d}h {minutes:02d}m"

    total_arcminutes = round(abs(value) * 60)
    degrees, minutes = divmod(total_arcminutes, 60)
    sign = "−" if value < 0 else "+"
    return f"{sign}{degrees:02d}° {minutes:02d}′"

def grid_edge_intersections(path, width, height):
    """Find where a grid path crosses the photograph's edges."""
    intersections = []

    def inside(x, y):
        return 0 <= x <= width - 1 and 0 <= y <= height - 1

    for i in range(1, path.elementCount()):
        previous = path.elementAt(i - 1)
        current = path.elementAt(i)

        x1, y1 = previous.x, previous.y
        x2, y2 = current.x, current.y

        if inside(x1, y1) == inside(x2, y2):
            continue
        dx = x2 - x1
        dy = y2 - y1

        edges = (
            ("left", 0, "x"),
            ("right", width - 1, "x"),
            ("top", 0, "y"),
            ("bottom", height - 1, "y"),
        )

        for edge, boundary, axis in edges:
            delta = dx if axis == "x" else dy
            start = x1 if axis == "x" else y1

            if abs(delta) < 1e-12:
                continue

            t = (boundary - start) / delta
            if not 0 <= t <= 1:
                continue

            x = x1 + t * dx
            y = y1 + t * dy

            if 0 <= x <= width - 1 and 0 <= y <= height - 1:
                point = (edge, x, y)
                if not any(
                    e == edge and abs(px - x) < 1 and abs(py - y) < 1
                    for e, px, py in intersections
                ):
                    intersections.append(point)

    return intersections
def render_coordinate_grid(
    image_path: str | Path,
    wcs_path: str | Path,
    output_path: str | Path,
) -> None:
    """Save a copy of an image with a celestial coordinate grid."""
    from PySide6.QtGui import QColor, QImage, QPainter, QPen

    image = QImage(str(image_path))
    if image.isNull():
        raise ValueError(f"Cannot open image: {image_path}")

    image = image.convertToFormat(QImage.Format.Format_ARGB32)
    # Allow enough room for full coordinate labels on every edge.
    border = max(48, round(image.width() * 0.035))

    canvas = QImage(
        image.width() + 2 * border,
        image.height() + 2 * border,
        QImage.Format.Format_ARGB32,
    )
    canvas.fill(QColor("#20252B"))

    canvas_painter = QPainter(canvas)
    try:
        canvas_painter.drawImage(border, border, image)
    finally:
        canvas_painter.end()
    paths = coordinate_grid_paths(
        wcs_path, image.width(), image.height()
    )

    painter = QPainter(canvas)
    painter.translate(border, border)
    painter.setClipRect(0, 0, image.width(), image.height())
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        colour = QColor("#83CBE8")
        colour.setAlpha(175)

        pen = QPen(colour)
        pen.setWidthF(max(1.0, image.width() / 1800))
        painter.setPen(pen)

        for axis, value, path in paths:
            painter.drawPath(path)

        painter.setClipping(False)

        painter.resetTransform()
        painter.setPen(QColor("#DCEAF0"))
        font = painter.font()
        font.setPixelSize(max(14, round(border * 0.40)))
        painter.setFont(font)

        # Track occupied label rectangles to prevent overlapping text.
        occupied_labels = []

        for axis, value, path in paths:
            label = format_grid_coordinate(axis, value)
            metrics = painter.fontMetrics()
            label_width = metrics.horizontalAdvance(label) + 12
            label_height = metrics.height() + 6
            crossings = grid_edge_intersections(
                path, image.width(), image.height()
            )
            for edge, x, y in crossings:
                if edge in ("top", "bottom"):
                    tx = border + x
                    ty = border / 2 if edge == "top" else border + image.height() + border / 2
                    rect = (tx - label_width / 2, ty - label_height / 2,
                            label_width, label_height)
                elif edge in ("left", "right"):
                    tx = border / 2 if edge == "left" else border + image.width() + border / 2
                    ty = border + y
                    rect = (tx - label_height / 2, ty - label_width / 2,
                            label_height, label_width)
                else:
                    continue

                rx, ry, rw, rh = rect
                if rx < 0 or ry < 0 or rx + rw > canvas.width() or ry + rh > canvas.height():
                    continue

                gap = max(6, round(font.pixelSize() * 0.35))
                if any(
                    rx < ox + ow + gap and rx + rw + gap > ox
                    and ry < oy + oh + gap and ry + rh + gap > oy
                    for ox, oy, ow, oh in occupied_labels
                ):
                    continue

                occupied_labels.append(rect)
                if edge in ("top", "bottom"):
                    painter.drawText(
                        int(rx), int(ry), label_width, label_height, 0x84, label
                    )
                else:
                    painter.save()
                    painter.translate(tx, ty)
                    painter.rotate(-90)
                    painter.drawText(
                        -label_width // 2, -label_height // 2,
                        label_width, label_height, 0x84, label
                    )
                    painter.restore()

    finally:
        painter.end()

    if not canvas.save(str(output_path)):
        raise OSError(f"Cannot save grid image: {output_path}")
