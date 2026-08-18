from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .equipment_library import CameraEntry, EquipmentLibrary, OpticalEntry


class EquipmentBrowserDialog(QDialog):
    """Development browser for the 1.1 equipment catalogue.

    This deliberately does not modify or save rigs yet.  It lets us test the
    browsing/search experience against the full catalogue before wiring it into
    AstroFrame's existing rig editor.
    """

    def __init__(self, library: EquipmentLibrary, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.library = library
        self.setWindowTitle("AstroFrame Equipment Library")
        self.resize(820, 560)

        outer = QVBoxLayout(self)

        filters = QHBoxLayout()
        self.kind_combo = QComboBox()
        self.kind_combo.addItems(["Optics", "Cameras"])
        self.manufacturer_combo = QComboBox()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search manufacturer or model…")
        filters.addWidget(QLabel("Type:"))
        filters.addWidget(self.kind_combo)
        filters.addWidget(QLabel("Manufacturer:"))
        filters.addWidget(self.manufacturer_combo, 1)
        filters.addWidget(self.search_edit, 2)
        outer.addLayout(filters)

        body = QHBoxLayout()
        self.results = QListWidget()
        self.results.setMinimumWidth(360)
        body.addWidget(self.results, 2)

        detail_panel = QWidget()
        detail_layout = QFormLayout(detail_panel)
        self.name_value = QLabel("—")
        self.name_value.setWordWrap(True)
        self.type_value = QLabel("—")
        self.spec1_label = QLabel("—")
        self.spec1_value = QLabel("—")
        self.spec2_label = QLabel("—")
        self.spec2_value = QLabel("—")
        self.spec3_label = QLabel("—")
        self.spec3_value = QLabel("—")
        self.source_value = QLabel("—")
        self.source_value.setWordWrap(True)
        detail_layout.addRow("Equipment:", self.name_value)
        detail_layout.addRow("Type:", self.type_value)
        detail_layout.addRow(self.spec1_label, self.spec1_value)
        detail_layout.addRow(self.spec2_label, self.spec2_value)
        detail_layout.addRow(self.spec3_label, self.spec3_value)
        detail_layout.addRow("Source:", self.source_value)
        body.addWidget(detail_panel, 1)
        outer.addLayout(body, 1)

        footer = QHBoxLayout()
        self.count_label = QLabel()
        footer.addWidget(self.count_label)
        footer.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        footer.addWidget(close_button)
        outer.addLayout(footer)

        self.kind_combo.currentIndexChanged.connect(self._reload_manufacturers)
        self.manufacturer_combo.currentIndexChanged.connect(self._refresh_results)
        self.search_edit.textChanged.connect(self._refresh_results)
        self.results.currentItemChanged.connect(self._show_current)

        self._reload_manufacturers()

    def _is_camera_mode(self) -> bool:
        return self.kind_combo.currentText() == "Cameras"

    def _reload_manufacturers(self) -> None:
        current = self.manufacturer_combo.currentText()
        manufacturers = (
            self.library.camera_manufacturers()
            if self._is_camera_mode()
            else self.library.optical_manufacturers()
        )
        self.manufacturer_combo.blockSignals(True)
        self.manufacturer_combo.clear()
        self.manufacturer_combo.addItem("All manufacturers")
        self.manufacturer_combo.addItems(manufacturers)
        if current:
            index = self.manufacturer_combo.findText(current)
            if index >= 0:
                self.manufacturer_combo.setCurrentIndex(index)
        self.manufacturer_combo.blockSignals(False)
        self._refresh_results()

    def _entries(self) -> tuple[CameraEntry | OpticalEntry, ...]:
        query = self.search_edit.text().strip()
        manufacturer = self.manufacturer_combo.currentText()
        if self._is_camera_mode():
            entries = self.library.search_cameras(query)
        else:
            entries = self.library.search_optics(query)
        if manufacturer and manufacturer != "All manufacturers":
            key = manufacturer.casefold()
            entries = tuple(item for item in entries if item.manufacturer.casefold() == key)
        return entries

    def _refresh_results(self) -> None:
        entries = self._entries()
        self.results.clear()
        for entry in entries:
            item = QListWidgetItem(entry.display_name)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.results.addItem(item)
        self.count_label.setText(f"{len(entries):,} matches")
        if self.results.count():
            self.results.setCurrentRow(0)
        else:
            self._clear_details()

    def _clear_details(self) -> None:
        for label in (self.name_value, self.type_value, self.spec1_value, self.spec2_value, self.spec3_value, self.source_value):
            label.setText("—")
        self.spec1_label.setText("—")
        self.spec2_label.setText("—")
        self.spec3_label.setText("—")

    def _show_current(self, item: QListWidgetItem | None) -> None:
        if item is None:
            self._clear_details()
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        if entry is None:
            self._clear_details()
            return
        self.name_value.setText(entry.display_name)
        self.source_value.setText(entry.source_attribution or "—")
        if isinstance(entry, CameraEntry):
            self.type_value.setText("Camera")
            self.spec1_label.setText("Sensor:")
            self.spec1_value.setText(f"{entry.sensor_width_mm:g} × {entry.sensor_height_mm:g} mm")
            self.spec2_label.setText("Pixel size:")
            if entry.pixel_size_width_um and entry.pixel_size_height_um:
                self.spec2_value.setText(f"{entry.pixel_size_width_um:g} × {entry.pixel_size_height_um:g} µm")
            else:
                self.spec2_value.setText("—")
            self.spec3_label.setText("Resolution:")
            if entry.horizontal_resolution_px and entry.vertical_resolution_px:
                self.spec3_value.setText(f"{entry.horizontal_resolution_px} × {entry.vertical_resolution_px} px")
            else:
                self.spec3_value.setText("—")
            return
        self.type_value.setText("Camera lens" if entry.component_type in {"lens", "lens_candidate"} else "Telescope")
        self.spec1_label.setText("Focal length:")
        self.spec1_value.setText(f"{entry.focal_length_mm:g} mm")
        self.spec2_label.setText("Aperture:")
        self.spec2_value.setText(f"{entry.aperture_mm:g} mm" if entry.aperture_mm else "—")
        self.spec3_label.setText("Focal ratio:")
        self.spec3_value.setText(f"f/{entry.focal_ratio:g}" if entry.focal_ratio else "—")
