from __future__ import annotations

import re

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from . import collection_import as _collection_import
from .observer import ObserverProfile


def install_rc1_onboarding_fixes(MainWindow) -> None:
    """Apply the small onboarding/import fixes discovered during RC1 testing."""

    original_apply_personalised_flow = MainWindow._apply_personalised_flow
    original_first_launch = MainWindow._prompt_for_personalisation_on_first_launch
    original_searchable_combo = MainWindow._searchable_combo

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
            ra_index = mapping["ra"]
            for key in ("ra_h", "ra_m", "ra_s"):
                if mapping.get(key) == ra_index:
                    mapping[key] = None
        if mapping.get("dec") is not None:
            dec_index = mapping["dec"]
            for key in ("dec_sign", "dec_d", "dec_m", "dec_s"):
                if mapping.get(key) == dec_index:
                    mapping[key] = None
        return mapping

    _collection_import.infer_flexible_mapping = infer_mapping_without_coordinate_duplicates

    def apply_personalised_flow(self) -> None:
        original_apply_personalised_flow(self)
        if hasattr(self, "observing_site_section"):
            self.observing_site_section.setVisible(True)

    def quick_first_run_site(self) -> bool:
        """Ask only for information a new user actually needs to understand."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Set up your observing site")
        dialog.setModal(True)
        layout = QVBoxLayout(dialog)

        help_text = QLabel(
            "Tell AstroFrame where you normally observe. It uses the location to "
            "calculate target visibility. Your Mac's time zone will be used automatically. "
            "You can change the site or advanced details later under Observing Site."
        )
        help_text.setWordWrap(True)
        help_text.setObjectName("helpText")
        layout.addWidget(help_text)

        form = QFormLayout()
        site_name = QLineEdit("Home")
        location_name = QLineEdit()
        location_name.setPlaceholderText("e.g. Oamaru, New Zealand")
        form.addRow("Site name", site_name)
        form.addRow("Town / city", location_name)
        layout.addLayout(form)

        found = {"lat": None, "lon": None, "display": ""}
        status = QLabel("")
        status.setWordWrap(True)
        status.setObjectName("helpText")

        def find_location() -> None:
            result = self._geocode_location(location_name.text())
            if result is None:
                QMessageBox.information(
                    dialog,
                    "Location not found",
                    "AstroFrame could not look up that location. Try a town/city and country. "
                    "You can also enter coordinates later under Observing Site.",
                )
                return
            lat, lon, display = result
            found["lat"], found["lon"], found["display"] = lat, lon, display
            location_name.setText(display)
            status.setText(
                f"Found {lat:+.4f}°, {lon:+.4f}°. Time zone: this Mac (automatic)."
            )

        find_button = QPushButton("Find location")
        find_button.clicked.connect(find_location)
        layout.addWidget(find_button)
        layout.addWidget(status)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Save observing site")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        if not location_name.text().strip():
            return False

        # If Save follows a successful lookup, use those coordinates. If the user
        # edited the text afterwards, try one final lookup before falling back.
        if found["lat"] is None or location_name.text().strip() != found["display"]:
            result = self._geocode_location(location_name.text())
            if result is None:
                QMessageBox.warning(
                    self,
                    "Observing site",
                    "Please use Find location first, or configure coordinates later under Observing Site.",
                )
                return False
            found["lat"], found["lon"], found["display"] = result
            location_name.setText(found["display"])

        self.observer_profile = ObserverProfile(
            profile_name=site_name.text().strip() or "Home",
            location_name=location_name.text().strip(),
            latitude_deg=float(found["lat"]),
            longitude_deg=float(found["lon"]),
            elevation_m=0.0,
            timezone_name="",  # blank deliberately means the computer's local zone
            bortle_class=0,     # optional metadata; do not burden first-run setup
            minimum_altitude_deg=30.0,
        )
        self._save_observer_profile(self.observer_profile)
        self._refresh_observer_summary()
        return True

    def first_launch(self) -> None:
        had_completed_setup = bool(self.settings.value("personal/setupComplete", False, bool))
        original_first_launch(self)
        if had_completed_setup:
            return
        completed_setup = bool(self.settings.value("personal/setupComplete", False, bool))
        if not completed_setup:
            return
        location_name = str(self.settings.value("observer/locationName", "") or "").strip()
        if not location_name:
            quick_first_run_site(self)
        if hasattr(self, "observing_site_section"):
            self.observing_site_section.setVisible(True)

    def searchable_combo(self, names: list[str]) -> QComboBox:
        combo = original_searchable_combo(self, names)
        candidates = list(names)

        def commit_best_match() -> None:
            edit = combo.lineEdit()
            if edit is None:
                return
            typed = edit.text().strip()
            if not typed:
                return
            completion = combo.completer().currentCompletion().strip()
            if completion and completion in candidates:
                combo.setCurrentText(completion)
                return
            folded = typed.casefold()
            exact = [name for name in candidates if name.casefold() == folded]
            if len(exact) == 1:
                combo.setCurrentText(exact[0])
                return
            matches = [name for name in candidates if name.casefold().startswith(folded)]
            if len(matches) == 1:
                combo.setCurrentText(matches[0])

        edit = combo.lineEdit()
        if edit is not None:
            edit.returnPressed.connect(commit_best_match)
            edit.editingFinished.connect(commit_best_match)
        completer = combo.completer()
        try:
            completer.activated[str].connect(combo.setCurrentText)
        except (AttributeError, TypeError):
            pass
        return combo

    MainWindow._apply_personalised_flow = apply_personalised_flow
    MainWindow._prompt_for_personalisation_on_first_launch = first_launch
    MainWindow._searchable_combo = searchable_combo
