from __future__ import annotations

import re

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
)

from . import collection_import as _collection_import
from .observer import ObserverProfile


class _MessageBoxMnemonicFilter(QObject):
    """Make visible Yes/No mnemonics work as plain keys in modal questions."""

    def eventFilter(self, watched, event) -> bool:
        if event.type() != QEvent.Type.KeyPress:
            return False
        box = QApplication.activeModalWidget()
        if not isinstance(box, QMessageBox):
            return False
        key = event.key()
        standard = None
        if key == Qt.Key.Key_Y:
            standard = QMessageBox.StandardButton.Yes
        elif key == Qt.Key.Key_N:
            standard = QMessageBox.StandardButton.No
        if standard is None:
            return False
        button = box.button(standard)
        if button is None or not button.isEnabled():
            return False
        button.click()
        return True


class _SidebarWheelRedirect(QObject):
    """Route trackpad/wheel scrolling over mosaic controls to the sidebar."""

    def __init__(self, scroll_area: QScrollArea, parent=None) -> None:
        super().__init__(parent)
        self.scroll_area = scroll_area

    def eventFilter(self, watched, event) -> bool:
        if event.type() != QEvent.Type.Wheel:
            return False

        bar = self.scroll_area.verticalScrollBar()
        pixel_delta = event.pixelDelta().y()
        if pixel_delta:
            delta = float(pixel_delta)
        else:
            angle_delta = event.angleDelta().y()
            if not angle_delta:
                return False
            delta = (float(angle_delta) / 120.0) * max(1, bar.singleStep()) * 3.0

        bar.setValue(round(bar.value() - delta))
        event.accept()
        return True


def install_rc1_onboarding_fixes(MainWindow) -> None:
    """Apply the small onboarding/import fixes discovered during RC1 testing."""

    original_init = MainWindow.__init__
    original_apply_personalised_flow = MainWindow._apply_personalised_flow
    original_first_launch = MainWindow._prompt_for_personalisation_on_first_launch
    original_searchable_combo = MainWindow._searchable_combo
    original_apply_solution = MainWindow._apply_solution

    # The mosaic grid combo and overlap spin box sit inside the main sidebar.
    # On a Mac trackpad they otherwise consume two-finger wheel events and can
    # change their own value while the user is simply trying to move down the
    # sidebar.  Treat wheel/trackpad motion over those controls as sidebar
    # scrolling; deliberate clicks/keyboard edits still operate the controls.
    def init_with_sidebar_wheel_guard(self) -> None:
        original_init(self)
        sidebar_scroll = self.findChild(QScrollArea, "sidebarScroll")
        controls = [
            getattr(self, "mosaic_grid_combo", None),
            getattr(self, "mosaic_overlap_spin", None),
        ]
        if sidebar_scroll is not None:
            wheel_redirect = _SidebarWheelRedirect(sidebar_scroll, self)
            for control in controls:
                if control is not None:
                    control.installEventFilter(wheel_redirect)
            self._astroframe_sidebar_wheel_redirect = wheel_redirect

    MainWindow.__init__ = init_with_sidebar_wheel_guard

    # A confirmed solver clue is resolved to a precise sky coordinate before ASTAP
    # runs. The main window previously cached only the clue's display name, throwing
    # that precision away. Catalogue markers could then fall back to a rounded
    # imported coordinate (M104 exposed this clearly). Preserve the independently
    # resolved clue coordinate in the image metadata; _target_pixel_position already
    # knows to prefer it for the matching catalogue identity. This changes no WCS,
    # framing, mosaic, or export geometry.
    def apply_solution_preserving_hint_coordinate(self, solution, *, cached: bool) -> None:
        original_apply_solution(self, solution, cached=cached)
        if (
            not self.current_image_path
            or not self.solving_hint_name
            or self.solving_hint_ra_hours is None
            or self.solving_hint_dec_deg is None
        ):
            return
        target_info = self._cached_target_for_current_image() or {}
        target_info.update(
            name=str(self.solving_hint_name),
            identification_source="user_hint",
            identification_version=self.SUBJECT_IDENTIFICATION_VERSION,
            subject_ra_deg=float(self.solving_hint_ra_hours) * 15.0,
            subject_dec_deg=float(self.solving_hint_dec_deg),
        )
        self.solve_cache.update_metadata(self.current_image_path, target=target_info)

    MainWindow._apply_solution = apply_solution_preserving_hint_coordinate

    # The underlined Y/N labels on QMessageBox buttons promise keyboard
    # accelerators. On macOS Qt does not reliably activate those mnemonics, so
    # honour the promise explicitly while a modal Yes/No question has focus.
    app = QApplication.instance()
    if app is not None and not hasattr(app, "_astroframe_messagebox_mnemonic_filter"):
        mnemonic_filter = _MessageBoxMnemonicFilter(app)
        app.installEventFilter(mnemonic_filter)
        app._astroframe_messagebox_mnemonic_filter = mnemonic_filter

    # Real-world astronomy lists commonly write right ascension as, for example,
    # ``05h 38.7 m`` or ``05h 38m 42s``.  The mature importer already supports
    # colon-separated and compact sexagesimal forms; add this conventional form
    # without changing the behaviour of any formats it already understands.
    original_ra_to_deg = _collection_import._ra_to_deg

    def ra_to_deg_with_units(value):
        if isinstance(value, str):
            text = value.strip().replace("−", "-").replace("–", "-")
            match = re.fullmatch(
                r"(\d{1,2})\s*[hH]\s*"
                r"(\d+(?:\.\d+)?)\s*[mM]"
                r"(?:\s*(\d+(?:\.\d+)?)\s*[sS])?\s*",
                text,
            )
            if match:
                hours = float(match.group(1))
                minutes = float(match.group(2))
                seconds = float(match.group(3) or 0.0)
                if 0.0 <= hours <= 24.0 and 0.0 <= minutes < 60.0 and 0.0 <= seconds < 60.0:
                    total_hours = hours + minutes / 60.0 + seconds / 3600.0
                    if total_hours <= 24.0:
                        return total_hours * 15.0
        return original_ra_to_deg(value)

    _collection_import._ra_to_deg = ra_to_deg_with_units

    # Do not map one physical column to both a complete coordinate and one of
    # its component fields.  A header named simply "DEC" is a complete single-
    # column declination, not also a "Dec sign" column; likewise for RA.
    original_infer_mapping = _collection_import.infer_flexible_mapping

    def infer_mapping_without_coordinate_duplicates(headers):
        mapping = original_infer_mapping(headers)
        if mapping.get("ra") is not None:
            for key in ("ra_hours", "ra_minutes", "ra_seconds"):
                if mapping.get(key) == mapping.get("ra"):
                    mapping[key] = None
        if mapping.get("dec") is not None:
            for key in ("dec_sign", "dec_degrees", "dec_minutes", "dec_seconds"):
                if mapping.get(key) == mapping.get("dec"):
                    mapping[key] = None
        return mapping

    _collection_import.infer_flexible_mapping = infer_mapping_without_coordinate_duplicates

    def searchable_combo_preserve_typing(self, names: list[str]) -> QComboBox:
        combo = original_searchable_combo(self, names)
        combo.setCurrentIndex(-1)
        combo.setEditText("")
        return combo

    MainWindow._searchable_combo = searchable_combo_preserve_typing

    def apply_personalised_flow_rc1(self) -> None:
        original_apply_personalised_flow(self)
        # The observing site is part of the visible imaging workflow in RC1.
        # Do not hide it merely because a first-run preference was unchecked.
        if hasattr(self, "observing_site_section"):
            self.observing_site_section.show()

    MainWindow._apply_personalised_flow = apply_personalised_flow_rc1

    def first_launch_with_site(self) -> None:
        original_first_launch(self)
        if hasattr(self, "observing_site_section"):
            self.observing_site_section.show()

    MainWindow._prompt_for_personalisation_on_first_launch = first_launch_with_site
