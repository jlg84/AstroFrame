"""Parse user-entered astronomical coordinates."""

import re

from astropy.coordinates import SkyCoord


def parse_target_coordinates(text: str) -> SkyCoord | None:
    """Return ICRS coordinates, or None for non-coordinate input.

    Supports colon-separated, space-separated, and unit-labelled
    sexagesimal RA/Dec, plus decimal coordinate pairs.
    """
    raw = text.strip()
    if not raw:
        return None

    raw = re.sub(r"^\s*RA\s*[:=]\s*", "", raw, flags=re.I)
    raw = re.sub(r"\bDec\s*[:=]\s*", " ", raw, flags=re.I)
    raw = raw.replace(",", " ")

    # Sexagesimal coordinates with explicit h/m/s or degree symbols.
    unit_pattern = (
        r"^\s*(\d{1,2})h\s*(\d{1,2})m\s*"
        r"(\d{1,2}(?:\.\d+)?)s?\s+"
        r"([+-]\d{1,2})[°d]\s*(\d{1,2})['′m]?\s*"
        r"(\d{1,2}(?:\.\d+)?)[\"″s]?\s*$"
    )
    match = re.fullmatch(unit_pattern, raw, flags=re.I)

    if match:
        fields = match.groups()
    else:
        # Colon-separated or whitespace-separated sexagesimal pairs.
        fields = re.fullmatch(
            r"\s*(\d{1,2})[:\s]+(\d{1,2})[:\s]+"
            r"(\d{1,2}(?:\.\d+)?)\s+"
            r"([+-]\d{1,2})[:\s]+(\d{1,2})[:\s]+"
            r"(\d{1,2}(?:\.\d+)?)\s*",
            raw,
        )
        if fields:
            fields = fields.groups()

    if fields:
        h, m, s, d, dm, ds = fields
        h, m, s = int(h), int(m), float(s)
        dm, ds = int(dm), float(ds)
        degrees = abs(int(d))

        if not (0 <= h < 24 and 0 <= m < 60 and 0 <= s < 60):
            raise ValueError("Invalid RA hours, minutes, or seconds.")
        if not (0 <= degrees <= 90 and 0 <= dm < 60 and 0 <= ds < 60):
            raise ValueError("Invalid declination degrees, minutes, or seconds.")
        if degrees == 90 and (dm != 0 or ds != 0):
            raise ValueError("Declination cannot exceed 90 degrees.")

        ra_hours = h + m / 60 + s / 3600
        dec_degrees = degrees + dm / 60 + ds / 3600
        if d.startswith("-"):
            dec_degrees = -dec_degrees

        return SkyCoord(
            ra=ra_hours,
            dec=dec_degrees,
            unit=("hourangle", "deg"),
            frame="icrs",
        )

    # Decimal coordinates: preserve AstroFrame's existing convention
    # that RA below 24 defaults to hours unless degrees are explicit.
    decimal = re.fullmatch(
        r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*[hHdD°]?)"
        r"\s+([+-](?:\d+(?:\.\d*)?|\.\d+)\s*[dD°]?)\s*",
        raw,
    )
    if decimal:
        left, right = (value.strip() for value in decimal.groups())
        ra = float(left.rstrip("hHdD° "))
        dec = float(right.rstrip("dD° "))
        explicit_hours = left.lower().endswith("h")
        explicit_degrees = left.lower().endswith(("d", "°"))

        if not -90 <= dec <= 90:
            raise ValueError("Declination must be between -90 and +90 degrees.")

        if explicit_degrees:
            ra_unit = "deg"
            valid = 0 <= ra < 360
        elif explicit_hours or 0 <= ra < 24:
            ra_unit = "hourangle"
            valid = 0 <= ra < 24
        else:
            ra_unit = "deg"
            valid = 0 <= ra < 360

        if not valid:
            raise ValueError("RA is outside the permitted range.")

        return SkyCoord(ra=ra, dec=dec, unit=(ra_unit, "deg"), frame="icrs")

    return None
