from pathlib import Path

from astroframe.plate_solve import AstrometrySubmissionCache, PlateSolution, SolveCache


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


def test_submission_cache_reservation_is_one_shot(tmp_path: Path) -> None:
    image = tmp_path / "image.jpg"
    image.write_bytes(b"same exact image")
    cache = AstrometrySubmissionCache(tmp_path / "submissions")
    assert cache.reserve_upload(image) is True
    assert cache.reserve_upload(image) is False
    assert cache.load(image)["status"] == "reserved"


def test_submission_cache_uses_content_not_filename(tmp_path: Path) -> None:
    first = tmp_path / "first.jpg"
    second = tmp_path / "renamed.jpg"
    first.write_bytes(b"identical")
    second.write_bytes(b"identical")
    cache = AstrometrySubmissionCache(tmp_path / "submissions")
    assert cache.reserve_upload(first) is True
    assert cache.reserve_upload(second) is False
