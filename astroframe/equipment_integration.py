from __future__ import annotations

import requests
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from .equipment_browser import EquipmentBrowserDialog
from .equipment_library import CameraEntry, EquipmentLibrary, OpticalEntry

CATALOG_URL = "https://raw.githubusercontent.com/tophrchris/astroguide-metadata/main/v1/packages/equipment/astrophotography_equipment_catalog_v1.json"

_library_cache: EquipmentLibrary | None = None


def _library(parent: QWidget) -> EquipmentLibrary | None:
    global _library_cache
    if _library_cache is not None:
        return _library_cache
    try:
        response = requests.get(CATALOG_URL, timeout=20)
        response.raise_for_status()
        _library_cache = EquipmentLibrary.from_astroguide_dict(response.json())
        return _library_cache
    except Exception as exc:
        QMessageBox.warning(
            parent,
            "Equipment Library",
            "AstroFrame could not load the expanded equipment catalogue.\n\n"
            f"{type(exc).__name__}: {exc}\n\n"
            "Your existing equipment remains unchanged.",
        )
        return None


def install_equipment_library_integration(main_window_class) -> None:
    """Install the 1.1 catalogue-backed setup editor without changing Rig itself."""

    def edit_setup_record(self, parent: QWidget, existing: dict | None = None) -> dict | None:
        existing = existing or {}
        dialog = QDialog(parent)
        dialog.setWindowTitle("Edit Imaging Setup" if existing else "Add Imaging Setup")
        dialog.setModal(True)
        dialog.resize(620, 430)
        layout = QVBoxLayout(dialog)

        intro = QLabel(
            "Choose a camera and telescope or lens from the Equipment Library. "
            "Catalogue values are copied into this setup, so you can still override "
            "the effective focal length for reducers, spacing or measured focal length."
        )
        intro.setObjectName("helpText"); intro.setWordWrap(True); layout.addWidget(intro)

        form = QFormLayout()
        setup_name = QLineEdit(str(existing.get("name", "")))
        setup_name.setPlaceholderText("e.g. EdgeHD 8 + ASI533")

        camera_name = QLineEdit(str(existing.get("camera_name", "")))
        camera_name.setReadOnly(True); camera_name.setPlaceholderText("No camera selected")
        camera_row = QWidget(); camera_row_layout = QHBoxLayout(camera_row); camera_row_layout.setContentsMargins(0,0,0,0)
        camera_row_layout.addWidget(camera_name, 1)
        choose_camera = QPushButton("Choose…"); camera_row_layout.addWidget(choose_camera)

        optic_name = QLineEdit(str(existing.get("telescope_name", "")))
        optic_name.setReadOnly(True); optic_name.setPlaceholderText("No telescope / lens selected")
        optic_row = QWidget(); optic_row_layout = QHBoxLayout(optic_row); optic_row_layout.setContentsMargins(0,0,0,0)
        optic_row_layout.addWidget(optic_name, 1)
        choose_optic = QPushButton("Choose…"); optic_row_layout.addWidget(choose_optic)

        sensor_width = QDoubleSpinBox(); sensor_width.setRange(0.1, 100.0); sensor_width.setDecimals(3); sensor_width.setSuffix(" mm")
        sensor_height = QDoubleSpinBox(); sensor_height.setRange(0.1, 100.0); sensor_height.setDecimals(3); sensor_height.setSuffix(" mm")
        native_focal = QDoubleSpinBox(); native_focal.setRange(1.0, 10000.0); native_focal.setDecimals(2); native_focal.setSuffix(" mm")
        effective_focal = QDoubleSpinBox(); effective_focal.setRange(1.0, 10000.0); effective_focal.setDecimals(2); effective_focal.setSuffix(" mm")
        sensor_width.setValue(float(existing.get("sensor_width_mm", 17.0)))
        sensor_height.setValue(float(existing.get("sensor_height_mm", 13.0)))
        native_value = float(existing.get("native_focal_length_mm", existing.get("focal_length_mm", 400.0)))
        native_focal.setValue(native_value)
        effective_focal.setValue(float(existing.get("focal_length_mm", native_value)))

        form.addRow("Setup name", setup_name)
        form.addRow("Camera", camera_row)
        form.addRow("Sensor width", sensor_width)
        form.addRow("Sensor height", sensor_height)
        form.addRow("Telescope / lens", optic_row)
        form.addRow("Native focal length", native_focal)
        form.addRow("Effective focal length", effective_focal)
        layout.addLayout(form)

        note = QLabel(
            "Sensor dimensions and native focal length come from the selected catalogue entries. "
            "They remain editable. Effective focal length is what AstroFrame uses for framing."
        )
        note.setObjectName("helpText"); note.setWordWrap(True); layout.addWidget(note)

        state = {
            "camera_key": str(existing.get("camera_key", "custom")),
            "telescope_key": str(existing.get("telescope_key", "custom")),
            "source_camera": existing.get("camera_source_attribution"),
            "source_optic": existing.get("telescope_source_attribution"),
        }

        def pick_camera() -> None:
            lib = _library(dialog)
            if lib is None: return
            browser = EquipmentBrowserDialog(lib, dialog, selection_kind="camera")
            if browser.exec() != QDialog.DialogCode.Accepted or not isinstance(browser.selected_entry, CameraEntry): return
            entry = browser.selected_entry
            state["camera_key"] = entry.key; state["source_camera"] = entry.source_attribution
            camera_name.setText(entry.display_name)
            sensor_width.setValue(entry.sensor_width_mm); sensor_height.setValue(entry.sensor_height_mm)
            if not setup_name.text().strip() and optic_name.text().strip():
                setup_name.setText(f"{optic_name.text().strip()} + {entry.display_name}")

        def pick_optic() -> None:
            lib = _library(dialog)
            if lib is None: return
            browser = EquipmentBrowserDialog(lib, dialog, selection_kind="optic")
            if browser.exec() != QDialog.DialogCode.Accepted or not isinstance(browser.selected_entry, OpticalEntry): return
            entry = browser.selected_entry
            state["telescope_key"] = entry.key; state["source_optic"] = entry.source_attribution
            optic_name.setText(entry.display_name)
            native_focal.setValue(entry.focal_length_mm); effective_focal.setValue(entry.focal_length_mm)
            if not setup_name.text().strip() and camera_name.text().strip():
                setup_name.setText(f"{entry.display_name} + {camera_name.text().strip()}")

        choose_camera.clicked.connect(pick_camera); choose_optic.clicked.connect(pick_optic)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.button(QDialogButtonBox.StandardButton.Save).setDefault(True)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted: return None

        if not camera_name.text().strip() or not optic_name.text().strip():
            QMessageBox.warning(dialog, "Incomplete setup", "Choose both a camera and a telescope or lens.")
            return None

        native = native_focal.value(); effective = effective_focal.value()
        return {
            "name": setup_name.text().strip() or f"{optic_name.text().strip()} + {camera_name.text().strip()}",
            "camera_key": state["camera_key"], "camera_name": camera_name.text().strip(),
            "telescope_key": state["telescope_key"], "telescope_name": optic_name.text().strip(),
            "native_focal_length_mm": native,
            "optical_factor": effective / native if native else 1.0,
            "focal_length_mm": effective,
            "sensor_width_mm": sensor_width.value(), "sensor_height_mm": sensor_height.value(),
            "camera_source_attribution": state["source_camera"],
            "telescope_source_attribution": state["source_optic"],
        }

    main_window_class._edit_setup_record = edit_setup_record
