from __future__ import annotations

from PySide6.QtWidgets import QComboBox


def install_rc1_onboarding_fixes(MainWindow) -> None:
    """Apply small, release-candidate-only onboarding fixes.

    Keeps the mature MainWindow implementation intact while fixing two
    first-run problems discovered during the clean-install DMG test:
    observing-site setup being hidden by personalisation choices, and editable
    equipment combos looking selected while retaining only the typed prefix.
    """

    original_apply_personalised_flow = MainWindow._apply_personalised_flow
    original_first_launch = MainWindow._prompt_for_personalisation_on_first_launch
    original_searchable_combo = MainWindow._searchable_combo

    def apply_personalised_flow(self) -> None:
        original_apply_personalised_flow(self)
        # Location is core application state, not an optional personalised
        # feature. It must always remain discoverable and editable.
        if hasattr(self, "observing_site_section"):
            self.observing_site_section.setVisible(True)

    def first_launch(self) -> None:
        had_completed_setup = bool(
            self.settings.value("personal/setupComplete", False, bool)
        )
        original_first_launch(self)

        if had_completed_setup:
            return

        # Only continue to location setup when the Welcome dialog was actually
        # completed. Cancelling Welcome should not force a second modal dialog.
        completed_setup = bool(
            self.settings.value("personal/setupComplete", False, bool)
        )
        if not completed_setup:
            return

        # A brand-new user should be asked where they observe immediately.
        # Existing/migrated users with a stored location are left alone.
        location_name = str(
            self.settings.value("observer/locationName", "") or ""
        ).strip()
        if not location_name:
            self.edit_observer_profile()

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

            # If the completer has a highlighted completion, Enter should
            # commit exactly what the user can already see in the popup.
            completion = combo.completer().currentCompletion().strip()
            if completion and completion in candidates:
                combo.setCurrentText(completion)
                return

            folded = typed.casefold()
            exact = [name for name in candidates if name.casefold() == folded]
            if len(exact) == 1:
                combo.setCurrentText(exact[0])
                return

            # Clicking Save after typing a unique prefix should also commit the
            # only valid choice rather than failing validation on the prefix.
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
