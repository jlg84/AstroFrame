from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QMessageBox

from astroframe.window import MainWindow


def make_window(filename_hint="M 33", decision=None):
    centre = SimpleNamespace(
        ra=SimpleNamespace(hour=1.565),
        dec=SimpleNamespace(deg=30.66),
    )

    window = SimpleNamespace(
        _filename_hint_decision=decision,
        solving_hint_ra_hours=None,
        solving_hint_dec_deg=None,
        solving_hint_name=None,
        solving_hint_search_radius_deg=None,
        current_image_path="/images/M33_stack.tif",
        _filename_catalogue_hint=Mock(return_value=filename_hint),
        _resolve_target_entry=Mock(return_value=(centre, [])),
        _append_solver_log=Mock(),
        solving_hint_label=Mock(),
        assisted_solve_button=Mock(),
    )
    return window


def test_accepting_filename_hint_sets_solver_coordinates():
    window = make_window()

    with patch(
        "astroframe.window.QMessageBox.question",
        return_value=QMessageBox.StandardButton.Yes,
    ) as question:
        MainWindow._offer_filename_hint_before_solve(window)

    question.assert_called_once()
    assert window._filename_hint_decision == "accepted"
    assert window.solving_hint_name == "M 33"
    assert window.solving_hint_ra_hours == 1.565
    assert window.solving_hint_dec_deg == 30.66
    assert window.solving_hint_search_radius_deg == 30.0


def test_declining_filename_hint_preserves_blind_solve():
    window = make_window()

    with patch(
        "astroframe.window.QMessageBox.question",
        return_value=QMessageBox.StandardButton.No,
    ):
        MainWindow._offer_filename_hint_before_solve(window)

    assert window._filename_hint_decision == "declined"
    assert window.solving_hint_ra_hours is None
    assert window.solving_hint_dec_deg is None
    window._resolve_target_entry.assert_not_called()


def test_unrecognised_filename_does_not_prompt():
    window = make_window(filename_hint=None)

    with patch(
        "astroframe.window.QMessageBox.question"
    ) as question:
        MainWindow._offer_filename_hint_before_solve(window)

    question.assert_not_called()
    assert window._filename_hint_decision is None


def test_existing_decision_prevents_repeated_prompt():
    window = make_window(decision="declined")

    with patch(
        "astroframe.window.QMessageBox.question"
    ) as question:
        MainWindow._offer_filename_hint_before_solve(window)

    question.assert_not_called()


def test_failed_target_resolution_does_not_set_coordinates():
    window = make_window()
    window._resolve_target_entry.side_effect = ValueError("SIMBAD unavailable")

    with patch(
        "astroframe.window.QMessageBox.question",
        return_value=QMessageBox.StandardButton.Yes,
    ):
        MainWindow._offer_filename_hint_before_solve(window)

    assert window._filename_hint_decision == "accepted"
    assert window.solving_hint_ra_hours is None
    assert window.solving_hint_dec_deg is None
