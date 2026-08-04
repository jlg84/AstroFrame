from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from .equipment import RIGS
from .viewer import ImageViewer

DEFAULT_REFERENCE_WIDTH = 3.0


class Section(QFrame):
    """Compact inspector section used throughout the sidebar."""

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("section")
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(14, 12, 14, 14)
        self.layout.setSpacing(9)

        heading = QLabel(title.upper())
        heading.setObjectName("sectionHeading")
        self.layout.addWidget(heading)


class RigToggle(QCheckBox):
    def __init__(self, rig, parent=None) -> None:
        super().__init__(parent)
        self.rig = rig
        self.setObjectName("rigToggle")
        self.setText(
            f"{rig.name}\n"
            f"{rig.fov_width_deg:.3f}° × {rig.fov_height_deg:.3f}°"
        )
        self.setStyleSheet(
            f"QCheckBox#rigToggle::indicator:checked {{ background: {rig.colour}; "
            f"border: 1px solid {rig.colour}; }}"
        )


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings("AstroFrame", "AstroFrame")
        self.setWindowTitle("AstroFrame 0.2.1")
        self.resize(1320, 860)
        self.setMinimumSize(1040, 680)

        self.viewer = ImageViewer()
        self.viewer.image_loaded.connect(self._on_image_loaded)

        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(286)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(12, 12, 12, 12)
        sidebar_layout.setSpacing(10)

        title = QLabel("AstroFrame")
        title.setObjectName("appTitle")
        subtitle = QLabel("Frame the image before collecting the photons.")
        subtitle.setObjectName("appSubtitle")
        subtitle.setWordWrap(True)
        sidebar_layout.addWidget(title)
        sidebar_layout.addWidget(subtitle)

        reference = Section("Reference image")
        self.file_label = QLabel("No image loaded")
        self.file_label.setObjectName("fileName")
        self.file_label.setWordWrap(True)
        reference.layout.addWidget(self.file_label)

        self.solve_status = QLabel("●  Image scale is estimated")
        self.solve_status.setObjectName("estimatedStatus")
        reference.layout.addWidget(self.solve_status)

        width_label = QLabel("Image angular width")
        width_label.setObjectName("fieldLabel")
        reference.layout.addWidget(width_label)

        self.width_spin = QDoubleSpinBox()
        self.width_spin.setRange(0.01, 20.0)
        self.width_spin.setDecimals(2)
        self.width_spin.setSingleStep(0.05)
        self.width_spin.setSuffix("°")
        self.width_spin.setValue(
            self.settings.value("referenceWidth", DEFAULT_REFERENCE_WIDTH, float)
        )
        self.width_spin.setToolTip(
            "Angular width of the entire reference image. This only scales the "
            "equipment frames; it does not change your telescope or camera."
        )
        self.width_spin.valueChanged.connect(self._reference_width_changed)
        reference.layout.addWidget(self.width_spin)

        width_help = QLabel(
            "Used only to scale equipment frames while the image is not plate-solved."
        )
        width_help.setObjectName("helpText")
        width_help.setWordWrap(True)
        reference.layout.addWidget(width_help)

        open_button = QPushButton("Open reference image…")
        open_button.setObjectName("primaryButton")
        open_button.clicked.connect(self.open_image)
        reference.layout.addWidget(open_button)
        sidebar_layout.addWidget(reference)

        equipment = Section("Equipment")
        self.rig_checks: dict[str, RigToggle] = {}
        for rig in RIGS:
            check = RigToggle(rig)
            check.toggled.connect(
                lambda checked, r=rig: self.viewer.set_rig_visible(r, checked)
            )
            equipment.layout.addWidget(check)
            self.rig_checks[rig.key] = check
        sidebar_layout.addWidget(equipment)

        framing = Section("Framing")
        rotation_row = QHBoxLayout()
        rotation_label = QLabel("Rotation")
        rotation_label.setObjectName("fieldLabel")
        self.rotation_value = QLabel("0.0°")
        self.rotation_value.setObjectName("valueLabel")
        rotation_row.addWidget(rotation_label)
        rotation_row.addStretch()
        rotation_row.addWidget(self.rotation_value)
        framing.layout.addLayout(rotation_row)

        self.rotation = QSlider(Qt.Orientation.Horizontal)
        self.rotation.setRange(0, 1800)
        self.rotation.setValue(0)
        self.rotation.valueChanged.connect(self._rotation_changed)
        framing.layout.addWidget(self.rotation)

        centre_button = QPushButton("Centre frames")
        centre_button.clicked.connect(self.viewer.centre_overlays)
        framing.layout.addWidget(centre_button)

        button_row = QHBoxLayout()
        reset_view = QPushButton("Reset view")
        reset_view.setToolTip("Restore fit-to-window zoom and centred panning.")
        reset_view.clicked.connect(self.viewer.reset_view)
        reset_framing = QPushButton("Reset framing")
        reset_framing.setToolTip(
            "Restore estimated image width, 0° rotation and centred frames."
        )
        reset_framing.clicked.connect(self._reset_framing)
        button_row.addWidget(reset_view)
        button_row.addWidget(reset_framing)
        framing.layout.addLayout(button_row)
        sidebar_layout.addWidget(framing)

        export = Section("Export")
        export_help = QLabel("NINA handoff and overlay export arrive in a later build.")
        export_help.setObjectName("helpText")
        export_help.setWordWrap(True)
        export.layout.addWidget(export_help)
        sidebar_layout.addWidget(export)
        sidebar_layout.addStretch()

        container = QWidget()
        container.setObjectName("mainContainer")
        outer = QHBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(sidebar)
        outer.addWidget(self.viewer, 1)
        self.setCentralWidget(container)

        self._restore_settings()

    def open_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open reference image",
            self.settings.value("lastImageDirectory", ""),
            "Images (*.jpg *.jpeg *.png *.tif *.tiff)",
        )
        if not path:
            return
        try:
            self.viewer.load_image(path)
            self.viewer.set_reference_width(self.width_spin.value())
            self.settings.setValue("lastImageDirectory", str(Path(path).parent))
            if not any(check.isChecked() for check in self.rig_checks.values()):
                self.rig_checks["asi1600_442"].setChecked(True)
        except Exception as exc:
            QMessageBox.critical(self, "Could not open image", str(exc))

    def closeEvent(self, event) -> None:
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("referenceWidth", self.width_spin.value())
        self.settings.setValue(
            "selectedRigs",
            [key for key, check in self.rig_checks.items() if check.isChecked()],
        )
        super().closeEvent(event)

    def _on_image_loaded(self, path: str, width: int, height: int) -> None:
        self.file_label.setText(f"{Path(path).name}\n{width} × {height} px")
        for rig in RIGS:
            check = self.rig_checks[rig.key]
            if check.isChecked():
                self.viewer.set_rig_visible(rig, True)

    def _reference_width_changed(self, value: float) -> None:
        self.viewer.set_reference_width(value)
        self.settings.setValue("referenceWidth", value)

    def _rotation_changed(self, value: int) -> None:
        degrees = value / 10.0
        self.rotation_value.setText(f"{degrees:.1f}°")
        self.viewer.set_rotation(degrees)

    def _reset_framing(self) -> None:
        self.width_spin.setValue(DEFAULT_REFERENCE_WIDTH)
        self.rotation.setValue(0)
        self.viewer.reset_framing(DEFAULT_REFERENCE_WIDTH)

    def _restore_settings(self) -> None:
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)

        selected = self.settings.value("selectedRigs", ["asi1600_442"])
        if isinstance(selected, str):
            selected = [selected]
        for key in selected:
            if key in self.rig_checks:
                self.rig_checks[key].setChecked(True)
