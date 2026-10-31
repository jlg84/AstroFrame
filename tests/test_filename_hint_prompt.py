from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QMessageBox

from astroframe.window import MainWindow


def make_window(filename_hint="M 33", decision=None, target_record=None):
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
        knowledge_store=SimpleNamespace(
            find_target=Mock(return_value=target_record)
        ),
        _append_solver_log=Mock(),
        solving_hint_label=Mock(),
        assisted_solve_button=Mock(),
    )
    return window


def make_target(
    canonical_name="NGC 3372",
    common_name="Carina Nebula",
    aliases=None,
    ra_deg=161.265,
    dec_deg=-59.684,
):
    return SimpleNamespace(
        id="ngc_3372",
        canonical_name=canonical_name,
        common_name=common_name,
        aliases=aliases or [],
        ra_deg=ra_deg,
        dec_deg=dec_deg,
    )


def show_prompt(window, answer=QMessageBox.StandardButton.Yes):
    with patch(
        "astroframe.window.QMessageBox.question",
        return_value=answer,
    ) as question:
        MainWindow._offer_filename_hint_before_solve(window)
    return question


def test_imported_catalogue_match_shows_catalogue_and_common_name():
    target = make_target()
    window = make_window("Carina", target_record=target)

    question = show_prompt(window)

    question.assert_called_once()
    message = question.call_args.args[2]

    assert "Carina" in message
    assert "NGC 3372" in message
    assert "Carina Nebula" in message
    assert "Imported target catalogue" in message
    assert "Local catalogue coordinates: Available" in message


def test_alias_match_retrieves_imported_target():
    target = make_target(aliases=["Keyhole Nebula"])
    window = make_window("Keyhole Nebula", target_record=target)

    question = show_prompt(window)

    window.knowledge_store.find_target.assert_called_once_with(
        "Keyhole Nebula"
    )
    message = question.call_args.args[2]

    assert "Keyhole Nebula" in message
    assert "NGC 3372" in message
    assert "Carina Nebula" in message


def test_missing_catalogue_coordinates_are_disclosed():
    target = make_target(ra_deg=None, dec_deg=None)
    window = make_window("Carina", target_record=target)

    question = show_prompt(window)

    message = question.call_args.args[2]

    assert "NGC 3372" in message
    assert "Local catalogue coordinates: Not stored" in message
    assert "resolve the target before solving" in message


def test_builtin_name_without_catalogue_record_shows_its_source():
    window = make_window(
        "Sombrero",
        target_record=None,
    )

    question = show_prompt(window)

    message = question.call_args.args[2]

    assert "Sombrero" in message
    assert "AstroFrame filename recognition" in message
    assert "Imported target catalogue" not in message


def test_accepting_filename_hint_sets_solver_coordinates():
    window = make_window()

    show_prompt(window)

    assert window._filename_hint_decision == "accepted"
    assert window.solving_hint_name == "M 33"
    assert window.solving_hint_ra_hours == 1.565
    assert window.solving_hint_dec_deg == 30.66
    assert window.solving_hint_search_radius_deg == 30.0


def test_declining_filename_hint_preserves_blind_solve():
    window = make_window()

    show_prompt(window, QMessageBox.StandardButton.No)

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
    window._resolve_target_entry.side_effect = ValueError(
        "SIMBAD unavailable"
    )

    show_prompt(window)

    assert window._filename_hint_decision == "accepted"
    assert window.solving_hint_ra_hours is None
    assert window.solving_hint_dec_deg is None
