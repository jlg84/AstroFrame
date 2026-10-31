from types import SimpleNamespace
from unittest.mock import Mock, patch

from astropy.coordinates import SkyCoord

from astroframe.window import MainWindow


def make_window(target=None):
    return SimpleNamespace(
        knowledge_store=SimpleNamespace(
            find_target=Mock(return_value=target)
        ),
        _target_lookup_candidates=Mock(
            side_effect=lambda name: [name]
        ),
    )


def make_target():
    return SimpleNamespace(
        id="ngc_3576",
        canonical_name="NGC 3576",
        common_name="Statue of Liberty Nebula",
        aliases=["Little Tarantula"],
        ra_deg=167.0,
        dec_deg=-61.0,
    )


def test_compact_identifier_resolves_from_local_catalogue():
    target = make_target()
    window = make_window(target)

    with patch(
        "astroframe.window.SkyCoord.from_name"
    ) as online_lookup:
        coordinate, resolved = MainWindow._resolve_target_coordinates(
            window, "ngc3576"
        )

    assert resolved == "NGC 3576"
    assert coordinate.ra.deg == 167.0
    assert coordinate.dec.deg == -61.0
    window.knowledge_store.find_target.assert_called_once_with("ngc3576")
    online_lookup.assert_not_called()


def test_spaced_identifier_resolves_from_local_catalogue():
    target = make_target()
    window = make_window(target)

    with patch(
        "astroframe.window.SkyCoord.from_name"
    ) as online_lookup:
        coordinate, resolved = MainWindow._resolve_target_coordinates(
            window, "NGC 3576"
        )

    assert resolved == "NGC 3576"
    assert coordinate.ra.deg == 167.0
    online_lookup.assert_not_called()


def test_common_name_and_alias_resolve_locally():
    target = make_target()

    for name in ("Statue of Liberty Nebula", "Little Tarantula"):
        window = make_window(target)

        with patch(
            "astroframe.window.SkyCoord.from_name"
        ) as online_lookup:
            coordinate, resolved = MainWindow._resolve_target_coordinates(
                window, name
            )

        assert coordinate.dec.deg == -61.0
        assert resolved == "NGC 3576"
        online_lookup.assert_not_called()


def test_missing_local_coordinates_fall_back_to_online():
    target = make_target()
    target.ra_deg = None
    target.dec_deg = None
    window = make_window(target)

    remote = SkyCoord(ra=167.1, dec=-61.1, unit="deg")

    with patch(
        "astroframe.window.SkyCoord.from_name",
        return_value=remote,
    ) as online_lookup:
        coordinate, resolved = MainWindow._resolve_target_coordinates(
            window, "NGC 3576"
        )

    assert coordinate.separation(remote).arcsec < 0.001
    online_lookup.assert_called()


def test_unmatched_target_falls_back_to_online():
    remote = SkyCoord(ra=100.0, dec=-30.0, unit="deg")
    window = make_window(None)

    with patch(
        "astroframe.window.SkyCoord.from_name",
        return_value=remote,
    ) as online_lookup:
        coordinate, resolved = MainWindow._resolve_target_coordinates(
            window, "Some Other Object"
        )

    assert coordinate.separation(remote).arcsec < 0.001
    assert resolved == "Some Other Object"
    online_lookup.assert_called_once()
