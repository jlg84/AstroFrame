from unittest.mock import patch

import requests

from astroframe.window import MainWindow


def test_empty_target_returns_three_values():
    result = MainWindow._lookup_target_aliases(None, "  ")
    assert result == (None, None, [])


def test_network_failure_returns_three_values():
    with patch(
        "astroframe.window.requests.get",
        side_effect=requests.ConnectionError("SIMBAD unavailable"),
    ):
        result = MainWindow._lookup_target_aliases(None, "M33")

    assert result == (None, None, [])
