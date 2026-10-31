"""Regression tests for user-entered RA/Dec coordinates."""

import pytest

from astroframe.coordinates import parse_target_coordinates


@pytest.mark.parametrize("entry", [
    "05:43:24.680 -67:50:38.70",
    "05 43 24.680 -67 50 38.70",
    "05h 43m 24.680s -67° 50′ 38.70″",
    "RA: 05 43 24.680 Dec: -67 50 38.70",
    "05:43:24.680, -67:50:38.70",
])
def test_equivalent_coordinate_formats(entry):
    coord = parse_target_coordinates(entry)
    assert coord is not None
    assert coord.ra.deg == pytest.approx(85.8528333333, abs=1e-7)
    assert coord.dec.deg == pytest.approx(-67.8440833333, abs=1e-7)


@pytest.mark.parametrize("entry", [
    "M33",
    "NGC 3372",
    "Carina Nebula",
    "",
])
def test_catalogue_names_are_not_coordinates(entry):
    assert parse_target_coordinates(entry) is None


@pytest.mark.parametrize("entry", [
    "25:00:00 -30:00:00",
    "05:60:00 -30:00:00",
    "05:00:60 -30:00:00",
    "05:00:00 -91:00:00",
])
def test_invalid_coordinates_are_rejected(entry):
    with pytest.raises(ValueError):
        parse_target_coordinates(entry)


def test_decimal_hours():
    coord = parse_target_coordinates("5.5 -30.25")
    assert coord is not None
    assert coord.ra.deg == pytest.approx(82.5)
    assert coord.dec.deg == pytest.approx(-30.25)


def test_explicit_decimal_degrees():
    coord = parse_target_coordinates("85.5d -30.25")
    assert coord is not None
    assert coord.ra.deg == pytest.approx(85.5)
    assert coord.dec.deg == pytest.approx(-30.25)
