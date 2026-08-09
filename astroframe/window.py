from __future__ import annotations

from pathlib import Path

import json
import math
import re

import requests

from PySide6.QtCore import QObject, QSettings, QThread, QTimer, Qt, Signal, Slot
from astropy.coordinates import SkyCoord, get_constellation
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from .equipment import USER_RIG_COLOURS, Rig
from .equipment_catalog import (
    CAMERAS,
    CAMERA_BY_KEY,
    OPTICAL_MODIFIERS,
    TELESCOPES,
    TELESCOPE_BY_KEY,
)
from .plate_solve import (
    AstrometrySubmissionCache,
    PlateSolution,
    SolveCache,
)
from .solve_worker import PlateSolveWorker
from .astrobin_import import AstroBinImportWorker
from .observer import ObserverProfile, visibility_for_tonight
from .viewer import ImageViewer

DEFAULT_REFERENCE_WIDTH = 3.0


class Section(QFrame):
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
        self.setText(f"{rig.name}\n{rig.fov_width_deg:.3f}° × {rig.fov_height_deg:.3f}°")
        self.setStyleSheet(
            f"QCheckBox#rigToggle::indicator:checked {{ background: {rig.colour}; "
            f"border: 1px solid {rig.colour}; }}"
        )


class SubjectIdentifyWorker(QObject):
    """Run catalogue subject identification away from the Qt GUI thread."""

    finished = Signal(object, int, str, object)
    failed = Signal(str, int, str, object)

    def __init__(
        self,
        identify_callable,
        solution: PlateSolution,
        request_id: int,
        image_path: str,
    ) -> None:
        super().__init__()
        self.identify_callable = identify_callable
        self.solution = solution
        self.request_id = request_id
        self.image_path = image_path

    @Slot()
    def run(self) -> None:
        try:
            result = self.identify_callable(self.solution)
        except Exception as exc:
            self.failed.emit(
                str(exc), self.request_id, self.image_path, self.solution
            )
            return
        self.finished.emit(
            result, self.request_id, self.image_path, self.solution
        )


class MainWindow(QMainWindow):
    # Worker callbacks are first relayed through these signals so every
    # widget update occurs on Qt's main GUI thread.
    solve_progress_ui = Signal(str, int, str)
    solve_success_ui = Signal(object, int, str)
    solve_failure_ui = Signal(str, int, str)
    solve_cancelled_ui = Signal(int, str)
    solve_log_ui = Signal(str, int, str)

    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings("AstroFrame", "AstroFrame")
        self.solve_cache = SolveCache()
        self.astrometry_submission_cache = AstrometrySubmissionCache()
        self.current_image_path: str | None = None
        self.current_image_size = (0, 0)
        self.current_solution: PlateSolution | None = None
        self.solve_thread: QThread | None = None
        self.solve_worker: PlateSolveWorker | None = None
        self.solve_request_id = 0
        self.solve_in_progress = False
        self.reference_import_thread: QThread | None = None
        self.reference_import_worker: AstroBinImportWorker | None = None
        self.subject_request_id = 0
        self.subject_threads: dict[int, tuple[QThread, SubjectIdentifyWorker]] = {}
        self.solving_hint_name: str | None = None
        self.solving_hint_ra_hours: float | None = None
        self.solving_hint_dec_deg: float | None = None
        self.observer_profile = self._load_observer_profile()
        self.user_profile = self._load_user_profile()
        self.user_rigs = self._load_user_rigs()
        self.available_rigs = tuple(self.user_rigs)

        self.solve_progress_ui.connect(
            self._solve_progress_for_request,
            Qt.ConnectionType.QueuedConnection,
        )
        self.solve_success_ui.connect(
            self._solve_succeeded_for_request,
            Qt.ConnectionType.QueuedConnection,
        )
        self.solve_failure_ui.connect(
            self._solve_failed_for_request,
            Qt.ConnectionType.QueuedConnection,
        )
        self.solve_cancelled_ui.connect(
            self._solve_cancelled_for_request,
            Qt.ConnectionType.QueuedConnection,
        )
        self.solve_log_ui.connect(
            self._solve_log_for_request,
            Qt.ConnectionType.QueuedConnection,
        )

        self.setWindowTitle("AstroFrame 0.9.2-dev8c")
        self.resize(1320, 860)
        self.setMinimumSize(1040, 680)

        self.viewer = ImageViewer()
        self.viewer.image_loaded.connect(self._on_image_loaded)

        sidebar_content = QWidget()
        sidebar_content.setObjectName("sidebar")
        sidebar_content.setFixedWidth(296)
        sidebar_layout = QVBoxLayout(sidebar_content)
        sidebar_layout.setContentsMargins(12, 12, 12, 12)
        sidebar_layout.setSpacing(10)

        title = QLabel("AstroFrame")
        title.setObjectName("appTitle")
        subtitle = QLabel("Frame the image before collecting the photons.")
        subtitle.setObjectName("appSubtitle")
        subtitle.setWordWrap(True)
        sidebar_layout.addWidget(title)
        sidebar_layout.addWidget(subtitle)

        personal = Section("Your AstroFrame")
        self.personal_summary = QLabel("")
        self.personal_summary.setObjectName("helpText")
        self.personal_summary.setWordWrap(True)
        personal.layout.addWidget(self.personal_summary)

        self.personalise_button = QPushButton("Personalise…")
        self.personalise_button.clicked.connect(self.edit_personalisation)
        personal.layout.addWidget(self.personalise_button)
        sidebar_layout.addWidget(personal)
        self._refresh_personal_summary()

        self.observing_site_section = Section("Observing Site")
        observer = self.observing_site_section
        self.observer_summary = QLabel("")
        self.observer_summary.setObjectName("helpText")
        self.observer_summary.setWordWrap(True)
        observer.layout.addWidget(self.observer_summary)

        self.observer_button = QPushButton("Set up observing site…")
        self.observer_button.clicked.connect(self.edit_observer_profile)
        observer.layout.addWidget(self.observer_button)
        sidebar_layout.addWidget(observer)
        self._refresh_observer_summary()

        reference = Section("Reference image")
        self.file_label = QLabel("No image loaded")
        self.file_label.setObjectName("fileName")
        self.file_label.setWordWrap(True)
        reference.layout.addWidget(self.file_label)

        self.solve_status = QLabel("●  No image loaded")
        self.solve_status.setObjectName("unknownStatus")
        self.solve_status.setWordWrap(True)
        reference.layout.addWidget(self.solve_status)

        self.solve_details = QLabel("")
        self.solve_details.setObjectName("helpText")
        self.solve_details.setWordWrap(True)
        self.solve_details.hide()
        reference.layout.addWidget(self.solve_details)

        solver_label = QLabel("Plate solver")
        solver_label.setObjectName("fieldLabel")
        reference.layout.addWidget(solver_label)
        self.solver_combo = QComboBox()
        self.solver_combo.addItem("Automatic — ASTAP, then online", "automatic")
        self.solver_combo.addItem("ASTAP only", "astap")
        self.solver_combo.addItem("Online only — Astrometry.net", "online")
        saved_solver = str(self.settings.value("solverPreference", "automatic"))
        index = self.solver_combo.findData(saved_solver)
        self.solver_combo.setCurrentIndex(max(0, index))
        self.solver_combo.currentIndexChanged.connect(self._solver_preference_changed)
        reference.layout.addWidget(self.solver_combo)

        self.api_key_button = QPushButton()
        self.api_key_button.clicked.connect(self.set_api_key)
        reference.layout.addWidget(self.api_key_button)
        self._refresh_api_key_button()

        width_label = QLabel("Image angular width")
        width_label.setObjectName("fieldLabel")
        reference.layout.addWidget(width_label)

        self.width_spin = QDoubleSpinBox()
        self.width_spin.setRange(0.01, 180.0)
        self.width_spin.setDecimals(3)
        self.width_spin.setSingleStep(0.05)
        self.width_spin.setSuffix("°")
        self.width_spin.setValue(self.settings.value("referenceWidth", DEFAULT_REFERENCE_WIDTH, float))
        self.width_spin.setToolTip(
            "Angular width of the entire reference image. Before plate solving this is "
            "an estimate used to scale the equipment frames."
        )
        self.width_spin.valueChanged.connect(self._reference_width_changed)
        reference.layout.addWidget(self.width_spin)

        width_help = QLabel(
            "Estimated until the image is plate solved. Solved values are read-only."
        )
        width_help.setObjectName("helpText")
        width_help.setWordWrap(True)
        reference.layout.addWidget(width_help)

        open_button = QPushButton("Open reference image…")
        open_button.setObjectName("primaryButton")
        open_button.clicked.connect(self.open_image)
        reference.layout.addWidget(open_button)

        self.url_button = QPushButton("Paste reference URL…")
        self.url_button.setToolTip(
            "Import a reference from a supported site. AstroBin URLs can reuse the published plate solution without re-solving."
        )
        self.url_button.clicked.connect(self.import_reference_url)
        reference.layout.addWidget(self.url_button)

        self.assisted_solve_button = QPushButton("Identify Target…")
        self.assisted_solve_button.setObjectName("identifyTargetButton")
        self.assisted_solve_button.setEnabled(False)
        self.assisted_solve_button.setToolTip(
            "Optionally identify the target before plate solving."
        )
        self.assisted_solve_button.clicked.connect(self.target_assisted_solve)
        reference.layout.addWidget(self.assisted_solve_button)

        self.solving_hint_label = QLabel("Target\nPlate solve to identify")
        self.solving_hint_label.setObjectName("helpText")
        self.solving_hint_label.setWordWrap(True)
        self.solving_hint_label.show()
        reference.layout.addWidget(self.solving_hint_label)

        self.solve_button = QPushButton("Plate Solve")
        self.solve_button.setObjectName("solveButton")
        self.solve_button.setEnabled(False)
        self.solve_button.clicked.connect(lambda _checked=False: self.plate_solve())
        reference.layout.addWidget(self.solve_button)

        self.astrometry_job_button = QPushButton("Import from Astrometry.net…")
        self.astrometry_job_button.setEnabled(False)
        self.astrometry_job_button.setToolTip(
            "Use a job number or Astrometry.net job/status/user-image URL for the loaded image without uploading it again."
        )
        self.astrometry_job_button.clicked.connect(self.use_astrometry_job)
        reference.layout.addWidget(self.astrometry_job_button)

        self.clear_solution_button = QPushButton("Forget verified solution…")
        self.clear_solution_button.hide()
        self.clear_solution_button.clicked.connect(self.clear_solution)
        reference.layout.addWidget(self.clear_solution_button)
        sidebar_layout.addWidget(reference)

        self.image_summary = Section("Image summary")
        self.summary_target = QLabel("Target\nPlate solve to identify")
        self.summary_target.setObjectName("fileName")
        self.summary_target.setWordWrap(True)
        self.image_summary.layout.addWidget(self.summary_target)

        self.summary_centre = QLabel("")
        self.summary_centre.setObjectName("helpText")
        self.summary_centre.setWordWrap(True)
        self.image_summary.layout.addWidget(self.summary_centre)

        self.summary_field = QLabel("")
        self.summary_field.setObjectName("helpText")
        self.summary_field.setWordWrap(True)
        self.image_summary.layout.addWidget(self.summary_field)

        self.summary_scale = QLabel("")
        self.summary_scale.setObjectName("helpText")
        self.summary_scale.setWordWrap(True)
        self.image_summary.layout.addWidget(self.summary_scale)

        self.summary_rotation = QLabel("")
        self.summary_rotation.setObjectName("helpText")
        self.summary_rotation.setWordWrap(True)
        self.image_summary.layout.addWidget(self.summary_rotation)

        self.summary_solver = QLabel("")
        self.summary_solver.setObjectName("helpText")
        self.summary_solver.setWordWrap(True)
        self.image_summary.layout.addWidget(self.summary_solver)

        self.summary_visibility = QLabel("")
        self.summary_visibility.setObjectName("helpText")
        self.summary_visibility.setWordWrap(True)
        self.image_summary.layout.addWidget(self.summary_visibility)

        self.image_summary.hide()
        sidebar_layout.addWidget(self.image_summary)

        equipment = Section("My Equipment")
        self.equipment_section = equipment
        self.rig_checks: dict[str, RigToggle] = {}

        self.equipment_items_container = QWidget()
        self.equipment_items_layout = QVBoxLayout(self.equipment_items_container)
        self.equipment_items_layout.setContentsMargins(0, 0, 0, 0)
        self.equipment_items_layout.setSpacing(6)
        equipment.layout.addWidget(self.equipment_items_container)

        self.equipment_button = QPushButton("Manage equipment…")
        self.equipment_button.clicked.connect(self.manage_equipment)
        equipment.layout.addWidget(self.equipment_button)
        sidebar_layout.addWidget(equipment)
        self._rebuild_equipment_section()

        self.advisor_section = Section("Equipment Advisor")
        self.advisor_intro = QLabel(
            "Plate solve the reference image to compare your saved setups."
        )
        self.advisor_intro.setObjectName("helpText")
        self.advisor_intro.setWordWrap(True)
        self.advisor_section.layout.addWidget(self.advisor_intro)

        self.advisor_results_container = QWidget()
        self.advisor_results_layout = QVBoxLayout(self.advisor_results_container)
        self.advisor_results_layout.setContentsMargins(0, 0, 0, 0)
        self.advisor_results_layout.setSpacing(7)
        self.advisor_section.layout.addWidget(self.advisor_results_container)
        self.advisor_section.hide()
        sidebar_layout.addWidget(self.advisor_section)

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
        fit_image = QPushButton("Fit Image")
        fit_image.setToolTip("Fit the whole reference image in the viewer.")
        fit_image.clicked.connect(self.viewer.fit_image)

        actual_pixels = QPushButton("100%")
        actual_pixels.setToolTip("Show the reference image at native pixel scale.")
        actual_pixels.clicked.connect(self.viewer.actual_pixels)

        reset_framing = QPushButton("Reset framing")
        reset_framing.setToolTip("Restore 0° rotation and centred frames.")
        reset_framing.clicked.connect(self._reset_framing)

        button_row.addWidget(fit_image)
        button_row.addWidget(actual_pixels)
        button_row.addWidget(reset_framing)
        framing.layout.addLayout(button_row)
        sidebar_layout.addWidget(framing)

        diagnostics = Section("Solver log")
        self.solver_log_section = diagnostics
        self.solver_log = QPlainTextEdit()
        self.solver_log.setReadOnly(True)
        self.solver_log.setMaximumBlockCount(500)
        self.solver_log.setMinimumHeight(150)
        diagnostics.layout.addWidget(self.solver_log)
        log_buttons = QHBoxLayout()
        copy_log = QPushButton("Copy log")
        copy_log.clicked.connect(self._copy_solver_log)
        clear_log = QPushButton("Clear")
        clear_log.clicked.connect(self.solver_log.clear)
        log_buttons.addWidget(copy_log)
        log_buttons.addWidget(clear_log)
        diagnostics.layout.addLayout(log_buttons)
        sidebar_layout.addWidget(diagnostics)

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
        sidebar_scroll = QScrollArea()
        sidebar_scroll.setObjectName("sidebarScroll")
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sidebar_scroll.setFrameShape(QFrame.Shape.NoFrame)
        sidebar_scroll.setFixedWidth(316)
        sidebar_scroll.setWidget(sidebar_content)

        outer.addWidget(sidebar_scroll)
        outer.addWidget(self.viewer, 1)
        self.setCentralWidget(container)
        QTimer.singleShot(0, self._prompt_for_personalisation_on_first_launch)

        self._restore_settings()

    def _load_user_profile(self) -> dict:
        return {
            "experience": str(
                self.settings.value("personal/experience", "new")
            ),
            "reproduce": bool(
                self.settings.value("personal/reproduce", True, bool)
            ),
            "conditions": bool(
                self.settings.value("personal/conditions", False, bool)
            ),
            "alternatives": bool(
                self.settings.value("personal/alternatives", False, bool)
            ),
        }

    def _load_setup_records(self) -> list[dict]:
        raw = str(self.settings.value("personal/rigsJson", "")).strip()
        if not raw:
            return []
        try:
            records = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        return records if isinstance(records, list) else []

    def _load_user_rigs(self) -> list[Rig]:
        rigs: list[Rig] = []
        for index, record in enumerate(self._load_setup_records()):
            try:
                sensor_width = float(record["sensor_width_mm"])
                sensor_height = float(record["sensor_height_mm"])

                # 0.9.0-dev1/dev2 saved resolved sensor dimensions into each
                # setup record.  Correcting the catalogue alone therefore
                # did not update already-saved Seestar setups.  Override the
                # legacy dimensions here so existing users get the corrected
                # portrait orientation immediately.
                if str(record.get("camera_key", "")) == "seestar_s50_camera":
                    sensor_width = 3.13
                    sensor_height = 5.57

                rigs.append(
                    Rig(
                        key=f"user_{index}",
                        name=str(record["name"]).strip(),
                        sensor_width_mm=sensor_width,
                        sensor_height_mm=sensor_height,
                        focal_length_mm=float(record["focal_length_mm"]),
                        colour=USER_RIG_COLOURS[
                            index % len(USER_RIG_COLOURS)
                        ],
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return rigs

    def _save_user_rigs(self, records: list[dict]) -> None:
        self.settings.setValue("personal/rigsJson", json.dumps(records))
        self.settings.sync()

    def _rebuild_equipment_section(self) -> None:
        if not hasattr(self, "equipment_items_layout"):
            return

        while self.equipment_items_layout.count():
            item = self.equipment_items_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self.rig_checks = {}
        self.user_rigs = self._load_user_rigs()
        self.available_rigs = tuple(self.user_rigs)

        if not self.available_rigs:
            empty = QLabel(
                "No imaging setups yet.\\n"
                "Add the telescope/camera combinations you actually use."
            )
            empty.setObjectName("helpText")
            empty.setWordWrap(True)
            self.equipment_items_layout.addWidget(empty)
            return

        for rig in self.available_rigs:
            check = RigToggle(rig)
            check.toggled.connect(
                lambda checked, r=rig: self.viewer.set_rig_visible(r, checked)
            )
            self.equipment_items_layout.addWidget(check)
            self.rig_checks[rig.key] = check

        if hasattr(self, "advisor_section"):
            self._update_equipment_advisor()

    def _searchable_combo(self, names: list[str]) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.addItems(names)
        combo.completer().setCaseSensitivity(
            Qt.CaseSensitivity.CaseInsensitive
        )
        combo.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        return combo

    def _edit_setup_record(
        self,
        parent: QWidget,
        existing: dict | None = None,
    ) -> dict | None:
        existing = existing or {}
        dialog = QDialog(parent)
        dialog.setWindowTitle(
            "Edit Imaging Setup" if existing else "Add Imaging Setup"
        )
        dialog.setModal(True)
        layout = QVBoxLayout(dialog)

        intro = QLabel(
            "Choose your equipment by name. AstroFrame supplies the technical "
            "dimensions behind the scenes. Use Custom only if your model "
            "isn't yet in the starter library."
        )
        intro.setObjectName("helpText")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        setup_name = QLineEdit(str(existing.get("name", "")))
        setup_name.setPlaceholderText("e.g. Widefield SHO")

        camera_combo = self._searchable_combo(
            [c.name for c in CAMERAS] + ["Custom camera…"]
        )
        telescope_combo = self._searchable_combo(
            [t.name for t in TELESCOPES] + ["Custom telescope / lens…"]
        )

        camera_key = str(existing.get("camera_key", ""))
        scope_key = str(existing.get("telescope_key", ""))
        if camera_key in CAMERA_BY_KEY:
            camera_combo.setCurrentText(CAMERA_BY_KEY[camera_key].name)
        elif existing.get("camera_name"):
            camera_combo.setCurrentText(str(existing["camera_name"]))
        if scope_key in TELESCOPE_BY_KEY:
            telescope_combo.setCurrentText(TELESCOPE_BY_KEY[scope_key].name)
        elif existing.get("telescope_name"):
            telescope_combo.setCurrentText(str(existing["telescope_name"]))

        modifier_combo = QComboBox()
        for label, factor in OPTICAL_MODIFIERS:
            modifier_combo.addItem(label, factor)
        modifier_combo.addItem("Custom factor…", "custom")

        existing_factor = float(existing.get("optical_factor", 1.0))
        factor_found = False
        for i in range(modifier_combo.count() - 1):
            if abs(float(modifier_combo.itemData(i)) - existing_factor) < 0.001:
                modifier_combo.setCurrentIndex(i)
                factor_found = True
                break
        if not factor_found:
            modifier_combo.setCurrentIndex(modifier_combo.count() - 1)

        custom_factor = QDoubleSpinBox()
        custom_factor.setRange(0.1, 5.0)
        custom_factor.setDecimals(3)
        custom_factor.setSingleStep(0.05)
        custom_factor.setValue(existing_factor)
        custom_factor.setSuffix("×")

        form.addRow("Setup name", setup_name)
        form.addRow("Camera", camera_combo)
        form.addRow("Telescope / lens", telescope_combo)
        form.addRow("Reducer / extender", modifier_combo)
        form.addRow("Custom factor", custom_factor)
        layout.addLayout(form)

        custom_group = QWidget()
        custom_form = QFormLayout(custom_group)
        custom_note = QLabel(
            "Custom details are only needed for equipment not in the library."
        )
        custom_note.setObjectName("helpText")
        custom_note.setWordWrap(True)
        custom_form.addRow(custom_note)

        custom_camera_name = QLineEdit(str(existing.get("camera_name", "")))
        custom_sensor_width = QDoubleSpinBox()
        custom_sensor_width.setRange(1.0, 100.0)
        custom_sensor_width.setDecimals(2)
        custom_sensor_width.setSuffix(" mm")
        custom_sensor_width.setValue(float(existing.get("sensor_width_mm", 17.0)))
        custom_sensor_height = QDoubleSpinBox()
        custom_sensor_height.setRange(1.0, 100.0)
        custom_sensor_height.setDecimals(2)
        custom_sensor_height.setSuffix(" mm")
        custom_sensor_height.setValue(float(existing.get("sensor_height_mm", 13.0)))

        custom_scope_name = QLineEdit(str(existing.get("telescope_name", "")))
        custom_focal = QDoubleSpinBox()
        custom_focal.setRange(1.0, 10000.0)
        custom_focal.setDecimals(1)
        custom_focal.setSuffix(" mm")
        custom_focal.setValue(
            float(existing.get(
                "native_focal_length_mm",
                existing.get("focal_length_mm", 400.0),
            ))
        )

        custom_form.addRow("Camera name", custom_camera_name)
        custom_form.addRow("Sensor width", custom_sensor_width)
        custom_form.addRow("Sensor height", custom_sensor_height)
        custom_form.addRow("Telescope / lens name", custom_scope_name)
        custom_form.addRow("Native focal length", custom_focal)
        layout.addWidget(custom_group)

        def update_custom_visibility() -> None:
            camera_custom = (
                camera_combo.currentText().strip() == "Custom camera…"
            )
            scope_custom = (
                telescope_combo.currentText().strip()
                == "Custom telescope / lens…"
            )
            factor_custom = modifier_combo.currentData() == "custom"

            fields = [
                (custom_camera_name, camera_custom),
                (custom_sensor_width, camera_custom),
                (custom_sensor_height, camera_custom),
                (custom_scope_name, scope_custom),
                (custom_focal, scope_custom),
            ]
            for field, visible in fields:
                field.setVisible(visible)
                label = custom_form.labelForField(field)
                if label is not None:
                    label.setVisible(visible)

            custom_group.setVisible(camera_custom or scope_custom)
            custom_factor.setVisible(factor_custom)
            factor_label = form.labelForField(custom_factor)
            if factor_label is not None:
                factor_label.setVisible(factor_custom)

        camera_combo.currentTextChanged.connect(update_custom_visibility)
        telescope_combo.currentTextChanged.connect(update_custom_visibility)
        modifier_combo.currentIndexChanged.connect(update_custom_visibility)
        update_custom_visibility()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Save
        )
        buttons.button(
            QDialogButtonBox.StandardButton.Save
        ).setDefault(True)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None

        camera_text = camera_combo.currentText().strip()
        scope_text = telescope_combo.currentText().strip()
        camera = next((c for c in CAMERAS if c.name == camera_text), None)
        scope = next((t for t in TELESCOPES if t.name == scope_text), None)

        if camera is None and camera_text != "Custom camera…":
            QMessageBox.warning(
                parent, "Unknown camera",
                "Choose a camera from the list or select Custom camera."
            )
            return None
        if scope is None and scope_text != "Custom telescope / lens…":
            QMessageBox.warning(
                parent, "Unknown telescope / lens",
                "Choose a telescope from the list or select Custom."
            )
            return None

        if camera:
            resolved_camera_key = camera.key
            camera_name = camera.name
            sensor_width = camera.sensor_width_mm
            sensor_height = camera.sensor_height_mm
        else:
            resolved_camera_key = "custom"
            camera_name = custom_camera_name.text().strip() or "Custom camera"
            sensor_width = custom_sensor_width.value()
            sensor_height = custom_sensor_height.value()

        if scope:
            resolved_scope_key = scope.key
            scope_name = scope.name
            native_focal = scope.focal_length_mm
        else:
            resolved_scope_key = "custom"
            scope_name = custom_scope_name.text().strip() or "Custom telescope / lens"
            native_focal = custom_focal.value()

        factor_data = modifier_combo.currentData()
        factor = (
            custom_factor.value()
            if factor_data == "custom"
            else float(factor_data)
        )

        resolved_name = setup_name.text().strip()
        if not resolved_name:
            resolved_name = f"{scope_name} + {camera_name}"

        return {
            "name": resolved_name,
            "camera_key": resolved_camera_key,
            "camera_name": camera_name,
            "telescope_key": resolved_scope_key,
            "telescope_name": scope_name,
            "native_focal_length_mm": native_focal,
            "optical_factor": factor,
            "focal_length_mm": native_focal * factor,
            "sensor_width_mm": sensor_width,
            "sensor_height_mm": sensor_height,
        }

    def manage_equipment(self) -> None:
        records = self._load_setup_records()

        dialog = QDialog(self)
        dialog.setWindowTitle("My Equipment")
        dialog.setModal(True)
        dialog.resize(640, 440)
        layout = QVBoxLayout(dialog)

        intro = QLabel(
            "<b>Imaging setups</b><br>"
            "Add every telescope/camera combination you use. AstroFrame can "
            "compare all of them with the reference image."
        )
        intro.setObjectName("helpText")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        setup_list = QListWidget()
        layout.addWidget(setup_list)

        def refresh_list() -> None:
            setup_list.clear()
            for record in records:
                factor = float(record.get("optical_factor", 1.0))
                detail = (
                    f"{record.get('telescope_name', '')} + "
                    f"{record.get('camera_name', '')}"
                )
                if abs(factor - 1.0) > 0.001:
                    detail += f" · {factor:.2f}×"
                setup_list.addItem(
                    QListWidgetItem(
                        f"{record.get('name', 'Setup')}\\n{detail}"
                    )
                )

        def add_setup() -> None:
            record = self._edit_setup_record(dialog)
            if record:
                records.append(record)
                refresh_list()

        def edit_setup() -> None:
            row = setup_list.currentRow()
            if 0 <= row < len(records):
                updated = self._edit_setup_record(dialog, records[row])
                if updated:
                    records[row] = updated
                    refresh_list()

        def remove_setup() -> None:
            row = setup_list.currentRow()
            if 0 <= row < len(records):
                records.pop(row)
                refresh_list()

        controls = QHBoxLayout()
        add_button = QPushButton("Add setup…")
        edit_button = QPushButton("Edit…")
        remove_button = QPushButton("Remove")
        add_button.clicked.connect(add_setup)
        edit_button.clicked.connect(edit_setup)
        remove_button.clicked.connect(remove_setup)
        setup_list.itemDoubleClicked.connect(lambda _item: edit_setup())
        controls.addWidget(add_button)
        controls.addWidget(edit_button)
        controls.addWidget(remove_button)
        layout.addLayout(controls)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Save
        )
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        save_button.setText("Save equipment")
        save_button.setDefault(True)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        refresh_list()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self._save_user_rigs(records)
        self._rebuild_equipment_section()
        self._refresh_personal_summary()

    def _refresh_personal_summary(self) -> None:
        profile = self.user_profile
        experience_labels = {
            "new": "New to astrophotography",
            "comfortable": "Comfortable with the basics",
            "experienced": "Experienced",
        }
        interests: list[str] = []
        if profile.get("reproduce", True):
            interests.append("match framing")
        if profile.get("conditions", False):
            interests.append("check today's suitability")
        if profile.get("alternatives", False):
            interests.append("find alternatives")

        interest_text = ", ".join(interests) if interests else "not set"
        equipment_count = len(self._load_setup_records())
        equipment_text = (
            f"{equipment_count} imaging setup"
            if equipment_count == 1
            else f"{equipment_count} imaging setups"
        )
        self.personal_summary.setText(
            f"{experience_labels.get(profile.get('experience'), 'New to astrophotography')}\n"
            f"{equipment_text}\n"
            f"Focus: {interest_text}"
        )
        self._apply_personalised_flow()

    def _apply_personalised_flow(self) -> None:
        if not hasattr(self, "observing_site_section"):
            return

        wants_location = bool(
            self.user_profile.get("conditions", False)
            or self.user_profile.get("alternatives", False)
        )
        self.observing_site_section.setVisible(wants_location)

        # Technical diagnostics stay available, but are de-emphasised for
        # beginners unless there is something to diagnose.
        if hasattr(self, "solver_log_section"):
            self.solver_log_section.setVisible(
                self.user_profile.get("experience") == "experienced"
            )

    def _prompt_for_personalisation_on_first_launch(self) -> None:
        if bool(self.settings.value("personal/setupComplete", False, bool)):
            self._apply_personalised_flow()
            return
        self.edit_personalisation(first_run=True)

    def _geocode_location(
        self, location_text: str
    ) -> tuple[float, float, str] | None:
        location_text = location_text.strip()
        if not location_text:
            return None
        try:
            response = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={
                    "q": location_text,
                    "format": "jsonv2",
                    "limit": 1,
                },
                headers={"User-Agent": "AstroFrame/0.9.0-dev1"},
                timeout=8,
            )
            response.raise_for_status()
            results = response.json()
            if not results:
                return None
            result = results[0]
            return (
                float(result["lat"]),
                float(result["lon"]),
                str(result.get("display_name", location_text)),
            )
        except (requests.RequestException, ValueError, KeyError, TypeError):
            return None

    def edit_personalisation(self, first_run: bool = False) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(
            "Welcome to AstroFrame" if first_run else "Personalise AstroFrame"
        )
        dialog.setModal(True)
        layout = QVBoxLayout(dialog)

        intro = QLabel(
            "<b>What would you like AstroFrame to help you decide?</b><br><br>"
            "You can keep this very simple, or add more detail now. "
            "Everything can be changed later."
        )
        intro.setWordWrap(True)
        intro.setObjectName("helpText")
        layout.addWidget(intro)

        experience_label = QLabel("Your experience")
        experience_label.setObjectName("fieldLabel")
        layout.addWidget(experience_label)

        experience = QComboBox()
        experience.addItem("New to astrophotography", "new")
        experience.addItem("Comfortable with the basics", "comfortable")
        experience.addItem("Experienced", "experienced")
        current_experience = experience.findData(
            self.user_profile.get("experience", "new")
        )
        if current_experience >= 0:
            experience.setCurrentIndex(current_experience)
        layout.addWidget(experience)

        interests_label = QLabel("What do you want to use AstroFrame for?")
        interests_label.setObjectName("fieldLabel")
        layout.addWidget(interests_label)

        reproduce = QCheckBox("Will my equipment reproduce this framing?")
        reproduce.setChecked(self.user_profile.get("reproduce", True))
        reproduce.setEnabled(False)
        layout.addWidget(reproduce)

        conditions = QCheckBox(
            "Is this a good target for me where and when I am imaging?"
        )
        conditions.setChecked(self.user_profile.get("conditions", False))
        layout.addWidget(conditions)

        alternatives = QCheckBox(
            "If not, suggest similar targets I can photograph instead"
        )
        alternatives.setChecked(self.user_profile.get("alternatives", False))
        layout.addWidget(alternatives)

        equipment_heading = QLabel("Your equipment")
        equipment_heading.setObjectName("fieldLabel")
        layout.addWidget(equipment_heading)

        equipment_help = QLabel("")
        equipment_help.setObjectName("helpText")
        equipment_help.setWordWrap(True)

        def refresh_equipment_help() -> None:
            count = len(self._load_setup_records())
            if count:
                equipment_help.setText(
                    f"{count} imaging setup"
                    + ("" if count == 1 else "s")
                    + " saved. AstroFrame will compare all of them."
                )
            else:
                equipment_help.setText(
                    "No equipment saved yet. Add the telescope/camera "
                    "combinations you actually use."
                )

        refresh_equipment_help()
        layout.addWidget(equipment_help)

        manage_equipment_button = QPushButton("Set up my equipment…")
        def open_equipment_manager() -> None:
            self.manage_equipment()
            refresh_equipment_help()
        manage_equipment_button.clicked.connect(open_equipment_manager)
        layout.addWidget(manage_equipment_button)

        site_group = QWidget()
        site_layout = QVBoxLayout(site_group)
        site_layout.setContentsMargins(0, 6, 0, 0)

        site_heading = QLabel("Observing Site")
        site_heading.setObjectName("fieldLabel")
        site_layout.addWidget(site_heading)

        site_help = QLabel(
            "Location is only needed for today's suitability and "
            "alternative-target recommendations."
        )
        site_help.setObjectName("helpText")
        site_help.setWordWrap(True)
        site_layout.addWidget(site_help)

        quick = QRadioButton("Quick setup")
        detailed = QRadioButton("Detailed setup")
        quick.setChecked(True)
        radio_row = QHBoxLayout()
        radio_row.addWidget(quick)
        radio_row.addWidget(detailed)
        site_layout.addLayout(radio_row)

        location_form = QFormLayout()
        site_name = QLineEdit(
            self.observer_profile.profile_name
            if self.observer_profile.is_configured
            else "Home"
        )
        location_name = QLineEdit(self.observer_profile.location_name)
        location_name.setPlaceholderText("e.g. Oamaru, New Zealand")
        location_form.addRow("Site name", site_name)
        location_form.addRow("Town / city", location_name)
        site_layout.addLayout(location_form)

        find_button = QPushButton("Find location")
        site_layout.addWidget(find_button)

        details_widget = QWidget()
        details_form = QFormLayout(details_widget)
        latitude = QDoubleSpinBox()
        latitude.setRange(-90.0, 90.0)
        latitude.setDecimals(6)
        latitude.setValue(self.observer_profile.latitude_deg)
        latitude.setSuffix("°")
        longitude = QDoubleSpinBox()
        longitude.setRange(-180.0, 180.0)
        longitude.setDecimals(6)
        longitude.setValue(self.observer_profile.longitude_deg)
        longitude.setSuffix("°")
        elevation = QDoubleSpinBox()
        elevation.setRange(-500.0, 10000.0)
        elevation.setValue(self.observer_profile.elevation_m)
        elevation.setSuffix(" m")
        timezone_name = QLineEdit(self.observer_profile.timezone_name)
        timezone_name.setPlaceholderText("Blank = this Mac")
        bortle = QSpinBox()
        bortle.setRange(0, 9)
        bortle.setSpecialValueText("Not set")
        bortle.setValue(self.observer_profile.bortle_class)
        minimum_altitude = QDoubleSpinBox()
        minimum_altitude.setRange(0.0, 80.0)
        minimum_altitude.setValue(
            self.observer_profile.minimum_altitude_deg
        )
        minimum_altitude.setSuffix("°")

        details_form.addRow("Latitude", latitude)
        details_form.addRow("Longitude", longitude)
        details_form.addRow("Elevation", elevation)
        details_form.addRow("Time zone", timezone_name)
        details_form.addRow("Bortle class", bortle)
        details_form.addRow("Minimum imaging altitude", minimum_altitude)
        site_layout.addWidget(details_widget)
        details_widget.hide()

        def update_site_visibility() -> None:
            site_group.setVisible(
                conditions.isChecked() or alternatives.isChecked()
            )

        def update_detail_visibility() -> None:
            details_widget.setVisible(detailed.isChecked())

        def find_location() -> None:
            result = self._geocode_location(location_name.text())
            if result is None:
                QMessageBox.information(
                    dialog,
                    "Location not found",
                    "AstroFrame could not look up that location. "
                    "Try a town/city and country, or choose Detailed Setup "
                    "and enter coordinates manually.",
                )
                return
            lat, lon, display = result
            latitude.setValue(lat)
            longitude.setValue(lon)
            location_name.setText(display)
            QMessageBox.information(
                dialog,
                "Location found",
                f"Using {lat:+.4f}°, {lon:+.4f}°.\\n"
                "AstroFrame will use this Mac's time zone unless you "
                "choose Detailed Setup and specify another one.",
            )

        conditions.toggled.connect(update_site_visibility)
        alternatives.toggled.connect(update_site_visibility)
        quick.toggled.connect(update_detail_visibility)
        detailed.toggled.connect(update_detail_visibility)
        find_button.clicked.connect(find_location)
        update_site_visibility()

        layout.addWidget(site_group)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Save
        )
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        save_button.setText("Save and continue")
        save_button.setDefault(True)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        experience_value = str(experience.currentData())
        self.user_profile = {
            "experience": experience_value,
            "reproduce": True,
            "conditions": conditions.isChecked(),
            "alternatives": alternatives.isChecked(),
        }
        self.settings.setValue("personal/experience", experience_value)
        self.settings.setValue("personal/reproduce", True)
        self.settings.setValue(
            "personal/conditions", conditions.isChecked()
        )
        self.settings.setValue(
            "personal/alternatives", alternatives.isChecked()
        )

        wants_site = conditions.isChecked() or alternatives.isChecked()
        if wants_site and location_name.text().strip():
            self.observer_profile = ObserverProfile(
                profile_name=site_name.text().strip() or "Home",
                location_name=location_name.text().strip(),
                latitude_deg=latitude.value(),
                longitude_deg=longitude.value(),
                elevation_m=elevation.value(),
                timezone_name=timezone_name.text().strip(),
                bortle_class=bortle.value(),
                minimum_altitude_deg=minimum_altitude.value(),
            )
            self._save_observer_profile(self.observer_profile)

        self.settings.setValue("personal/setupComplete", True)
        self.settings.sync()

        self.user_rigs = self._load_user_rigs()
        self.available_rigs = tuple(self.user_rigs)
        self._refresh_personal_summary()
        self._refresh_observer_summary()


    def _load_observer_profile(self) -> ObserverProfile:
        return ObserverProfile(
            profile_name=str(self.settings.value("observer/profileName", "Home")),
            location_name=str(self.settings.value("observer/locationName", "")),
            latitude_deg=float(self.settings.value("observer/latitude", 0.0, float)),
            longitude_deg=float(self.settings.value("observer/longitude", 0.0, float)),
            elevation_m=float(self.settings.value("observer/elevation", 0.0, float)),
            timezone_name=str(self.settings.value("observer/timezone", "")),
            bortle_class=int(self.settings.value("observer/bortle", 0, int)),
            minimum_altitude_deg=float(
                self.settings.value("observer/minimumAltitude", 30.0, float)
            ),
        )

    def _save_observer_profile(self, profile: ObserverProfile) -> None:
        self.settings.setValue("observer/profileName", profile.profile_name)
        self.settings.setValue("observer/locationName", profile.location_name)
        self.settings.setValue("observer/latitude", profile.latitude_deg)
        self.settings.setValue("observer/longitude", profile.longitude_deg)
        self.settings.setValue("observer/elevation", profile.elevation_m)
        self.settings.setValue("observer/timezone", profile.timezone_name)
        self.settings.setValue("observer/bortle", profile.bortle_class)
        self.settings.setValue(
            "observer/minimumAltitude", profile.minimum_altitude_deg
        )
        self.settings.sync()

    def _refresh_observer_summary(self) -> None:
        profile = self.observer_profile
        if not profile.is_configured:
            self.observer_summary.setText(
                "Not configured\n"
                "Add an observing site so AstroFrame can judge target visibility."
            )
            self.observer_button.setText("Set up observing site…")
            return

        def coordinate(value: float, positive: str, negative: str) -> str:
            suffix = positive if value >= 0 else negative
            return f"{abs(value):.4f}° {suffix}"

        lines = [
            profile.profile_name or profile.location_name,
            (
                f"{coordinate(profile.latitude_deg, 'N', 'S')}, "
                f"{coordinate(profile.longitude_deg, 'E', 'W')}"
            ),
        ]

        conditions = []
        if 1 <= profile.bortle_class <= 9:
            conditions.append(f"Bortle {profile.bortle_class}")
        conditions.append(
            f"Min altitude {profile.minimum_altitude_deg:.0f}°"
        )
        lines.append(" • ".join(conditions))

        self.observer_summary.setText("\n".join(lines))
        self.observer_button.setText("Change observing site…")

    def edit_observer_profile(self) -> None:
        profile = self.observer_profile

        dialog = QDialog(self)
        dialog.setWindowTitle("Observer Profile")
        dialog.setModal(True)
        layout = QVBoxLayout(dialog)

        help_text = QLabel(
            "AstroFrame uses this location for visibility and target "
            "recommendations. Nothing is uploaded."
        )
        help_text.setWordWrap(True)
        help_text.setObjectName("helpText")
        layout.addWidget(help_text)

        form = QFormLayout()
        profile_name = QLineEdit(profile.profile_name)
        location_name = QLineEdit(profile.location_name)

        latitude = QDoubleSpinBox()
        latitude.setRange(-90.0, 90.0)
        latitude.setDecimals(6)
        latitude.setValue(profile.latitude_deg)
        latitude.setSuffix("°")

        longitude = QDoubleSpinBox()
        longitude.setRange(-180.0, 180.0)
        longitude.setDecimals(6)
        longitude.setValue(profile.longitude_deg)
        longitude.setSuffix("°")

        elevation = QDoubleSpinBox()
        elevation.setRange(-500.0, 10000.0)
        elevation.setDecimals(0)
        elevation.setValue(profile.elevation_m)
        elevation.setSuffix(" m")

        timezone_name = QLineEdit(profile.timezone_name)
        timezone_name.setPlaceholderText(
            "e.g. Pacific/Auckland (blank = this Mac)"
        )

        bortle = QSpinBox()
        bortle.setRange(0, 9)
        bortle.setSpecialValueText("Not set")
        bortle.setValue(profile.bortle_class)

        minimum_altitude = QDoubleSpinBox()
        minimum_altitude.setRange(0.0, 80.0)
        minimum_altitude.setDecimals(0)
        minimum_altitude.setValue(profile.minimum_altitude_deg)
        minimum_altitude.setSuffix("°")

        form.addRow("Profile name", profile_name)
        form.addRow("Location", location_name)
        form.addRow("Latitude", latitude)
        form.addRow("Longitude", longitude)
        form.addRow("Elevation", elevation)
        form.addRow("Time zone", timezone_name)
        form.addRow("Bortle class", bortle)
        form.addRow("Minimum imaging altitude", minimum_altitude)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Save
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        if not location_name.text().strip():
            QMessageBox.warning(
                self,
                "Observer Profile",
                "Please enter a location name.",
            )
            return

        self.observer_profile = ObserverProfile(
            profile_name=profile_name.text().strip() or "Home",
            location_name=location_name.text().strip(),
            latitude_deg=latitude.value(),
            longitude_deg=longitude.value(),
            elevation_m=elevation.value(),
            timezone_name=timezone_name.text().strip(),
            bortle_class=bortle.value(),
            minimum_altitude_deg=minimum_altitude.value(),
        )
        self._save_observer_profile(self.observer_profile)
        self._refresh_observer_summary()

        if self.current_solution is not None:
            self._update_visibility_summary(self.current_solution)

    def _clear_advisor_results(self) -> None:
        if not hasattr(self, "advisor_results_layout"):
            return
        while self.advisor_results_layout.count():
            item = self.advisor_results_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _orientation_metrics(
        self,
        rig: Rig,
        solution: PlateSolution,
        rotated: bool = False,
    ) -> tuple[float, str, float, float, float]:
        """Return score, note, width ratio, height ratio and retained area.

        The Advisor intentionally evaluates the orientation currently shown.
        It may report that the alternate 90-degree orientation scores better,
        but it does not silently choose that orientation for the user.
        """
        ref_w = max(float(solution.image_width_deg), 1e-6)
        ref_h = max(float(solution.image_height_deg), 1e-6)

        rig_w = rig.fov_height_deg if rotated else rig.fov_width_deg
        rig_h = rig.fov_width_deg if rotated else rig.fov_height_deg

        width_ratio = rig_w / ref_w
        height_ratio = rig_h / ref_h

        error = (
            abs(math.log(max(width_ratio, 1e-9)))
            + abs(math.log(max(height_ratio, 1e-9)))
        ) / 2.0
        ref_aspect = ref_w / ref_h
        rig_aspect = rig_w / rig_h
        aspect_error = abs(
            math.log(max(rig_aspect / ref_aspect, 1e-9))
        )
        total_error = error + 0.22 * aspect_error
        score = 100.0 * math.exp(-1.35 * total_error)
        score = max(0.0, min(100.0, score))

        max_delta = max(abs(width_ratio - 1.0), abs(height_ratio - 1.0))
        if max_delta <= 0.10:
            note = "Excellent framing match"
        elif score >= 82:
            note = "Very close framing"
        elif width_ratio < 0.88 or height_ratio < 0.88:
            if width_ratio > 1.12 or height_ratio > 1.12:
                note = "Different aspect ratio — crop needed"
            else:
                note = "Tighter field than the reference"
        elif width_ratio > 1.75 and height_ratio > 1.75:
            note = "Much wider than the reference"
        elif width_ratio > 1.15 or height_ratio > 1.15:
            note = "Wider than the reference"
        else:
            note = "Good framing match"

        # With the setup centred on the same point as the reference, this is
        # the fraction of the reference field that remains inside the setup.
        retained_fraction = (
            min(1.0, max(width_ratio, 0.0))
            * min(1.0, max(height_ratio, 0.0))
        )

        return score, note, width_ratio, height_ratio, retained_fraction

    def _score_setup_for_reference(
        self,
        rig: Rig,
        solution: PlateSolution,
    ) -> tuple[float, str, bool, float, float]:
        """Compatibility wrapper returning best score plus 0°/90° scores."""
        base_score, base_note, _, _, _ = self._orientation_metrics(
            rig, solution, rotated=False
        )
        rotated_score, rotated_note, _, _, _ = self._orientation_metrics(
            rig, solution, rotated=True
        )
        if rotated_score > base_score:
            return rotated_score, rotated_note, True, base_score, rotated_score
        return base_score, base_note, False, base_score, rotated_score

    def _framing_analysis_lines(
        self,
        width_ratio: float,
        height_ratio: float,
        retained_fraction: float,
    ) -> list[str]:
        """Objective framing facts for a centred comparison."""
        lines: list[str] = []

        if width_ratio >= 1.0 and height_ratio >= 1.0:
            lines.append("✓ Entire reference framing fits inside this field.")
            extra_area = max(0.0, width_ratio * height_ratio - 1.0)
            if extra_area >= 0.25:
                lines.append("• Additional surrounding sky will be included.")
            return lines

        # Any centred candidate frame contains the reference centre.  State
        # that explicitly because it is useful when a tighter setup is being
        # considered for the central subject.
        lines.append("✓ Reference centre remains in frame when centred.")

        retained_pct = int(round(retained_fraction * 100.0))
        if retained_pct < 100:
            lines.append(
                f"• About {retained_pct}% of the reference area is retained."
            )

        if width_ratio >= 1.0 or height_ratio >= 1.0:
            lines.append("• Cropping occurs mainly along one axis.")

        return lines

    def _show_advisor_setup(self, rig_key: str) -> None:
        check = self.rig_checks.get(rig_key)
        if check is not None:
            check.setChecked(True)

    def _toggle_advisor_rotation(self, rig_key: str) -> None:
        check = self.rig_checks.get(rig_key)
        if check is not None:
            check.setChecked(True)
        current = self.viewer.rig_rotation(rig_key)
        self.viewer.set_rig_rotation(
            rig_key, 0.0 if abs(current - 90.0) < 0.1 else 90.0
        )
        self._update_equipment_advisor()

    def _mosaic_suggestion(
        self,
        rigs: list[Rig],
        solution: PlateSolution,
        overlap: float = 0.15,
    ) -> tuple[Rig, int, int, bool] | None:
        """Find a practical mosaic using one of the user's tighter setups."""
        ref_w = max(float(solution.image_width_deg), 1e-6)
        ref_h = max(float(solution.image_height_deg), 1e-6)

        candidates: list[tuple[int, float, Rig, int, int, bool]] = []

        def panels_needed(reference: float, panel: float) -> int:
            if panel >= reference:
                return 1
            step = panel * (1.0 - overlap)
            return 1 + math.ceil((reference - panel) / max(step, 1e-9))

        for rig in rigs:
            for rotated, rig_w, rig_h in (
                (False, rig.fov_width_deg, rig.fov_height_deg),
                (True, rig.fov_height_deg, rig.fov_width_deg),
            ):
                if rig_w >= ref_w or rig_h >= ref_h:
                    continue

                cols = panels_needed(ref_w, rig_w)
                rows = panels_needed(ref_h, rig_h)
                panels = cols * rows
                if panels < 2 or panels > 12:
                    continue

                area_ratio = (rig_w * rig_h) / (ref_w * ref_h)
                candidates.append(
                    (panels, -area_ratio, rig, cols, rows, rotated)
                )

        if not candidates:
            return None

        candidates.sort(key=lambda item: (item[0], item[1]))
        _, _, rig, cols, rows, rotated = candidates[0]
        return rig, cols, rows, rotated

    def _update_equipment_advisor(self) -> None:
        if not hasattr(self, "advisor_section"):
            return

        self._clear_advisor_results()

        if self.current_solution is None:
            self.advisor_intro.setText(
                "Plate solve the reference image to compare your saved setups."
            )
            self.advisor_section.hide()
            return

        rigs = list(self.available_rigs)
        if not rigs:
            self.advisor_intro.setText(
                "Add at least one imaging setup to get a recommendation."
            )
            self.advisor_section.show()
            return

        # The ranking is deliberately based on each setup's original
        # orientation. Previewing a 90° rotation is a local what-if experiment
        # and must not reshuffle the rest of the Advisor list.
        ranked = []
        for rig in rigs:
            shown_rotated = abs(self.viewer.rig_rotation(rig.key) - 90.0) < 0.1
            base = self._orientation_metrics(
                rig, self.current_solution, rotated=False
            )
            rotated = self._orientation_metrics(
                rig, self.current_solution, rotated=True
            )
            current = rotated if shown_rotated else base
            alternate = base if shown_rotated else rotated
            ranked.append((base, current, alternate, shown_rotated, rig))

        ranked.sort(key=lambda item: item[0][0], reverse=True)
        top_score = ranked[0][0][0]

        if top_score < 40:
            self.advisor_intro.setText(
                "<b>No close framing match.</b><br>"
                "None of your saved setups closely reproduces this "
                "reference as a single frame."
            )

            mosaic = self._mosaic_suggestion(rigs, self.current_solution)
            if mosaic is not None:
                rig, cols, rows, rotated = mosaic
                mosaic_label = QLabel(
                    "<b>Suggestion</b><br>"
                    f"Try a {cols}×{rows} mosaic with "
                    f"<b>{rig.name}</b> using about 15% overlap."
                    + (
                        "<br>Rotate the camera 90° for the more efficient layout."
                        if rotated else ""
                    )
                )
                mosaic_label.setObjectName("helpText")
                mosaic_label.setWordWrap(True)
                self.advisor_results_layout.addWidget(mosaic_label)

            closest = QLabel("Closest single-frame matches")
            closest.setObjectName("fieldLabel")
            closest.setWordWrap(True)
            self.advisor_results_layout.addWidget(closest)
        else:
            self.advisor_intro.setText(
                "Best geometric matches to the solved reference framing:"
            )

        for rank, (base, current, alternate, shown_rotated, rig) in enumerate(
            ranked, start=1
        ):
            base_score = base[0]
            score, note, width_ratio, height_ratio, retained_fraction = current
            alternate_score = alternate[0]

            result = QWidget()
            result_layout = QVBoxLayout(result)
            result_layout.setContentsMargins(0, 0, 0, 0)
            result_layout.setSpacing(4)

            button = QPushButton()
            score_text = (
                f"{base_score:.0f}% → {score:.0f}%"
                if shown_rotated else f"{score:.0f}%"
            )
            button.setText(
                f"{rank}. {rig.name}    {score_text}\n{note}"
            )
            button.setMinimumHeight(60)
            button.setToolTip("Show this setup's framing overlay.")
            if rank == 1 and top_score >= 40:
                button.setObjectName("primaryButton")
            button.clicked.connect(
                lambda _checked=False, key=rig.key:
                    self._show_advisor_setup(key)
            )
            result_layout.addWidget(button)

            analysis_lines = self._framing_analysis_lines(
                width_ratio,
                height_ratio,
                retained_fraction,
            )
            if analysis_lines:
                analysis = QLabel(
                    "<b>Framing analysis</b><br>"
                    + "<br>".join(analysis_lines)
                )
                analysis.setObjectName("helpText")
                analysis.setWordWrap(True)
                result_layout.addWidget(analysis)

            if shown_rotated:
                rotation_note = QLabel(
                    f"<b>{rig.name}</b>: previewing 90° rotation."
                )
                rotation_note.setObjectName("helpText")
                rotation_note.setWordWrap(True)
                result_layout.addWidget(rotation_note)

                rotation_button = QPushButton("Restore original orientation")
                rotation_button.setToolTip(
                    "Return only this setup to its original orientation."
                )
                rotation_button.clicked.connect(
                    lambda _checked=False, key=rig.key:
                        self._toggle_advisor_rotation(key)
                )
                result_layout.addWidget(rotation_button)
            elif alternate_score > score + 0.05:
                rotation_note = QLabel(
                    f"<b>{rig.name}</b>: 90° rotation improves match "
                    f"to {alternate_score:.0f}%."
                )
                rotation_note.setObjectName("helpText")
                rotation_note.setWordWrap(True)
                result_layout.addWidget(rotation_note)

                rotation_button = QPushButton("Preview 90°")
                rotation_button.setToolTip(
                    "Temporarily rotate only this setup's overlay by 90°."
                )
                rotation_button.clicked.connect(
                    lambda _checked=False, key=rig.key:
                        self._toggle_advisor_rotation(key)
                )
                result_layout.addWidget(rotation_button)

            self.advisor_results_layout.addWidget(result)

        self.advisor_section.show()

    def _update_visibility_summary(self, solution: PlateSolution) -> None:
        if not (
            self.user_profile.get("conditions", False)
            or self.user_profile.get("alternatives", False)
        ):
            self.summary_visibility.clear()
            return

        if not self.observer_profile.is_configured:
            self.summary_visibility.setText(
                "Tonight\nSet up an Observer Profile for visibility."
            )
            return

        try:
            target = SkyCoord(
                ra=solution.ra_deg,
                dec=solution.dec_deg,
                unit=("deg", "deg"),
                frame="icrs",
            )
            visibility = visibility_for_tonight(
                target,
                self.observer_profile,
            )
        except Exception as exc:
            self.summary_visibility.setText(
                "Tonight\nVisibility calculation unavailable."
            )
            self._append_solver_log(
                f"Visibility calculation unavailable: {exc}"
            )
            return

        peak = visibility.peak_time.strftime("%H:%M")
        if visibility.has_useful_window:
            start = visibility.visible_start.strftime("%H:%M")
            end = visibility.visible_end.strftime("%H:%M")
            self.summary_visibility.setText(
                f"Tonight from {self.observer_profile.location_name}\n"
                f"Highest altitude: {visibility.maximum_altitude_deg:.0f}° "
                f"at {peak}\n"
                f"Above {visibility.minimum_altitude_deg:.0f}°: "
                f"{start}–{end}"
            )
        else:
            self.summary_visibility.setText(
                f"Tonight from {self.observer_profile.location_name}\n"
                f"Highest altitude: {visibility.maximum_altitude_deg:.0f}° "
                f"at {peak}\n"
                f"Does not reach your {visibility.minimum_altitude_deg:.0f}° "
                "minimum imaging altitude."
            )

    def _load_reference_path(self, path: str) -> None:
        """Load a local/cached reference image and reset reference-specific UI."""
        # Invalidate callbacks still arriving from a solve for the previous image.
        self.solve_request_id += 1
        self.current_image_path = path
        self.current_solution = None
        self.solve_in_progress = False
        self.solving_hint_name = None
        self.solving_hint_ra_hours = None
        self.solving_hint_dec_deg = None
        self.solving_hint_label.setText("Target\nPlate solve to identify")
        self.solving_hint_label.show()
        self.assisted_solve_button.setText("Identify Target…")
        self.solve_button.setText("Plate Solve")
        self.solve_button.setToolTip("")
        self.solve_button.setEnabled(False)
        self.assisted_solve_button.setEnabled(False)
        self.clear_solution_button.hide()
        self.solve_details.clear()
        self.solve_details.hide()
        self.image_summary.hide()
        if hasattr(self, "advisor_section"):
            self.advisor_section.hide()
        self.summary_target.setText("Target\nPlate solve to identify")
        self.summary_centre.clear()
        self.summary_field.clear()
        self.summary_scale.clear()
        self.summary_rotation.clear()
        self.summary_solver.clear()
        self.summary_visibility.clear()
        self.setWindowTitle("AstroFrame 0.9.2-dev8c")
        self.width_spin.setEnabled(True)
        self._show_estimated_status()

        self.viewer.load_image(path)
        self.viewer.set_reference_width(self.width_spin.value())
        self.settings.setValue("lastImageDirectory", str(Path(path).parent))
        self.solve_button.setEnabled(True)
        self.assisted_solve_button.setEnabled(True)
        self.astrometry_job_button.setEnabled(True)
        if self.rig_checks and not any(check.isChecked() for check in self.rig_checks.values()):
            next(iter(self.rig_checks.values())).setChecked(True)

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
            self._load_reference_path(path)
        except Exception as exc:
            QMessageBox.critical(self, "Could not open image", str(exc))

    def import_reference_url(self) -> None:
        if self.reference_import_thread is not None:
            return
        value, accepted = QInputDialog.getText(
            self,
            "Paste reference URL",
            "AstroBin URL:",
        )
        if not accepted or not value.strip():
            return
        reference = value.strip()
        if "astrobin.com" not in reference.lower():
            QMessageBox.information(
                self,
                "Reference URL",
                "This build supports AstroBin URLs first. AstroFrame can add other reference sites in later builds.",
            )
            return

        self.url_button.setEnabled(False)
        self.solve_status.setObjectName("solvingStatus")
        self.solve_status.setText("⏳  Reading AstroBin reference…")
        self.solve_status.style().unpolish(self.solve_status)
        self.solve_status.style().polish(self.solve_status)
        self._append_solver_log(f"AstroBin: resolving reference {reference}")

        thread = QThread(self)
        worker = AstroBinImportWorker(reference)
        self.reference_import_thread = thread
        self.reference_import_worker = worker
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._astrobin_import_progress)
        worker.log.connect(self._append_solver_log)
        worker.succeeded.connect(self._astrobin_import_succeeded)
        worker.failed.connect(self._astrobin_import_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._astrobin_import_finished)
        thread.start()

    @Slot(str)
    def _astrobin_import_progress(self, message: str) -> None:
        self.solve_status.setText(f"●  {message}")

    @Slot(str, object, str)
    def _astrobin_import_succeeded(self, path: str, solution: object, title: str) -> None:
        try:
            self._load_reference_path(path)
            self.solving_hint_name = title
            self.setWindowTitle(f"AstroFrame — {title}")
            if isinstance(solution, PlateSolution):
                # The AstroBin page itself supplies verified geometry, so no
                # ASTAP/Astrometry.net solve is necessary.
                self.solve_cache.save(path, solution)
                self._append_solver_log("AstroBin: imported published plate solution")
                self._apply_solution(solution, cached=False)
                self.summary_target.setText(f"Reference\n{title}")
            else:
                # AstroBin supplies the reference pixels only. _on_image_loaded
                # has already checked AstroFrame's content-addressed solution
                # cache for these exact bytes.
                if self.current_solution is not None:
                    self._append_solver_log(
                        "AstroBin: matching AstroFrame solution reused; no solver contacted"
                    )
                    return
                self._append_solver_log(
                    "AstroBin: reference loaded. No matching AstroFrame solution yet; waiting for the user before contacting a solver"
                )
                self.solve_status.setObjectName("estimatedStatus")
                self.solve_status.setText(
                    "⚠  AstroBin reference loaded. AstroFrame has not solved these image pixels before. "
                    "Press Plate Solve or import an existing Astrometry.net job if you want an accurate solution."
                )
                self.solve_status.style().unpolish(self.solve_status)
                self.solve_status.style().polish(self.solve_status)
                # Do not automatically submit the same difficult AstroBin
                # reference to Astrometry.net every time it is imported. The
                # user can explicitly request a solve if desired.
        except Exception as exc:
            self._astrobin_import_failed(str(exc))

    @Slot(str)
    def _astrobin_import_failed(self, message: str) -> None:
        self._append_solver_log(f"AstroBin import failed: {message}")
        self.solve_status.setObjectName("estimatedStatus")
        self.solve_status.setText("⚠  AstroBin import failed. You can still open the image locally and plate solve it.")
        self.solve_status.style().unpolish(self.solve_status)
        self.solve_status.style().polish(self.solve_status)
        QMessageBox.warning(self, "AstroBin import", message)

    @Slot()
    def _astrobin_import_finished(self) -> None:
        self.reference_import_thread = None
        self.reference_import_worker = None
        self.url_button.setEnabled(True)

    def use_astrometry_job(self) -> None:
        if not self.current_image_path or self.solve_in_progress:
            return

        value, accepted = QInputDialog.getText(
            self,
            "Import from Astrometry.net",
            "Job number or Astrometry.net URL:",
        )
        if not accepted:
            return

        reference = value.strip()
        if not reference:
            return

        self._append_solver_log(
            f"Astrometry.net: resolving reference {reference}"
        )
        self.plate_solve(astrometry_job_reference=reference)

    def plate_solve(
        self,
        target_ra_hours: float | None = None,
        target_dec_deg: float | None = None,
        astrometry_job_reference: str | int | None = None,
    ) -> None:
        if self.solve_in_progress:
            self.cancel_solve()
            return

        if not self.current_image_path:
            return

        # If this image is already solved, the button means "Solve Again".
        # Remove only AstroFrame's cached WCS so a fresh solve can run.
        if self.current_solution is not None:
            self.solve_cache.remove(self.current_image_path)
            self.current_solution = None
            self.width_spin.setEnabled(True)
            self.clear_solution_button.hide()

        # Check the content-addressed cache at the exact moment the user asks
        # to solve. This prevents a duplicate solve when the image has a cached
        # solution but the UI has not yet applied it.
        cached = self.solve_cache.load(self.current_image_path)
        if cached is not None and astrometry_job_reference is None:
            self._append_solver_log(
                "Existing verified solution found; no solver was contacted."
            )
            self._apply_solution(cached, cached=True)
            return

        if target_ra_hours is None:
            target_ra_hours = self.solving_hint_ra_hours
        if target_dec_deg is None:
            target_dec_deg = self.solving_hint_dec_deg

        solver_preference = str(self.solver_combo.currentData())

        # Never create another Astrometry.net upload for a known failed exact
        # image without a second, explicit confirmation from the user.
        if solver_preference == "online" and astrometry_job_reference is None:
            prior_online = self.astrometry_submission_cache.load(self.current_image_path)
            if prior_online and str(prior_online.get("status", "")).lower() in {
                "failure", "upload_error", "reserved"
            }:
                answer = QMessageBox.question(
                    self,
                    "Upload this image again?",
                    "Astrometry.net has already received this exact image and the previous "
                    "attempt did not solve. Uploading again will create another Astrometry.net "
                    "submission.\n\nDo you want to submit a fresh copy?",
                    QMessageBox.StandardButton.No | QMessageBox.StandardButton.Yes,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    self._append_solver_log(
                        "Astrometry.net: repeat upload cancelled; existing failed attempt retained"
                    )
                    return
                self.astrometry_submission_cache.remove(self.current_image_path)
                self._append_solver_log(
                    "Astrometry.net: user explicitly approved a fresh upload"
                )

        api_key = str(self.settings.value("astrometryApiKey", "")).strip()
        if solver_preference == "online" and astrometry_job_reference is None and not api_key:
            if not self.set_api_key():
                return
            api_key = str(self.settings.value("astrometryApiKey", "")).strip()

        width_px, height_px = self.current_image_size
        if width_px <= 0 or height_px <= 0:
            QMessageBox.warning(self, "Plate solve", "The loaded image dimensions are unavailable.")
            return

        self.solve_in_progress = True
        self.solve_button.setText("Cancel Solve")
        self.solve_button.setEnabled(True)
        self.solve_button.setToolTip("Stop the current plate solve.")
        self.assisted_solve_button.setEnabled(False)
        self.astrometry_job_button.setEnabled(False)
        self.solve_status.setObjectName("solvingStatus")
        has_hint = target_ra_hours is not None and target_dec_deg is not None
        if astrometry_job_reference is not None:
            self.solve_status.setText(
                "⏳  Retrieving Astrometry.net solution…"
            )
        elif solver_preference == "online":
            self.solve_status.setText(
                "⏳  Uploading image to Astrometry.net…"
            )
        elif solver_preference == "astap":
            self.solve_status.setText(
                "⏳  Solving locally with ASTAP using target hint…"
                if has_hint
                else "⏳  Blind-solving locally with ASTAP…"
            )
        else:
            self.solve_status.setText(
                "⏳  Trying ASTAP locally with target hint…"
                if has_hint
                else "⏳  Trying ASTAP locally (blind solve)…"
            )
        self.solve_status.style().unpolish(self.solve_status)
        self.solve_status.style().polish(self.solve_status)
        self.solve_details.hide()

        self.solve_thread = QThread(self)
        self.solve_worker = PlateSolveWorker(
            solver_preference=solver_preference,
            api_key=api_key,
            image_path=self.current_image_path,
            image_width_px=width_px,
            image_height_px=height_px,
            estimated_width_deg=self.width_spin.value(),
            target_ra_hours=target_ra_hours,
            target_dec_deg=target_dec_deg,
            astrometry_job_reference=astrometry_job_reference,
        )
        solve_path = self.current_image_path
        self.solve_request_id += 1
        request_id = self.solve_request_id

        self.solve_worker.moveToThread(self.solve_thread)
        self.solve_thread.started.connect(self.solve_worker.run)

        # These lambdas run in the worker thread, but they only emit bridge
        # signals. The bridge slots above are explicitly queued to the GUI
        # thread before touching any widgets or opening dialogs.
        self.solve_worker.progress.connect(
            lambda message, rid=request_id, path=solve_path:
                self.solve_progress_ui.emit(message, rid, path)
        )
        self.solve_worker.succeeded.connect(
            lambda solution, rid=request_id, path=solve_path:
                self.solve_success_ui.emit(solution, rid, path)
        )
        self.solve_worker.failed.connect(
            lambda message, rid=request_id, path=solve_path:
                self.solve_failure_ui.emit(message, rid, path)
        )
        self.solve_worker.cancelled.connect(
            lambda rid=request_id, path=solve_path:
                self.solve_cancelled_ui.emit(rid, path)
        )
        self.solve_worker.log.connect(
            lambda message, rid=request_id, path=solve_path:
                self.solve_log_ui.emit(message, rid, path)
        )
        self.solve_worker.finished.connect(self.solve_thread.quit)
        self.solve_worker.finished.connect(self.solve_worker.deleteLater)
        self.solve_thread.finished.connect(self.solve_thread.deleteLater)
        self.solve_thread.finished.connect(self._solve_finished)
        self.solve_thread.start()

    def cancel_solve(self) -> None:
        if self.solve_worker is None or not self.solve_in_progress:
            return
        self._append_solver_log("Cancellation requested")
        self.solve_button.setText("Cancelling…")
        self.solve_button.setEnabled(False)
        self.solve_status.setText("●  Cancelling plate solve…")
        self.solve_worker.cancel()

    @staticmethod
    def _normalise_identifier(identifier: str) -> str:
        return " ".join(identifier.replace("_", " ").split()).strip()

    @staticmethod
    def _target_lookup_candidates(target: str) -> list[str]:
        clean = " ".join(target.replace("_", " ").split()).strip()
        candidates = [clean]
        match = re.fullmatch(r"(?i)(?:C|CALDWELL)\s*[- ]?\s*(\d{1,3})", clean)
        if match:
            number = int(match.group(1))
            caldwell = {
                14: "NGC 869", 20: "NGC 7000", 33: "NGC 6992",
                49: "NGC 2237", 64: "NGC 2362", 76: "NGC 6231",
                80: "NGC 5139", 92: "NGC 3372",
                99: "Coalsack Nebula", 103: "NGC 2070",
            }
            if number in caldwell:
                candidates.insert(1, caldwell[number])
            candidates.extend([f"Caldwell {number}", f"Caldwell{number}", f"C {number}"])
        seen=set(); result=[]
        for candidate in candidates:
            key=candidate.casefold()
            if key not in seen:
                seen.add(key); result.append(candidate)
        return result

    def _resolve_target_coordinates(self, target: str) -> tuple[SkyCoord, str]:
        errors=[]
        for candidate in self._target_lookup_candidates(target):
            try:
                return SkyCoord.from_name(candidate), candidate
            except Exception as exc:
                errors.append(str(exc))
        raise ValueError("No recognised catalogue or common-name match was found.")

    def _lookup_target_aliases(
        self, target: str
    ) -> tuple[str | None, str | None, list[str]]:
        """Best-effort SIMBAD lookup for the main identifier and useful aliases.

        Coordinate resolution remains authoritative. Alias lookup is optional:
        a network or catalogue failure must never prevent adding a solving hint.
        """
        safe_target = " ".join(target.replace("\r", " ").replace("\n", " ").split())
        if not safe_target:
            return None, []

        script = (
            "output console=off script=off\n"
            'format object "MAIN=%MAIN_ID\\n%IDLIST[ID=%*(S)\\n]"\n'
            f"query id {safe_target}"
        )

        try:
            response = requests.get(
                "https://simbad.cds.unistra.fr/simbad/sim-script",
                params={"script": script},
                timeout=8,
            )
            response.raise_for_status()
        except requests.RequestException:
            return None, []

        text = response.text
        if "::data::" in text:
            text = text.split("::data::", 1)[1]

        main_id: str | None = None
        object_type: str | None = None
        identifiers: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line.startswith("MAIN="):
                value = self._normalise_identifier(line[5:])
                if value:
                    main_id = value
            elif line.startswith("TYPE="):
                value = self._normalise_identifier(line[5:])
                if value:
                    object_type = value
            elif line.startswith("ID="):
                value = self._normalise_identifier(line[3:])
                if value:
                    identifiers.append(value)

        def display_form(identifier: str) -> str:
            if identifier.upper().startswith("NAME "):
                return identifier[5:].strip()
            return identifier

        input_key = re.sub(r"[^A-Z0-9]", "", safe_target.upper())
        seen: set[str] = set()
        candidates: list[str] = []

        # Common names first, then familiar catalogue designations.
        preferred_prefixes = (
            "NAME ", "M ", "NGC ", "IC ", "RCW ", "GUM ", "SH 2-",
            "SH2-", "CED ", "LBN ", "LDN ", "C ", "BARNARD ",
        )
        ordered = sorted(
            identifiers,
            key=lambda item: (
                next(
                    (
                        index
                        for index, prefix in enumerate(preferred_prefixes)
                        if item.upper().startswith(prefix)
                    ),
                    len(preferred_prefixes),
                ),
                len(item),
            ),
        )

        for identifier in ordered:
            shown = display_form(identifier)
            key = re.sub(r"[^A-Z0-9]", "", shown.upper())
            if not key or key == input_key or key in seen:
                continue
            seen.add(key)
            candidates.append(shown)
            if len(candidates) >= 5:
                break

        return main_id, object_type, candidates

    @staticmethod
    def _split_target_hint(text: str) -> list[str]:
        """Split a compound target hint without confusing numeric RA, Dec."""
        clean = " ".join(text.split()).strip()
        if not clean:
            return []

        # A pair of numeric values separated by one comma remains a coordinate
        # entry. Everything else may be a list of target names.
        if "," in clean:
            left, right, *extra = [part.strip() for part in clean.split(",")]
            if not extra:
                try:
                    float(left)
                    float(right)
                    return [clean]
                except ValueError:
                    pass

        parts = re.split(r"\s*(?:\+|/|&|\band\b|;)\s*", clean, flags=re.I)
        if len(parts) == 1 and "," in clean:
            parts = [part.strip() for part in clean.split(",")]
        return [part for part in parts if part]

    @staticmethod
    def _midpoint_coordinate(coordinates: list[SkyCoord]) -> SkyCoord:
        """Return the spherical centre of one or more ICRS coordinates."""
        if not coordinates:
            raise ValueError("No target coordinates were supplied.")
        if len(coordinates) == 1:
            return coordinates[0].icrs

        x = sum(coord.icrs.cartesian.x.value for coord in coordinates)
        y = sum(coord.icrs.cartesian.y.value for coord in coordinates)
        z = sum(coord.icrs.cartesian.z.value for coord in coordinates)
        length = (x * x + y * y + z * z) ** 0.5
        if length == 0:
            raise ValueError("The selected targets do not define a unique midpoint.")
        ra_deg = math.degrees(math.atan2(y, x)) % 360.0
        dec_deg = math.degrees(math.asin(z / length))
        return SkyCoord(ra=ra_deg, dec=dec_deg, unit=("deg", "deg"), frame="icrs")

    def _resolve_target_entry(self, text: str) -> tuple[SkyCoord, list[dict[str, object]]]:
        """Resolve a single coordinate entry or one/more catalogue targets."""
        parts = self._split_target_hint(text)
        if not parts:
            raise ValueError("No target was entered.")

        if len(parts) == 1 and "," in parts[0]:
            left, right = [value.strip() for value in parts[0].split(",", 1)]
            try:
                ra_hours = float(left)
                dec_deg = float(right)
            except ValueError:
                pass
            else:
                if not (0 <= ra_hours < 24 and -90 <= dec_deg <= 90):
                    raise ValueError("Coordinates are outside the valid range.")
                coordinate = SkyCoord(
                    ra=ra_hours, dec=dec_deg, unit=("hourangle", "deg")
                )
                return coordinate, [{
                    "input": "Entered coordinates",
                    "resolved": "Entered coordinates",
                    "coordinate": coordinate,
                    "main_identifier": None,
                    "object_type": None,
                    "alternate_names": [],
                }]

        targets: list[dict[str, object]] = []
        coordinates: list[SkyCoord] = []
        for part in parts:
            coordinate, resolved_query = self._resolve_target_coordinates(part)
            main_identifier, object_type, alternate_names = (
                self._lookup_target_aliases(resolved_query)
            )
            coordinates.append(coordinate)
            targets.append({
                "input": part,
                "resolved": resolved_query,
                "coordinate": coordinate,
                "main_identifier": main_identifier,
                "object_type": object_type,
                "alternate_names": alternate_names,
            })

        return self._midpoint_coordinate(coordinates), targets

    def target_assisted_solve(self) -> None:
        if not self.current_image_path:
            return

        while True:
            hint, accepted = QInputDialog.getText(
                self,
                "Identify Target",
                "Enter one target, or several targets that share the frame.\n\n"
                "Examples: NGC 2070, C80, Omega Centauri, M8 and M20",
            )
            if not accepted or not hint.strip():
                return

            text = hint.strip()
            try:
                self.solve_status.setObjectName("solvingStatus")
                self.solve_status.setText("●  Looking up target…")
                centre, targets = self._resolve_target_entry(text)
                ra_hours = float(centre.ra.hour)
                dec_deg = float(centre.dec.deg)
            except Exception as exc:
                QMessageBox.warning(
                    self, "Target could not be resolved",
                    f"AstroFrame could not resolve '{text}'.\n\n"
                    "Try another catalogue designation or common name, a compound "
                    "entry such as M8 and M20, or decimal RA hours and Dec degrees "
                    "such as 5.6453, -69.1.\n\n"
                    f"Details: {exc}",
                )
                self._show_estimated_status()
                continue

            ra_label = centre.ra.to_string(
                unit="hour", sep=("h ", "m ", "s"), precision=1, pad=True
            )
            dec_label = centre.dec.to_string(
                unit="deg", sep=("° ", "′ ", "″"), precision=0,
                alwayssign=True, pad=True,
            )

            recognised_blocks: list[str] = []
            display_names: list[str] = []
            for target in targets:
                entered = str(target["input"])
                main = target.get("main_identifier")
                object_type = target.get("object_type")
                aliases = list(target.get("alternate_names") or [])
                primary = (
                    self._normalise_identifier(str(main)) if main else entered
                )
                display_names.append(primary)
                lines = [f"<b>{primary}</b>"]
                if primary.casefold() != entered.casefold():
                    lines.insert(0, f"<b>{entered}</b>")
                if object_type:
                    lines.append(f"<i>{object_type}</i>")
                if aliases:
                    lines.append("Also known as: " + " · ".join(aliases[:4]))
                recognised_blocks.append("<br>".join(lines))

            confirmation = QMessageBox(self)
            confirmation.setWindowTitle(
                "Targets recognised" if len(targets) > 1 else "Target recognised"
            )
            confirmation.setIcon(QMessageBox.Icon.Information)
            confirmation.setText("<br><br>".join(recognised_blocks))
            centre_wording = (
                "Midpoint used for solving" if len(targets) > 1
                else "Centre used for solving"
            )
            confirmation.setInformativeText(
                f"{centre_wording}\n\nRA   {ra_label}\nDec  {dec_label}\n\n"
                "AstroFrame will use these coordinates to assist the local plate solve."
            )
            solve_button = confirmation.addButton(
                "Plate Solve", QMessageBox.ButtonRole.AcceptRole
            )
            solve_button.setObjectName("primaryButton")
            solve_button.style().unpolish(solve_button)
            solve_button.style().polish(solve_button)
            choose_button = confirmation.addButton(
                "Choose Different Target" if len(targets) == 1 else "Choose Different Targets",
                QMessageBox.ButtonRole.ActionRole,
            )
            confirmation.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            confirmation.setDefaultButton(solve_button)
            solve_button.setAutoDefault(True)
            solve_button.setDefault(True)
            confirmation.exec()

            clicked = confirmation.clickedButton()
            if clicked is solve_button:
                shown_name = " + ".join(display_names)
                self.solving_hint_name = shown_name
                self.solving_hint_ra_hours = ra_hours
                self.solving_hint_dec_deg = dec_deg
                self.solving_hint_label.setText(f"Target\n{shown_name}")
                self.summary_target.setText(f"Target\n{shown_name}")
                self.setWindowTitle(f"AstroFrame — {shown_name}")
                self.assisted_solve_button.setText("Change Target…")
                self._append_solver_log(
                    f"Target identified: {shown_name}; "
                    f"RA {ra_hours:.8f} h, Dec {dec_deg:+.8f}°"
                )
                self.plate_solve(ra_hours, dec_deg)
                return
            if clicked is choose_button:
                continue
            self._show_estimated_status()
            return

    def _refresh_api_key_button(self) -> None:
        has_key = bool(
            str(self.settings.value("astrometryApiKey", "")).strip()
        )
        if has_key:
            self.api_key_button.setText("Change Astrometry.net API key…")
            self.api_key_button.setToolTip(
                "An Astrometry.net API key is already saved on this Mac."
            )
        else:
            self.api_key_button.setText("Set Astrometry.net API key…")
            self.api_key_button.setToolTip(
                "Required only when AstroFrame needs the online solver."
            )

    def set_api_key(self) -> bool:
        current = str(self.settings.value("astrometryApiKey", "")).strip()
        key, accepted = QInputDialog.getText(
            self,
            "Astrometry.net API key",
            "Paste your Astrometry.net API key. It is stored only in this Mac's user settings:",
            QLineEdit.EchoMode.Password,
            current,
        )
        if not accepted or not key.strip():
            return False
        self.settings.setValue("astrometryApiKey", key.strip())
        self.settings.sync()
        self._refresh_api_key_button()
        self._append_solver_log("Astrometry.net API key saved for this Mac")
        return True

    def _solver_preference_changed(self) -> None:
        preference = str(self.solver_combo.currentData())
        self.settings.setValue("solverPreference", preference)
        self._append_solver_log(f"Solver preference changed to: {preference}")

    def _append_solver_log(self, message: str) -> None:
        from datetime import datetime
        stamp = datetime.now().strftime("%H:%M:%S")
        self.solver_log.appendPlainText(f"[{stamp}] {message}")

    def _copy_solver_log(self) -> None:
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.solver_log.toPlainText())

    @Slot(str, int, str)
    def _solve_log_for_request(
        self, message: str, request_id: int, image_path: str
    ) -> None:
        if self._request_is_current(request_id, image_path):
            self._append_solver_log(message)

    def clear_solution(self) -> None:
        if not self.current_image_path:
            return
        answer = QMessageBox.question(
            self,
            "Forget verified solution?",
            "This removes the cached plate solution for this image. "
            "You can solve it again at any time.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        # "Forget verified solution" clears AstroFrame's displayed/cached WCS,
        # but it must never erase knowledge that this exact file was already
        # submitted to Astrometry.net.
        if (
            self.current_solution is not None
            and self.current_solution.solver == "Astrometry.net"
            and self.current_solution.job_id is not None
        ):
            self.astrometry_submission_cache.save(
                self.current_image_path,
                job_id=self.current_solution.job_id,
                status="success",
            )

        self.solve_cache.remove(self.current_image_path)
        self.solve_in_progress = False
        self.current_solution = None
        self.image_summary.hide()
        if hasattr(self, "advisor_section"):
            self.advisor_section.hide()
        self.width_spin.setEnabled(True)
        self.solve_button.setText("Plate Solve")
        self.solve_button.setToolTip("")
        self.solve_button.setEnabled(True)
        self.assisted_solve_button.setEnabled(True)
        self.clear_solution_button.hide()
        self.solve_details.hide()
        self._show_estimated_status()

    def closeEvent(self, event) -> None:
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("referenceWidth", self.width_spin.value())
        self.settings.setValue("solverPreference", str(self.solver_combo.currentData()))
        self.settings.setValue(
            "selectedRigs",
            [key for key, check in self.rig_checks.items() if check.isChecked()],
        )
        super().closeEvent(event)

    def _on_image_loaded(self, path: str, width: int, height: int) -> None:
        # Any in-flight subject result belongs to the previous image.
        self.subject_request_id += 1
        self.current_image_path = path
        self.current_image_size = (width, height)
        self.file_label.setText(f"{Path(path).name}\n{width} × {height} px")
        self._append_solver_log(f"Image loaded: {Path(path).name} ({width} × {height})")
        for rig in self.available_rigs:
            check = self.rig_checks.get(rig.key)
            if check is not None and check.isChecked():
                self.viewer.set_rig_visible(rig, True)

        cached = self.solve_cache.load(path)
        if cached:
            self._append_solver_log(
                "AstroFrame: identical image recognised; existing solution restored from local cache. "
                "No solver contacted."
            )
            if cached.solver == "Astrometry.net" and cached.job_id is not None:
                self.astrometry_submission_cache.save(
                    path,
                    job_id=cached.job_id,
                    status="success",
                )
            self._apply_solution(cached, cached=True)
        else:
            self.solve_in_progress = False
            self.current_solution = None
            self.width_spin.setEnabled(True)
            self.solve_button.setText("Plate Solve")
            self.solve_button.setToolTip("")
            self.solve_button.setEnabled(True)
            self.assisted_solve_button.setEnabled(True)
            self.clear_solution_button.hide()
            self.solve_details.clear()
            self.solve_details.hide()
            self._show_estimated_status()

    def _show_estimated_status(self) -> None:
        self.solve_status.setObjectName("estimatedStatus")
        self.solve_status.setText(
            "⚠  Current overlays are approximate. "
            "Plate solve for accurate framing."
        )
        self.solve_status.style().unpolish(self.solve_status)
        self.solve_status.style().polish(self.solve_status)

    def _request_is_current(self, request_id: int, image_path: str) -> bool:
        return (
            request_id == self.solve_request_id
            and image_path == self.current_image_path
        )

    @Slot(str, int, str)
    def _solve_progress_for_request(
        self, message: str, request_id: int, image_path: str
    ) -> None:
        if self._request_is_current(request_id, image_path):
            self._solve_progress(message)

    @Slot(object, int, str)
    def _solve_succeeded_for_request(
        self, solution: PlateSolution, request_id: int, image_path: str
    ) -> None:
        if not self._request_is_current(request_id, image_path):
            return
        self._solve_succeeded(solution)

    @Slot(str, int, str)
    def _solve_failed_for_request(
        self, message: str, request_id: int, image_path: str
    ) -> None:
        if not self._request_is_current(request_id, image_path):
            return
        self._solve_failed(message)

    @Slot(int, str)
    def _solve_cancelled_for_request(
        self, request_id: int, image_path: str
    ) -> None:
        if not self._request_is_current(request_id, image_path):
            return
        self.solve_in_progress = False
        self.current_solution = None
        self.solve_details.clear()
        self.solve_details.hide()
        self.solve_button.setText("Plate Solve")
        self.solve_button.setToolTip("")
        self.solve_button.setEnabled(self.current_image_path is not None)
        self.assisted_solve_button.setEnabled(self.current_image_path is not None)
        self.width_spin.setEnabled(True)
        self._show_estimated_status()

    def _solve_progress(self, message: str) -> None:
        if self.current_solution is not None:
            return
        self.solve_status.setText(f"●  {message}")

    def _solve_succeeded(self, solution: PlateSolution) -> None:
        if self.current_image_path:
            self.solve_cache.save(self.current_image_path, solution)
            self._append_solver_log(
                "AstroFrame: solution saved by image fingerprint for automatic reuse."
            )
            if solution.solver == "Astrometry.net" and solution.job_id is not None:
                self.astrometry_submission_cache.save(
                    self.current_image_path,
                    job_id=solution.job_id,
                    status="success",
                )
        self._apply_solution(solution, cached=False)

    SUBJECT_IDENTIFICATION_VERSION = 3

    @staticmethod
    def _subject_identifier_priority(identifier: str) -> tuple[int, str]:
        """Return an astrophotography-oriented priority and display form.

        SIMBAD is an astronomical database, not a photography catalogue.  A
        tiny survey galaxy can be closer to the solved centre than the object
        the photographer actually framed.  Prefer familiar deep-sky
        designations while still allowing genuinely central unusual objects.
        """
        shown = MainWindow._normalise_identifier(identifier)
        upper = shown.upper()
        if upper.startswith("NAME "):
            return 86, shown[5:].strip()

        patterns = (
            (r"^M\s*\d+\b", 100),
            (r"^NGC\s*\d+\b", 96),
            (r"^IC\s*\d+\b", 94),
            (r"^(?:SH\s*2-|SH2-)\s*\d+\b", 90),
            (r"^RCW\s*\d+\b", 88),
            (r"^GUM\s*\d+\b", 86),
            (r"^(?:BARNARD|B)\s*\d+\b", 84),
            (r"^LDN\s*\d+\b", 82),
            (r"^LBN\s*\d+\b", 82),
            (r"^ABELL\s*\d+\b", 80),
            (r"^PK\s*[-+0-9.]+", 76),
            (r"^CED\s*", 74),
            (r"^ESO\s*", 62),
            (r"^PGC\s*", 35),
            (r"^2MASX\s*", 8),
            (r"^GAIA\s*", 0),
        )
        for pattern, score in patterns:
            if re.match(pattern, upper):
                return score, shown
        return 45, shown

    @staticmethod
    def _subject_type_priority(object_type: str | None) -> int:
        if not object_type:
            return 0
        value = object_type.upper().replace(" ", "")
        # Large/recognisable deep-sky subjects.
        if any(token in value for token in ("PLANETARYNEBULA", "PN", "HII", "NEBULA", "RNE", "EMN")):
            return 55
        if any(token in value for token in ("SUPERNOVAREMNANT", "SNR")):
            return 52
        if any(token in value for token in ("OPENCLUSTER", "GLOBULARCLUSTER", "CL*", "OPC", "GLC")):
            return 48
        if any(token in value for token in ("GALAXY", "GPAIR", "GGROUP")) or value in {"G", "GINPAIR", "GINGROUP"}:
            return 34
        # Individual stars and survey detections are very rarely the intended
        # subject of a deep-sky reference image.
        if value in {"*", "STAR", "PM*", "HB*", "RG*", "WD*", "V*"} or value.endswith("*"):
            return -45
        if any(token in value for token in ("IR", "RAD", "XRAY", "SOURCE", "CANDIDATE")):
            return -20
        return 8

    @classmethod
    def _best_subject_identifier(cls, identifiers: list[str], main_id: str) -> tuple[int, str]:
        choices = identifiers[:] if identifiers else []
        if main_id:
            choices.append(main_id)
        best_score = -10_000
        best_name = cls._normalise_identifier(main_id) if main_id else ""
        seen: set[str] = set()
        for identifier in choices:
            key = identifier.casefold()
            if key in seen:
                continue
            seen.add(key)
            score, shown = cls._subject_identifier_priority(identifier)
            if score > best_score:
                best_score, best_name = score, shown
        return best_score, best_name

    def _identify_target_from_solution(self, solution: PlateSolution) -> dict[str, object] | None:
        """Identify the likely photographic subject, not merely the nearest row.

        SIMBAD coordinate results are ordered by increasing distance.  We use
        that ordering as one signal, but combine it with catalogue familiarity
        and object type so anonymous survey sources do not displace a clearly
        framed Messier/NGC/IC/nebula/cluster subject.
        """
        centre = SkyCoord(
            ra=solution.ra_deg,
            dec=solution.dec_deg,
            unit=("deg", "deg"),
            frame="icrs",
        )
        try:
            constellation = get_constellation(centre, short_name=False)
        except Exception:
            constellation = None

        half_diagonal_deg = 0.5 * math.hypot(
            max(float(solution.image_width_deg), 0.01),
            max(float(solution.image_height_deg), 0.01),
        )
        # The intended subject is normally near the composition centre, but a
        # wide reference may place it noticeably off-centre.  Avoid enormous
        # SIMBAD queries while searching enough of the useful central field.
        radius_arcmin = max(10.0, min(30.0, half_diagonal_deg * 60.0 * 0.75))

        ra_text = centre.ra.to_string(unit="hour", sep=" ", precision=4, pad=True)
        dec_text = centre.dec.to_string(
            unit="deg", sep=" ", precision=3, alwayssign=True, pad=True
        )
        script = (
            "output console=off script=off\n"
            "set limit 50\n"
            'format object "BEGIN\\nMAIN=%MAIN_ID\\nTYPE=%OTYPE\\n%IDLIST[ID=%*(S)\\n]END\\n"\n'
            f"query coo {ra_text} {dec_text} radius={radius_arcmin:.2f}m frame=ICRS equi=2000\n"
        )
        try:
            response = requests.get(
                "https://simbad.cds.unistra.fr/simbad/sim-script",
                params={"script": script},
                timeout=4,
            )
            response.raise_for_status()
            text = response.text.split("::data::", 1)[-1]
        except requests.RequestException:
            return {"constellation": constellation} if constellation else None

        candidates: list[dict[str, object]] = []
        current: dict[str, object] | None = None
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line == "BEGIN":
                current = {"ids": []}
                continue
            if line == "END":
                if current and current.get("main"):
                    candidates.append(current)
                current = None
                continue
            if current is None:
                continue
            if line.startswith("MAIN="):
                current["main"] = self._normalise_identifier(line[5:])
            elif line.startswith("TYPE="):
                current["type"] = self._normalise_identifier(line[5:])
            elif line.startswith("ID="):
                value = self._normalise_identifier(line[3:])
                if value:
                    cast_ids = current.setdefault("ids", [])
                    if isinstance(cast_ids, list):
                        cast_ids.append(value)

        if not candidates:
            return {"constellation": constellation} if constellation else None

        scored: list[tuple[float, dict[str, object], str]] = []
        for rank, candidate in enumerate(candidates):
            main_id = str(candidate.get("main") or "")
            identifiers = [str(item) for item in candidate.get("ids", []) if item]
            catalogue_score, display_name = self._best_subject_identifier(identifiers, main_id)
            type_score = self._subject_type_priority(
                str(candidate.get("type")) if candidate.get("type") else None
            )
            # SIMBAD sorts coordinate results by increasing angular distance.
            # Centre proximity matters, but should not overwhelm a strong,
            # familiar deep-sky designation a few rows farther away.
            centre_score = max(0.0, 42.0 - rank * 1.4)
            total = float(catalogue_score + type_score) + centre_score

            upper_name = display_name.upper()
            # Strongly demote survey identifiers unless nothing better exists.
            if upper_name.startswith(("2MASX ", "GAIA ")):
                total -= 30.0
            scored.append((total, candidate, display_name))

        scored.sort(key=lambda item: item[0], reverse=True)
        _, best, display_name = scored[0]
        if not display_name:
            return {"constellation": constellation} if constellation else None

        result: dict[str, object] = {
            "name": display_name,
            "identification_source": "automatic",
            "identification_version": self.SUBJECT_IDENTIFICATION_VERSION,
        }
        object_type = best.get("type")
        if object_type:
            result["object_type"] = str(object_type)
        if constellation:
            result["constellation"] = constellation
        return result

    def _cached_target_for_current_image(self) -> dict[str, object] | None:
        if not self.current_image_path:
            return None
        record = self.solve_cache.load_record(self.current_image_path)
        target = record.get("target") if record else None
        return target if isinstance(target, dict) else None

    def _target_needs_identification(self, target_info: dict[str, object] | None) -> bool:
        if not target_info or not target_info.get("name"):
            return True
        source = target_info.get("identification_source")
        if source == "user_hint":
            return False
        version = int(target_info.get("identification_version") or 0)
        # Old automatic/legacy identifications are deliberately re-run when
        # the subject-identification algorithm changes.
        return source != "automatic" or version < self.SUBJECT_IDENTIFICATION_VERSION

    def _show_target_summary(self, target_info: dict[str, object] | None) -> None:
        target_name = (target_info or {}).get("name")
        if not target_name:
            self.summary_target.setText("Target\nNo named object identified")
            return
        lines = ["Target", str(target_name)]
        object_type = (target_info or {}).get("object_type")
        constellation = (target_info or {}).get("constellation")
        if object_type:
            lines.append(str(object_type))
        if constellation:
            lines.append(str(constellation))
        self.summary_target.setText("\n".join(lines))
        self.setWindowTitle(f"AstroFrame — {target_name}")

    def _start_subject_identification(
        self, expected_path: str, expected_solution: PlateSolution
    ) -> None:
        """Start a bounded subject lookup without blocking the interface."""
        if (
            self.current_image_path != expected_path
            or self.current_solution is not expected_solution
        ):
            return

        self.subject_request_id += 1
        request_id = self.subject_request_id
        self.summary_target.setText("Target\nIdentifying…")
        self._append_solver_log(
            "Subject identification: checking likely deep-sky subject in background"
        )

        thread = QThread(self)
        worker = SubjectIdentifyWorker(
            self._identify_target_from_solution,
            expected_solution,
            request_id,
            expected_path,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(
            self._subject_identification_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        worker.failed.connect(
            self._subject_identification_failed,
            Qt.ConnectionType.QueuedConnection,
        )
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(
            lambda rid=request_id: self.subject_threads.pop(rid, None)
        )
        self.subject_threads[request_id] = (thread, worker)
        thread.start()

    def _subject_request_is_current(
        self, request_id: int, image_path: str, solution: PlateSolution
    ) -> bool:
        return (
            request_id == self.subject_request_id
            and image_path == self.current_image_path
            and solution is self.current_solution
        )

    @Slot(object, int, str, object)
    def _subject_identification_finished(
        self,
        target_info: dict[str, object] | None,
        request_id: int,
        image_path: str,
        solution: PlateSolution,
    ) -> None:
        if not self._subject_request_is_current(
            request_id, image_path, solution
        ):
            return

        if target_info and target_info.get("name"):
            self.solve_cache.update_metadata(image_path, target=target_info)
            self._show_target_summary(target_info)
            self._append_solver_log(
                f"Subject identification: {target_info.get('name')} selected as primary subject"
            )
            return

        if target_info:
            self.solve_cache.update_metadata(image_path, target=target_info)
        self.summary_target.setText("Target\nNo named object identified")
        self._append_solver_log(
            "Subject identification: no plausible primary subject found"
        )

    @Slot(str, int, str, object)
    def _subject_identification_failed(
        self,
        message: str,
        request_id: int,
        image_path: str,
        solution: PlateSolution,
    ) -> None:
        if not self._subject_request_is_current(
            request_id, image_path, solution
        ):
            return
        self.summary_target.setText("Target\nIdentification unavailable")
        self._append_solver_log(
            f"Subject identification unavailable: {message}"
        )

    def _apply_solution(self, solution: PlateSolution, *, cached: bool) -> None:
        self.current_solution = solution
        self.width_spin.blockSignals(True)
        self.width_spin.setValue(solution.image_width_deg)
        self.width_spin.blockSignals(False)
        self.viewer.set_reference_width(solution.image_width_deg)
        self.width_spin.setEnabled(False)

        self.solve_status.setObjectName("verifiedStatus")
        source = f"AstroFrame cache — originally {solution.solver}" if cached else solution.solver
        self.solve_status.setText(f"●  Verified — {source}")
        self.solve_status.style().unpolish(self.solve_status)
        self.solve_status.style().polish(self.solve_status)
        self.solve_details.setText(
            f"Centre: RA {solution.ra_deg:.5f}°, Dec {solution.dec_deg:+.5f}°\n"
            f"Scale: {solution.pixel_scale_arcsec:.3f} arcsec/px\n"
            f"Image: {solution.image_width_deg:.3f}° × {solution.image_height_deg:.3f}°\n"
            f"Orientation: {solution.orientation_deg:.2f}°\n"
            f"Mode: {solution.solve_mode}"
            + (f"\nSolve time: {solution.solve_seconds:.1f} s" if solution.solve_seconds is not None else "")
        )
        self.solve_details.show()

        target_info = self._cached_target_for_current_image() if cached else None
        identify_after_render = False
        if self.solving_hint_name:
            target_info = {
                "name": self.solving_hint_name,
                "identification_source": "user_hint",
                "identification_version": self.SUBJECT_IDENTIFICATION_VERSION,
            }
            if self.current_image_path:
                self.solve_cache.update_metadata(self.current_image_path, target=target_info)
        elif self._target_needs_identification(target_info):
            identify_after_render = True
            self.summary_target.setText("Target\nIdentifying…")
        else:
            self._show_target_summary(target_info)

        centre = SkyCoord(
            ra=solution.ra_deg,
            dec=solution.dec_deg,
            unit=("deg", "deg"),
        )
        ra_label = centre.ra.to_string(
            unit="hour", sep=("h ", "m ", "s"), precision=1, pad=True
        )
        dec_label = centre.dec.to_string(
            unit="deg",
            sep=("° ", "′ ", "″"),
            precision=0,
            alwayssign=True,
            pad=True,
        )
        self.summary_centre.setText(
            "Centre\n"
            f"RA  {ra_label}\n"
            f"Dec {dec_label}"
        )
        self.summary_field.setText(
            "Field of view\n"
            f"{solution.image_width_deg:.3f}° × "
            f"{solution.image_height_deg:.3f}°"
        )
        self.summary_scale.setText(
            "Image scale\n"
            f"{solution.pixel_scale_arcsec:.3f} arcsec/pixel"
        )
        self.summary_rotation.setText(
            "Rotation\n"
            f"{solution.orientation_deg:.2f}°"
        )

        solve_source = solution.solver
        if cached:
            solve_source = f"AstroFrame cache · {solution.solver}"
        solve_time = (
            f"\nSolved in {solution.solve_seconds:.1f} s"
            if solution.solve_seconds is not None and not cached
            else ""
        )
        self.summary_solver.setText(
            "Solved with\n"
            f"{solve_source}"
            f"{solve_time}"
        )
        self._update_visibility_summary(solution)
        self._update_equipment_advisor()
        self.image_summary.show()

        if target_info and target_info.get("name") and not identify_after_render:
            self._show_target_summary(target_info)

        self.solve_in_progress = False
        self.solve_button.setText("Solve Again")
        self.solve_button.setToolTip(
            "Run a fresh plate solve for this image."
        )
        self.solve_button.setEnabled(True)
        self.assisted_solve_button.setEnabled(True)
        self.clear_solution_button.show()

        if identify_after_render and self.current_image_path:
            expected_path = self.current_image_path
            # Run target identification independently of the solver.  The
            # network/catalogue lookup is bounded and never blocks the GUI.
            QTimer.singleShot(
                0,
                lambda p=expected_path, sol=solution:
                    self._start_subject_identification(p, sol),
            )

    def _solve_failed(self, message: str) -> None:
        self.solve_in_progress = False
        self.solve_button.setText("Plate Solve")
        self.solve_button.setToolTip("")
        self.solve_button.setEnabled(self.current_image_path is not None)
        self.solve_status.setObjectName("failedStatus")
        self.solve_status.setText("●  Plate solve failed")
        self.solve_status.style().unpolish(self.solve_status)
        self.solve_status.style().polish(self.solve_status)
        self.solve_details.setText(message)
        self.solve_details.show()
        self.assisted_solve_button.setEnabled(True)
        # Keep failures in the Reference Image panel. A modal dialog here
        # is disruptive and, on macOS, unsafe if ever called off-thread.

    def _solve_finished(self) -> None:
        # A finished worker may belong to an image that has since been
        # replaced. The current image's state is managed by _on_image_loaded.
        if self.current_solution is None and self.solve_in_progress:
            self.solve_in_progress = False
            self.solve_button.setText("Plate Solve")
            self.solve_button.setToolTip("")
            self.solve_button.setEnabled(self.current_image_path is not None)
        self.solve_thread = None
        self.solve_worker = None
        self.astrometry_job_button.setEnabled(bool(self.current_image_path))

    def _reference_width_changed(self, value: float) -> None:
        if self.current_solution is None:
            self.viewer.set_reference_width(value)
            self.settings.setValue("referenceWidth", value)

    def _rotation_changed(self, value: int) -> None:
        degrees = value / 10.0
        self.rotation_value.setText(f"{degrees:.1f}°")
        self.viewer.set_rotation(degrees)

    def _reset_framing(self) -> None:
        self.rotation.setValue(0)
        self.viewer.clear_rig_rotations()
        self.viewer.set_rotation(0.0)
        self.viewer.centre_overlays()
        if self.current_solution is not None:
            self._update_equipment_advisor()
        if self.current_solution is None:
            self.width_spin.setValue(DEFAULT_REFERENCE_WIDTH)
            self.viewer.set_reference_width(DEFAULT_REFERENCE_WIDTH)

    def _restore_settings(self) -> None:
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)

        selected = self.settings.value("selectedRigs", [])
        if isinstance(selected, str):
            selected = [selected]
        for key in selected:
            if key in self.rig_checks:
                self.rig_checks[key].setChecked(True)
