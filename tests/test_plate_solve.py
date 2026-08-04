from pathlib import Path

from astroframe.plate_solve import PlateSolution, SolveCache


def test_cache_round_trip(tmp_path: Path) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"fake-image")
    cache = SolveCache(tmp_path / "cache")
    solution = PlateSolution(
        ra_deg=181.9,
        dec_deg=-51.8,
        pixel_scale_arcsec=1.5,
        orientation_deg=42.0,
        parity=1.0,
        radius_deg=1.0,
        image_width_deg=2.5,
        image_height_deg=1.5,
        job_id=123,
    )
    cache.save(image, solution)
    assert cache.load(image) == solution
    cache.remove(image)
    assert cache.load(image) is None
