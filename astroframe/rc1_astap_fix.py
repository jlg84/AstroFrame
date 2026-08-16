from __future__ import annotations

import math

from astropy.io import fits
from astropy.wcs import WCS

from . import plate_solve as _plate_solve


RC1_WCS_CACHE_MODEL_VERSION = 4


def install_rc1_astap_reference_pixel_fix() -> None:
    """Treat ASTAP CRVAL as the world coordinate at CRPIX, not image centre.

    ASTAP's .wcs sidecar may place CRPIX away from the geometric centre of the
    image. The previous direct-keyword parser cached CRVAL1/2 as if they were the
    image-centre coordinates, which can shift every catalogue overlay by hundreds
    of pixels even though the underlying ASTAP solution is valid.

    This RC1 patch reconstructs a minimal celestial WCS from the safe keyword
    reader and explicitly evaluates the world coordinate at the geometric image
    centre. It also invalidates pre-fix cached WCS records so affected images are
    solved once again instead of silently reusing a displaced centre.
    """

    _plate_solve.WCS_CACHE_MODEL_VERSION = RC1_WCS_CACHE_MODEL_VERSION

    original_load_record = _plate_solve.SolveCache.load_record

    def load_record_rejecting_pre_fix_cache(self, image_path, *, expected_size=None):
        cache_path = self._path_for(image_path)
        if cache_path.exists():
            try:
                import json

                raw = json.loads(cache_path.read_text(encoding="utf-8"))
                if raw.get("wcs_cache_model_version") != RC1_WCS_CACHE_MODEL_VERSION:
                    return None
            except Exception:
                return None
        return original_load_record(self, image_path, expected_size=expected_size)

    _plate_solve.SolveCache.load_record = load_record_rejecting_pre_fix_cache

    @classmethod
    def solution_from_astap_keywords_at_image_centre(
        cls,
        path,
        *,
        image_width_px: int,
        image_height_px: int,
    ):
        values = cls._read_astap_keywords(path)

        crval1 = cls._float_keyword(values, "CRVAL1") % 360.0
        crval2 = cls._float_keyword(values, "CRVAL2")
        crpix1 = cls._float_keyword(values, "CRPIX1")
        crpix2 = cls._float_keyword(values, "CRPIX2")

        try:
            cd11 = cls._float_keyword(values, "CD1_1")
            cd12 = cls._float_keyword(values, "CD1_2")
            cd21 = cls._float_keyword(values, "CD2_1")
            cd22 = cls._float_keyword(values, "CD2_2")
        except KeyError:
            cdelt1 = cls._float_keyword(values, "CDELT1")
            cdelt2 = cls._float_keyword(values, "CDELT2")
            pc11 = float(values.get("PC1_1", "1"))
            pc12 = float(values.get("PC1_2", "0"))
            pc21 = float(values.get("PC2_1", "0"))
            pc22 = float(values.get("PC2_2", "1"))
            cd11, cd12 = cdelt1 * pc11, cdelt1 * pc12
            cd21, cd22 = cdelt2 * pc21, cdelt2 * pc22

        header = fits.Header()
        header["WCSAXES"] = 2
        header["CTYPE1"] = values.get("CTYPE1", "RA---TAN")
        header["CTYPE2"] = values.get("CTYPE2", "DEC--TAN")
        header["CUNIT1"] = values.get("CUNIT1", "deg")
        header["CUNIT2"] = values.get("CUNIT2", "deg")
        header["CRPIX1"] = crpix1
        header["CRPIX2"] = crpix2
        header["CRVAL1"] = crval1
        header["CRVAL2"] = crval2
        header["CD1_1"] = cd11
        header["CD1_2"] = cd12
        header["CD2_1"] = cd21
        header["CD2_2"] = cd22
        for key in ("RADESYS", "EQUINOX", "LONPOLE", "LATPOLE"):
            if key in values:
                raw = values[key]
                try:
                    header[key] = float(raw)
                except ValueError:
                    header[key] = raw

        wcs = WCS(header).celestial
        centre_x = (image_width_px - 1) / 2.0
        centre_y = (image_height_px - 1) / 2.0
        ra_deg, dec_deg = wcs.pixel_to_world_values(centre_x, centre_y)

        scale_x_arcsec = math.hypot(cd11, cd21) * 3600.0
        scale_y_arcsec = math.hypot(cd12, cd22) * 3600.0
        pixel_scale = (scale_x_arcsec + scale_y_arcsec) / 2.0
        orientation = math.degrees(math.atan2(cd12, cd11))
        determinant = cd11 * cd22 - cd12 * cd21
        parity = 1.0 if determinant >= 0 else -1.0
        width_deg = scale_x_arcsec * image_width_px / 3600.0
        height_deg = scale_y_arcsec * image_height_px / 3600.0
        radius_deg = math.hypot(width_deg, height_deg) / 2.0

        return (
            float(ra_deg) % 360.0,
            float(dec_deg),
            pixel_scale,
            orientation,
            parity,
            width_deg,
            height_deg,
            radius_deg,
        )

    _plate_solve.AstapClient._solution_from_astap_keywords = (
        solution_from_astap_keywords_at_image_centre
    )
