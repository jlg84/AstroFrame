from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, QThread, Qt, Signal, Slot
from astropy.coordinates import SkyCoord
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from .equipment import RIGS
from .plate_solve import PlateSolution, SolveCache
from .solve_worker import PlateSolveWorker
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


class MainWindow(QMainWindow):
    # Worker callbacks are first relayed through these signals so every
    # widget update occurs on Qt's main GUI thread.
    solve_progress_ui = Signal(str, int, str)
    solve_success_ui = Signal(object, int, str)
    solve_failure_ui = Signal(str, int, str)
    solve_log_ui = Signal(str, int, str)

    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings("AstroFrame", "AstroFrame")
        self.solve_cache = SolveCache()
        self.current_image_path: str | None = None
        self.current_image_size = (0, 0)
        self.current_solution: PlateSolution | None = None
        self.solve_thread: QThread | None = None
        self.solve_worker: PlateSolveWorker | None = None
        self.solve_request_id = 0

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
        self.solve_log_ui.connect(
            self._solve_log_for_request,
            Qt.ConnectionType.QueuedConnection,
        )

        self.setWindowTitle("AstroFrame 0.4.2a")
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

        self.solve_button = QPushButton("Plate Solve")
        self.solve_button.setObjectName("solveButton")
        self.solve_button.setEnabled(False)
        self.solve_button.clicked.connect(lambda _checked=False: self.plate_solve())
        reference.layout.addWidget(self.solve_button)

        self.assisted_solve_button = QPushButton("Target-assisted solve…")
        self.assisted_solve_button.setEnabled(False)
        self.assisted_solve_button.setToolTip(
            "Give ASTAP an approximate target centre for difficult images."
        )
        self.assisted_solve_button.clicked.connect(self.target_assisted_solve)
        reference.layout.addWidget(self.assisted_solve_button)

        self.solver_log = QPlainTextEdit()
        self.solver_log.setReadOnly(True)
        self.solver_log.setMaximumBlockCount(1500)
        self.solver_log.setPlaceholderText(
            "Solver diagnostics will appear here."
        )
        self.solver_log.setMinimumHeight(150)
        reference.layout.addWidget(self.solver_log)

        log_buttons = QHBoxLayout()
        self.copy_log_button = QPushButton("Copy Solver Log")
        self.copy_log_button.clicked.connect(self.copy_solver_log)
        log_buttons.addWidget(self.copy_log_button)
        self.clear_log_button = QPushButton("Clear Log")
        self.clear_log_button.clicked.connect(self.solver_log.clear)
        log_buttons.addWidget(self.clear_log_button)
        reference.layout.addLayout(log_buttons)

        self.clear_solution_button = QPushButton("Forget verified solution…")
        self.clear_solution_button.hide()
        self.clear_solution_button.clicked.connect(self.clear_solution)
        reference.layout.addWidget(self.clear_solution_button)
        sidebar_layout.addWidget(reference)

        equipment = Section("Equipment")
        self.rig_checks: dict[str, RigToggle] = {}
        for rig in RIGS:
            check = RigToggle(rig)
            check.toggled.connect(lambda checked, r=rig: self.viewer.set_rig_visible(r, checked))
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
        reset_framing.setToolTip("Restore 0° rotation and centred frames.")
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
            # Invalidate any callbacks still arriving from a solve started for
            # the previously loaded image.
            self.solve_request_id += 1
            self.current_image_path = path
            self.current_solution = None
            self.solve_button.setText("Plate Solve")
            self.solve_button.setEnabled(False)
            self.assisted_solve_button.setEnabled(False)
            self.clear_solution_button.hide()
            self.solve_details.clear()
            self.solve_details.hide()
            self.solver_log.clear()
            self._append_solver_log(f"Image loaded: {Path(path).name}")
            self.width_spin.setEnabled(True)
            self._show_estimated_status()

            self.viewer.load_image(path)
            self.viewer.set_reference_width(self.width_spin.value())
            self.settings.setValue("lastImageDirectory", str(Path(path).parent))
            self.solve_button.setEnabled(True)
            self.assisted_solve_button.setEnabled(True)
            if not any(check.isChecked() for check in self.rig_checks.values()):
                self.rig_checks["asi1600_442"].setChecked(True)
        except Exception as exc:
            QMessageBox.critical(self, "Could not open image", str(exc))

    def plate_solve(self, target_ra_hours: float | None = None, target_dec_deg: float | None = None) -> None:
        if not self.current_image_path:
            return

        # ASTAP is attempted first and needs no credentials. A previously
        # stored Astrometry.net key is passed only as an automatic fallback.
        api_key = str(self.settings.value("astrometryApiKey", "")).strip()

        width_px, height_px = self.current_image_size
        if width_px <= 0 or height_px <= 0:
            QMessageBox.warning(self, "Plate solve", "The loaded image dimensions are unavailable.")
            return

        self.solve_button.setEnabled(False)
        self.assisted_solve_button.setEnabled(False)
        self.solve_status.setObjectName("solvingStatus")
        self.solve_status.setText("●  Starting plate solve…")
        self.solver_log.clear()
        self._append_solver_log(
            f"Image: {Path(self.current_image_path).name}"
        )
        self._append_solver_log(
            f"Pixels: {width_px} × {height_px}"
        )
        self._append_solver_log(
            "Solve requested: "
            + (
                "Target-assisted"
                if target_ra_hours is not None and target_dec_deg is not None
                else "Blind"
            )
        )
        self.solve_status.style().unpolish(self.solve_status)
        self.solve_status.style().polish(self.solve_status)
        self.solve_details.hide()

        self.solve_thread = QThread(self)
        self.solve_worker = PlateSolveWorker(
            api_key=api_key,
            image_path=self.current_image_path,
            image_width_px=width_px,
            image_height_px=height_px,
            estimated_width_deg=self.width_spin.value(),
            target_ra_hours=target_ra_hours,
            target_dec_deg=target_dec_deg,
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
        self.solve_worker.diagnostic.connect(
            lambda message, rid=request_id, path=solve_path:
                self.solve_log_ui.emit(message, rid, path)
        )
        self.solve_worker.succeeded.connect(
            lambda solution, rid=request_id, path=solve_path:
                self.solve_success_ui.emit(solution, rid, path)
        )
        self.solve_worker.failed.connect(
            lambda message, rid=request_id, path=solve_path:
                self.solve_failure_ui.emit(message, rid, path)
        )
        self.solve_worker.finished.connect(self.solve_thread.quit)
        self.solve_worker.finished.connect(self.solve_worker.deleteLater)
        self.solve_thread.finished.connect(self.solve_thread.deleteLater)
        self.solve_thread.finished.connect(self._solve_finished)
        self.solve_thread.start()

    def target_assisted_solve(self) -> None:
        if not self.current_image_path:
            return

        hint, accepted = QInputDialog.getText(
            self,
            "Target-assisted ASTAP solve",
            "Enter a target name (for example NGC 2070 or Omega Centauri)\n"
            "or RA hours and Dec degrees separated by a comma\n"
            "(for example 5.6453, -69.1):",
        )
        if not accepted or not hint.strip():
            return

        text = hint.strip()
        try:
            if "," in text:
                ra_text, dec_text = text.split(",", 1)
                ra_hours = float(ra_text.strip())
                dec_deg = float(dec_text.strip())
            else:
                self.solve_status.setObjectName("solvingStatus")
                self.solve_status.setText("●  Looking up target coordinates…")
                self.solve_status.style().unpolish(self.solve_status)
                self.solve_status.style().polish(self.solve_status)
                coordinates = SkyCoord.from_name(text)
                ra_hours = float(coordinates.ra.hour)
                dec_deg = float(coordinates.dec.deg)

            if not (0.0 <= ra_hours < 24.0 and -90.0 <= dec_deg <= 90.0):
                raise ValueError("Coordinates are outside the valid range.")
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Target could not be resolved",
                f"AstroFrame could not resolve '{text}'.\n\n"
                "Try decimal RA hours and Dec degrees, such as:\n"
                "5.6453, -69.1\n\n"
                f"Details: {exc}",
            )
            self._show_estimated_status()
            return

        self.plate_solve(ra_hours, dec_deg)

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
        self.solve_cache.remove(self.current_image_path)
        self.current_solution = None
        self.width_spin.setEnabled(True)
        self.solve_button.setEnabled(True)
        self.clear_solution_button.hide()
        self.solve_details.hide()
        self._show_estimated_status()

    def closeEvent(self, event) -> None:
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("referenceWidth", self.width_spin.value())
        self.settings.setValue(
            "selectedRigs",
            [key for key, check in self.rig_checks.items() if check.isChecked()],
        )
        super().closeEvent(event)

    def _on_image_loaded(self, path: str, width: int, height: int) -> None:
        self.current_image_path = path
        self.current_image_size = (width, height)
        self.file_label.setText(f"{Path(path).name}\n{width} × {height} px")
        for rig in RIGS:
            check = self.rig_checks[rig.key]
            if check.isChecked():
                self.viewer.set_rig_visible(rig, True)

        cached = self.solve_cache.load(path)
        if cached:
            self._apply_solution(cached, cached=True)
        else:
            self.current_solution = None
            self.width_spin.setEnabled(True)
            self.solve_button.setText("Plate Solve")
            self.solve_button.setEnabled(True)
            self.assisted_solve_button.setEnabled(True)
            self.clear_solution_button.hide()
            self.solve_details.clear()
            self.solve_details.hide()
            self._show_estimated_status()

    def _show_estimated_status(self) -> None:
        self.solve_status.setObjectName("estimatedStatus")
        self.solve_status.setText("●  Estimated — reference image not plate solved")
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

    @Slot(str, int, str)
    def _solve_log_for_request(
        self, message: str, request_id: int, image_path: str
    ) -> None:
        if self._request_is_current(request_id, image_path):
            self._append_solver_log(message)

    def _append_solver_log(self, message: str) -> None:
        from datetime import datetime
        stamp = datetime.now().strftime("%H:%M:%S")
        for line in str(message).splitlines() or [""]:
            self.solver_log.appendPlainText(f"{stamp}  {line}")

    def copy_solver_log(self) -> None:
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(
            self.solver_log.toPlainText()
        )
        self.statusBar().showMessage(
            "Solver log copied to clipboard", 3000
        )

    def _solve_progress(self, message: str) -> None:
        if self.current_solution is not None:
            return
        self.solve_status.setText(f"●  {message}")

    def _solve_succeeded(self, solution: PlateSolution) -> None:
        self._append_solver_log(
            f"VERIFIED by {solution.solver}; "
            f"mode {solution.solve_mode}; "
            f"time {solution.solve_seconds:.2f} s"
            if solution.solve_seconds is not None
            else f"VERIFIED by {solution.solver}"
        )
        if self.current_image_path:
            self.solve_cache.save(self.current_image_path, solution)
        self._apply_solution(solution, cached=False)

    def _apply_solution(self, solution: PlateSolution, *, cached: bool) -> None:
        self.current_solution = solution
        self.width_spin.blockSignals(True)
        self.width_spin.setValue(solution.image_width_deg)
        self.width_spin.blockSignals(False)
        self.viewer.set_reference_width(solution.image_width_deg)
        self.width_spin.setEnabled(False)

        self.solve_status.setObjectName("verifiedStatus")
        source = f"cached {solution.solver} solution" if cached else solution.solver
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
        self.solve_button.setText("Solve again")
        self.solve_button.setEnabled(True)
        self.assisted_solve_button.setEnabled(True)
        self.clear_solution_button.show()

    def _solve_failed(self, message: str) -> None:
        self._append_solver_log("Plate solve failed: " + message)
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
        if self.solve_thread is self.sender():
            self.solve_button.setEnabled(self.current_image_path is not None)
        self.solve_thread = None
        self.solve_worker = None

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
        self.viewer.set_rotation(0.0)
        self.viewer.centre_overlays()
        if self.current_solution is None:
            self.width_spin.setValue(DEFAULT_REFERENCE_WIDTH)
            self.viewer.set_reference_width(DEFAULT_REFERENCE_WIDTH)

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
