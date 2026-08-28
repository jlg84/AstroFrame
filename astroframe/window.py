from __future__ import annotations

from pathlib import Path
from datetime import date, datetime, timedelta
from dataclasses import replace

import json
import math
import re
import textwrap

import requests

from PySide6.QtCore import QObject, QDate, QSettings, QThread, QTimer, Qt, Signal, Slot, QPointF
from astropy.coordinates import SkyCoord, get_constellation
from PySide6.QtGui import QColor, QPen, QBrush, QPolygonF
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDateEdit,
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
    QProgressBar,
    QRadioButton,
    QScrollArea,
    QTextBrowser,
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
from .observer import (
    ObserverProfile,
    SeasonalGoodNightResult,
    ObservingSeasonResult,
    analyse_observing_season,
    find_next_good_nights_seasonal,
    local_observing_date,
    observability_for_date,
    tonight_bounds,
)
from .knowledge import KnowledgeStore, catalogue_identifiers, normalise_identifier
from .collection_import import (
    FLEXIBLE_FIELDS,
    count_flexible_rows,
    discover_flexible_source,
    flexible_preview_rows,
    import_flexible_collection,
    import_target_collection,
    infer_flexible_mapping,
    preview_collection_import,
    _read_flexible_table,
)
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
    # RC22h: availability and active-framing selection are deliberately
    # separate. Clicking the small checkbox toggles availability; clicking
    # the rig card text selects that already-available rig for framing.
    activated = Signal(str)

    def __init__(self, rig, parent=None) -> None:
        super().__init__(parent)
        self.rig = rig
        self.setObjectName("rigToggle")
        self.setProperty("activeRig", False)
        self.setText(f"{rig.name}\n{rig.fov_width_deg:.3f}° × {rig.fov_height_deg:.3f}°")
        # RC22e: colour identifies the rig; it no longer floods the card.
        # A restrained stripe and indicator match the frame drawn on the image.
        colour = QColor(rig.colour)
        r, g, b = colour.red(), colour.green(), colour.blue()
        selected_tint = f"rgba({r}, {g}, {b}, 38)"
        hover_tint = f"rgba({r}, {g}, {b}, 20)"
        self.setStyleSheet(
            f"QCheckBox#rigToggle {{ background: #171C23; "
            f"border: 1px solid #3A424F; border-left: 7px solid {rig.colour}; "
            f"border-radius: 9px; padding: 11px 10px; padding-left: 14px; "
            f"font-weight: 700; color: #FFFFFF; }}"
            f"QCheckBox#rigToggle:hover {{ background: {hover_tint}; "
            f"border: 1px solid #586272; border-left: 7px solid {rig.colour}; }}"
            f"QCheckBox#rigToggle:checked {{ background: #171C23; "
            f"border: 1px solid #3A424F; border-left: 7px solid {rig.colour}; }}"
            f"QCheckBox#rigToggle[activeRig=\"true\"] {{ background: {selected_tint}; "
            f"border: 2px solid {rig.colour}; border-left: 8px solid {rig.colour}; }}"
            f"QCheckBox#rigToggle::indicator {{ width: 18px; height: 18px; "
            f"border-radius: 5px; border: 2px solid {rig.colour}; background: #0F1318; }}"
            f"QCheckBox#rigToggle::indicator:checked {{ background: {rig.colour}; "
            f"border: 2px solid #FFFFFF; }}"
        )


    def setActiveRig(self, active: bool) -> None:
        self.setProperty("activeRig", bool(active))
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def mousePressEvent(self, event) -> None:
        # Keep a dedicated checkbox hit area at the left. The rest of the
        # card is a rig-selection target and must not silently toggle
        # availability.
        try:
            x = float(event.position().x())
        except Exception:
            x = 0.0
        if x > 42.0:
            if self.isChecked():
                self.activated.emit(self.rig.key)
            else:
                # An unavailable rig cannot become active. Keep the click
                # harmless and let the checkbox itself remain the explicit
                # availability control.
                QApplication.beep()
            event.accept()
            return
        super().mousePressEvent(event)


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


class ReferenceCatalogWorker(QObject):
    """Fetch a Bica reference layer without blocking the Qt GUI thread."""

    finished = Signal(object, str)
    failed = Signal(str, str)

    def __init__(self, ra_deg: float, dec_deg: float, width_deg: float, height_deg: float, mode: str) -> None:
        super().__init__()
        self.ra_deg = ra_deg
        self.dec_deg = dec_deg
        self.width_deg = width_deg
        self.height_deg = height_deg
        self.mode = mode

    @Slot()
    def run(self) -> None:
        try:
            from .reference_catalog import query_bica
            objects = query_bica(
                self.ra_deg, self.dec_deg, self.width_deg, self.height_deg, self.mode
            )
        except Exception as exc:
            self.failed.emit(str(exc), self.mode)
            return
        self.finished.emit(objects, self.mode)


class GoodNightSearchWorker(QObject):
    """Search future observing nights without blocking the Qt GUI thread."""

    progress = Signal(str, int, int, object)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, target, profile, start_date, near_term_days=45, max_days=365, limit=3) -> None:
        super().__init__()
        self.target = target
        self.profile = profile
        self.start_date = start_date
        self.near_term_days = int(near_term_days)
        self.max_days = int(max_days)
        self.limit = int(limit)

    @Slot()
    def run(self) -> None:
        try:
            result = find_next_good_nights_seasonal(
                self.target,
                self.profile,
                self.start_date,
                near_term_days=self.near_term_days,
                max_days=self.max_days,
                limit=self.limit,
                detailed_sample_minutes=5,
                progress_callback=lambda stage, current, total, d: self.progress.emit(
                    stage, current, total, d
                ),
            )
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.finished.emit(result)




class SeasonSearchWorker(QObject):
    """Analyse annual observing-season geometry without blocking the GUI."""

    progress = Signal(str, int, int, object)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, target, profile, selected_date) -> None:
        super().__init__()
        self.target = target
        self.profile = profile
        self.selected_date = selected_date

    @Slot()
    def run(self) -> None:
        try:
            result = analyse_observing_season(
                self.target,
                self.profile,
                self.selected_date,
                sample_minutes=15,
                progress_callback=lambda stage, current, total, d: self.progress.emit(
                    stage, current, total, d
                ),
            )
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.finished.emit(result)

class MainWindow(QMainWindow):
    # Worker callbacks are first relayed through these signals so every
    # widget update occurs on Qt's main GUI thread.
    solve_progress_ui = Signal(str, int, str)
    solve_success_ui = Signal(object, int, str)
    solve_failure_ui = Signal(str, int, str)
    solve_cancelled_ui = Signal(int, str)
    solve_log_ui = Signal(str, int, str)
    general_log_ui = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings("AstroFrame", "AstroFrame")
        self.solve_cache = SolveCache()
        self.astrometry_submission_cache = AstrometrySubmissionCache()
        self.knowledge_store = KnowledgeStore()
        self.current_image_path: str | None = None
        self.current_image_size = (0, 0)
        self.current_solution: PlateSolution | None = None
        self._advisor_refresh_pending = False
        # The recommendation currently being composed on the image.  Once an
        # alternative is chosen, dragging its rig frame or changing its rotation
        # continuously updates the exact celestial centre retained here.
        self._working_framing: dict[str, object] | None = None
        self._accepted_framing: dict[str, object] | None = None
        self.solve_thread: QThread | None = None
        self.solve_worker: PlateSolveWorker | None = None
        self.solve_request_id = 0
        self.solve_in_progress = False
        self._pending_assisted_solve: tuple[float, float, float, str] | None = None
        self._pending_auto_solve: tuple[str, float | None, float | None, float | None, str | None] | None = None
        self._retired_solve_jobs: list[tuple[QThread, PlateSolveWorker]] = []
        self.reference_import_thread: QThread | None = None
        self.reference_import_worker: AstroBinImportWorker | None = None
        self.subject_request_id = 0
        self.subject_threads: dict[int, tuple[QThread, SubjectIdentifyWorker]] = {}
        self.reference_catalog_thread: QThread | None = None
        self.reference_catalog_worker: ReferenceCatalogWorker | None = None
        self.good_night_thread: QThread | None = None
        self.good_night_worker: GoodNightSearchWorker | None = None
        self.season_thread: QThread | None = None
        self.season_worker: SeasonSearchWorker | None = None
        self._reference_marker_cache: dict[tuple, tuple[list[tuple[str, float, float]], int, dict[str, object]]] = {}
        self._active_reference_query_key: tuple | None = None
        self.solving_hint_name: str | None = None
        self.solving_hint_ra_hours: float | None = None
        self.solving_hint_dec_deg: float | None = None
        self.solving_hint_search_radius_deg: float | None = None
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
        self.general_log_ui.connect(
            self._append_solver_log,
            Qt.ConnectionType.QueuedConnection,
        )

        self.setWindowTitle("AstroFrame 1.0 RC22v")
        self.resize(1320, 860)
        self.setMinimumSize(1040, 680)

        self.viewer = ImageViewer()
        self.viewer.image_loaded.connect(self._on_image_loaded)
        self.viewer.overlay_changed.connect(self._working_framing_changed)
        self.viewer.placement_clicked.connect(self._place_working_framing_at)
        self.viewer.rig_label_clicked.connect(self._select_rig_from_image_label)

        sidebar_content = QWidget()
        sidebar_content.setObjectName("sidebar")
        sidebar_content.setFixedWidth(380)
        sidebar_layout = QVBoxLayout(sidebar_content)
        sidebar_layout.setContentsMargins(14, 14, 14, 16)
        sidebar_layout.setSpacing(11)

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
        self._refresh_personal_summary()

        self.observing_site_section = Section("2 · Observing Site")
        observer = self.observing_site_section
        self.observer_summary = QLabel("")
        self.observer_summary.setObjectName("helpText")
        self.observer_summary.setWordWrap(True)
        observer.layout.addWidget(self.observer_summary)

        self.observer_button = QPushButton("Set up observing site…")
        self.observer_button.clicked.connect(self.edit_observer_profile)
        observer.layout.addWidget(self.observer_button)
        self._refresh_observer_summary()

        # Core observability planner: deliberately independent of catalogue
        # identification.  A valid plate-solved RA/Dec is sufficient.
        self.observability_section = Section("3 · Observability")
        obs_date_row = QHBoxLayout()
        self.observability_date = QDateEdit()
        self.observability_date.setCalendarPopup(True)
        self.observability_date.setDisplayFormat("dd MMM yyyy")
        self.observability_date.setToolTip(
            "Local calendar date on which the observing night begins."
        )
        self.observability_today_button = QPushButton("Tonight")
        self.observability_today_button.setToolTip(
            "Use tonight at the observing site: local noon today through local noon tomorrow."
        )
        obs_date_row.addWidget(self.observability_date, 1)
        obs_date_row.addWidget(self.observability_today_button)
        self.observability_section.layout.addLayout(obs_date_row)

        self.observing_season_button = QPushButton("Analyse observing season")
        self.observing_season_button.setToolTip(
            "Work out when this field is astronomically in season from the observing site."
        )
        self.observing_season_button.clicked.connect(self._analyse_observing_season)
        self.observability_section.layout.addWidget(self.observing_season_button)

        self.observing_season_result = QLabel("")
        self.observing_season_result.setObjectName("helpText")
        self.observing_season_result.setWordWrap(True)
        self.observing_season_result.hide()
        self.observability_section.layout.addWidget(self.observing_season_result)

        self.observability_find_button = QPushButton("Find next good night")
        self.observability_find_button.setToolTip(
            "Search from tomorrow for a strong individual dark imaging window."
        )
        self.observability_find_button.clicked.connect(self._find_next_good_night)
        self.observability_section.layout.addWidget(self.observability_find_button)

        self.observability_search_result = QLabel("")
        self.observability_search_result.setObjectName("helpText")
        self.observability_search_result.setWordWrap(True)
        self.observability_search_result.hide()
        self.observability_section.layout.addWidget(self.observability_search_result)

        self.observability_selected_heading = QLabel("SELECTED NIGHT")
        self.observability_selected_heading.setObjectName("fieldLabel")
        self.observability_section.layout.addWidget(self.observability_selected_heading)

        self.observability_result = QLabel(
            "Plate solve an image to calculate its imaging window."
        )
        self.observability_result.setObjectName("helpText")
        self.observability_result.setWordWrap(True)
        self.observability_section.layout.addWidget(self.observability_result)
        self.observability_section.hide()
        self._set_observability_date_to_tonight(refresh=False)
        self.observability_today_button.clicked.connect(
            lambda _checked=False: self._set_observability_date_to_tonight(refresh=True)
        )
        self.observability_date.dateChanged.connect(
            lambda _date: self._observability_date_changed()
        )

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
        self.solver_combo.addItem("Automatic — ASTAP, then Astrometry.net if needed", "automatic")
        self.solver_combo.addItem("Local only — ASTAP", "astap")
        self.solver_combo.addItem("Online only — Astrometry.net", "online")
        saved_solver = str(self.settings.value("solverPreference", "automatic"))
        index = self.solver_combo.findData(saved_solver)
        self.solver_combo.setCurrentIndex(max(0, index))
        self.solver_combo.currentIndexChanged.connect(self._solver_preference_changed)
        reference.layout.addWidget(self.solver_combo)

        self.solver_help_label = QLabel("")
        self.solver_help_label.setObjectName("helpText")
        self.solver_help_label.setWordWrap(True)
        reference.layout.addWidget(self.solver_help_label)
        self._refresh_solver_help()

        self.api_key_button = QPushButton()
        self.api_key_button.clicked.connect(self.set_api_key)
        reference.layout.addWidget(self.api_key_button)
        self._refresh_api_key_button()

        width_label = QLabel("Image angular width")
        width_label.setObjectName("fieldLabel")
        reference.layout.addWidget(width_label)

        self.width_spin = QDoubleSpinBox()
        self.width_spin.setRange(0.0, 180.0)
        self.width_spin.setDecimals(3)
        self.width_spin.setSingleStep(0.05)
        self.width_spin.setSuffix("°")
        self.width_spin.setSpecialValueText("Unknown")
        self.width_spin.setValue(0.0)
        self.width_spin.setToolTip(
            "Angular width of the entire reference image. Leave this as Unknown for "
            "a random image; enter a value only when you genuinely know the field width."
        )
        self.width_spin.valueChanged.connect(self._reference_width_changed)
        reference.layout.addWidget(self.width_spin)

        width_help = QLabel(
            "Unknown until the image is plate solved. If you know the field width, "
            "entering it can make local solving faster. Solved values are read-only."
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

        self.assisted_solve_button = QPushButton("Give Solver a Clue…")
        self.assisted_solve_button.setObjectName("identifyTargetButton")
        self.assisted_solve_button.setEnabled(False)
        self.assisted_solve_button.setToolTip(
            "Give ASTAP the position of any known object visible in the image, either "
            "as an object name or RA/Dec coordinates. It does not need to be the main subject."
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

        self.online_fallback_button = QPushButton("Try Astrometry.net blind solve…")
        self.online_fallback_button.setToolTip(
            "Explicitly send this image to the slower public Astrometry.net service."
        )
        self.online_fallback_button.clicked.connect(self.try_online_solve)
        self.online_fallback_button.hide()
        reference.layout.addWidget(self.online_fallback_button)

        self.astrometry_job_button = QPushButton("Import from Astrometry.net…")
        self.astrometry_job_button.setEnabled(False)
        self.astrometry_job_button.setToolTip(
            "Use a job number or Astrometry.net job/status/user-image URL for the loaded image without uploading it again."
        )
        self.astrometry_job_button.clicked.connect(self.use_astrometry_job)
        reference.layout.addWidget(self.astrometry_job_button)

        self.external_solution_button = QPushButton("Import External Solution…")
        self.external_solution_button.setEnabled(False)
        self.external_solution_button.setToolTip(
            "Use astrometry obtained in another program (for example BlindSolver2000). "
            "AstroFrame will use it for framing but will mark it as externally supplied."
        )
        self.external_solution_button.clicked.connect(self.import_external_solution)
        reference.layout.addWidget(self.external_solution_button)

        self.clear_solution_button = QPushButton("Forget current solution…")
        self.clear_solution_button.hide()
        self.clear_solution_button.clicked.connect(self.clear_solution)
        reference.layout.addWidget(self.clear_solution_button)

        # dev13h: the action that creates the reference state comes first.
        reference.layout.removeWidget(open_button)
        reference.layout.removeWidget(self.url_button)
        reference.layout.insertWidget(0, open_button)
        reference.layout.insertWidget(1, self.url_button)

        self.image_summary = Section("Image summary")
        self.summary_target = QLabel("Target\nPlate solve to identify")
        self.summary_target.setObjectName("fileName")
        self.summary_target.setWordWrap(True)
        self.image_summary.layout.addWidget(self.summary_target)

        self.summary_reference = QLabel("")
        self.summary_reference.setObjectName("helpText")
        self.summary_reference.setWordWrap(True)
        self.image_summary.layout.addWidget(self.summary_reference)

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

        self.collections_section = Section("Collections")

        # QTextBrowser gives us both the tidy rich-text presentation and reliable
        # clickable catalogue links.  It also owns its scrollbar, so nearby-object
        # links remain clickable even when the Collections panel has overflowed.
        self.collections_scroll = QTextBrowser()
        self.collections_scroll.setObjectName("collectionsScroll")
        self.collections_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.collections_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.collections_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.collections_scroll.setMaximumHeight(360)
        self.collections_scroll.setOpenExternalLinks(False)
        self.collections_scroll.setOpenLinks(False)
        self.collections_scroll.setReadOnly(True)
        self.collections_scroll.document().setDocumentMargin(0)
        self.collections_scroll.anchorClicked.connect(
            lambda url: self._collection_link_activated(url.toString())
        )
        # Keep the existing attribute name used throughout the refresh code.
        self.collections_summary = self.collections_scroll
        self.collections_section.layout.addWidget(self.collections_scroll)
        reference_row = QHBoxLayout()
        reference_label = QLabel("Object layer")
        reference_label.setObjectName("fieldLabel")
        self.object_layer_combo = QComboBox()
        self.object_layer_combo.addItem("Curated collections", "curated")
        self.object_layer_combo.addItem("Featured — Bica", "featured")
        self.object_layer_combo.addItem("Clusters — Bica", "clusters")
        self.object_layer_combo.addItem("Associations — Bica", "associations")
        self.object_layer_combo.addItem("Nebulae — Bica", "nebulae")
        self.object_layer_combo.setToolTip(
            "Choose the source used by Show objects. Featured shows up to 30 prominent "
            "Bica objects; the category modes show every in-frame object of that type."
        )
        self.object_layer_combo.currentIndexChanged.connect(self._object_layer_changed)
        reference_row.addWidget(reference_label)
        reference_row.addWidget(self.object_layer_combo, 1)
        self.collections_section.layout.addLayout(reference_row)
        self.show_field_objects_button = QPushButton("Show objects in this image")
        self.show_field_objects_button.setCheckable(True)
        self.show_field_objects_button.setEnabled(False)
        self.show_field_objects_button.setToolTip(
            "Show clickable markers from the selected curated/reference object layer."
        )
        self.show_field_objects_button.toggled.connect(self._toggle_field_objects)
        self.collections_section.layout.addWidget(self.show_field_objects_button)
        self.reference_catalog_progress = QProgressBar()
        self.reference_catalog_progress.setRange(0, 0)
        self.reference_catalog_progress.setTextVisible(False)
        self.reference_catalog_progress.setMaximumHeight(7)
        self.reference_catalog_progress.setVisible(False)
        self.reference_catalog_progress.setToolTip("Searching the reference catalogue…")
        self.collections_section.layout.addWidget(self.reference_catalog_progress)
        self.viewer.catalogue_marker_clicked.connect(self._catalogue_dot_activated)
        self._reference_objects_by_id = {}
        self.browse_collections_button = QPushButton("Browse collections…")
        self.browse_collections_button.clicked.connect(self.browse_collections)
        self.collections_section.layout.addWidget(self.browse_collections_button)
        self.import_collection_button = QPushButton("Import target catalogue…")
        self.import_collection_button.clicked.connect(self.import_target_collection)
        self.collections_section.layout.addWidget(self.import_collection_button)
        self._refresh_collections_summary()

        equipment = Section("4 · Available Equipment")
        self.equipment_section = equipment
        self.rig_checks: dict[str, RigToggle] = {}

        equipment_help = QLabel("Checked rigs are available for this target and are considered by Equipment Advisor.")
        equipment_help.setObjectName("helpText")
        equipment_help.setWordWrap(True)
        equipment.layout.addWidget(equipment_help)

        self.equipment_items_container = QWidget()
        self.equipment_items_layout = QVBoxLayout(self.equipment_items_container)
        self.equipment_items_layout.setContentsMargins(0, 0, 0, 0)
        self.equipment_items_layout.setSpacing(6)
        equipment.layout.addWidget(self.equipment_items_container)

        self.equipment_button = QPushButton("Manage equipment…")
        self.equipment_button.clicked.connect(self.manage_equipment)
        equipment.layout.addWidget(self.equipment_button)
        self._rebuild_equipment_section()

        # dev15a: combine framing, geography, season and selected-night
        # geometry into one concise, user-facing answer.
        self.verdict_section = Section("AstroFrame Verdict")
        self.imaging_verdict = QLabel("Load and plate solve a reference image to get an imaging verdict.")
        self.imaging_verdict.setObjectName("helpText")
        self.imaging_verdict.setWordWrap(True)
        self.verdict_section.layout.addWidget(self.imaging_verdict)
        self.verdict_section.hide()

        self.advisor_section = Section("Equipment Advisor")
        self.advisor_intro = QLabel(
            "Plate solve the reference image to compare your saved setups."
        )
        self.advisor_intro.setObjectName("helpText")
        self.advisor_intro.setWordWrap(True)
        self.advisor_section.layout.addWidget(self.advisor_intro)

        # dev13e: show astronomical accessibility beside the framing advice.
        self.advisor_observability = QLabel("")
        self.advisor_observability.setObjectName("helpText")
        self.advisor_observability.setWordWrap(True)
        self.advisor_observability.hide()
        self.advisor_section.layout.addWidget(self.advisor_observability)

        self.working_framing_panel = QFrame()
        self.working_framing_panel.setObjectName("resultCard")
        working_layout = QVBoxLayout(self.working_framing_panel)
        working_layout.setContentsMargins(10, 9, 10, 9)
        working_layout.setSpacing(6)
        self.working_framing_label = QLabel("")
        self.working_framing_label.setWordWrap(True)
        self.working_framing_label.setObjectName("helpText")
        working_layout.addWidget(self.working_framing_label)
        self.place_working_framing_button = QPushButton("Reframe")
        self.place_working_framing_button.setCheckable(True)
        self.place_working_framing_button.setToolTip("Enter reframing mode. Click anywhere in the reference image as many times as you like, then click Done.")
        self.place_working_framing_button.toggled.connect(self._toggle_working_framing_placement)
        self.use_working_framing_button = QPushButton("Use this framing")
        self.use_working_framing_button.clicked.connect(self._accept_working_framing)
        working_layout.addWidget(self.use_working_framing_button)
        self.working_framing_panel.hide()
        self.advisor_section.layout.addWidget(self.working_framing_panel)

        self.advisor_results_container = QWidget()
        self.advisor_results_layout = QVBoxLayout(self.advisor_results_container)
        self.advisor_results_layout.setContentsMargins(0, 0, 0, 0)
        self.advisor_results_layout.setSpacing(7)
        self.advisor_section.layout.addWidget(self.advisor_results_container)
        self.advisor_section.hide()

        # RC22h: the active rig is a prerequisite for framing, so show it as
        # a bridge between Available Equipment and Step 5 rather than burying
        # it inside the Plan Framing section.
        self.active_framing_rig_panel = QFrame()
        self.active_framing_rig_panel.setObjectName("resultCard")
        active_rig_layout = QVBoxLayout(self.active_framing_rig_panel)
        active_rig_layout.setContentsMargins(10, 9, 10, 9)
        active_rig_layout.setSpacing(4)
        active_rig_heading = QLabel("ACTIVE RIG FOR FRAMING")
        active_rig_heading.setObjectName("fieldLabel")
        active_rig_layout.addWidget(active_rig_heading)
        self.active_framing_rig_label = QLabel("Select an available rig above")
        self.active_framing_rig_label.setObjectName("helpText")
        self.active_framing_rig_label.setWordWrap(True)
        active_rig_layout.addWidget(self.active_framing_rig_label)

        framing = Section("5 · Plan Framing")
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

        # dev17d: the one-shot Reframe control belongs in the visible Framing
        # section. dev17c created the button inside a normally-hidden advisor
        # panel, leaving the UI instructions pointing to a control users could
        # not actually see.
        framing.layout.addWidget(self.place_working_framing_button)

        # Keep framing controls readable in the narrow sidebar: two compact
        # view buttons share a row, while Reset gets the full width below.
        button_row = QHBoxLayout()
        fit_image = QPushButton("Fit Image")
        fit_image.setToolTip("Fit the whole reference image in the viewer.")
        fit_image.clicked.connect(self.viewer.fit_image)

        actual_pixels = QPushButton("100%")
        actual_pixels.setToolTip("Show the reference image at native pixel scale.")
        actual_pixels.clicked.connect(self.viewer.actual_pixels)

        button_row.addWidget(fit_image, 1)
        button_row.addWidget(actual_pixels, 1)
        framing.layout.addLayout(button_row)

        # dev16d: restore explicit zoom controls. Wheel/trackpad zoom remains
        # available, but buttons are important for discoverability and for
        # users without a convenient scroll gesture.
        zoom_row = QHBoxLayout()
        zoom_out = QPushButton("Zoom −")
        zoom_out.setToolTip("Zoom out from the reference image.")
        zoom_out.clicked.connect(self.viewer.zoom_out)
        zoom_in = QPushButton("Zoom +")
        zoom_in.setToolTip("Zoom in on the reference image.")
        zoom_in.clicked.connect(self.viewer.zoom_in)
        zoom_row.addWidget(zoom_out, 1)
        zoom_row.addWidget(zoom_in, 1)
        framing.layout.addLayout(zoom_row)

        reset_framing = QPushButton("Reset framing")
        reset_framing.setToolTip("Restore 0° rotation and centred frames.")
        reset_framing.clicked.connect(self._reset_framing)
        framing.layout.addWidget(reset_framing)

        mosaic_title = QLabel("<b>Mosaic planning</b>")
        mosaic_title.setObjectName("helpText")
        framing.layout.addWidget(mosaic_title)
        mosaic_reframe_help = QLabel(
            "<b>Reposition mosaic:</b> Click <b>Reframe</b>, then click anywhere "
            "on the image to move the mosaic centre. Click <b>Done</b> when finished."
        )
        mosaic_reframe_help.setObjectName("helpText")
        mosaic_reframe_help.setWordWrap(True)
        framing.layout.addWidget(mosaic_reframe_help)
        mosaic_row = QHBoxLayout()
        self.mosaic_grid_combo = QComboBox()
        self.mosaic_grid_combo.addItems(["Single frame", "2 × 1", "1 × 2", "2 × 2", "3 × 2", "2 × 3", "3 × 3", "Auto"] )
        self.mosaic_grid_combo.currentIndexChanged.connect(self._mosaic_controls_changed)
        self.mosaic_overlap_spin = QSpinBox()
        self.mosaic_overlap_spin.setRange(5, 50)
        self.mosaic_overlap_spin.setValue(25)
        self.mosaic_overlap_spin.setSuffix("% overlap")
        self.mosaic_overlap_spin.valueChanged.connect(self._mosaic_controls_changed)
        mosaic_row.addWidget(self.mosaic_grid_combo, 1)
        mosaic_row.addWidget(self.mosaic_overlap_spin, 1)
        framing.layout.addLayout(mosaic_row)
        self.mosaic_summary_label = QLabel("Single-frame export.")
        self.mosaic_summary_label.setObjectName("helpText")
        self.mosaic_summary_label.setWordWrap(True)
        framing.layout.addWidget(self.mosaic_summary_label)
        self._mosaic_preview_items = []


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

        export = Section("6 · Export")
        export_help = QLabel(
            "Choose a rig or subfield in Equipment Advisor, then hand the framing to N.I.N.A. or ASIAIR."
        )
        export_help.setObjectName("helpText")
        export_help.setWordWrap(True)
        export.layout.addWidget(export_help)

        self.nina_handoff_label = QLabel(
            "<b>N.I.N.A. handoff</b><br>Solve or import astrometry for this image, then choose a rig or framing."
        )
        self.nina_handoff_label.setObjectName("helpText")
        self.nina_handoff_label.setWordWrap(True)
        export.layout.addWidget(self.nina_handoff_label)

        nina_row1 = QHBoxLayout()
        self.copy_nina_ra_button = QPushButton("Copy RA")
        self.copy_nina_ra_button.clicked.connect(self._copy_nina_ra)
        self.copy_nina_dec_button = QPushButton("Copy Dec")
        self.copy_nina_dec_button.clicked.connect(self._copy_nina_dec)
        nina_row1.addWidget(self.copy_nina_ra_button, 1)
        nina_row1.addWidget(self.copy_nina_dec_button, 1)
        export.layout.addLayout(nina_row1)

        nina_row2 = QHBoxLayout()
        self.copy_nina_rotation_button = QPushButton("Copy rotation")
        self.copy_nina_rotation_button.clicked.connect(self._copy_nina_rotation)
        self.copy_nina_all_button = QPushButton("Copy coordinates")
        self.copy_nina_all_button.clicked.connect(self._copy_nina_all)
        nina_row2.addWidget(self.copy_nina_rotation_button, 1)
        nina_row2.addWidget(self.copy_nina_all_button, 1)
        export.layout.addLayout(nina_row2)

        self.nina_export_buttons = (
            self.copy_nina_ra_button,
            self.copy_nina_dec_button,
            self.copy_nina_rotation_button,
            self.copy_nina_all_button,
        )
        for button in self.nina_export_buttons:
            button.setEnabled(False)

        asiair_title = QLabel("<b>ASIAIR / Telescopius CSV</b>")
        asiair_title.setObjectName("helpText")
        export.layout.addWidget(asiair_title)
        self.asiair_handoff_label = QLabel(
            "Exports the selected framing or mosaic in the CSV layout accepted by ASIAIR Plan import."
        )
        self.asiair_handoff_label.setObjectName("helpText")
        self.asiair_handoff_label.setWordWrap(True)
        export.layout.addWidget(self.asiair_handoff_label)
        self.copy_asiair_csv_button = QPushButton("Copy ASIAIR CSV")
        self.copy_asiair_csv_button.clicked.connect(self._copy_asiair_csv)
        self.copy_asiair_csv_button.setMinimumHeight(42)
        export.layout.addWidget(self.copy_asiair_csv_button)
        self.save_asiair_csv_button = QPushButton("Save ASIAIR CSV…")
        self.save_asiair_csv_button.clicked.connect(self._save_asiair_csv)
        self.save_asiair_csv_button.setMinimumHeight(42)
        export.layout.addWidget(self.save_asiair_csv_button)
        self.asiair_export_buttons = (self.copy_asiair_csv_button, self.save_asiair_csv_button)
        for button in self.asiair_export_buttons:
            button.setEnabled(False)

        # dev13h: arrange the sidebar around the user's actual journey rather
        # than the historical order in which features were implemented.
        # LOAD → IDENTIFY → EQUIPMENT → OBSERVABILITY → FRAME → EXPORT.
        # RC22: the sidebar is now an explicit top-to-bottom workflow rather
        # than a historical collection of controls. Site and equipment are
        # working choices, so they belong in the journey rather than Setup.
        workflow_heading = QLabel("IMAGING WORKFLOW")
        workflow_heading.setObjectName("workflowHeading")
        sidebar_layout.addWidget(workflow_heading)
        sidebar_layout.addWidget(reference)
        sidebar_layout.addWidget(self.image_summary)
        sidebar_layout.addWidget(observer)
        sidebar_layout.addWidget(self.observability_section)
        sidebar_layout.addWidget(equipment)
        sidebar_layout.addWidget(self.active_framing_rig_panel)
        sidebar_layout.addWidget(framing)

        results_heading = QLabel("ASTROFRAME RESULTS")
        results_heading.setObjectName("resultsHeading")
        sidebar_layout.addWidget(results_heading)
        sidebar_layout.addWidget(self.verdict_section)
        sidebar_layout.addWidget(self.advisor_section)
        sidebar_layout.addWidget(export)
        sidebar_layout.addWidget(self.collections_section)

        setup_heading = QLabel("SETUP & DIAGNOSTICS")
        setup_heading.setObjectName("setupHeading")
        sidebar_layout.addWidget(setup_heading)
        sidebar_layout.addWidget(personal)
        sidebar_layout.addWidget(diagnostics)
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
        sidebar_scroll.setFixedWidth(402)
        sidebar_scroll.setWidget(sidebar_content)

        outer.addWidget(sidebar_scroll)
        outer.addWidget(self.viewer, 1)
        self.setCentralWidget(container)
        QTimer.singleShot(0, self._prompt_for_personalisation_on_first_launch)

        self._restore_settings()

    def _refresh_collections_summary(self, target_name: str | None = None) -> None:
        from html import escape

        collections = self.knowledge_store.list_collections()
        if hasattr(self, "show_field_objects_button"):
            self.show_field_objects_button.setEnabled(self.current_solution is not None)
        if not collections:
            self.collections_summary.setText(
                '<span style="color:#9DA7B6">No collections imported yet.<br>'
                'Import your target spreadsheet to start the Knowledge Engine.</span>'
            )
            return

        count = len(collections)
        parts = [
            f'<div style="color:#9DA7B6; margin-bottom:10px">'
            f'{count} collection{"s" if count != 1 else ""} loaded</div>'
        ]

        if target_name:
            matches = self.knowledge_store.entries_for_target_name(target_name)
            if matches:
                parts.append('<div style="color:#DDE3EB; margin:8px 0 6px 0"><b>Appears in ' +
                             f'{len(matches)} collection{"s" if len(matches) != 1 else ""}</b></div>')
                for collection, entry in matches:
                    title = escape(collection.name)
                    badges = []
                    if entry.rank is not None:
                        badges.append(f'#{escape(str(entry.rank))}')
                    if entry.tier:
                        badges.append(escape(str(entry.tier)).upper())
                    if entry.fov_class:
                        badges.append(escape(str(entry.fov_class)).upper())
                    badge_line = ' &nbsp;·&nbsp; '.join(badges)
                    parts.append(
                        '<div style="margin:7px 0 2px 0; padding-top:7px; border-top:1px solid #303744">'
                        f'<b style="color:#EEF1F5">{title}</b>'
                        + (f'<br><span style="color:#AAB4C3; font-size:11px">{badge_line}</span>' if badge_line else '')
                    )
                    for extra in self._entry_detail_lines(entry):
                        if extra.startswith('Imaging:'):
                            label, value = extra.split(':', 1)
                            parts.append(f'<br><span style="color:#8F99A8">{label}</span> &nbsp; {escape(value.strip())}')
                        elif extra.startswith('Moon OK:'):
                            value = extra.split(':', 1)[1].strip()
                            parts.append(f'<br><span style="color:#8F99A8">Moon</span> &nbsp; {escape(value)}')
                        elif extra.startswith('Best month:'):
                            value = extra.split(':', 1)[1].strip()
                            parts.append(f'<br><span style="color:#8F99A8">Best season</span> &nbsp; {escape(value)}')
                        elif extra.startswith('Notes:'):
                            value = extra.split(':', 1)[1].strip()
                            parts.append(f'<br><span style="color:#C8D0DB">{escape(value)}</span>')
                    parts.append('</div>')

            if self.current_solution is not None:
                nearby = self.knowledge_store.entries_in_field(
                    self.current_solution.ra_deg,
                    self.current_solution.dec_deg,
                    self.current_solution.image_width_deg,
                    self.current_solution.image_height_deg,
                    self.current_solution.orientation_deg or 0.0,
                )
                exact_ids = {entry.target_id for _collection, entry in matches}
                nearby = [item for item in nearby if item[2].target_id not in exact_ids]
                if nearby:
                    shown = set()
                    nearby_lines = []
                    for target, collection, entry, _sep in nearby:
                        key = (target.id, collection.id)
                        if key in shown:
                            continue
                        shown.add(key)
                        display = target.common_name or target.canonical_name
                        if target.common_name and target.canonical_name != target.common_name:
                            display = f"{target.canonical_name} — {target.common_name}"
                        nearby_lines.append(
                            f'<a href="astroframe-target:{escape(target.id)}" '
                            f'style="color:#DDE3EB; text-decoration:none"><b>{escape(display)}</b></a><br>'
                            f'<span style="color:#8F99A8; font-size:11px">{escape(collection.name)}</span>'
                        )
                        if len(shown) >= 8:
                            break
                    parts.append(
                        '<div style="margin-top:14px; padding-top:9px; border-top:1px solid #3A424F">'
                        f'<b style="color:#8F99A8; font-size:10px">ALSO IN THIS FIELD &nbsp; {len(shown)}</b><br>'
                        + '<div style="margin-top:6px">' + '<br><br>'.join(nearby_lines) + '</div></div>'
                    )
        else:
            for collection in collections:
                parts.append(
                    '<div style="margin:6px 0; padding-top:6px; border-top:1px solid #303744">'
                    f'<b>{escape(collection.name)}</b><br>'
                    f'<span style="color:#8F99A8">{len(collection.entries)} targets</span></div>'
                )

        self.collections_summary.setText(''.join(parts))
        # Return to the top when the target changes; the scrollbar appears only
        # when the rendered content exceeds the available height.
        QTimer.singleShot(0, lambda: self.collections_scroll.verticalScrollBar().setValue(0))

    def _target_pixel_position(self, target) -> tuple[float, float] | None:
        """Convert a catalogue target sky position to source-image pixel coordinates."""
        if self.current_solution is None or target is None or target.ra_deg is None or target.dec_deg is None:
            return None
        width_px, height_px = self.current_image_size
        if width_px <= 0 or height_px <= 0:
            return None
        centre = SkyCoord(ra=self.current_solution.ra_deg, dec=self.current_solution.dec_deg, unit=("deg", "deg"), frame="icrs")
        target_ra = float(target.ra_deg)
        target_dec = float(target.dec_deg)
        # If the current subject was independently resolved from a filename clue,
        # prefer that verified coordinate for the matching target marker.  This
        # protects the overlay from a stale/bad coordinate in an older imported
        # catalogue without changing the catalogue record globally.
        target_info = self._cached_target_for_current_image() or {}
        subject_name = str(target_info.get("name") or "").strip()
        verified_ra = target_info.get("subject_ra_deg")
        verified_dec = target_info.get("subject_dec_deg")
        if subject_name and verified_ra is not None and verified_dec is not None:
            subject_ids = catalogue_identifiers(subject_name)
            target_names = [target.canonical_name, target.common_name or "", *(target.aliases or [])]
            target_ids: set[str] = set()
            for value in target_names:
                if value:
                    target_ids.update(catalogue_identifiers(str(value)))
            same_name = any(
                normalise_identifier(str(value)) == normalise_identifier(subject_name)
                for value in target_names if value
            )
            if same_name or (subject_ids and subject_ids & target_ids):
                target_ra = float(verified_ra)
                target_dec = float(verified_dec)
        coord = SkyCoord(ra=target_ra, dec=target_dec, unit=("deg", "deg"), frame="icrs")
        east, north = centre.spherical_offsets_to(coord.icrs)
        theta = math.radians(float(self.current_solution.orientation_deg or 0.0))
        parity = float(self.current_solution.parity or 1.0) or 1.0
        scale_deg = max(float(self.current_solution.pixel_scale_arcsec), 1e-9) / 3600.0
        dx = (math.cos(theta) * float(east.deg) - parity * math.sin(theta) * float(north.deg)) / scale_deg
        # Image pixel Y increases downward, whereas the solved sky-plane Y
        # convention used here increases upward.  Convert between those two
        # coordinate handednesses explicitly.
        dy = -(math.sin(theta) * float(east.deg) + parity * math.cos(theta) * float(north.deg)) / scale_deg
        return (width_px - 1) / 2.0 + dx, (height_px - 1) / 2.0 + dy

    @staticmethod
    def _catalogue_identity_key(target) -> str:
        """Best stable identity for display-time de-duplication across collections."""
        from .knowledge import catalogue_identifiers, normalise_identifier
        names = [target.canonical_name, target.common_name, *(target.aliases or [])]
        ids = set()
        for name in names:
            if name:
                ids.update(catalogue_identifiers(name))
        if ids:
            # Prefer the canonical catalogue designation if present.  This merges
            # e.g. NGC 2070, NGC2070 and NGC 2070 (LMC).
            return sorted(ids)[0]
        return normalise_identifier(target.canonical_name or target.common_name or target.id)

    def _object_layer_changed(self, _index: int) -> None:
        # Changing layers clears the visible overlay but preserves any already-computed
        # marker sets for this same solved image. Returning to a layer can therefore be
        # instantaneous.
        self._clear_field_objects_display()

    def _reference_cache_key(self, mode: str) -> tuple | None:
        """Stable cache key for one solved image + one reference layer."""
        if self.current_solution is None:
            return None
        return (
            self.current_image_path or "",
            round(float(self.current_solution.ra_deg), 9),
            round(float(self.current_solution.dec_deg), 9),
            round(float(self.current_solution.image_width_deg), 9),
            round(float(self.current_solution.image_height_deg), 9),
            round(float(self.current_solution.orientation_deg or 0.0), 9),
            round(float(self.current_solution.parity or 1.0), 3),
            round(float(self.current_solution.pixel_scale_arcsec or 0.0), 9),
            tuple(self.current_image_size),
            str(mode),
        )

    def _clear_field_objects_display(self) -> None:
        """Clear only what is drawn; keep per-image reference results cached."""
        self.viewer.clear_catalogue_markers()
        self.viewer.clear_target_marker()
        self._reference_objects_by_id = {}
        if hasattr(self, "reference_catalog_progress"):
            self.reference_catalog_progress.setVisible(False)
        if hasattr(self, "object_layer_combo"):
            self.object_layer_combo.setEnabled(True)
        if hasattr(self, "show_field_objects_button"):
            self.show_field_objects_button.setEnabled(self.current_solution is not None)
            self.show_field_objects_button.blockSignals(True)
            self.show_field_objects_button.setChecked(False)
            self.show_field_objects_button.setText("Show objects in this image")
            self.show_field_objects_button.blockSignals(False)

    def _reference_pixel_position(self, obj) -> tuple[float, float] | None:
        """Reference-catalogue equivalent of _target_pixel_position."""
        if self.current_solution is None or obj is None:
            return None
        width_px, height_px = self.current_image_size
        if width_px <= 0 or height_px <= 0:
            return None
        centre = SkyCoord(ra=self.current_solution.ra_deg, dec=self.current_solution.dec_deg, unit=("deg", "deg"), frame="icrs")
        coord = SkyCoord(ra=obj.ra_deg, dec=obj.dec_deg, unit=("deg", "deg"), frame="icrs")
        east, north = centre.spherical_offsets_to(coord.icrs)
        theta = math.radians(float(self.current_solution.orientation_deg or 0.0))
        parity = float(self.current_solution.parity or 1.0) or 1.0
        scale_deg = max(float(self.current_solution.pixel_scale_arcsec), 1e-9) / 3600.0
        dx = (math.cos(theta) * float(east.deg) - parity * math.sin(theta) * float(north.deg)) / scale_deg
        # Keep reference-catalogue dots in the same display-space handedness as
        # curated target markers (RC13).  The scene's pixel Y increases downward.
        dy = -(math.sin(theta) * float(east.deg) + parity * math.cos(theta) * float(north.deg)) / scale_deg
        return (width_px - 1) / 2.0 + dx, (height_px - 1) / 2.0 + dy

    @staticmethod
    def _reference_aliases(obj) -> list[str]:
        text = str(getattr(obj, "aliases", "") or "")
        return [part.strip() for part in re.split(r"[,;=]", text) if part.strip()]

    def _featured_reference_score(self, obj, curated_names: set[str], curated_catalogue_ids: set[str]) -> float:
        """Rank reference objects for a useful photographic Featured layer.

        The curated cross-match is intentionally set-based.  dev10d called
        KnowledgeStore.find_target() once per Bica candidate, which repeatedly scanned
        the entire target store on the GUI thread and was the main source of the
        first-show beach ball.
        """
        name = str(getattr(obj, "name", "") or "").strip()
        aliases = self._reference_aliases(obj)
        searchable = " ".join([name, *aliases]).upper()

        ref_names = {normalise_identifier(name)}
        ref_names.update(normalise_identifier(alias) for alias in aliases if alias)
        ref_catalogue_ids: set[str] = set()
        for value in ref_names:
            ref_catalogue_ids.update(catalogue_identifiers(value))
        curated_boost = 55.0 if (ref_names & curated_names or ref_catalogue_ids & curated_catalogue_ids) else 0.0

        # Prefer labels that photographers are likely to recognise.  The order is
        # intentionally conservative: NGC/IC first, then common nebula/association
        # catalogues, while survey-only designations receive no familiarity bonus.
        familiarity = 0.0
        if re.search(r"\bNGC\s*\d+", searchable):
            familiarity = 38.0
        elif re.search(r"\bIC\s*\d+", searchable):
            familiarity = 34.0
        elif re.search(r"\b(?:SH2|SHARPLESS)[ -]?\d+", searchable):
            familiarity = 27.0
        elif re.search(r"\b(?:N|DEM|LHA|HENIZE)\s*\d+", searchable):
            familiarity = 20.0
        elif re.search(r"\bLH\s*\d+", searchable):
            familiarity = 15.0

        category = str(getattr(obj, "category", "") or "")
        category_score = {"Nebula": 18.0, "Cluster": 12.0, "Association": 7.0}.get(category, 0.0)
        size = max(float(getattr(obj, "major_arcmin", 0.0) or 0.0),
                   float(getattr(obj, "minor_arcmin", 0.0) or 0.0), 0.15)
        size_score = min(28.0, 8.0 * math.log1p(size))
        return curated_boost + familiarity + category_score + size_score

    def _prepare_reference_markers(self, objects, mode: str):
        if self.current_solution is None or self.viewer.pixmap_item is None:
            return [], 0
        rect = self.viewer.pixmap_item.boundingRect()
        in_frame = []
        for obj in objects:
            pos = self._reference_pixel_position(obj)
            if pos is not None and rect.contains(QPointF(*pos)):
                in_frame.append((obj, pos))
        total_in_frame = len(in_frame)
        if mode == "featured":
            curated_target_ids = {
                entry.target_id
                for collection in self.knowledge_store.list_collections()
                for entry in collection.entries
            }
            curated_names: set[str] = set()
            curated_catalogue_ids: set[str] = set()
            for target_id in curated_target_ids:
                target = self.knowledge_store.get_target(target_id)
                if target is None:
                    continue
                values = [target.canonical_name, target.common_name or "", *target.aliases]
                for value in values:
                    if not value:
                        continue
                    normal = normalise_identifier(value)
                    curated_names.add(normal)
                    curated_catalogue_ids.update(catalogue_identifiers(normal))
            in_frame.sort(
                key=lambda item: self._featured_reference_score(
                    item[0], curated_names, curated_catalogue_ids
                ),
                reverse=True,
            )
            in_frame = in_frame[:30]
        self._reference_objects_by_id = {obj.id: obj for obj, _pos in in_frame}
        return [(obj.id, pos[0], pos[1]) for obj, pos in in_frame], total_in_frame

    def _set_reference_query_busy(self, busy: bool) -> None:
        if hasattr(self, "reference_catalog_progress"):
            self.reference_catalog_progress.setVisible(busy)
        if hasattr(self, "object_layer_combo"):
            self.object_layer_combo.setEnabled(not busy)
        if hasattr(self, "show_field_objects_button"):
            self.show_field_objects_button.setEnabled(not busy)
            if busy:
                self.show_field_objects_button.setText("Searching catalogue…")

    def _start_reference_query(self, mode: str) -> None:
        if self.current_solution is None:
            return
        self._active_reference_query_key = self._reference_cache_key(mode)
        self._set_reference_query_busy(True)
        try:
            from .reference_catalog import bica_cache_ready
            cache_ready = bica_cache_ready(mode)
        except Exception:
            cache_ready = False
        if cache_ready:
            self.statusBar().showMessage("Searching local Bica reference catalogue…")
            self.show_field_objects_button.setText("Loading local catalogue…")
        else:
            self.statusBar().showMessage("Downloading Bica reference catalogue for local use (one-time)…")
            self.show_field_objects_button.setText("Downloading catalogue…")
        thread = QThread(self)
        worker = ReferenceCatalogWorker(
            self.current_solution.ra_deg, self.current_solution.dec_deg,
            self.current_solution.image_width_deg, self.current_solution.image_height_deg,
            mode,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._reference_query_finished)
        worker.failed.connect(self._reference_query_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self.reference_catalog_thread = thread
        self.reference_catalog_worker = worker
        thread.start()

    @Slot(object, str)
    def _reference_query_finished(self, objects, mode: str) -> None:
        query_key = self._active_reference_query_key
        self._active_reference_query_key = None
        self.reference_catalog_thread = None
        self.reference_catalog_worker = None
        # Keep the busy state visible while the local result is converted into image
        # markers.  The dev10e matcher below makes this stage much shorter, but the UI
        # should never imply the operation has finished before the markers are ready.
        # The user may have loaded/unsolved a new image while the request was in flight.
        current_key = self._reference_cache_key(mode)
        if self.current_solution is None or query_key is None or query_key != current_key:
            self._set_reference_query_busy(False)
            self._clear_field_objects_display()
            return
        markers, total_in_frame = self._prepare_reference_markers(objects, mode)
        self._set_reference_query_busy(False)
        object_map = dict(self._reference_objects_by_id)
        self._reference_marker_cache[query_key] = (list(markers), int(total_in_frame), object_map)
        if not self.show_field_objects_button.isChecked():
            self._clear_field_objects_display()
            return
        self.viewer.show_catalogue_markers(markers)
        self.show_field_objects_button.setText("Hide objects in this image")
        if mode == "featured":
            self.statusBar().showMessage(
                f"Showing {len(markers)} Featured Bica objects from {total_in_frame} reference objects in frame.", 7000
            )
        else:
            self.statusBar().showMessage(
                f"Showing {len(markers)} Bica {mode} in this image.", 7000
            )

    @Slot(str, str)
    def _reference_query_failed(self, message: str, mode: str) -> None:
        self._active_reference_query_key = None
        self.reference_catalog_thread = None
        self.reference_catalog_worker = None
        self._set_reference_query_busy(False)
        self.show_field_objects_button.blockSignals(True)
        self.show_field_objects_button.setChecked(False)
        self.show_field_objects_button.setText("Show objects in this image")
        self.show_field_objects_button.blockSignals(False)
        self.statusBar().showMessage(f"Reference catalogue query failed: {message}", 7000)
        QMessageBox.warning(
            self, "Reference catalogue unavailable",
            "AstroFrame could not retrieve the Bica reference catalogue for this field.\n\n" + message
        )

    def _reset_field_objects_state(self) -> None:
        """Clean default for a new/unsolved image and invalidate computed overlays."""
        self._reference_marker_cache.clear()
        self._active_reference_query_key = None
        self._clear_field_objects_display()

    def _toggle_field_objects(self, checked: bool) -> None:
        if not checked:
            # Hide is absolute visually, but keep any computed reference-layer result in
            # memory so Show on this same image/layer can redraw instantly.
            self.viewer.clear_catalogue_markers()
            self.viewer.clear_target_marker()
            self._reference_objects_by_id = {}
            self.show_field_objects_button.setText("Show objects in this image")
            self.statusBar().showMessage("Object markers hidden.", 2500)
            return
        if self.current_solution is None or self.viewer.pixmap_item is None:
            self.show_field_objects_button.blockSignals(True)
            self.show_field_objects_button.setChecked(False)
            self.show_field_objects_button.blockSignals(False)
            return

        mode = self.object_layer_combo.currentData() if hasattr(self, "object_layer_combo") else "curated"
        if mode != "curated":
            mode = str(mode)
            cache_key = self._reference_cache_key(mode)
            cached = self._reference_marker_cache.get(cache_key) if cache_key is not None else None
            if cached is not None:
                markers, total_in_frame, object_map = cached
                self._reference_objects_by_id = dict(object_map)
                self.viewer.show_catalogue_markers(markers)
                self.show_field_objects_button.setText("Hide objects in this image")
                if mode == "featured":
                    self.statusBar().showMessage(
                        f"Showing {len(markers)} Featured Bica objects from {total_in_frame} reference objects in frame (cached).",
                        3500,
                    )
                else:
                    self.statusBar().showMessage(
                        f"Showing {len(markers)} Bica {mode} in this image (cached).", 3500
                    )
                return
            self._start_reference_query(mode)
            return

        entries = self.knowledge_store.entries_in_field(
            self.current_solution.ra_deg, self.current_solution.dec_deg,
            self.current_solution.image_width_deg, self.current_solution.image_height_deg,
            self.current_solution.orientation_deg or 0.0,
        )
        rect = self.viewer.pixmap_item.boundingRect()
        # RC22w CCA (Canonical Coordinate Arbitration): one physical catalogue
        # identity gets one marker, but precision alone is not enough to choose
        # the surviving coordinate.  A catalogue can give a highly precise yet
        # slightly different centre.  Choose the candidate that best agrees with
        # the other independent sources (spherical medoid), then use preserved
        # coordinate precision as the tie-breaker.
        grouped = {}
        for target, collection, entry, sep in entries:
            key = self._catalogue_identity_key(target)
            candidate = self.knowledge_store._source_coordinate_candidate(entry)
            if candidate is None:
                if target.ra_deg is None or target.dec_deg is None:
                    continue
                ra, dec, precision = float(target.ra_deg), float(target.dec_deg), -1
            else:
                ra, dec, precision = candidate
            grouped.setdefault(key, []).append((target, collection, entry, float(sep), float(ra), float(dec), int(precision)))

        def _cca_sep_deg(a_ra, a_dec, b_ra, b_dec):
            import math
            a1, d1, a2, d2 = map(math.radians, (a_ra, a_dec, b_ra, b_dec))
            c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(a1 - a2)
            return math.degrees(math.acos(max(-1.0, min(1.0, c))))

        markers = []
        for key, candidates in grouped.items():
            ranked = []
            for cand in candidates:
                _target, _collection, _entry, field_sep, ra, dec, precision = cand
                consensus = sum(_cca_sep_deg(ra, dec, other[4], other[5]) for other in candidates)
                ranked.append((consensus, -precision, field_sep, cand))
            ranked.sort(key=lambda item: (item[0], item[1], item[2]))
            _consensus, _neg_precision, _field_sep, winner = ranked[0]
            target, collection, entry, _sep, ra, dec, precision = winner

            # Project the arbitrated coordinate directly; do not fall back to a
            # previously merged target coordinate that may have come from a
            # different source record.
            from dataclasses import replace
            display_target = replace(target, ra_deg=ra, dec_deg=dec)
            pos = self._target_pixel_position(display_target)
            if pos is not None and rect.contains(QPointF(*pos)):
                markers.append((target.id, pos[0], pos[1]))

        self.viewer.show_catalogue_markers(markers)
        self.show_field_objects_button.setText("Hide objects in this image")
        self.statusBar().showMessage(f"Showing {len(markers)} curated catalogue object centre{'s' if len(markers) != 1 else ''} in this image.", 5000)

    def _catalogue_dot_activated(self, target_id: str) -> None:
        ref_obj = self._reference_objects_by_id.get(target_id)
        if ref_obj is not None:
            pos = self._reference_pixel_position(ref_obj)
            if pos is None:
                return
            self.viewer.show_target_marker(ref_obj.id, ref_obj.name, pos[0], pos[1])
            size_text = ""
            if ref_obj.major_arcmin:
                size_text = f" · {ref_obj.major_arcmin:g}′"
            kind = ref_obj.object_type or ref_obj.category
            self.statusBar().showMessage(
                f"{ref_obj.name} — {kind}{size_text} — {ref_obj.source}", 6000
            )
            return
        target = self.knowledge_store.get_target(target_id)
        pos = self._target_pixel_position(target)
        if target is None or pos is None:
            return
        display = target.canonical_name or target.common_name or target.id
        self.viewer.show_target_marker(target.id, display, pos[0], pos[1])
        self.statusBar().showMessage(f"Marked {display} on the image.", 3000)

    def _collection_link_activated(self, href: str) -> None:
        """Mark a clicked 'Also in this field' catalogue object on the image."""
        prefix = "astroframe-target:"
        if not href.startswith(prefix) or self.current_solution is None:
            return
        target_id = href[len(prefix):]
        target = self.knowledge_store.get_target(target_id)
        if target is None or target.ra_deg is None or target.dec_deg is None:
            return
        pos = self._target_pixel_position(target)
        if pos is None:
            return
        x, y = pos
        # Keep image annotations concise.  Long common names and aliases remain
        # available in Collections, while the photograph uses the primary
        # catalogue designation (for example, "NGC 6231").
        display = target.canonical_name or target.common_name or target.id
        image_rect = self.viewer.pixmap_item.boundingRect() if self.viewer.pixmap_item is not None else None
        if image_rect is None:
            return
        if not image_rect.contains(x, y):
            self.viewer.show_edge_target_marker(target.id, display, x, y)
            self.statusBar().showMessage(
                f"{display}: catalogue centre is outside the image; arrow shows its direction.", 5000
            )
            return
        self.viewer.show_target_marker(target.id, display, x, y)
        self.statusBar().showMessage(f"Marked {display} on the image.", 3000)

    def _entry_detail_lines(self, entry) -> list[str]:
        details: list[str] = []
        if entry.rank is not None:
            details.append(f"Rank: {entry.rank}")
        if entry.tier:
            details.append(f"Tier: {entry.tier}")
        if entry.fov_class:
            details.append(f"Best field: {entry.fov_class}")
        if entry.difficulty:
            details.append(f"Level: {entry.difficulty}")
        imaging = []
        for label, value in (("Narrowband", entry.narrowband), ("Broadband", entry.broadband),
                             ("SHO", entry.sho), ("HOO", entry.hoo)):
            if value and str(value).strip().lower() not in {"no", "false", "0", "-"}:
                imaging.append(label if str(value).strip().lower() in {"yes", "true", "1", "x"} else f"{label}: {value}")
        if imaging:
            details.append("Imaging: " + ", ".join(imaging))
        if entry.moon_ok:
            details.append(f"Moon OK: {entry.moon_ok}")
        if entry.best_month:
            details.append(f"Best month: {entry.best_month}")
        if entry.notes:
            details.append(f"Notes: {entry.notes}")
        return details

    def browse_collections(self) -> None:
        collections = self.knowledge_store.list_collections()
        if not collections:
            QMessageBox.information(self, "Collections", "No collections have been imported yet.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("AstroFrame Collections")
        dialog.resize(760, 560)
        layout = QVBoxLayout(dialog)
        selector = QComboBox()
        for collection in collections:
            selector.addItem(f"{collection.name} — {len(collection.entries)} targets", collection.id)
        search = QLineEdit()
        search.setPlaceholderText("Search targets…")
        targets = QListWidget()
        detail = QPlainTextEdit()
        detail.setReadOnly(True)
        detail.setMaximumHeight(180)
        layout.addWidget(selector)
        layout.addWidget(search)
        layout.addWidget(targets, 1)
        layout.addWidget(detail)
        button_row = QHBoxLayout()
        remove_button = QPushButton("Remove collection…")
        remove_button.setToolTip("Remove the selected imported collection from AstroFrame.")
        button_row.addWidget(remove_button)
        button_row.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        button_row.addWidget(buttons)
        layout.addLayout(button_row)

        def current_collection():
            cid = selector.currentData()
            return next((c for c in collections if c.id == cid), collections[0])

        def refill():
            query = search.text().strip().casefold()
            targets.clear()
            collection = current_collection()
            for entry in collection.entries:
                target = self.knowledge_store.get_target(entry.target_id)
                if target is None:
                    continue
                names = [target.canonical_name, target.common_name or "", *target.aliases]
                haystack = " ".join(names).casefold()
                if query and query not in haystack:
                    continue
                label = target.canonical_name
                if target.common_name and target.common_name.casefold() != target.canonical_name.casefold():
                    label += f" — {target.common_name}"
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, entry.target_id)
                targets.addItem(item)
            if targets.count():
                targets.setCurrentRow(0)
            else:
                detail.clear()

        def show_detail():
            item = targets.currentItem()
            if item is None:
                detail.clear(); return
            collection = current_collection()
            tid = item.data(Qt.ItemDataRole.UserRole)
            target = self.knowledge_store.get_target(tid)
            entry = next((e for e in collection.entries if e.target_id == tid), None)
            if target is None or entry is None:
                detail.clear(); return
            lines = [target.canonical_name]
            if target.common_name and target.common_name.casefold() != target.canonical_name.casefold():
                lines.append(target.common_name)
            if target.object_type:
                lines.append(target.object_type)
            if target.constellation:
                lines.append(target.constellation)
            if target.ra_deg is not None and target.dec_deg is not None:
                lines.append(f"Canonical position: RA {target.ra_deg:.6f}°, Dec {target.dec_deg:+.6f}°")
                provenance = self.knowledge_store.coordinate_provenance(target.id)
                if provenance:
                    lines.append(f"Position source: {provenance[0][0]}")
                    if len(provenance) > 1:
                        lines.append("Coordinate sources: " + ", ".join(p[0] for p in provenance))
            lines.append("")
            lines.extend(self._entry_detail_lines(entry))
            detail.setPlainText("\n".join(lines))

        def remove_current_collection():
            nonlocal collections
            collection = current_collection()
            answer = QMessageBox.question(
                dialog,
                "Remove collection",
                f"Remove ‘{collection.name}’ from AstroFrame?\n\n"
                "This removes the collection and rebuilds shared target coordinates from the best surviving source. Your image solutions are not deleted.",
                QMessageBox.StandardButton.Remove if hasattr(QMessageBox.StandardButton, "Remove") else QMessageBox.StandardButton.Yes,
                QMessageBox.StandardButton.Cancel,
            )
            accepted = (
                answer == (QMessageBox.StandardButton.Remove if hasattr(QMessageBox.StandardButton, "Remove") else QMessageBox.StandardButton.Yes)
            )
            if not accepted:
                return
            if not self.knowledge_store.remove_collection(collection.id):
                QMessageBox.warning(dialog, "Remove collection", "AstroFrame could not remove that collection.")
                return
            collections = [c for c in collections if c.id != collection.id]
            selector.blockSignals(True)
            selector.clear()
            for remaining in collections:
                selector.addItem(f"{remaining.name} — {len(remaining.entries)} targets", remaining.id)
            selector.blockSignals(False)
            self._reference_marker_cache.clear()
            self._refresh_collections_summary()
            if hasattr(self, "show_field_objects_button") and self.show_field_objects_button.isChecked():
                self.show_field_objects_button.setChecked(False)
                self.show_field_objects_button.setChecked(True)
            if not collections:
                dialog.accept()
                return
            refill()
            QMessageBox.information(dialog, "Collection removed", f"Removed {collection.name}.")

        remove_button.clicked.connect(remove_current_collection)
        selector.currentIndexChanged.connect(refill)
        search.textChanged.connect(refill)
        targets.currentItemChanged.connect(lambda *_: show_detail())
        refill()
        dialog.exec()

    def _flexible_collection_import_dialog(self, path: str) -> object | None:
        """Map an unfamiliar spreadsheet/CSV into AstroFrame target fields."""
        try:
            table = discover_flexible_source(path)
        except Exception as exc:
            QMessageBox.warning(self, "Flexible catalogue import", str(exc))
            return None

        dialog = QDialog(self)
        dialog.setWindowTitle("Flexible Catalogue Import")
        dialog.resize(760, 780)
        layout = QVBoxLayout(dialog)
        intro = QLabel(
            "AstroFrame does not recognise this catalogue format, but it found a likely target table. "
            "Check the column mapping below, preview the interpreted targets, then import."
        )
        intro.setWordWrap(True)
        intro.setObjectName("helpText")
        layout.addWidget(intro)

        source_form = QFormLayout()
        sheet_combo = QComboBox()
        sheet_combo.addItems(table.get("sheet_names") or [table["sheet"]])
        sheet_combo.setCurrentText(table["sheet"])
        source_form.addRow("Worksheet", sheet_combo)
        header_spin = QSpinBox()
        header_spin.setRange(1, 9999)
        header_spin.setValue(int(table["header_row"]))
        source_form.addRow("Header row", header_spin)
        collection_name = QLineEdit(Path(path).stem)
        source_form.addRow("Collection name", collection_name)
        author_edit = QLineEdit()
        source_form.addRow("Author (optional)", author_edit)
        size_units = QComboBox()
        size_units.addItem("Arcminutes (common for DSO catalogues)", "arcmin")
        size_units.addItem("Degrees", "degrees")
        size_units.addItem("Arcseconds", "arcsec")
        source_form.addRow("Numeric size units", size_units)
        layout.addLayout(source_form)

        mapping_title = QLabel("COLUMN MAPPING")
        mapping_title.setObjectName("sectionHeading")
        layout.addWidget(mapping_title)
        mapping_form = QFormLayout()
        mapping_boxes: dict[str, QComboBox] = {}
        for key, label, required in FLEXIBLE_FIELDS:
            combo = QComboBox()
            mapping_boxes[key] = combo
            mapping_form.addRow(label + (" *" if required else ""), combo)
        layout.addLayout(mapping_form)

        preview_label = QLabel("PREVIEW")
        preview_label.setObjectName("sectionHeading")
        layout.addWidget(preview_label)
        preview = QPlainTextEdit()
        preview.setReadOnly(True)
        preview.setMaximumHeight(190)
        layout.addWidget(preview)
        status = QLabel()
        status.setWordWrap(True)
        status.setObjectName("helpText")
        layout.addWidget(status)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Import")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        current_table = {"value": table}
        rebuilding = {"value": False}

        def refill_mapping(*_args, preserve: bool = False) -> None:
            rebuilding["value"] = True
            tbl = current_table["value"]
            headers = tbl["headers"]
            inferred = infer_flexible_mapping(headers)
            for key, combo in mapping_boxes.items():
                previous_header = combo.currentData() if preserve else None
                combo.blockSignals(True)
                combo.clear()
                combo.addItem("— not mapped —", None)
                for i, header in enumerate(headers):
                    combo.addItem(header, i)
                wanted = previous_header if previous_header is not None else inferred.get(key)
                idx = combo.findData(wanted)
                combo.setCurrentIndex(idx if idx >= 0 else 0)
                combo.blockSignals(False)
            rebuilding["value"] = False
            refresh_preview()

        def mapping_now() -> dict[str, int | None]:
            return {key: combo.currentData() for key, combo in mapping_boxes.items()}

        def refresh_preview(*_args) -> None:
            if rebuilding["value"]:
                return
            mapping = mapping_now()
            tbl = current_table["value"]
            rows = flexible_preview_rows(tbl, mapping, size_unit=str(size_units.currentData()), limit=5)
            count = count_flexible_rows(tbl, mapping)
            lines = []
            for item in rows:
                common = f" — {item['common_name']}" if item.get("common_name") else ""
                kind = f" · {item['type']}" if item.get("type") else ""
                con = f" · {item['constellation']}" if item.get("constellation") else ""
                lines.append(
                    f"{item['name']}{common}{kind}{con}\n"
                    f"  RA {item['ra_deg']:.5f}°   Dec {item['dec_deg']:+.5f}°"
                )
            preview.setPlainText("\n".join(lines) if lines else "No usable target rows with the current mapping.")
            status.setText(
                f"{count} target{'s' if count != 1 else ''} currently have a usable name, RA and Dec. "
                "Only those rows will be imported. Unmapped source columns are still preserved as source metadata."
            )
            buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
                mapping.get("name") is not None and count > 0
            )

        def reload_source(*_args) -> None:
            try:
                tbl = _read_flexible_table(
                    path, sheet_name=sheet_combo.currentText(), header_row=header_spin.value()
                )
            except Exception as exc:
                status.setText(str(exc))
                buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
                return
            current_table["value"] = tbl
            refill_mapping()

        sheet_combo.currentTextChanged.connect(reload_source)
        header_spin.valueChanged.connect(reload_source)
        size_units.currentIndexChanged.connect(refresh_preview)
        for combo in mapping_boxes.values():
            combo.currentIndexChanged.connect(refresh_preview)
        refill_mapping()

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        mapping = mapping_now()
        try:
            return import_flexible_collection(
                path, self.knowledge_store, table=current_table["value"], mapping=mapping,
                collection_name=collection_name.text().strip() or Path(path).stem,
                author=author_edit.text().strip() or None, size_unit=str(size_units.currentData()),
            )
        except Exception as exc:
            QMessageBox.warning(self, "Flexible catalogue import", str(exc))
            return None

    def import_target_collection(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import target catalogue",
            self.settings.value("lastCollectionDirectory", ""),
            "Catalogues (*.xlsx *.xlsm *.csv);;Excel workbooks (*.xlsx *.xlsm);;CSV files (*.csv)",
        )
        if not path:
            return
        try:
            preview = preview_collection_import(path)
            if preview.get("format") == "flexible":
                collection = self._flexible_collection_import_dialog(path)
                if collection is None:
                    return
            else:
                columns = ", ".join(preview["columns"])
                answer = QMessageBox.question(
                    self, "Import collection",
                    f"AstroFrame recognised: {preview['label']}\n\n"
                    f"Sheet: {preview['sheet']}\nHeader row: {preview['header_row']}\n"
                    f"Targets found: {preview['targets']}\n\nColumns: {columns}\n\nImport this collection?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
                collection = import_target_collection(path, self.knowledge_store)
        except Exception as exc:
            QMessageBox.warning(self, "Collection import", str(exc))
            return
        self.settings.setValue("lastCollectionDirectory", str(Path(path).parent))
        self.settings.sync()
        current_target = None
        target_info = self._cached_target_for_current_image()
        if target_info:
            current_target = str(target_info.get("name") or "").strip() or None
        self._refresh_collections_summary(current_target)
        self._append_solver_log(
            f"Knowledge Engine: imported {collection.name} ({len(collection.entries)} targets)"
        )
        QMessageBox.information(
            self,
            "Collection imported",
            f"Imported {len(collection.entries)} targets into {collection.name}."
        )

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
                        colour=str(record.get("colour") or USER_RIG_COLOURS[
                            index % len(USER_RIG_COLOURS)
                        ]),
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
        # RC22e: saved equipment edits must immediately update any existing
        # image overlays.  Previously OverlayItem retained the old Rig object,
        # leaving the FOV stale after focal-length or sensor changes.
        self.viewer.refresh_rig_definitions(self.available_rigs)

        if not self.available_rigs:
            empty = QLabel(
                "No imaging setups yet.\n"
                "Add the telescope/camera combinations you actually use."
            )
            empty.setObjectName("helpText")
            empty.setWordWrap(True)
            self.equipment_items_layout.addWidget(empty)
            return

        for rig in self.available_rigs:
            check = RigToggle(rig)
            check.toggled.connect(
                lambda checked, r=rig: self._available_rig_toggled(r, checked)
            )
            check.activated.connect(self._select_rig_from_equipment_card)
            self.equipment_items_layout.addWidget(check)
            self.rig_checks[rig.key] = check

        if hasattr(self, "advisor_section"):
            self._update_equipment_advisor()

    def _select_rig_from_equipment_card(self, rig_key: str) -> None:
        """Make an available rig the active framing rig without changing availability."""
        if self.current_solution is None:
            return
        check = self.rig_checks.get(rig_key)
        if check is None or not check.isChecked():
            return
        self._activate_reference_rig_framing(rig_key)
        self._apply_active_rig_emphasis()
        self._update_active_framing_rig_label()
        self._refresh_mosaic_preview()
        self._refresh_nina_export()
        QTimer.singleShot(0, self._update_equipment_advisor)
        rig = next((r for r in self.available_rigs if r.key == rig_key), None)
        if rig is not None:
            self.statusBar().showMessage(f"Active framing rig: {rig.name}", 2500)

    def _available_rig_toggled(self, rig: Rig, checked: bool) -> None:
        """Availability controls both overlays and Advisor eligibility."""
        self.viewer.set_rig_visible(rig, checked)
        active_key = str((self._working_framing or {}).get("rig_key") or "")
        if not checked and active_key == rig.key:
            self._working_framing = None
            self._accepted_framing = None
            if hasattr(self, "mosaic_grid_combo"):
                self.mosaic_grid_combo.blockSignals(True)
                self.mosaic_grid_combo.setCurrentText("Single frame")
                self.mosaic_grid_combo.blockSignals(False)
                self._clear_mosaic_preview()
            self._apply_active_rig_emphasis()
            self._refresh_nina_export()
        self._update_active_framing_rig_label()
        if hasattr(self, "advisor_section"):
            QTimer.singleShot(0, self._update_equipment_advisor)
            QTimer.singleShot(0, self._update_imaging_verdict)

    def _update_active_framing_rig_label(self) -> None:
        if not hasattr(self, "active_framing_rig_label"):
            return
        key = str((self._working_framing or {}).get("rig_key") or "")
        rig = next((r for r in self.available_rigs if r.key == key), None)
        if rig is None:
            self.active_framing_rig_label.setText("Select an available rig above")
            self.active_framing_rig_label.setStyleSheet("")
            return
        self.active_framing_rig_label.setText(f"<b>{rig.name.upper()}</b>")
        self.active_framing_rig_label.setStyleSheet(
            f"border-left: 7px solid {rig.colour}; padding: 7px 9px; background: #171C23;"
        )

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
        form.addRow("Reducer / Barlow", modifier_combo)
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
                effective_focal = float(record.get("focal_length_mm", 0.0))
                sensor_width = float(record.get("sensor_width_mm", 0.0))
                sensor_height = float(record.get("sensor_height_mm", 0.0))

                # Keep legacy Seestar records consistent with the framing code.
                if str(record.get("camera_key", "")) == "seestar_s50_camera":
                    sensor_width = 3.13
                    sensor_height = 5.57

                detail = (
                    f"{record.get('telescope_name', '')} + "
                    f"{record.get('camera_name', '')}"
                )
                if abs(factor - 1.0) > 0.001:
                    detail += f" · {factor:.2f}×"

                if effective_focal > 0 and sensor_width > 0 and sensor_height > 0:
                    display_rig = Rig(
                        key="display",
                        name="display",
                        sensor_width_mm=sensor_width,
                        sensor_height_mm=sensor_height,
                        focal_length_mm=effective_focal,
                        colour="#FFFFFF",
                    )
                    detail += (
                        f" · {effective_focal:.0f} mm"
                        f" · {display_rig.fov_width_deg:.2f}° × "
                        f"{display_rig.fov_height_deg:.2f}°"
                    )

                setup_list.addItem(
                    QListWidgetItem(
                        f"{record.get('name', 'Setup')}\n{detail}"
                    )
                )

        def add_setup() -> None:
            record = self._edit_setup_record(dialog)
            if record:
                used = {str(r.get("colour") or "") for r in records}
                record["colour"] = next(
                    (c for c in USER_RIG_COLOURS if c not in used),
                    USER_RIG_COLOURS[len(records) % len(USER_RIG_COLOURS)],
                )
                records.append(record)
                refresh_list()

        def edit_setup() -> None:
            row = setup_list.currentRow()
            if 0 <= row < len(records):
                updated = self._edit_setup_record(dialog, records[row])
                if updated:
                    # Editing optics/camera details must not change the visual
                    # identity the user already associates with this setup.
                    updated["colour"] = records[row].get(
                        "colour", USER_RIG_COLOURS[row % len(USER_RIG_COLOURS)]
                    )
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
            self._refresh_observability_panel()

    def _clear_advisor_results(self) -> None:
        if not hasattr(self, "advisor_results_layout"):
            return
        while self.advisor_results_layout.count():
            item = self.advisor_results_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    @staticmethod
    def _polygon_area(points: list[tuple[float, float]]) -> float:
        if len(points) < 3:
            return 0.0
        total = 0.0
        for i, (x1, y1) in enumerate(points):
            x2, y2 = points[(i + 1) % len(points)]
            total += x1 * y2 - x2 * y1
        return abs(total) * 0.5

    @staticmethod
    def _clip_polygon_axis(
        points: list[tuple[float, float]],
        axis: int,
        bound: float,
        keep_greater: bool,
    ) -> list[tuple[float, float]]:
        """Clip a convex polygon against one axis-aligned half-plane."""
        if not points:
            return []

        def inside(point: tuple[float, float]) -> bool:
            value = point[axis]
            return value >= bound - 1e-12 if keep_greater else value <= bound + 1e-12

        def intersection(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
            av, bv = a[axis], b[axis]
            denom = bv - av
            if abs(denom) < 1e-12:
                return a
            t = (bound - av) / denom
            x = a[0] + t * (b[0] - a[0])
            y = a[1] + t * (b[1] - a[1])
            return (x, y)

        output: list[tuple[float, float]] = []
        previous = points[-1]
        previous_inside = inside(previous)
        for current in points:
            current_inside = inside(current)
            if current_inside:
                if not previous_inside:
                    output.append(intersection(previous, current))
                output.append(current)
            elif previous_inside:
                output.append(intersection(previous, current))
            previous = current
            previous_inside = current_inside
        return output

    def _centred_rectangle_overlap(
        self,
        ref_w: float,
        ref_h: float,
        rig_w: float,
        rig_h: float,
        angle_deg: float,
    ) -> float:
        """Exact overlap area of centred reference/rig rectangles.

        The reference rectangle is axis-aligned.  The rig rectangle is rotated
        by ``angle_deg`` relative to the reference image.  Working in angular
        units makes this independent of the display pixel scale.
        """
        angle = math.radians(angle_deg)
        c, sn = math.cos(angle), math.sin(angle)
        hw, hh = rig_w * 0.5, rig_h * 0.5
        corners = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
        poly = [
            (x * c - y * sn, x * sn + y * c)
            for x, y in corners
        ]

        left, right = -ref_w * 0.5, ref_w * 0.5
        top, bottom = -ref_h * 0.5, ref_h * 0.5
        poly = self._clip_polygon_axis(poly, 0, left, True)
        poly = self._clip_polygon_axis(poly, 0, right, False)
        poly = self._clip_polygon_axis(poly, 1, top, True)
        poly = self._clip_polygon_axis(poly, 1, bottom, False)
        return self._polygon_area(poly)

    def _orientation_metrics(
        self,
        rig: Rig,
        solution: PlateSolution,
        rotated: bool = False,
        angle_deg: float | None = None,
    ) -> tuple[float, str, float, float, float]:
        """Return framing score/facts for a centred rig at an arbitrary angle.

        Score is based on the exact rectangular overlap, not the bounding box.
        It is symmetric: a field that is much too wide and one that is much too
        tight are both penalised, while a near-identical footprint approaches
        100%.  ``retained_fraction`` is the percentage of the reference image
        actually covered by the rig.
        """
        ref_w = max(float(solution.image_width_deg), 1e-6)
        ref_h = max(float(solution.image_height_deg), 1e-6)
        rig_w = max(float(rig.fov_width_deg), 1e-6)
        rig_h = max(float(rig.fov_height_deg), 1e-6)
        if angle_deg is None:
            angle_deg = 90.0 if rotated else 0.0
        angle_deg = float(angle_deg) % 180.0

        overlap = self._centred_rectangle_overlap(
            ref_w, ref_h, rig_w, rig_h, angle_deg
        )
        ref_area = ref_w * ref_h
        rig_area = rig_w * rig_h
        retained_fraction = max(0.0, min(1.0, overlap / ref_area))
        rig_fill_fraction = max(0.0, min(1.0, overlap / rig_area))
        score = 100.0 * math.sqrt(retained_fraction * rig_fill_fraction)
        score = max(0.0, min(100.0, score))

        # Ratios remain useful explanatory facts.  At arbitrary rotation these
        # are the dimensions of the rotated rig's bounding box relative to the
        # reference, while the score above uses exact polygon overlap.
        rad = math.radians(angle_deg)
        bbox_w = abs(rig_w * math.cos(rad)) + abs(rig_h * math.sin(rad))
        bbox_h = abs(rig_w * math.sin(rad)) + abs(rig_h * math.cos(rad))
        width_ratio = bbox_w / ref_w
        height_ratio = bbox_h / ref_h

        if score >= 92 and retained_fraction >= 0.90 and rig_fill_fraction >= 0.90:
            note = "Excellent framing match"
        elif score >= 80:
            note = "Very close framing"
        elif retained_fraction < 0.55:
            note = "Partial field — substantial crop"
        elif rig_fill_fraction < 0.55:
            note = "Much wider than the reference"
        elif retained_fraction < 0.82:
            note = "Tighter field than the reference"
        elif rig_fill_fraction < 0.82:
            note = "Wider than the reference"
        else:
            note = "Good framing match"

        return score, note, width_ratio, height_ratio, retained_fraction

    def _best_rig_rotation(
        self,
        rig: Rig,
        solution: PlateSolution,
    ) -> tuple[float, tuple[float, str, float, float, float]]:
        """Find the best centred relative camera rotation in the range 0–180°."""
        # A one-degree sweep is cheap (rectangles have only four vertices) and
        # robust.  Refine the best neighbourhood to a tenth of a degree so the
        # recommendation is precise enough to hand off to capture software.
        best_angle = 0.0
        best_metrics = self._orientation_metrics(rig, solution, angle_deg=0.0)
        for angle in range(1, 180):
            metrics = self._orientation_metrics(rig, solution, angle_deg=float(angle))
            if metrics[0] > best_metrics[0] + 1e-9:
                best_angle, best_metrics = float(angle), metrics

        coarse = best_angle
        for step in range(-10, 11):
            angle = (coarse + step / 10.0) % 180.0
            metrics = self._orientation_metrics(rig, solution, angle_deg=angle)
            if metrics[0] > best_metrics[0] + 1e-9:
                best_angle, best_metrics = angle, metrics

        return best_angle % 180.0, best_metrics

    def _score_setup_for_reference(
        self,
        rig: Rig,
        solution: PlateSolution,
    ) -> tuple[float, str, bool, float, float]:
        """Compatibility wrapper: best arbitrary rotation plus 0°/90° scores."""
        best_angle, best = self._best_rig_rotation(rig, solution)
        base = self._orientation_metrics(rig, solution, angle_deg=0.0)
        ninety = self._orientation_metrics(rig, solution, angle_deg=90.0)
        return best[0], best[1], abs(best_angle - 90.0) < 0.1, base[0], ninety[0]

    def _framing_analysis_lines(
        self,
        rig: Rig,
        solution: PlateSolution,
        angle_deg: float,
        retained_fraction: float,
    ) -> list[str]:
        """Objective framing facts for the currently previewed rotation."""
        ref_area = max(float(solution.image_width_deg * solution.image_height_deg), 1e-9)
        rig_area = max(float(rig.fov_width_deg * rig.fov_height_deg), 1e-9)
        overlap = retained_fraction * ref_area
        rig_fill = max(0.0, min(1.0, overlap / rig_area))
        lines: list[str] = []

        retained_pct = int(round(retained_fraction * 100.0))
        rig_fill_pct = int(round(rig_fill * 100.0))
        if retained_fraction >= 0.995:
            lines.append("✓ Entire reference framing fits inside this field.")
        else:
            lines.append("✓ Reference centre remains in frame when centred.")
            lines.append(f"• About {retained_pct}% of the reference area is retained.")

        if rig_fill < 0.995:
            lines.append(f"• About {rig_fill_pct}% of this rig's frame overlaps the reference.")

        if abs((angle_deg % 90.0)) > 0.15:
            lines.append("• An intermediate camera angle improves the centred match.")
        return lines

    def _activate_reference_rig_framing(self, rig_key: str) -> None:
        """Make a centred Equipment Advisor rig immediately exportable.

        A N.I.N.A. handoff does not require a "Shoot this instead" target.
        When a user chooses an ordinary Advisor result, the intended framing is
        simply that rig centred on the solved reference image at its current
        rotation.  This is especially important for rigs wider than the loaded
        reference, where no alternative-framing recommendation is needed.
        """
        # RC9: changing rigs starts a fresh framing context.
        # Mosaic choices belong to the rig on which they were made.
        if hasattr(self, "mosaic_grid_combo"):
            self.mosaic_grid_combo.blockSignals(True)
            self.mosaic_grid_combo.setCurrentText("Single frame")
            self.mosaic_grid_combo.blockSignals(False)
            self._clear_mosaic_preview()

        if self.current_solution is None:
            return
        rig = next((r for r in self.available_rigs if r.key == rig_key), None)
        if rig is None:
            return

        target_info = self._cached_target_for_current_image() or {}
        subject = str(target_info.get("name") or "").strip()
        label = f"{subject} — reference centre" if subject else "Reference image centre"
        relative_rotation = float(self.viewer.rig_rotation(rig_key)) % 180.0
        sky_pa = (float(self.current_solution.orientation_deg or 0.0) + relative_rotation) % 180.0
        self._working_framing = {
            "mode": "reference",
            "rig_key": rig_key,
            "rig_name": rig.name,
            "label": label,
            "ra_deg": float(self.current_solution.ra_deg),
            "dec_deg": float(self.current_solution.dec_deg),
            "relative_rotation_deg": relative_rotation,
            "sky_pa_deg": sky_pa,
        }
        self._accepted_framing = None
        self._update_active_framing_rig_label()
        ra_text, dec_text = self._format_framing_coords(
            float(self.current_solution.ra_deg), float(self.current_solution.dec_deg)
        )
        if hasattr(self, "working_framing_label"):
            self.working_framing_label.setText(
                "<b>Working framing</b><br>"
                f"{label}<br>"
                f"{rig.name}<br>"
                f"Centre&nbsp;&nbsp;RA {ra_text}&nbsp;&nbsp;Dec {dec_text}<br>"
                f"Camera PA&nbsp;&nbsp;{sky_pa:.1f}°<br>"
                "<span style='color:#8F99A8'>Centred on the solved reference image; use this rig's rotation controls to adjust the camera angle.</span>"
            )
            self.working_framing_panel.show()
        self._refresh_nina_export()

    def _show_advisor_setup(self, rig_key: str, angle_deg: float | None = None) -> None:
        check = self.rig_checks.get(rig_key)
        if check is not None:
            check.setChecked(True)
        if angle_deg is not None:
            self.viewer.set_rig_rotation(rig_key, angle_deg)
        self.viewer.centre_overlays()
        # Any Advisor rig is a valid N.I.N.A. handoff, even when its field is
        # wider than the reference and therefore has no "Shoot this instead"
        # recommendation.  Selecting the card makes it the active framing.
        self._activate_reference_rig_framing(rig_key)
        self._enable_working_framing_placement()
        self._apply_active_rig_emphasis()
        # Rebuild on the next event turn so the Advisor card itself gains the
        # unmistakable SELECTED state without destroying the button mid-click.
        QTimer.singleShot(0, self._update_equipment_advisor)

    def _set_advisor_rotation(self, rig_key: str, angle_deg: float) -> None:
        check = self.rig_checks.get(rig_key)
        if check is not None and not check.isChecked():
            check.setChecked(True)
        self.viewer.set_rig_rotation(rig_key, angle_deg)
        state = self._working_framing
        if state and str(state.get("rig_key") or "") == rig_key:
            # RC19: rotation is an edit of the existing framing, not a new rig
            # selection.  Re-activating a reference rig here used to reset the
            # mosaic control to Single frame, so turning the selected rig's
            # rotation slider destroyed an active mosaic.  Keep the current
            # mosaic pattern and centre; only update PA/coordinates and redraw.
            self._working_framing_changed()
            self._refresh_mosaic_preview()
            self._apply_active_rig_emphasis()
        else:
            # Rotating a different rig is effectively selecting a new framing
            # context, so the normal rig-activation behaviour still applies.
            self._activate_reference_rig_framing(rig_key)
            self._enable_working_framing_placement()
            self._refresh_mosaic_preview()
            self._apply_active_rig_emphasis()

    def _alternative_framings_for_rig(
        self,
        rig: Rig,
        solution: PlateSolution,
        *,
        limit: int = 3,
    ) -> list[tuple[float, object, float, str]]:
        """Rank photographically useful curated subfields inside the reference.

        Geometry alone is not enough for a useful "shoot this instead" suggestion.
        This pass combines target fit, sensor fill, previewability inside the loaded
        reference, object identity/type, editorial support across all collections,
        and nearby curated companions.
        """
        if self.viewer.pixmap_item is None:
            return []
        try:
            entries = self.knowledge_store.entries_in_field(
                solution.ra_deg, solution.dec_deg,
                solution.image_width_deg, solution.image_height_deg,
                solution.orientation_deg or 0.0,
            )
        except Exception:
            return []

        grouped: dict[str, dict[str, object]] = {}
        for target, collection, entry, sep in entries:
            bucket = grouped.setdefault(
                target.id,
                {"target": target, "entries": [], "collections": set(), "sep": sep},
            )
            bucket["entries"].append(entry)
            bucket["collections"].add(collection.id)
            bucket["sep"] = min(float(bucket["sep"]), float(sep))

        current_info = self._cached_target_for_current_image() or {}
        current_raw_name = str(current_info.get("name") or "")
        current_name = normalise_identifier(current_raw_name)
        current_ids = catalogue_identifiers(current_name)
        current_target = self.knowledge_store.find_target(current_raw_name) if current_raw_name else None
        current_target_id = current_target.id if current_target is not None else None
        current_parent_names: set[str] = {current_name} if current_name else set()
        if current_target is not None:
            current_parent_names.add(normalise_identifier(current_target.canonical_name))
            if current_target.common_name:
                current_parent_names.add(normalise_identifier(current_target.common_name))
            current_parent_names.update(
                normalise_identifier(alias) for alias in current_target.aliases if alias
            )

        image_rect = self.viewer.pixmap_item.boundingRect()
        rig_w = max(float(rig.fov_width_deg), 1e-6)
        rig_h = max(float(rig.fov_height_deg), 1e-6)
        ref_w = max(float(solution.image_width_deg), 1e-6)
        ref_h = max(float(solution.image_height_deg), 1e-6)
        ref_area = ref_w * ref_h

        target_offsets: dict[str, tuple[float, float]] = {}
        theta = math.radians(solution.orientation_deg or 0.0)
        c, sn = math.cos(theta), math.sin(theta)
        for tid, bucket in grouped.items():
            target = bucket["target"]
            if target.ra_deg is None or target.dec_deg is None:
                continue
            east, north, _sep = self.knowledge_store._field_offsets_deg(
                solution.ra_deg, solution.dec_deg, target.ra_deg, target.dec_deg
            )
            target_offsets[tid] = (east * c + north * sn, -east * sn + north * c)

        def object_type_bonus(value: str | None) -> float:
            text = (value or "").lower()
            if any(k in text for k in ("planetary", "emission", "reflection", "nebula")):
                return 9.0
            if "globular" in text:
                return 8.0
            if any(k in text for k in ("open cluster", "cluster")):
                return 6.0
            if "galaxy" in text:
                return 7.0
            if any(k in text for k in ("dark", "association", "supernova")):
                return 4.0
            return 0.0

        ranked: list[tuple[float, object, float, str]] = []
        for tid, bucket in grouped.items():
            target = bucket["target"]
            target_entries = list(bucket["entries"])
            collection_count = len(bucket["collections"])

            canonical_norm = normalise_identifier(target.canonical_name)
            target_ids = catalogue_identifiers(target.canonical_name)
            if target.common_name:
                target_ids |= catalogue_identifiers(target.common_name)

            same_identifier = bool(current_ids and target_ids and current_ids & target_ids)
            same_subject = bool(
                (current_target_id and target.id == current_target_id)
                or (current_name and canonical_norm == current_name)
                or same_identifier
            )
            parent_norm = normalise_identifier(target.parent_region or "")
            related_to_subject = bool(
                parent_norm and any(
                    parent_norm == name or parent_norm in name or name in parent_norm
                    for name in current_parent_names if name
                )
            )

            pos = self._target_pixel_position(target)
            if pos is None or not image_rect.contains(QPointF(*pos)):
                continue

            tw = float(target.angular_width_deg or 0.0)
            th = float(target.angular_height_deg or 0.0)
            if tw <= 0.0 and th > 0.0:
                tw = th
            if th <= 0.0 and tw > 0.0:
                th = tw

            sep = float(bucket["sep"])
            # Large objects centred on the reference used to be discarded as
            # "the whole field" before we knew whether they were the loaded
            # image's principal subject.  That made a Prawn/Carina detail crop
            # impossible to recommend.  Keep the principal subject; only suppress
            # other field-sized catalogue regions.
            if (
                (not same_subject)
                and tw > 0.0
                and th > 0.0
                and sep < 0.10
                and tw * th >= 0.45 * ref_area
            ):
                continue

            geometry = 0.0
            best_angle = 0.0
            target_fill = 0.0
            coverage = 0.0

            # The photographer loaded this image because they are interested in
            # its principal subject.  A smaller rig should therefore first be
            # offered a detail/core composition of that SAME subject, rather than
            # an unrelated catalogue object that merely happens to fit well.
            # For the primary subject we intentionally do not demand that the rig
            # cover most of the object's catalogue extent: a detail crop is the
            # point of the recommendation.
            if same_subject:
                best_angle, primary_metrics = self._best_rig_rotation(rig, solution)
                primary_retained = float(primary_metrics[4])
                geometry = 105.0 + 25.0 * min(1.0, primary_retained / 0.35)
                coverage = 1.0
                if tw > 0.0 and th > 0.0:
                    target_fill = min(1.0, (tw * th) / max(rig_w * rig_h, 1e-9))
            elif tw > 0.0 and th > 0.0:
                for angle, rw, rh in ((0.0, rig_w, rig_h), (90.0, rig_h, rig_w)):
                    this_coverage = min(1.0, rw / tw) * min(1.0, rh / th)
                    this_fill = min(1.0, (tw * th) / max(rw * rh, 1e-9))
                    if this_fill < 0.04:
                        fill_quality = this_fill / 0.04 * 0.25
                    elif this_fill < 0.12:
                        fill_quality = 0.25 + 0.75 * (this_fill - 0.04) / 0.08
                    elif this_fill <= 0.70:
                        fill_quality = 1.0
                    else:
                        fill_quality = max(0.0, 1.0 - (this_fill - 0.70) / 0.30)

                    x, y = target_offsets.get(tid, (0.0, 0.0))
                    avail_x = max(0.0, ref_w / 2.0 - abs(x))
                    avail_y = max(0.0, ref_h / 2.0 - abs(y))
                    edge_x = min(1.0, (2.0 * avail_x) / max(rw, 1e-9))
                    edge_y = min(1.0, (2.0 * avail_y) / max(rh, 1e-9))
                    edge_quality = edge_x * edge_y
                    value = 58.0 * this_coverage + 32.0 * fill_quality + 10.0 * edge_quality
                    if value > geometry:
                        geometry = value
                        best_angle = angle
                        target_fill = this_fill
                        coverage = this_coverage
                if coverage < 0.55:
                    continue

            editorial = 0.0
            best_rank: int | None = None
            best_rating = 0.0
            best_tier = ""
            for entry in target_entries:
                if entry.rank is not None:
                    rank = int(entry.rank)
                    best_rank = rank if best_rank is None else min(best_rank, rank)
                if entry.rating is not None:
                    best_rating = max(best_rating, float(entry.rating))
                tier = str(entry.tier or "").upper()
                if "TOP 25" in tier:
                    best_tier = "TOP 25"
                elif "TOP 50" in tier and best_tier != "TOP 25":
                    best_tier = "TOP 50"
                elif "TOP 100" in tier and not best_tier:
                    best_tier = "TOP 100"

            if best_rank is not None:
                editorial += max(0.0, 18.0 - min(float(best_rank), 180.0) / 10.0)
            editorial += min(10.0, max(0.0, best_rating))
            editorial += {"TOP 25": 18.0, "TOP 50": 14.0, "TOP 100": 9.0}.get(best_tier, 0.0)
            editorial += min(10.0, max(0, collection_count - 1) * 5.0)
            editorial += object_type_bonus(target.object_type)

            identity_bonus = 5.0 if target_ids else 0.0
            if target.common_name and normalise_identifier(target.common_name) != canonical_norm:
                identity_bonus += 4.0

            companion_count = 0
            cx, cy = target_offsets.get(tid, (0.0, 0.0))
            rw, rh = (rig_w, rig_h) if best_angle < 45.0 else (rig_h, rig_w)
            for other_id, (ox, oy) in target_offsets.items():
                if other_id == tid:
                    continue
                if abs(ox - cx) <= rw / 2.0 and abs(oy - cy) <= rh / 2.0:
                    companion_count += 1
            companion_bonus = min(12.0, companion_count * 4.0)

            # Continuity with the photographer's chosen subject outranks a
            # peripheral object with a strong catalogue score.  Named structures
            # explicitly recorded as part of the same parent region come next.
            continuity_bonus = 180.0 if same_subject else (70.0 if related_to_subject else 0.0)

            if (not same_subject) and tw > 0.0 and th > 0.0 and target_fill < 0.04 and editorial < 20.0:
                continue
            if (not same_subject) and geometry <= 0.0 and editorial < 14.0:
                continue

            score = (
                geometry
                + min(editorial, 48.0)
                + identity_bonus
                + companion_bonus
                + continuity_bonus
            )

            if same_subject:
                quality = "Primary subject detail"
            elif related_to_subject:
                quality = "Related feature"
            elif target_fill >= 0.12 and coverage >= 0.95:
                quality = "Strong subfield"
            elif target_fill >= 0.06 and coverage >= 0.85:
                quality = "Good subfield"
            elif coverage >= 0.75:
                quality = "Usable subfield"
            else:
                quality = "Curated subtarget"

            details: list[str] = []
            if same_subject:
                _primary_angle, primary_metrics = self._best_rig_rotation(rig, solution)
                details.append(f"{int(round(float(primary_metrics[4]) * 100))}% of reference")
            elif tw > 0.0 and th > 0.0:
                details.append(f"{int(round(target_fill * 100))}% frame fill")
            if companion_count:
                details.append(f"{companion_count} nearby curated object" + ("s" if companion_count != 1 else ""))
            if collection_count > 1:
                details.append(f"in {collection_count} collections")
            description = quality + ((" · " + " · ".join(details[:2])) if details else "")
            ranked.append((score, target, best_angle, description))

        ranked.sort(key=lambda item: item[0], reverse=True)
        return ranked[:limit]

    def _viewer_pixel_to_sky(self, x: float, y: float) -> tuple[float, float] | None:
        """Convert a source-image pixel position back to ICRS RA/Dec.

        This is the exact inverse of ``_target_pixel_position`` and is what lets
        a manually dragged rig frame become an actionable celestial framing.
        """
        if self.current_solution is None:
            return None
        width_px, height_px = self.current_image_size
        if width_px <= 0 or height_px <= 0:
            return None
        scale_deg = max(float(self.current_solution.pixel_scale_arcsec), 1e-9) / 3600.0
        dx = (float(x) - (width_px - 1) / 2.0) * scale_deg
        # Invert the display-space Y flip used by _target_pixel_position().
        dy = -(float(y) - (height_px - 1) / 2.0) * scale_deg
        theta = math.radians(float(self.current_solution.orientation_deg or 0.0))
        parity = float(self.current_solution.parity or 1.0) or 1.0
        c, sn = math.cos(theta), math.sin(theta)
        east_deg = c * dx + sn * dy
        north_deg = parity * (-sn * dx + c * dy)
        centre = SkyCoord(
            ra=self.current_solution.ra_deg,
            dec=self.current_solution.dec_deg,
            unit=("deg", "deg"),
            frame="icrs",
        )
        # spherical_offsets_by accepts angular quantities; importing Angle here
        # avoids adding another top-level dependency just for this small inverse.
        from astropy.coordinates import Angle
        coord = centre.spherical_offsets_by(Angle(east_deg, unit="deg"), Angle(north_deg, unit="deg"))
        return float(coord.ra.deg), float(coord.dec.deg)

    @staticmethod
    def _format_framing_coords(ra_deg: float, dec_deg: float) -> tuple[str, str]:
        coord = SkyCoord(ra=ra_deg, dec=dec_deg, unit=("deg", "deg"), frame="icrs")
        ra_text = coord.ra.to_string(unit="hour", sep=":", precision=1, pad=True)
        dec_text = coord.dec.to_string(unit="deg", sep=":", precision=0, alwayssign=True, pad=True)
        return ra_text, dec_text

    def _clear_working_framing(self) -> None:
        self._working_framing = None
        self._accepted_framing = None
        if hasattr(self, "place_working_framing_button"):
            self.place_working_framing_button.blockSignals(True)
            self.place_working_framing_button.setChecked(False)
            self.place_working_framing_button.blockSignals(False)
            self.place_working_framing_button.setText("Reframe")
        self.viewer.set_placement_mode(False)
        if hasattr(self, "working_framing_panel"):
            self.working_framing_panel.hide()
        self._apply_active_rig_emphasis()
        self._refresh_nina_export()

    def _toggle_working_framing_placement(self, enabled: bool) -> None:
        if enabled and not self._working_framing:
            self.place_working_framing_button.blockSignals(True)
            self.place_working_framing_button.setChecked(False)
            self.place_working_framing_button.blockSignals(False)
            return
        self.viewer.set_placement_mode(enabled)
        self.place_working_framing_button.setText("Done" if enabled else "Reframe")
        if enabled:
            self.statusBar().showMessage(
                "Reframe mode: click anywhere in the image to move the working frame or mosaic centre; click Done when finished."
            )
        else:
            self.statusBar().showMessage("Reframing finished. Ordinary image clicks are locked again.", 3000)

    def _enable_working_framing_placement(self) -> None:
        """Keep reframing safely disarmed until the user explicitly requests it.

        dev17e uses a persistent Reframe mode: selecting a rig never arms image
        clicks, but once Reframe is pressed the user may reposition repeatedly
        until pressing Done.
        """
        self.viewer.set_placement_mode(False)
        if hasattr(self, "place_working_framing_button"):
            self.place_working_framing_button.blockSignals(True)
            self.place_working_framing_button.setChecked(False)
            self.place_working_framing_button.blockSignals(False)
            self.place_working_framing_button.setText("Reframe")

    def _place_working_framing_at(self, x: float, y: float) -> None:
        state = self._working_framing
        if not state:
            return
        rig_key = str(state.get("rig_key") or "")
        if not rig_key:
            return
        self.viewer.set_rig_center(rig_key, x, y)
        self._working_framing_changed()
        # dev17e: stay in Reframe mode after each placement so the composition
        # can be refined with as many clicks as needed. The user exits explicitly
        # with Done; only then do ordinary image clicks become harmless again.
        self.statusBar().showMessage(
            f"Working frame moved to image position {x:.0f}, {y:.0f}. Reframe mode is still active — click again or press Done.",
            4000,
        )

    def _working_framing_changed(self) -> None:
        """Keep RA/Dec and rotation in sync while the chosen rig is adjusted."""
        state = self._working_framing
        if not state or self.current_solution is None:
            return
        rig_key = str(state.get("rig_key") or "")
        item = self.viewer.overlays.get(rig_key)
        if item is None:
            return
        pos = item.scene_center()
        sky = self._viewer_pixel_to_sky(pos.x(), pos.y())
        if sky is None:
            return
        ra_deg, dec_deg = sky
        relative_rotation = float(self.viewer.rig_rotation(rig_key)) % 180.0
        # The rig angle is defined relative to the solved reference image.  The
        # corresponding sky position angle is therefore the reference PA plus
        # that offset, modulo the 180° symmetry of a rectangular sensor.
        sky_pa = (float(self.current_solution.orientation_deg or 0.0) + relative_rotation) % 180.0
        state.update(
            ra_deg=ra_deg,
            dec_deg=dec_deg,
            relative_rotation_deg=relative_rotation,
            sky_pa_deg=sky_pa,
        )
        self._accepted_framing = None
        label = str(state.get("label") or "Custom framing")
        rig_name = str(state.get("rig_name") or rig_key)
        ra_text, dec_text = self._format_framing_coords(ra_deg, dec_deg)
        self.working_framing_label.setText(
            "<b>Working framing</b><br>"
            f"{label}<br>"
            f"{rig_name}<br>"
            f"Centre&nbsp;&nbsp;RA {ra_text}&nbsp;&nbsp;Dec {dec_text}<br>"
            f"Camera PA&nbsp;&nbsp;{sky_pa:.1f}°<br>"
            "<span style='color:#8F99A8'>Use Reframe to reposition freely, then click Done; use the rig rotation controls for angle.</span>"
        )
        self.working_framing_panel.show()
        self._refresh_nina_export()

    def _nina_framing_state(self) -> dict[str, object] | None:
        """Return framing only when it belongs to the currently solved image.

        Export is deliberately fail-closed: loading a new unsolved reference must
        never leave the previous image's sky position available to N.I.N.A./ASIAIR.
        """
        if self.current_solution is None or not self.current_image_path:
            return None
        state = self._working_framing or self._accepted_framing
        if not state:
            return None
        required = ("ra_deg", "dec_deg", "sky_pa_deg")
        if not all(key in state for key in required):
            return None
        return state

    def _solution_orientation_known(self) -> bool:
        if self.current_solution is None:
            return False
        external = self.current_solution.solver.startswith("External —")
        if self.current_solution.orientation_known is not None:
            return bool(self.current_solution.orientation_known)
        return not external

    def _active_rig(self, state: dict[str, object]) -> Rig | None:
        key = str(state.get("rig_key") or "")
        return next((rig for rig in self.available_rigs if rig.key == key), None)

    def _apply_active_rig_emphasis(self) -> None:
        """Make the current working rig unmistakable in the image viewer.

        The Equipment Advisor recommendation and the user's active framing are
        different concepts.  When a rig has been selected for framing/export,
        dim the other rig overlays.  For a multi-pane mosaic, suppress the
        selected rig's original single-frame outline so the mosaic itself is
        the dominant geometry.
        """
        active_key = str((self._working_framing or {}).get("rig_key") or "")
        # RC22i ARDD: canvas dragging follows the explicit active-rig selection.
        self.viewer.set_active_drag_rig(active_key or None)
        mosaic_multi = False
        if active_key and hasattr(self, "mosaic_grid_combo"):
            try:
                state = self._nina_framing_state()
                if state is not None:
                    cols, rows = self._mosaic_grid(state)
                    mosaic_multi = cols * rows > 1
            except Exception:
                mosaic_multi = False
        for key, item in self.viewer.overlays.items():
            if not active_key:
                item.setOpacity(1.0)
            elif key == active_key:
                item.setOpacity(0.0 if mosaic_multi else 1.0)
            else:
                item.setOpacity(0.22)
        for key, check in self.rig_checks.items():
            if hasattr(check, "setActiveRig"):
                check.setActiveRig(bool(active_key and key == active_key))
        self._update_active_framing_rig_label()

    def _mosaic_grid(self, state: dict[str, object]) -> tuple[int, int]:
        text = self.mosaic_grid_combo.currentText() if hasattr(self, "mosaic_grid_combo") else "Single frame"
        if text == "Single frame":
            return 1, 1
        if text == "Auto":
            rig = self._active_rig(state)
            if rig is None or self.current_solution is None:
                return 1, 1
            overlap = float(self.mosaic_overlap_spin.value()) / 100.0
            def need(ref, panel):
                if panel >= ref: return 1
                return 1 + math.ceil((ref-panel) / max(panel*(1-overlap), 1e-9))
            return max(1, min(6, need(float(self.current_solution.image_width_deg), rig.fov_width_deg))), max(1, min(6, need(float(self.current_solution.image_height_deg), rig.fov_height_deg)))
        m = re.match(r"(\d+)\s*×\s*(\d+)", text)
        return (int(m.group(1)), int(m.group(2))) if m else (1, 1)

    def _mosaic_panes(self, state: dict[str, object]) -> list[dict[str, object]]:
        rig = self._active_rig(state)
        if rig is None:
            return []
        cols, rows = self._mosaic_grid(state)
        overlap = float(self.mosaic_overlap_spin.value()) / 100.0 if hasattr(self, "mosaic_overlap_spin") else 0.25
        pa = float(state["sky_pa_deg"]) % 180.0
        theta = math.radians(pa)
        step_x = rig.fov_width_deg * (1.0-overlap)
        step_y = rig.fov_height_deg * (1.0-overlap)
        centre = SkyCoord(ra=float(state["ra_deg"]), dec=float(state["dec_deg"]), unit="deg", frame="icrs")
        from astropy.coordinates import Angle
        panes=[]
        n=0
        for row in range(rows):
            for col in range(cols):
                n += 1
                x = (col-(cols-1)/2.0)*step_x
                y = ((rows-1)/2.0-row)*step_y
                east = x*math.cos(theta) - y*math.sin(theta)
                north = x*math.sin(theta) + y*math.cos(theta)
                c = centre.spherical_offsets_by(Angle(east, unit="deg"), Angle(north, unit="deg"))
                panes.append({"pane":n,"row":row+1,"col":col+1,"ra_deg":float(c.ra.deg),"dec_deg":float(c.dec.deg)})
        return panes

    def _asiair_csv_text(self, state: dict[str, object]) -> str:
        rig = self._active_rig(state)
        width_arcmin = rig.fov_width_deg * 60.0 if rig else 0.0
        height_arcmin = rig.fov_height_deg * 60.0 if rig else 0.0
        pa_text = f"{float(state['sky_pa_deg']) % 180.0:.2f}" if self._solution_orientation_known() else ""
        overlap_pct = int(self.mosaic_overlap_spin.value()) if hasattr(self, "mosaic_overlap_spin") else 25
        panes = self._mosaic_panes(state)
        header = "Pane, RA, DEC, Position Angle (East), Pane width (arcmins), Pane height (arcmins), Overlap, Row, Column"
        rows=[]
        multi=len(panes)>1
        for pane in panes:
            coord=SkyCoord(ra=pane["ra_deg"], dec=pane["dec_deg"], unit="deg", frame="icrs")
            ra_text=coord.ra.to_string(unit="hour", sep=("hr ", "' ", '"'), precision=0, pad=True)
            dec_text=coord.dec.to_string(unit="deg", sep=("º ", "' ", '"'), precision=0, alwayssign=False, pad=True)
            row = pane["row"] if multi else "-"
            col = pane["col"] if multi else "-"
            rows.append(f"Pane {pane['pane']}, {ra_text}, {dec_text}, {pa_text}, {width_arcmin:.2f}, {height_arcmin:.2f}, {overlap_pct}%, {row}, {col}")
        return header + "\n" + "\n".join(rows)

    def _clear_mosaic_preview(self) -> None:
        for item in getattr(self, "_mosaic_preview_items", []):
            try: self.viewer.scene.removeItem(item)
            except Exception: pass
        self._mosaic_preview_items = []

        # RC12: mosaic and single-frame previews are mutually exclusive.
        # Restore the single-frame overlay that was hidden when mosaic mode began.
        hidden_key = getattr(self, "_mosaic_hidden_single_rig_key", None)
        if hidden_key:
            overlay = self.viewer.overlays.get(hidden_key)
            if overlay is not None:
                check = self.rig_checks.get(hidden_key)
                overlay.setVisible(bool(check is None or check.isChecked()))
        self._mosaic_hidden_single_rig_key = None

    def _refresh_mosaic_preview(self) -> None:
        self._clear_mosaic_preview()
        state=self._nina_framing_state()
        if state is None: return
        panes=self._mosaic_panes(state)
        cols,rows=self._mosaic_grid(state)
        if len(panes)<=1:
            if hasattr(self,"mosaic_summary_label"): self.mosaic_summary_label.setText("Single-frame export.")
            return
        rig=self._active_rig(state)
        if rig is None: return

        # RC12: once a real mosaic is active, hide that rig's ordinary
        # single-frame overlay. The panes themselves become the framing preview.
        single_overlay = self.viewer.overlays.get(rig.key)
        if single_overlay is not None:
            single_overlay.setVisible(False)
            self._mosaic_hidden_single_rig_key = rig.key

        scale=max(float(self.current_solution.pixel_scale_arcsec),1e-9)/3600.0
        w=rig.fov_width_deg/scale; h=rig.fov_height_deg/scale
        theta=math.radians(float(self.viewer.rig_rotation(str(state.get("rig_key") or ""))))
        c,sn=math.cos(theta),math.sin(theta)
        for pane in panes:
            coord=type("T",(),{
                "ra_deg": pane["ra_deg"],
                "dec_deg": pane["dec_deg"],
                "canonical_name": "",
                "common_name": "",
                "aliases": [],
            })()
            pos=self._target_pixel_position(coord)
            if pos is None: continue
            cx,cy=pos; pts=[]
            for dx,dy in ((-w/2,-h/2),(w/2,-h/2),(w/2,h/2),(-w/2,h/2)):
                pts.append(QPointF(cx+c*dx-sn*dy, cy+sn*dx+c*dy))
            item=self.viewer.scene.addPolygon(QPolygonF(pts), QPen(QColor(rig.colour),2), QBrush(Qt.BrushStyle.NoBrush))
            item.setZValue(11); self._mosaic_preview_items.append(item)
            # RC15: presentation only — restore rig name / pane numbering.
            pane_no = int(pane.get("index", len(self._mosaic_preview_items)))
            label_text = f"{rig.name} — {pane_no}" if pane_no == 1 else str(pane_no)
            label = self.viewer.scene.addSimpleText(label_text)
            label.setBrush(QBrush(QColor(rig.colour)))
            label.setPos(pts[0] + QPointF(4, 3))
            label.setZValue(12)
            self._mosaic_preview_items.append(label)
        if hasattr(self,"mosaic_summary_label"):
            self.mosaic_summary_label.setText(f"{cols} × {rows} mosaic · {len(panes)} panes · {self.mosaic_overlap_spin.value()}% overlap. Click-to-place moves the mosaic centre.")

    def _choose_rig_for_mosaic(self, requested_pattern: str | None = None) -> bool:
        """Choose an active rig when any mosaic pattern has no framing context yet."""
        if not self.available_rigs or self.current_solution is None:
            return False
        names = [rig.name for rig in self.available_rigs]
        name, accepted = QInputDialog.getItem(
            self,
            "Choose mosaic rig",
            "Which imaging setup should AstroFrame use for this mosaic?",
            names,
            0,
            False,
        )
        if not accepted:
            return False
        rig = next((r for r in self.available_rigs if r.name == name), None)
        if rig is None:
            return False
        check = self.rig_checks.get(rig.key)
        if check is not None:
            check.setChecked(True)
        self._activate_reference_rig_framing(rig.key)
        # RC18: changing rigs normally resets mosaic settings to Single frame.
        # When the rig chooser was invoked by a mosaic selection, restore the
        # exact pattern the user requested (Auto, 2 x 1, 2 x 2, etc.) so the
        # mosaic is created immediately without a second click.
        if hasattr(self, "mosaic_grid_combo") and requested_pattern:
            self.mosaic_grid_combo.blockSignals(True)
            self.mosaic_grid_combo.setCurrentText(requested_pattern)
            self.mosaic_grid_combo.blockSignals(False)
        self._apply_active_rig_emphasis()
        QTimer.singleShot(0, self._update_equipment_advisor)
        return True

    def _select_rig_from_image_label(self, rig_key: str) -> None:
        """Select a rig via its label only; the frame body keeps its old behaviour."""
        if self.current_solution is None:
            return
        check = self.rig_checks.get(rig_key)
        if check is not None:
            check.setChecked(True)
        self._activate_reference_rig_framing(rig_key)
        self._apply_active_rig_emphasis()
        self._refresh_mosaic_preview()
        QTimer.singleShot(0, self._update_equipment_advisor)
        self.statusBar().showMessage("Rig selected from image label.", 2500)

    def _mosaic_controls_changed(self, *args) -> None:
        # RC18: every real mosaic pattern needs an active rig, not just Auto.
        # If none is active, ask once, preserve the requested pattern through
        # rig activation, then build the mosaic immediately.
        if hasattr(self, "mosaic_grid_combo"):
            requested_pattern = self.mosaic_grid_combo.currentText()
            if (
                requested_pattern != "Single frame"
                and self._nina_framing_state() is None
                and self.current_solution is not None
            ):
                if not self._choose_rig_for_mosaic(requested_pattern):
                    self.mosaic_grid_combo.blockSignals(True)
                    self.mosaic_grid_combo.setCurrentText("Single frame")
                    self.mosaic_grid_combo.blockSignals(False)
        self._refresh_mosaic_preview()
        self._refresh_nina_export()

    def _refresh_nina_export(self) -> None:
        if not hasattr(self, "nina_handoff_label"):
            return
        state = self._nina_framing_state()
        enabled = state is not None
        for button in getattr(self, "nina_export_buttons", ()):
            button.setEnabled(enabled)
        for button in getattr(self, "asiair_export_buttons", ()):
            button.setEnabled(enabled)
        if state is None:
            self.nina_handoff_label.setText(
                "<b>N.I.N.A. handoff</b><br>Solve or import astrometry for this image, then choose a rig or framing."
            )
            if hasattr(self, "asiair_handoff_label"):
                self.asiair_handoff_label.setText("Solve or import astrometry for this image, then choose a framing to create an ASIAIR-compatible single-pane CSV.")
            return
        ra = float(state["ra_deg"])
        dec = float(state["dec_deg"])
        pa_known = self._solution_orientation_known()
        pa = float(state["sky_pa_deg"]) % 180.0
        ra_text, dec_text = self._format_framing_coords(ra, dec)
        label = str(state.get("label") or "Custom framing")
        rig = str(state.get("rig_name") or state.get("rig_key") or "")
        pa_display = f"{pa:.1f}°" if pa_known else "Unknown"
        self.nina_handoff_label.setText(
            "<b>N.I.N.A. handoff</b><br>"
            f"{label}<br>"
            f"{rig}<br>"
            f"RA&nbsp;&nbsp;{ra_text}<br>"
            f"Dec&nbsp;&nbsp;{dec_text}<br>"
            f"Rotation&nbsp;&nbsp;{pa_display}<br>"
            "<span style='color:#8F99A8'>Copy coordinates pastes RA + Dec into N.I.N.A.; enter rotation separately.</span>"
        )
        if hasattr(self, "copy_nina_rotation_button"):
            self.copy_nina_rotation_button.setEnabled(enabled and pa_known)
        if hasattr(self, "asiair_handoff_label"):
            note = "PA included." if pa_known else "PA left blank because orientation is unknown."
            cols, rows = self._mosaic_grid(state)
            pane_count = cols * rows
            overlap = self.mosaic_overlap_spin.value() if hasattr(self, "mosaic_overlap_spin") else 25
            mode = "Single pane" if pane_count == 1 else f"{cols} × {rows} mosaic · {pane_count} panes"
            self.asiair_handoff_label.setText(f"{mode} · {overlap}% overlap · {note}")
        self._refresh_mosaic_preview()

    def _copy_nina_value(self, kind: str) -> None:
        state = self._nina_framing_state()
        if state is None:
            self.statusBar().showMessage("Choose a rig or subfield in Equipment Advisor before copying N.I.N.A. values.", 5000)
            return
        ra = float(state["ra_deg"])
        dec = float(state["dec_deg"])
        pa = float(state["sky_pa_deg"]) % 180.0
        ra_text, dec_text = self._format_framing_coords(ra, dec)
        label = str(state.get("label") or "AstroFrame framing")
        rig = str(state.get("rig_name") or state.get("rig_key") or "")
        if kind == "ra":
            text = ra_text
            what = "RA"
        elif kind == "dec":
            text = dec_text
            what = "Dec"
        elif kind == "rotation":
            if not self._solution_orientation_known():
                self.statusBar().showMessage("Rotation is unknown for this astrometric solution.", 5000)
                return
            text = f"{pa:.1f}"
            what = "rotation"
        else:
            # N.I.N.A. recognises a coordinate pair pasted into either coordinate
            # field and fills RA + Dec together.  Rotation is a separate N.I.N.A.
            # control and is intentionally not implied to transfer here.
            text = f"{ra_text} {dec_text}"
            what = "N.I.N.A. coordinates"
        QApplication.clipboard().setText(text)
        self.statusBar().showMessage(f"Copied {what} to clipboard.", 5000)

    def _copy_nina_ra(self) -> None:
        self._copy_nina_value("ra")

    def _copy_nina_dec(self) -> None:
        self._copy_nina_value("dec")

    def _copy_nina_rotation(self) -> None:
        self._copy_nina_value("rotation")

    def _copy_nina_all(self) -> None:
        self._copy_nina_value("all")

    def _copy_asiair_csv(self) -> None:
        state = self._nina_framing_state()
        if state is None:
            self.statusBar().showMessage("Choose a rig or subfield before exporting to ASIAIR.", 5000)
            return
        QApplication.clipboard().setText(self._asiair_csv_text(state))
        self.statusBar().showMessage("Copied ASIAIR / Telescopius CSV to clipboard.", 5000)

    def _save_asiair_csv(self) -> None:
        state = self._nina_framing_state()
        if state is None:
            self.statusBar().showMessage("Choose a rig or subfield before exporting to ASIAIR.", 5000)
            return
        label = re.sub(r"[^A-Za-z0-9._-]+", "_", str(state.get("label") or "AstroFrame" )).strip("_") or "AstroFrame"
        path, _ = QFileDialog.getSaveFileName(self, "Save ASIAIR framing CSV", f"{label}_ASIAIR.csv", "CSV files (*.csv)")
        if not path:
            return
        Path(path).write_text(self._asiair_csv_text(state) + "\n", encoding="utf-8")
        self.statusBar().showMessage(f"Saved ASIAIR CSV: {Path(path).name}", 7000)

    def _accept_working_framing(self) -> None:
        if not self._working_framing:
            return
        self._working_framing_changed()
        if not self._working_framing:
            return
        self._accepted_framing = dict(self._working_framing)
        self._refresh_nina_export()
        ra = float(self._accepted_framing.get("ra_deg", 0.0))
        dec = float(self._accepted_framing.get("dec_deg", 0.0))
        pa = float(self._accepted_framing.get("sky_pa_deg", 0.0))
        ra_text, dec_text = self._format_framing_coords(ra, dec)
        label = str(self._accepted_framing.get("label") or "framing")
        self.statusBar().showMessage(
            f"Framing retained: {label} · RA {ra_text} · Dec {dec_text} · PA {pa:.1f}°",
            10000,
        )
        self.use_working_framing_button.setText("Framing selected ✓")
        QTimer.singleShot(1800, lambda: self.use_working_framing_button.setText("Use this framing"))

    def _preview_alternative_framing(
        self, rig_key: str, target_id: str, angle_deg: float
    ) -> None:
        target = self.knowledge_store.get_target(target_id)
        if target is None:
            return
        rig = next((r for r in self.available_rigs if r.key == rig_key), None)
        check = self.rig_checks.get(rig_key)
        if check is not None:
            check.setChecked(True)
        label = target.common_name or target.canonical_name
        self._working_framing = {
            "rig_key": rig_key,
            "rig_name": rig.name if rig is not None else rig_key,
            "target_id": target_id,
            "label": label,
        }
        self._accepted_framing = None
        self.viewer.set_rig_rotation(rig_key, angle_deg)
        pos = self._target_pixel_position(target)
        if pos is None:
            self._clear_working_framing()
            return
        self.viewer.set_rig_center(rig_key, *pos)
        self._working_framing_changed()
        self._enable_working_framing_placement()
        self.statusBar().showMessage(
            f"Working framing: {label} with {rig.name if rig is not None else rig_key}. Click the image to move the frame; choose Done when finished.",
            7000,
        )

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

    def _schedule_equipment_advisor_refresh(self) -> None:
        """Coalesce Advisor rebuilds and run after the current UI update finishes.

        Cached images can restore a plate solution and target metadata synchronously
        while ImageViewer is still completing its image-loaded signal chain.  Rebuilding
        the Advisor repeatedly during that chain can leave the section hidden/stale on
        some images.  One deferred refresh gives the viewer and reference UI a chance
        to settle first.
        """
        if not hasattr(self, "advisor_section"):
            return
        if self._advisor_refresh_pending:
            return
        self._advisor_refresh_pending = True

        def run() -> None:
            self._advisor_refresh_pending = False
            try:
                self._update_equipment_advisor()
                self._update_imaging_verdict()
            except Exception as exc:
                self._append_solver_log(
                    f"Equipment Advisor refresh failed: {type(exc).__name__}: {exc}"
                )

        QTimer.singleShot(0, run)

    def _advisor_observability_context(self):
        """Selected-night observability summary for the Equipment Advisor."""
        if self.current_solution is None or not self.observer_profile.is_configured:
            return None
        try:
            target = SkyCoord(
                ra=self.current_solution.ra_deg,
                dec=self.current_solution.dec_deg,
                unit=("deg", "deg"),
                frame="icrs",
            )
            return observability_for_date(
                target, self.observer_profile, self._observability_selected_date()
            )
        except Exception as exc:
            self.general_log_ui.emit(
                f"Equipment Advisor observability unavailable: {type(exc).__name__}: {exc}"
            )
            return None

    @staticmethod
    def _advisor_night_grade(obs) -> str:
        if obs is None:
            return "OBSERVABILITY UNAVAILABLE"
        if not obs.has_astronomical_darkness:
            return "NO ASTRONOMICAL DARKNESS"
        if not obs.has_useful_window:
            if obs.maximum_altitude_deg < obs.minimum_altitude_deg:
                return "TARGET TOO LOW"
            return "NO DARK IMAGING WINDOW"
        if obs.useful_duration_hours >= 4 and obs.maximum_altitude_deg >= 45:
            return "EXCELLENT NIGHT GEOMETRY"
        if obs.useful_duration_hours >= 2:
            return "GOOD NIGHT GEOMETRY"
        return "SHORT NIGHT WINDOW"

    def _update_imaging_verdict(self) -> None:
        """dev15a combined framing + site + season + selected-night verdict."""
        if not hasattr(self, "verdict_section"):
            return
        if self.current_solution is None:
            self.verdict_section.hide()
            return

        rigs = [rig for rig in self.available_rigs if self.rig_checks.get(rig.key) is not None and self.rig_checks[rig.key].isChecked()]
        if not rigs:
            self.imaging_verdict.setText(
                "<b>SETUP NEEDED</b><br>Add an imaging setup so AstroFrame can combine framing with observability."
            )
            self.verdict_section.show()
            return

        ranked = []
        for rig in rigs:
            best_angle, best = self._best_rig_rotation(rig, self.current_solution)
            ranked.append((best[0], best_angle, best, rig))
        ranked.sort(key=lambda item: item[0], reverse=True)
        score, angle, best, rig = ranked[0]

        if score >= 80:
            framing = "excellent framing"
        elif score >= 60:
            framing = "good framing"
        elif score >= 40:
            framing = "workable framing"
        else:
            framing = "no close single-frame match"

        obs = self._advisor_observability_context()
        season = getattr(self, "_current_observing_season", None)

        # Geography is decisive: don't bury an impossible target beneath a rig score.
        if isinstance(season, ObservingSeasonResult) and season.classification == "NOT_VISIBLE":
            heading = "NO — NOT FROM THIS LOCATION"
            recommendation = (
                f"Your equipment can frame the field, but it never rises from {self.observer_profile.location_name}. "
                f"Maximum altitude {season.maximum_possible_altitude_deg:.0f}°."
            )
        elif isinstance(season, ObservingSeasonResult) and season.classification == "TOO_LOW":
            heading = "NO — BELOW YOUR ALTITUDE LIMIT"
            recommendation = (
                f"The field rises, but only to {season.maximum_possible_altitude_deg:.0f}°, below your "
                f"{season.minimum_altitude_deg:.0f}° imaging limit."
            )
        elif score < 40:
            heading = "TRY A DIFFERENT FRAMING"
            recommendation = "No saved rig closely reproduces this reference in a single frame; check the Advisor for the closest match or mosaic suggestion."
        elif isinstance(season, ObservingSeasonResult) and season.classification == "OUT_OF_SEASON":
            heading = "YES — BUT NOT NOW"
            start = season.next_season_start or season.season_start
            when = start.strftime('%d %b %Y') if start else "the next observing season"
            recommendation = f"Use {rig.name} at about {angle:.1f}°. The target returns to a useful astronomical window around {when}."
        elif isinstance(season, ObservingSeasonResult) and (
            not season.classification.startswith("YEAR_ROUND")
            and (season.longest_useful_duration_hours < 3.0 or season.maximum_possible_altitude_deg < season.minimum_altitude_deg + 5.0)
        ):
            heading = "YES — BUT MARGINAL FROM THIS SITE"
            recommendation = (
                f"{rig.name} is the best framing match, but the target has a limited season: "
                f"up to {self._format_duration(season.longest_useful_duration_hours)} useful darkness and "
                f"a maximum altitude of {season.maximum_possible_altitude_deg:.0f}°."
            )
        elif obs is not None and obs.has_useful_window:
            if obs.useful_duration_hours >= 4 and obs.maximum_altitude_deg >= 45:
                heading = "YES — GOOD TARGET TONIGHT"
            else:
                heading = "YES — SHOOTABLE TONIGHT"
            recommendation = (
                f"Use {rig.name} at about {angle:.1f}°. Tonight offers "
                f"{self._format_duration(obs.useful_duration_hours)} useful darkness; Moon interference {obs.moon_interference}."
            )
        else:
            heading = "YES — BUT NOT TONIGHT"
            recommendation = f"{rig.name} is the best framing match, but the selected night has no useful dark imaging window."

        season_line = "Analyse observing season for the annual picture."
        if isinstance(season, ObservingSeasonResult):
            if season.classification.startswith("YEAR_ROUND"):
                season_line = "Season: year-round"
            elif season.season_start and season.season_end:
                season_line = f"Season: {season.season_start.strftime('%d %b')}–{season.season_end.strftime('%d %b')}"

        night_line = "Selected night: observing site not configured"
        if obs is not None:
            if obs.has_useful_window:
                night_line = (
                    f"Selected night: {self._format_duration(obs.useful_duration_hours)} useful · "
                    f"peak {obs.maximum_altitude_deg:.0f}° · Moon {obs.moon_interference}"
                )
            else:
                night_line = f"Selected night: {self._advisor_night_grade(obs).lower()}"

        self.imaging_verdict.setText(
            f"<b>{heading}</b><br>"
            f"<b>Best rig:</b> {rig.name} · {framing} · {score:.0f}% · camera angle {angle:.1f}°<br>"
            f"{season_line}<br>{night_line}<br><br>"
            f"<b>Recommendation:</b> {recommendation}"
        )
        self.verdict_section.show()

    def _update_equipment_advisor(self) -> None:
        if not hasattr(self, "advisor_section"):
            return

        self._clear_advisor_results()

        if self.current_solution is None:
            self.advisor_intro.setText(
                "Plate solve the reference image to compare your saved setups."
            )
            self.advisor_observability.hide()
            self.advisor_section.hide()
            return

        # RC22f: Equipment Advisor must rank only rigs the user has marked available.
        # The Part 4 toggles are the source of truth for both overlay visibility
        # and recommendation eligibility.
        rigs = [
            rig for rig in self.available_rigs
            if self.rig_checks.get(rig.key) is not None
            and self.rig_checks[rig.key].isChecked()
        ]
        if not rigs:
            self.advisor_intro.setText(
                "Select at least one available imaging setup to get a recommendation."
            )
            self.advisor_observability.hide()
            self.advisor_section.show()
            return

        advisor_obs = self._advisor_observability_context()
        if advisor_obs is not None:
            if advisor_obs.has_useful_window:
                window_text = (
                    f"{self._format_local_time(advisor_obs.useful_start)}–"
                    f"{self._format_local_time(advisor_obs.useful_end)} · "
                    f"{self._format_duration(advisor_obs.useful_duration_hours)} usable"
                )
            else:
                window_text = "no usable dark window"
            self.advisor_observability.setText(
                f"<b>{self._advisor_night_grade(advisor_obs)}</b><br>"
                f"{advisor_obs.evening_date.strftime('%d %b %Y')} · {window_text}<br>"
                f"Peak {advisor_obs.maximum_altitude_deg:.0f}° · "
                f"Moon {advisor_obs.moon_interference}"
            )
            self.advisor_observability.show()
        else:
            self.advisor_observability.setText(
                "Set up an Observing Site to combine framing with a night-time visibility verdict."
            )
            self.advisor_observability.show()

        ranked = []
        for rig in rigs:
            best_angle, best = self._best_rig_rotation(rig, self.current_solution)
            current_angle = self.viewer.rig_rotation(rig.key) % 180.0
            current = self._orientation_metrics(
                rig, self.current_solution, angle_deg=current_angle
            )
            ranked.append((best[0], best_angle, best, current_angle, current, rig))

        ranked.sort(key=lambda item: item[0], reverse=True)
        top_score = ranked[0][0]

        if top_score < 40:
            self.advisor_intro.setText(
                "<b>No close single-frame match.</b><br>"
                "AstroFrame has searched camera rotation from 0–180° for every "
                "saved setup. None closely reproduces the complete reference framing."
            )
            mosaic = self._mosaic_suggestion(rigs, self.current_solution)
            if mosaic is not None:
                rig, cols, rows, rotated = mosaic
                mosaic_label = QLabel(
                    "<b>Suggestion</b><br>"
                    f"Try a {cols}×{rows} mosaic with <b>{rig.name}</b> using about 15% overlap."
                    + ("<br>A portrait camera orientation gives the more efficient grid." if rotated else "")
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
                "AstroFrame searched the full 0–180° camera rotation range for each "
                "setup. Click a result to preview its recommended centred framing."
            )

        for rank, (best_score, best_angle, best, current_angle, current, rig) in enumerate(
            ranked, start=1
        ):
            best_note = best[1]
            score, note, _width_ratio, _height_ratio, retained_fraction = current

            result = QFrame()
            active_key = str((self._working_framing or {}).get("rig_key") or "")
            is_selected = active_key == rig.key
            is_recommended = rank == 1 and top_score >= 40
            if is_selected:
                result.setObjectName("selectedRigCard")
            elif is_recommended:
                result.setObjectName("recommendedRigCard")
            else:
                result.setObjectName("rigCard")
            # RC22e: carry the same restrained rig colour into Advisor results.
            # Keep the body neutral; the colour is an identifier, not decoration.
            colour = QColor(rig.colour)
            rr, gg, bb = colour.red(), colour.green(), colour.blue()
            advisor_tint = f"rgba({rr}, {gg}, {bb}, 22)" if is_selected else "#171C23"
            border_width = 2 if is_selected else 1
            result.setStyleSheet(
                f"QFrame {{ background: {advisor_tint}; border: {border_width}px solid #46515F; "
                f"border-left: 7px solid {rig.colour}; border-radius: 10px; }} "
                f"QLabel {{ border: none; background: transparent; }}"
            )
            result_layout = QVBoxLayout(result)
            result_layout.setContentsMargins(14, 10, 10, 10)
            result_layout.setSpacing(7)

            button = QPushButton()
            status_line = "✓ SELECTED" if is_selected else ("★ RECOMMENDED" if is_recommended else "")
            title_line = f"{rank}. {rig.name} · {best_score:.0f}%"
            if status_line:
                button.setText(
                    f"{title_line}\n"
                    f"{status_line}\n"
                    f"{best_note}\n"
                    f"Best rotation {best_angle:.1f}°"
                )
                button.setMinimumHeight(142)
            else:
                button.setText(
                    f"{title_line}\n"
                    f"{best_note}\n"
                    f"Best rotation {best_angle:.1f}°"
                )
                button.setMinimumHeight(122)
            # Three deliberately short lines read much better in the narrow
            # Equipment Advisor column than one long, clipped second line.
            # Give the three-line verdict enough breathing room at the narrowest
            # supported sidebar width.  The old 88 px minimum could still clip
            # line 2 on macOS with larger UI/font scaling.
            button.setToolTip(
                "Show this setup centred on the reference at AstroFrame's best rotation."
            )
            if is_selected:
                button.setObjectName("selectedRigButton")
            elif is_recommended:
                button.setObjectName("recommendedRigButton")
            button.clicked.connect(
                lambda _checked=False, key=rig.key, angle=best_angle:
                    self._show_advisor_setup(key, angle)
            )
            result_layout.addWidget(button)

            analysis_lines = self._framing_analysis_lines(
                rig,
                self.current_solution,
                current_angle,
                retained_fraction,
            )
            analysis = QLabel(
                "<b>Current preview</b><br>"
                f"{current_angle:.1f}° · {score:.0f}%<br>"
                f"{note}<br>"
                + "<br>".join(analysis_lines)
            )
            analysis.setObjectName("helpText")
            analysis.setWordWrap(True)
            result_layout.addWidget(analysis)

            # First combined verdict: rig geometry + selected-night astronomy.
            # Weather is intentionally not implied here.
            if advisor_obs is not None:
                if advisor_obs.has_useful_window:
                    night_phrase = (
                        f"{self._format_duration(advisor_obs.useful_duration_hours)} dark imaging · "
                        f"Moon {advisor_obs.moon_interference}"
                    )
                else:
                    night_phrase = self._advisor_night_grade(advisor_obs).title()
                combined = QLabel(
                    f"<b>Imaging verdict</b> · {best_note}<br>{night_phrase}"
                )
                combined.setObjectName("helpText")
                combined.setWordWrap(True)
                result_layout.addWidget(combined)

            # Individual rotation control: moving this slider affects this rig only.
            # The spin box provides precise entry; the slider remains useful for
            # exploration; and the snap button makes AstroFrame's recommendation
            # a one-click action rather than a fiddly slider target.
            rotation_row = QHBoxLayout()
            rotation_caption = QLabel("Rig rotation")
            rotation_caption.setObjectName("fieldLabel")
            rotation_spin = QDoubleSpinBox()
            rotation_spin.setRange(0.0, 179.9)
            rotation_spin.setDecimals(1)
            rotation_spin.setSingleStep(0.1)
            rotation_spin.setSuffix("°")
            rotation_spin.setValue(current_angle)
            rotation_spin.setMinimumWidth(92)
            rotation_spin.setToolTip("Enter an exact camera rotation for this setup.")
            rotation_row.addWidget(rotation_caption)
            rotation_row.addStretch()
            rotation_row.addWidget(rotation_spin)
            result_layout.addLayout(rotation_row)

            rotation_slider = QSlider(Qt.Orientation.Horizontal)
            rotation_slider.setRange(0, 1799)
            rotation_slider.setSingleStep(1)
            rotation_slider.setPageStep(50)
            rotation_slider.setValue(int(round(current_angle * 10.0)))
            rotation_slider.setToolTip(
                "Drag to explore rotation. Use the number box for an exact angle."
            )

            # Bind the rig key into each callback.  These handlers are created
            # inside the advisor loop; relying on a shared outer helper makes
            # every slider eventually act on the last rig in the list (Python's
            # late-bound closure behaviour).
            def slider_rotation(
                value: int, *, spin=rotation_spin, key=rig.key
            ) -> None:
                angle = value / 10.0
                spin.blockSignals(True)
                spin.setValue(angle)
                spin.blockSignals(False)
                self._set_advisor_rotation(key, angle)

            def spin_rotation(
                value: float, *, slider=rotation_slider, key=rig.key
            ) -> None:
                slider.blockSignals(True)
                slider.setValue(int(round(value * 10.0)))
                slider.blockSignals(False)
                self._set_advisor_rotation(key, value)

            rotation_slider.valueChanged.connect(slider_rotation)
            rotation_slider.sliderReleased.connect(self._update_equipment_advisor)
            rotation_spin.valueChanged.connect(spin_rotation)
            rotation_spin.editingFinished.connect(self._update_equipment_advisor)
            result_layout.addWidget(rotation_slider)

            best_button = QPushButton(f"Use best rotation · {best_angle:.1f}°")
            best_button.setToolTip(
                "Apply AstroFrame's calculated best rotation to this setup exactly."
            )
            best_button.clicked.connect(
                lambda _checked=False, key=rig.key, angle=best_angle:
                    (self._show_advisor_setup(key, angle), self._update_equipment_advisor())
            )
            result_layout.addWidget(best_button)

            # If this setup cannot reproduce most of the full reference, offer
            # curated, photographically meaningful subfields rather than stopping
            # at a negative verdict.  This is the first "shoot this instead" pass.
            if best[4] < 0.82:
                try:
                    alternatives = self._alternative_framings_for_rig(
                        rig, self.current_solution, limit=2
                    )
                except Exception as exc:
                    # Alternative-framing suggestions are enrichment, not the
                    # Equipment Advisor itself.  A bad catalogue row or future
                    # scoring regression must never make the core framing
                    # assessments disappear.
                    alternatives = []
                    self._append_solver_log(
                        f"Alternative framing skipped for {rig.name}: {type(exc).__name__}: {exc}"
                    )
                if alternatives:
                    alt_heading = QLabel("Shoot this instead")
                    alt_heading.setObjectName("fieldLabel")
                    result_layout.addWidget(alt_heading)
                    for alt_score, target, alt_angle, alt_description in alternatives:
                        display = target.common_name or target.canonical_name
                        if target.common_name and target.canonical_name != target.common_name:
                            display = f"{target.canonical_name} — {target.common_name}"
                        # QPushButton does not word-wrap.  Long catalogue/common
                        # names therefore used to disappear off both sides of the
                        # narrow Advisor column.  Split the identity deliberately
                        # and put the action on its own line.
                        canonical = target.canonical_name or display
                        common = target.common_name or ""
                        identity_parts = [canonical]
                        if common and common != canonical:
                            # Buttons do not word-wrap, so deliberately wrap long
                            # common names into short centred lines for the narrow
                            # Advisor column.
                            identity_parts.extend(textwrap.wrap(common, width=30) or [common])
                        identity_lines = "\n".join(identity_parts)
                        # The description can itself be fairly long (for example
                        # “Object detail · 26% of reference · in 3 collections”).
                        # Wrap that too, and size the button from the actual number
                        # of displayed lines.  This avoids macOS clipping/bunching
                        # when the sidebar is narrow or Display scaling is enlarged.
                        description_parts = textwrap.wrap(alt_description, width=28) or [alt_description]
                        button_lines = identity_parts + description_parts + ["Centre this rig"]
                        alt_button = QPushButton("\n".join(button_lines))
                        alt_button.setObjectName("alternativeTargetButton")
                        line_count = len(button_lines)
                        alt_button.setMinimumHeight(max(82, 22 * line_count))
                        alt_button.setToolTip(
                            "Move this rig's framing rectangle to the catalogue centre of this subtarget."
                        )
                        alt_button.clicked.connect(
                            lambda _checked=False, key=rig.key, tid=target.id, angle=alt_angle:
                                self._preview_alternative_framing(key, tid, angle)
                        )
                        result_layout.addWidget(alt_button)

            self.advisor_results_layout.addWidget(result)

        self.advisor_section.show()

    def _set_observability_date_to_tonight(self, *, refresh: bool = True) -> None:
        tz = self.observer_profile.timezone
        now = datetime.now(tz)
        # “Tonight” is the observing site's current local civil date.  The
        # observability calculation itself then evaluates local noon today to
        # local noon tomorrow.  Do not reuse the legacy tonight_bounds() helper
        # here: before local noon it intentionally refers to the previous
        # evening, which is not what a planner button labelled “Tonight” means.
        d = local_observing_date(now, tz)
        self.observability_date.blockSignals(True)
        self.observability_date.setDate(QDate(d.year, d.month, d.day))
        self.observability_date.blockSignals(False)
        if refresh:
            self._refresh_observability_panel()

    def _observability_selected_date(self):
        qdate = self.observability_date.date()
        return date(qdate.year(), qdate.month(), qdate.day())

    def _observability_date_changed(self) -> None:
        self._current_observing_season = None
        if hasattr(self, "observing_season_result"):
            self.observing_season_result.hide()
        if hasattr(self, "observability_find_button"):
            self.observability_find_button.show()
            self.observability_find_button.setText("Find next good night")
        if self.current_solution is not None:
            self._update_visibility_summary(self.current_solution)
        else:
            self._refresh_observability_panel()

    def _analyse_observing_season(self) -> None:
        if self.current_solution is None:
            self.observing_season_result.setText(
                "Load and plate solve a reference image first."
            )
            self.observing_season_result.show()
            return
        if not self.observer_profile.is_configured:
            self.observing_season_result.setText(
                "Set up your Observing Site before analysing the observing season."
            )
            self.observing_season_result.show()
            return
        if self.season_thread is not None:
            return

        solution = self.current_solution
        target = SkyCoord(
            ra=solution.ra_deg,
            dec=solution.dec_deg,
            unit=("deg", "deg"),
            frame="icrs",
        )
        self.observing_season_button.setEnabled(False)
        self.observing_season_button.setText("Analysing season…")
        self.observing_season_result.setText(
            "OBSERVING SEASON\nChecking annual Sun/target geometry…"
        )
        self.observing_season_result.show()
        self.statusBar().showMessage("Analysing observing season…")

        thread = QThread(self)
        worker = SeasonSearchWorker(
            target,
            self.observer_profile,
            self._observability_selected_date(),
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._season_search_progress)
        worker.finished.connect(self._season_search_finished)
        worker.failed.connect(self._season_search_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self.season_thread = thread
        self.season_worker = worker
        thread.start()

    @Slot(str, int, int, object)
    def _season_search_progress(self, stage: str, current: int, total: int, d) -> None:
        pct = int(round(100.0 * current / max(1, total)))
        self.observing_season_button.setText(f"Analysing season… {pct}%")
        self.observing_season_result.setText(
            "OBSERVING SEASON\n"
            f"Checking {d.strftime('%d %b %Y')} · {current}/{total} nights"
        )
        self.statusBar().showMessage(
            f"Observing-season analysis {pct}% · {d.strftime('%d %b %Y')}"
        )

    @Slot(object)
    def _season_search_finished(self, result) -> None:
        self.season_thread = None
        self.season_worker = None
        self.observing_season_button.setEnabled(True)
        self.observing_season_button.setText("Analyse observing season")
        self.statusBar().showMessage("Observing-season analysis complete.", 5000)
        if not isinstance(result, ObservingSeasonResult):
            self.observing_season_result.setText("Observing-season analysis unavailable.")
            self.observing_season_result.show()
            return

        self._current_observing_season = result
        # The season engine is authoritative.  Clear any legacy independent
        # search result so the panel never presents two competing season stories.
        self.observability_search_result.hide()
        self.observability_search_result.clear()

        max_alt = result.maximum_possible_altitude_deg
        min_alt = result.minimum_altitude_deg
        if result.classification == "NOT_VISIBLE":
            self.observing_season_result.setText(
                "NOT VISIBLE FROM THIS LOCATION\n"
                f"This field never rises above the horizon from {self.observer_profile.location_name}.\n"
                f"Maximum possible altitude  {max_alt:.0f}°."
            )
            self.observability_find_button.hide()
        elif result.classification == "TOO_LOW":
            self.observing_season_result.setText(
                f"TOO LOW FOR YOUR {min_alt:.0f}° LIMIT\n"
                f"Maximum possible altitude  {max_alt:.0f}°.\n"
                "This field rises from the site, but never reaches your preferred minimum imaging altitude."
            )
            self.observability_find_button.hide()
        elif result.classification == "NOT_PRACTICAL":
            self.observing_season_result.setText(
                "NO USEFUL ASTRONOMICAL SEASON\n"
                f"The field can reach {max_alt:.0f}°, but AstroFrame found no ≥2 h dark imaging season "
                f"above {min_alt:.0f}° in the annual scan."
            )
            self.observability_find_button.hide()
        else:
            self.observability_find_button.show()

            # A target whose *best* night only barely clears the minimum useful
            # window, or whose upper culmination barely clears the user's
            # altitude limit, deserves different language from a generous
            # multi-hour season.  This is deliberately a presentation-level
            # distinction: the season geometry itself remains unchanged.
            limited_season = (
                not result.classification.startswith("YEAR_ROUND")
                and (
                    result.longest_useful_duration_hours < 3.0
                    or max_alt < (min_alt + 5.0)
                )
            )

            if result.classification.startswith("YEAR_ROUND"):
                self.observability_find_button.setText("Find better upcoming night")
                if result.classification == "YEAR_ROUND_PRIME":
                    heading = "YEAR-ROUND TARGET — PRIME PERIOD"
                    intro = "Photographable throughout the year; the selected night lies in its strongest annual period."
                elif result.classification == "YEAR_ROUND_IMPROVING":
                    heading = "YEAR-ROUND TARGET — IMPROVING"
                    intro = "Photographable throughout the year; useful dark imaging time is increasing."
                elif result.classification == "YEAR_ROUND_DECLINING":
                    heading = "YEAR-ROUND TARGET — DECLINING"
                    intro = "Photographable throughout the year; useful dark imaging time is decreasing."
                else:
                    heading = "YEAR-ROUND TARGET"
                    intro = "This field retains a useful astronomical imaging window throughout the year."
            elif limited_season:
                if result.classification == "OUT_OF_SEASON":
                    self.observability_find_button.setText("Go to next observing season")
                    heading = "LIMITED SEASON — RETURNING"
                    intro = "A future window exists, but this target only just clears your useful imaging criteria from this site."
                else:
                    self.observability_find_button.setText("Find better upcoming night")
                    heading = "LIMITED SEASON — IN PROGRESS"
                    intro = "Photographable now, but even the best part of the season offers only a marginal window from this site."
            elif result.classification == "OUT_OF_SEASON":
                self.observability_find_button.setText("Go to next observing season")
                heading = "OUT OF SEASON — RETURNING"
                intro = "Not currently in a ≥2 h astronomical imaging window."
            elif result.classification == "IMPROVING":
                self.observability_find_button.setText("Find better upcoming night")
                heading = "IN SEASON — IMPROVING"
                intro = "Already photographable; useful dark imaging time is increasing."
            elif result.classification == "DECLINING":
                self.observability_find_button.setText("Find better upcoming night")
                heading = "IN SEASON — DECLINING"
                intro = "Still photographable; useful dark imaging time is decreasing."
            elif result.classification == "PRIME":
                self.observability_find_button.setText("Find better upcoming night")
                heading = "PRIME SEASON"
                intro = "The selected night lies within the strongest part of the annual imaging season."
            else:
                self.observability_find_button.setText("Find better upcoming night")
                heading = "IN SEASON"
                intro = "The field currently has a useful astronomical imaging window."

            lines = [heading, intro]
            if result.season_start and result.season_end:
                lines.append(
                    f"Useful season  {result.season_start.strftime('%d %b %Y')}–"
                    f"{result.season_end.strftime('%d %b %Y')}"
                )
            if limited_season:
                if result.longest_date:
                    lines.append(
                        f"Best around  {result.longest_date.strftime('%d %b %Y')} · "
                        f"up to {self._format_duration(result.longest_useful_duration_hours)} useful darkness"
                    )
            else:
                if result.prime_start and result.prime_end:
                    lines.append(
                        f"Prime season  {result.prime_start.strftime('%d %b %Y')}–"
                        f"{result.prime_end.strftime('%d %b %Y')}"
                    )
                if result.longest_date:
                    lines.append(
                        f"Longest dark window  {self._format_duration(result.longest_useful_duration_hours)} "
                        f"around {result.longest_date.strftime('%d %b %Y')}"
                    )
            lines.append(f"Maximum possible altitude  {max_alt:.0f}°")
            self.observing_season_result.setText("\n".join(lines))
        self.observing_season_result.show()
        self._update_imaging_verdict()

    @Slot(str)
    def _season_search_failed(self, message: str) -> None:
        self.season_thread = None
        self.season_worker = None
        self.observing_season_button.setEnabled(True)
        self.observing_season_button.setText("Analyse observing season")
        self.observing_season_result.setText(
            f"Observing-season analysis unavailable.\n{message}"
        )
        self.observing_season_result.show()
        self.statusBar().showMessage("Observing-season analysis failed.", 7000)
        self.general_log_ui.emit(f"Observing-season analysis unavailable: {message}")

    def _find_next_good_night(self) -> None:
        season = getattr(self, "_current_observing_season", None)
        if isinstance(season, ObservingSeasonResult) and season.classification == "OUT_OF_SEASON":
            target_date = season.next_season_start or season.season_start
            if target_date is not None:
                qd = QDate(target_date.year, target_date.month, target_date.day)
                self.observability_date.blockSignals(True)
                self.observability_date.setDate(qd)
                self.observability_date.blockSignals(False)
                if self.current_solution is not None:
                    self._update_visibility_summary(self.current_solution)

                # The old result was calculated against the previously selected
                # date.  After jumping to the season boundary, refresh its state
                # immediately so the panel cannot simultaneously say
                # "OUT OF SEASON" and show a qualifying selected night.  The
                # expensive annual geometry does not need to be recalculated: the
                # season/prime boundaries are already known.
                if (
                    season.prime_start is not None
                    and season.prime_end is not None
                    and season.prime_start <= target_date <= season.prime_end
                ):
                    new_classification = "PRIME"
                else:
                    new_classification = "IMPROVING"
                refreshed = replace(
                    season,
                    selected_date=target_date,
                    classification=new_classification,
                )
                self._season_search_finished(refreshed)
                self.observability_search_result.setText(
                    "NEXT OBSERVING SEASON\n"
                    f"Moved to the first useful night: {target_date.strftime('%d %b %Y')}."
                )
                self.observability_search_result.show()
                return

        if self.current_solution is None:
            self.observability_search_result.setText("Plate solve an image first.")
            self.observability_search_result.show()
            return
        if not self.observer_profile.is_configured:
            self.observability_search_result.setText("Set up your Observing Site first.")
            self.observability_search_result.show()
            return
        if self.good_night_thread is not None:
            return

        solution = self.current_solution
        target = SkyCoord(
            ra=solution.ra_deg, dec=solution.dec_deg, unit=("deg", "deg"), frame="icrs"
        )
        selected_date = self._observability_selected_date()
        # "Next" means a later night. The selected night already has its own
        # assessment, so start with tomorrow rather than recommending today again.
        start_date = selected_date + timedelta(days=1)

        self.observability_find_button.setEnabled(False)
        self.observability_find_button.setText("Searching…")
        self.observability_search_result.setText(
            f"SEARCHING NEAR TERM\nStarting after {selected_date.strftime('%d %b %Y')} · first check {start_date.strftime('%d %b %Y')}…"
        )
        self.observability_search_result.show()
        self.statusBar().showMessage("Searching near-term observability; AstroFrame will extend into the next season if needed…")

        thread = QThread(self)
        worker = GoodNightSearchWorker(target, self.observer_profile, start_date, 45, 365, 3)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._good_night_search_progress)
        worker.finished.connect(self._good_night_search_finished)
        worker.failed.connect(self._good_night_search_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self.good_night_thread = thread
        self.good_night_worker = worker
        thread.start()

    @Slot(str, int, int, object)
    def _good_night_search_progress(self, stage: str, current: int, total: int, d) -> None:
        if stage == "near":
            heading = "SEARCHING NEAR TERM"
            detail = f"Checking {d.strftime('%d %b %Y')} · night {current} of {total}"
            button = f"Searching {current}/{total}…"
            status = f"Near-term observability: {current}/{total} · {d.strftime('%d %b %Y')}"
        elif stage == "coarse":
            heading = "NO GOOD NIGHT IN 45 DAYS — LOCATING NEXT SEASON"
            detail = f"Scanning {d.strftime('%d %b %Y')} · step {current} of {total}"
            button = "Finding season…"
            status = f"Locating next observing season · {d.strftime('%d %b %Y')}"
        else:
            heading = "SEASON FOUND — CHECKING INDIVIDUAL NIGHTS"
            detail = f"Checking {d.strftime('%d %b %Y')} · night {current} of {total}"
            button = f"Refining {current}/{total}…"
            status = f"Refining seasonal opportunity: {current}/{total} · {d.strftime('%d %b %Y')}"
        self.observability_find_button.setText(button)
        self.observability_search_result.setText(f"{heading}\n{detail}")
        self.statusBar().showMessage(status)

    @Slot(object)
    def _good_night_search_finished(self, result) -> None:
        self.good_night_thread = None
        self.good_night_worker = None
        self.observability_find_button.setText("Find next good night")
        self.observability_find_button.setEnabled(True)
        self.statusBar().showMessage("Observability search complete.", 5000)

        candidates = list(result.candidates) if isinstance(result, SeasonalGoodNightResult) else list(result or [])
        extended = bool(getattr(result, "extended_beyond_near_term", False))
        searched_through = getattr(result, "searched_through_date", None)

        if not candidates:
            if extended:
                through = searched_through.strftime('%d %b %Y') if searched_through else "one year ahead"
                self.observability_search_result.setText(
                    "NOT PRACTICAL FROM THIS LOCATION\n"
                    f"No ≥2 h dark imaging window above {self.observer_profile.minimum_altitude_deg:.0f}° "
                    f"was found through {through}."
                )
            else:
                self.observability_search_result.setText(
                    "No useful dark imaging window found."
                )
            self.observability_search_result.show()
            return

        best = candidates[0]
        qd = QDate(best.evening_date.year, best.evening_date.month, best.evening_date.day)
        self.observability_date.setDate(qd)

        season = getattr(self, "_current_observing_season", None)
        if isinstance(season, ObservingSeasonResult):
            lines = ["BETTER UPCOMING NIGHT"]
        elif extended:
            lines = [
                "NEXT OBSERVING SEASON",
                "No qualifying night in the next 45 days; AstroFrame searched ahead for the next observing season.",
            ]
        else:
            lines = ["NEXT GOOD NIGHT"]
        for i, candidate in enumerate(candidates):
            obs = candidate.summary
            if i == 0:
                if isinstance(season, ObservingSeasonResult):
                    prefix = "Best upcoming night"
                else:
                    prefix = "First useful night" if extended else "Next good night"
            else:
                prefix = "Alternative"
            lines.append(
                f"{prefix}  {candidate.evening_date.strftime('%d %b %Y')} · "
                f"{self._format_local_time(obs.useful_start)}–"
                f"{self._format_local_time(obs.useful_end)} · "
                f"{self._format_duration(obs.useful_duration_hours)} · "
                f"peak {obs.maximum_altitude_deg:.0f}° · Moon {obs.moon_interference}"
            )
        self.observability_search_result.setText("\n".join(lines))
        self.observability_search_result.show()

    @Slot(str)
    def _good_night_search_failed(self, message: str) -> None:
        self.good_night_thread = None
        self.good_night_worker = None
        self.observability_find_button.setText("Find next good night")
        self.observability_find_button.setEnabled(True)
        self.observability_search_result.setText(
            f"Good-night search unavailable.\n{message}"
        )
        self.observability_search_result.show()
        self.statusBar().showMessage("Observability search failed.", 7000)
        self.general_log_ui.emit(f"Good-night search unavailable: {message}")

    @staticmethod
    def _format_local_time(value) -> str:
        return value.strftime("%H:%M") if value is not None else "—"

    @staticmethod
    def _format_duration(hours: float) -> str:
        """Format a canonical duration as photographer-friendly hours/minutes."""
        total_minutes = max(0, int(round(float(hours) * 60.0)))
        h, m = divmod(total_minutes, 60)
        if h and m:
            return f"{h}h {m:02d}m"
        if h:
            return f"{h}h"
        return f"{m}m"

    def _refresh_observability_panel(self) -> None:
        if not hasattr(self, "observability_result"):
            return
        if self.current_solution is None:
            self.observability_section.hide()
            self.observability_result.setText(
                "Plate solve an image to calculate its imaging window."
            )
            return
        self.observability_section.show()
        if not self.observer_profile.is_configured:
            self.observability_result.setText(
                "Set up your Observing Site to calculate darkness and target visibility."
            )
            return

        solution = self.current_solution
        try:
            target = SkyCoord(
                ra=solution.ra_deg,
                dec=solution.dec_deg,
                unit=("deg", "deg"),
                frame="icrs",
            )
            obs = observability_for_date(
                target,
                self.observer_profile,
                self._observability_selected_date(),
            )
        except Exception as exc:
            self.observability_result.setText("Observability calculation unavailable.")
            self._append_solver_log(f"Observability calculation unavailable: {exc}")
            return

        peak = self._format_local_time(obs.peak_time)
        if obs.has_astronomical_darkness:
            darkness = (
                f"Astronomical darkness  {self._format_local_time(obs.dark_start)}–"
                f"{self._format_local_time(obs.dark_end)}"
            )
        else:
            darkness = "Astronomical darkness  None on this date"

        if obs.has_useful_window:
            window = (
                f"Imaging window  {self._format_local_time(obs.useful_start)}–"
                f"{self._format_local_time(obs.useful_end)}  "
                f"({self._format_duration(obs.useful_duration_hours)} above "
                f"{obs.minimum_altitude_deg:.0f}° in darkness)"
            )
            if obs.useful_duration_hours >= 4 and obs.maximum_altitude_deg >= 45:
                verdict = "EXCELLENT OPPORTUNITY"
            elif obs.useful_duration_hours >= 2:
                verdict = "GOOD OPPORTUNITY"
            else:
                verdict = "SHORT OPPORTUNITY"
        elif not obs.has_astronomical_darkness:
            verdict = "NO ASTRONOMICAL DARKNESS"
            window = "Imaging window  None"
        elif obs.maximum_altitude_deg < obs.minimum_altitude_deg:
            verdict = "TOO LOW FROM THIS SITE"
            window = (
                f"Imaging window  None above your "
                f"{obs.minimum_altitude_deg:.0f}° minimum"
            )
        else:
            verdict = "NO DARK IMAGING WINDOW"
            window = "Imaging window  Target and darkness do not overlap"

        moon_pct = obs.moon_illumination_fraction * 100.0
        moon_line = (
            f"Moon  {moon_pct:.0f}% illuminated · "
            f"{obs.moon_separation_deg:.0f}° from target · "
            f"interference {obs.moon_interference}"
        )
        self.observability_result.setText(
            f"{verdict}\n"
            f"{self.observer_profile.location_name}\n"
            f"{darkness}\n"
            f"{window}\n"
            f"Culmination  {peak} at {obs.maximum_altitude_deg:.0f}°\n"
            f"{moon_line}"
        )

    def _update_visibility_summary(self, solution: PlateSolution) -> None:
        """Keep the compact Image Summary line in sync with the planner."""
        if not self.observer_profile.is_configured:
            self.summary_visibility.setText(
                "Observability\nSet up an Observing Site for visibility."
            )
            self._refresh_observability_panel()
            return
        try:
            target = SkyCoord(
                ra=solution.ra_deg,
                dec=solution.dec_deg,
                unit=("deg", "deg"),
                frame="icrs",
            )
            obs = observability_for_date(
                target,
                self.observer_profile,
                self._observability_selected_date(),
            )
            if obs.has_useful_window:
                self.summary_visibility.setText(
                    f"Observability · {obs.evening_date.strftime('%d %b')}\n"
                    f"{self._format_local_time(obs.useful_start)}–"
                    f"{self._format_local_time(obs.useful_end)} above "
                    f"{obs.minimum_altitude_deg:.0f}° in darkness\n"
                    f"Peaks {obs.maximum_altitude_deg:.0f}° at "
                    f"{self._format_local_time(obs.peak_time)}"
                )
            else:
                self.summary_visibility.setText(
                    f"Observability · {obs.evening_date.strftime('%d %b')}\n"
                    f"No dark window above {obs.minimum_altitude_deg:.0f}°\n"
                    f"Peaks {obs.maximum_altitude_deg:.0f}° at "
                    f"{self._format_local_time(obs.peak_time)}"
                )
        except Exception as exc:
            self.summary_visibility.setText("Observability\nCalculation unavailable.")
            self._append_solver_log(f"Observability calculation unavailable: {exc}")
        self._refresh_observability_panel()

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
        self.solving_hint_search_radius_deg = None
        self.solving_hint_label.setText("Solver clue\nNone")
        self.solving_hint_label.show()
        self.assisted_solve_button.setText("Give Solver a Clue…")
        self.online_fallback_button.hide()
        self.solve_button.setText("Plate Solve")
        self.solve_button.setToolTip("")
        self.solve_button.setEnabled(False)
        self.assisted_solve_button.setEnabled(False)
        self.clear_solution_button.hide()
        self.solve_details.clear()
        self.solve_details.hide()
        self.image_summary.hide()
        self._clear_working_framing()
        if hasattr(self, "advisor_section"):
            self.advisor_section.hide()
        self.summary_target.setText("Target\nPlate solve to identify")
        self.summary_reference.setText(f"Reference\n{Path(path).name}")
        self.summary_centre.clear()
        self.summary_field.clear()
        self.summary_scale.clear()
        self.summary_rotation.clear()
        self.summary_solver.clear()
        self.summary_visibility.clear()
        self._reset_field_objects_state()
        self._refresh_collections_summary()
        self.setWindowTitle("AstroFrame 1.0 RC22v")
        self.width_spin.setEnabled(True)

        # dev16e: a random new image starts with genuinely unknown scale.
        # Do not recycle a solved width (or a saved UI value) from another image.
        # The viewer still needs a harmless provisional scale for drawing overlays,
        # but that internal value is never sent to the plate solver as evidence.
        self.width_spin.blockSignals(True)
        self.width_spin.setValue(0.0)
        self.width_spin.blockSignals(False)
        self._append_solver_log(
            "New image: angular width is Unknown; no previous image scale will be supplied to ASTAP."
        )
        self._show_estimated_status()

        # Set the provisional scale BEFORE loading. load_image() emits image_loaded
        # synchronously; a cached solution may therefore set the true solved width
        # inside _on_image_loaded(). Do not overwrite that solved width afterwards.
        self.viewer.set_reference_width(DEFAULT_REFERENCE_WIDTH)
        self.viewer.load_image(path)
        self.settings.setValue("lastImageDirectory", str(Path(path).parent))
        self.solve_button.setEnabled(True)
        self.assisted_solve_button.setEnabled(True)
        self.astrometry_job_button.setEnabled(True)
        self.external_solution_button.setEnabled(True)
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
        worker.log.connect(
            self.general_log_ui.emit,
            Qt.ConnectionType.QueuedConnection,
        )
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
                self.solve_cache.save(path, solution, image_size_px=self.current_image_size)
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
        target_search_radius_deg: float | None = None,
        solver_preference_override: str | None = None,
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
        cached = self.solve_cache.load(self.current_image_path, expected_size=self.current_image_size)
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
        if target_search_radius_deg is None:
            target_search_radius_deg = self.solving_hint_search_radius_deg

        solver_preference = (
            solver_preference_override
            if solver_preference_override is not None
            else str(self.solver_combo.currentData())
        )

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
        self.assisted_solve_button.setEnabled(
            solver_preference == "online" and astrometry_job_reference is None
        )
        self.astrometry_job_button.setEnabled(False)
        self.online_fallback_button.hide()
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
            target_search_radius_deg=target_search_radius_deg,
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
        thread = self.solve_thread
        worker = self.solve_worker
        self.solve_thread.finished.connect(
            lambda t=thread, w=worker: self._solve_finished_for(t, w)
        )
        self.solve_thread.start()

    def cancel_solve(self) -> None:
        if self.solve_worker is None or not self.solve_in_progress:
            return

        # Cancellation is an immediate UI action.  The worker is asked to stop,
        # but a remote HTTP request can be inside the requests library for a
        # few more seconds.  Retire that worker in the background instead of
        # holding the interface hostage while it winds down.
        worker = self.solve_worker
        thread = self.solve_thread
        self._append_solver_log("Cancellation requested — AstroFrame returned control immediately")
        worker.cancel()

        if thread is not None:
            self._retired_solve_jobs.append((thread, worker))

        # Invalidate every callback still arriving from the retired request.
        self.solve_request_id += 1
        self.solve_in_progress = False
        self.solve_worker = None
        self.solve_thread = None
        self.current_solution = None

        self.solve_button.setText("Plate Solve")
        self.solve_button.setToolTip("")
        self.solve_button.setEnabled(self.current_image_path is not None)
        self.assisted_solve_button.setEnabled(self.current_image_path is not None)
        self.astrometry_job_button.setEnabled(bool(self.current_image_path))
        self.solve_status.setText("●  Solve cancelled by user")
        self.solve_details.setText(
            "The previous solver is being stopped in the background. "
            "You can start another solve immediately."
        )
        self.solve_details.show()
        self.width_spin.setEnabled(True)

        pending = self._pending_assisted_solve
        self._pending_assisted_solve = None
        if pending is not None:
            ra_hours, dec_deg, radius_deg, shown_name = pending
            self._append_solver_log(
                f"Switching immediately to clue-assisted ASTAP: {shown_name}"
            )
            QTimer.singleShot(0, lambda: self.plate_solve(
                ra_hours, dec_deg,
                target_search_radius_deg=radius_deg,
                solver_preference_override="astap",
            ))

    def try_online_solve(self) -> None:
        """Explicit remote fallback after local solving has been exhausted."""
        if not self.current_image_path or self.solve_in_progress:
            return
        self._append_solver_log(
            "User chose the Astrometry.net blind-solve fallback"
        )
        self.plate_solve(solver_preference_override="online")

    @staticmethod
    def _normalise_identifier(identifier: str) -> str:
        return " ".join(identifier.replace("_", " ").split()).strip()

    @staticmethod
    def _target_lookup_candidates(target: str) -> list[str]:
        clean = " ".join(target.replace("_", " ").split()).strip()
        candidates = [clean]
        # Famous common names are useful photographic filename clues, but name
        # resolvers are not equally consistent about them.  Seed the familiar
        # catalogue identifier first so a clue such as “Sombrero” remains useful
        # even when the online resolver does not recognise the nickname itself.
        famous_common = {
            "sombrero": "M 104",
            "sombrero galaxy": "M 104",
            "sombrero hat galaxy": "M 104",
            "andromeda": "M 31",
            "andromeda galaxy": "M 31",
            "carina nebula": "NGC 3372",
            "tarantula nebula": "NGC 2070",
            "running chicken": "IC 2944",
            "running chicken nebula": "IC 2944",
        }
        canonical = famous_common.get(clean.casefold())
        if canonical:
            candidates.insert(0, canonical)
        wr_match = re.fullmatch(r"(?i)WR\s*[- ]?\s*(\d{1,3})", clean)
        if wr_match:
            number = int(wr_match.group(1))
            candidates.extend([f"WR {number}", f"WR-{number}"])
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

        # Keep a sexagesimal RA/Dec pair intact; the plus/minus sign belongs
        # to declination and must not be mistaken for a multi-target separator.
        if re.fullmatch(
            r"\d{1,2}:\d{1,2}:\d{1,2}(?:\.\d+)?\s+[+-]\d{1,2}:\d{1,2}:\d{1,2}(?:\.\d+)?",
            clean,
        ):
            return [clean]

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
        # Coordinates get first refusal, before catalogue-target splitting.  In
        # particular, a comma between RA and Dec is a normal coordinate
        # separator and must not be mistaken for a separator between objects.
        raw_entry = text.strip()
        coordinate = None

        # Sexagesimal pair, with an optional comma:
        #   20:10:14 +36:10:36
        #   20:10:14, +36:10:36
        sexagesimal = re.fullmatch(
            r"\s*(\d{1,2}:\d{1,2}:\d{1,2}(?:\.\d+)?)\s*,?\s*([+-]\d{1,2}:\d{1,2}:\d{1,2}(?:\.\d+)?)\s*",
            raw_entry,
        )
        if sexagesimal:
            try:
                coordinate = SkyCoord(
                    f"{sexagesimal.group(1)} {sexagesimal.group(2)}",
                    unit=("hourangle", "deg"),
                    frame="icrs",
                )
            except Exception as exc:
                raise ValueError(f"Could not parse the entered RA/Dec: {exc}") from exc

        # Decimal pair, also with an optional comma.  Values above 24 are
        # unambiguously RA degrees; 0..24 defaults to RA hours.  Requiring a
        # signed Dec for the whitespace-only form keeps ordinary catalogue
        # names/numbers from being misread as coordinates.
        if coordinate is None:
            decimal = re.fullmatch(
                r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*[hHdD°]?)\s*(?:,\s*|\s+)([+-](?:\d+(?:\.\d*)?|\.\d+)\s*[dD°]?)\s*",
                raw_entry,
            )
            if decimal:
                left = decimal.group(1).strip()
                right = decimal.group(2).strip()
                ra_value = float(left.rstrip("dD°hH "))
                dec_deg = float(right.rstrip("dD° "))
                explicit_hours = left.lower().rstrip().endswith("h")
                if not -90 <= dec_deg <= 90:
                    raise ValueError("Declination must be between -90° and +90°.")
                if explicit_hours or 0 <= ra_value < 24:
                    if not 0 <= ra_value < 24:
                        raise ValueError("RA hours must be between 0 and 24.")
                    coordinate = SkyCoord(ra=ra_value, dec=dec_deg, unit=("hourangle", "deg"))
                elif 0 <= ra_value < 360:
                    coordinate = SkyCoord(ra=ra_value, dec=dec_deg, unit=("deg", "deg"))
                else:
                    raise ValueError("RA must be 0–24 hours or 0–360 degrees.")

        if coordinate is not None:
            return coordinate.icrs, [{
                "input": "Entered coordinates",
                "resolved": "Entered coordinates",
                "coordinate": coordinate.icrs,
                "main_identifier": None,
                "object_type": None,
                "alternate_names": [],
            }]

        parts = self._split_target_hint(text)
        if not parts:
            raise ValueError("No target was entered.")

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

    def import_external_solution(self) -> None:
        """Accept a manually supplied astrometric solution from another program."""
        if not self.current_image_path:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Import External Astrometric Solution")
        layout = QVBoxLayout(dialog)
        intro = QLabel(
            "Enter the astrometry reported by another program. AstroFrame will use it "
            "for framing, but will clearly mark the result as externally supplied."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        centre_edit = QLineEdit()
        centre_edit.setPlaceholderText("20:10:28.382, +36:11:54.37")
        centre_edit.setToolTip("Centre RA/Dec; decimal or sexagesimal coordinates are accepted.")
        form.addRow("Centre RA / Dec", centre_edit)

        scale_spin = QDoubleSpinBox()
        scale_spin.setRange(0.001, 9999.0)
        scale_spin.setDecimals(4)
        scale_spin.setSingleStep(0.1)
        scale_spin.setSuffix(" arcsec/px")
        scale_spin.setValue(1.0)
        form.addRow("Pixel scale", scale_spin)

        orientation_spin = QDoubleSpinBox()
        orientation_spin.setRange(-360.0, 360.0)
        orientation_spin.setDecimals(3)
        orientation_spin.setSingleStep(0.1)
        orientation_spin.setSuffix("°")
        orientation_known = QCheckBox("Orientation supplied")
        orientation_known.setChecked(False)
        orientation_spin.setEnabled(False)
        orientation_known.toggled.connect(orientation_spin.setEnabled)
        orientation_row = QWidget()
        orientation_layout = QHBoxLayout(orientation_row)
        orientation_layout.setContentsMargins(0, 0, 0, 0)
        orientation_layout.addWidget(orientation_spin)
        orientation_layout.addWidget(orientation_known)
        form.addRow("Orientation", orientation_row)

        parity_combo = QComboBox()
        parity_combo.addItem("Unknown / not supplied", 1.0)
        parity_combo.addItem("Normal", 1.0)
        parity_combo.addItem("Flipped", -1.0)
        form.addRow("Parity", parity_combo)

        source_edit = QLineEdit("BlindSolver2000")
        source_edit.setPlaceholderText("e.g. BlindSolver2000")
        form.addRow("Source", source_edit)
        layout.addLayout(form)

        caveat = QLabel(
            "AstroFrame will not independently verify these values. Framing and exports "
            "will therefore be based on external astrometric information."
        )
        caveat.setObjectName("helpText")
        caveat.setWordWrap(True)
        layout.addWidget(caveat)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Use External Solution")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        text = centre_edit.text().strip()
        if not text:
            QMessageBox.warning(self, "External solution", "Enter the image-centre RA and Dec.")
            return
        try:
            centre, _targets = self._resolve_target_entry(text)
            ra_deg = float(centre.ra.deg)
            dec_deg = float(centre.dec.deg)
        except Exception as exc:
            QMessageBox.warning(
                self, "External solution",
                f"AstroFrame could not read those centre coordinates.\n\nDetails: {exc}"
            )
            return

        width_px, height_px = self.current_image_size
        if width_px <= 0 or height_px <= 0:
            QMessageBox.warning(self, "External solution", "The loaded image dimensions are unavailable.")
            return

        scale = float(scale_spin.value())
        width_deg = width_px * scale / 3600.0
        height_deg = height_px * scale / 3600.0
        radius_deg = 0.5 * math.hypot(width_deg, height_deg)
        source_name = source_edit.text().strip() or "External source"
        orientation_supplied = orientation_known.isChecked()
        orientation = float(orientation_spin.value()) % 360.0 if orientation_supplied else None
        parity = float(parity_combo.currentData())

        solution = PlateSolution(
            ra_deg=ra_deg,
            dec_deg=dec_deg,
            pixel_scale_arcsec=scale,
            orientation_deg=orientation,
            parity=parity,
            radius_deg=radius_deg,
            image_width_deg=width_deg,
            image_height_deg=height_deg,
            solver=f"External — {source_name}",
            solve_mode=("Imported external astrometry" if orientation_supplied
                        else "Imported external astrometry — orientation unknown"),
            orientation_known=orientation_supplied,
        )
        self.solve_cache.save(self.current_image_path, solution, image_size_px=self.current_image_size)
        self._append_solver_log(
            f"External solution imported from {source_name}: centre RA {ra_deg:.8f}°, "
            f"Dec {dec_deg:+.8f}°, scale {scale:.4f} arcsec/px, "
            + (f"orientation {orientation:.3f}°" if orientation_supplied else "orientation unknown")
        )
        self._apply_solution(solution, cached=False)

    def target_assisted_solve(self) -> None:
        if not self.current_image_path:
            return

        while True:
            hint, accepted = QInputDialog.getText(
                self,
                "Give Solver a Clue",
                "Enter any known object visible anywhere in this image, or enter RA/Dec directly.\n"
                "It does not need to be the main subject or near the centre.\n\n"
                "Examples: NGC 2070 · WR 134 · 20:10:14 +36:10:36 · 302.558, +36.177",
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
                    "Try another catalogue designation or common name, or enter coordinates "
                    "such as 20:10:14 +36:10:36, 302.558, +36.177 (degrees), or "
                    "5.6453, -69.1 (RA hours, Dec degrees).\n\n"
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
                "AstroFrame will use this as a sky anchor, not as an assumed image centre. "
                "ASTAP will search a generous area around it."
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
                # A clue may be a tiny object near an edge of a wide internet image,
                # so do not treat its coordinates as the image centre.
                self.solving_hint_search_radius_deg = 30.0
                self.solving_hint_label.setText(f"Solver clue\n{shown_name}")
                # A solver clue is only a positional anchor. Do not present it as
                # the image's subject; subject identification happens after WCS.
                self.assisted_solve_button.setText("Change Solver Clue…")
                self._append_solver_log(
                    f"Solver clue accepted: {shown_name}; "
                    f"RA {ra_hours:.8f} h, Dec {dec_deg:+.8f}°"
                )
                selected_solver = str(self.solver_combo.currentData())
                if selected_solver == "online":
                    # "Online only" is a hard routing promise: a target clue
                    # must never switch the solve back to local ASTAP.  Keep
                    # the clue for the UI/future local use, but let the selected
                    # Astrometry.net solve remain genuinely online-only.
                    if self.solve_in_progress:
                        self._append_solver_log(
                            "Solver clue saved, but Online only is active; "
                            "the current Astrometry.net solve continues unchanged"
                        )
                    else:
                        self._append_solver_log(
                            "Online only is active; starting Astrometry.net "
                            "without switching to ASTAP"
                        )
                        self.plate_solve(
                            ra_hours, dec_deg,
                            target_search_radius_deg=30.0,
                            solver_preference_override="online",
                        )
                elif self.solve_in_progress:
                    # In Automatic/local mode a newly supplied clue is an
                    # explicit request to abandon the current attempt and use
                    # clue-assisted local ASTAP.
                    self._pending_assisted_solve = (
                        ra_hours, dec_deg, 30.0, shown_name
                    )
                    self.cancel_solve()
                else:
                    self.plate_solve(
                        ra_hours, dec_deg,
                        target_search_radius_deg=30.0,
                        solver_preference_override="astap",
                    )
                return
            if clicked is choose_button:
                continue
            self._show_estimated_status()
            return

    def _refresh_solver_help(self) -> None:
        if not hasattr(self, "solver_help_label"):
            return
        preference = str(self.solver_combo.currentData())
        if preference == "automatic":
            text = (
                "Tries fast local ASTAP first. If local solving cannot identify the field, "
                "AstroFrame can use a credible filename clue and then falls back to Astrometry.net if needed."
            )
        elif preference == "astap":
            text = "Keeps the image on this computer and uses ASTAP only."
        else:
            text = "Sends the image to Astrometry.net for online blind solving; ASTAP is not used."
        self.solver_help_label.setText(text)

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
        self._refresh_solver_help()
        preference = str(self.solver_combo.currentData())
        self.settings.setValue("solverPreference", preference)
        self._append_solver_log(f"Solver preference changed to: {preference}")
    def _append_solver_log(self, message: str) -> None:
        from datetime import date, datetime
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
            "Forget current solution?",
            "This removes the cached plate solution for this image. "
            "You can solve it again at any time.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        # "Forget current solution" clears AstroFrame's displayed/cached WCS,
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
        self._reset_field_objects_state()
        self._refresh_collections_summary()
        self.image_summary.hide()
        if hasattr(self, "advisor_section"):
            self.advisor_section.hide()
        self.width_spin.setEnabled(True)
        self.solve_button.setText("Plate Solve")
        self.solve_button.setToolTip("")
        self.solve_button.setEnabled(True)
        self.assisted_solve_button.setEnabled(True)
        self.online_fallback_button.hide()
        self.clear_solution_button.hide()
        self.solve_details.hide()
        self._show_estimated_status()

    def closeEvent(self, event) -> None:
        self.settings.setValue("geometry", self.saveGeometry())
        if self.current_solution is None and self.width_spin.value() > 0:
            self.settings.setValue("referenceWidthEstimate", self.width_spin.value())
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

        cached = self.solve_cache.load(path, expected_size=(width, height))
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
            self.external_solution_button.setEnabled(True)
            self.online_fallback_button.hide()
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
        self.solve_status.setText("●  Solve cancelled by user")

    def _solve_progress(self, message: str) -> None:
        if self.current_solution is not None:
            return
        self.solve_status.setText(f"●  {message}")

    def _solve_succeeded(self, solution: PlateSolution) -> None:
        if self.current_image_path:
            self.solve_cache.save(self.current_image_path, solution, image_size_px=self.current_image_size)
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

    SUBJECT_IDENTIFICATION_VERSION = 11

    def _filename_catalogue_hint(self, image_path: str | None) -> str | None:
        """Extract a conservative target hint from a photographer's filename.

        Catalogue designations remain the first choice, but RC22l also recognises
        common names and aliases already present in AstroFrame's target knowledge.
        The result is *still only a clue*: it is confirmed by the user before ASTAP
        uses it and independently verified against the solved field afterwards.
        """
        if not image_path:
            return None
        raw_stem = Path(image_path).stem
        stem = raw_stem.upper()
        patterns = (
            (r"(?<![A-Z0-9])NGC[ _-]*(\d{1,4})(?!\d)", "NGC {}"),
            (r"(?<![A-Z0-9])IC[ _-]*(\d{1,4})(?!\d)", "IC {}"),
            (r"(?<![A-Z0-9])M[ _-]*(\d{1,3})(?!\d)", "M {}"),
            (r"(?<![A-Z0-9])SH(?:2)?[ _-]*(\d{1,3})(?!\d)", "SH2-{}"),
            (r"(?<![A-Z0-9])RCW[ _-]*(\d{1,3})(?!\d)", "RCW {}"),
            (r"(?<![A-Z0-9])GUM[ _-]*(\d{1,3})(?!\d)", "GUM {}"),
            (r"(?<![A-Z0-9])WR[ _-]*(\d{1,3})(?!\d)", "WR {}"),
        )
        for pattern, template in patterns:
            match = re.search(pattern, stem)
            if match:
                return template.format(int(match.group(1)))

        stem_words = re.sub(r"[^A-Z0-9]+", " ", stem).strip()
        if not stem_words:
            return None

        # Prefer names already known from imported catalogues.  Requiring a
        # multi-character phrase prevents generic tokens such as "galaxy" or
        # "nebula" from becoming accidental clues.
        generic = {
            "GALAXY", "NEBULA", "CLUSTER", "STAR", "IMAGE", "FINAL",
            "RGB", "LRGB", "SHO", "HOO", "JPEG", "LARGE", "COMBI",
        }
        matches: list[tuple[int, str]] = []
        try:
            targets = list(self.knowledge_store._targets.values())
        except Exception:
            targets = []
        for target in targets:
            names = []
            if target.common_name:
                names.append(str(target.common_name))
            names.extend(str(alias) for alias in (target.aliases or []) if alias)
            names.append(str(target.canonical_name or ""))
            for name in names:
                norm = re.sub(r"[^A-Z0-9]+", " ", name.upper()).strip()
                if len(norm) < 5 or norm in generic or norm.isdigit():
                    continue
                # Bare catalogue IDs are handled by the stricter regexes above.
                if self._split_catalogue_identifier(name) is not None:
                    continue
                if re.search(rf"(?<![A-Z0-9]){re.escape(norm)}(?![A-Z0-9])", stem_words):
                    score = len(norm) + (20 if target.common_name and name == target.common_name else 0)
                    matches.append((score, name))

        if matches:
            matches.sort(key=lambda item: item[0], reverse=True)
            # Ambiguous equal-strength aliases are not safe enough to offer.
            if len(matches) == 1 or matches[0][0] > matches[1][0]:
                return matches[0][1]

        # Small built-in safety net for famous names commonly used as filenames.
        # Local catalogue aliases above still take precedence.
        famous = (
            ("SOMBRERO HAT GALAXY", "Sombrero Galaxy"),
            ("SOMBRERO GALAXY", "Sombrero Galaxy"),
            ("SOMBRERO", "Sombrero Galaxy"),
            ("ANDROMEDA GALAXY", "Andromeda Galaxy"),
            ("ANDROMEDA", "Andromeda Galaxy"),
            ("CARINA NEBULA", "Carina Nebula"),
            ("TARANTULA NEBULA", "Tarantula Nebula"),
            ("RUNNING CHICKEN", "Running Chicken Nebula"),
        )
        for phrase, display in famous:
            if phrase in stem_words:
                return display
        return None

    @staticmethod
    def _identifier_key(identifier: str) -> str:
        return re.sub(r"[^A-Z0-9]", "", identifier.upper())

    @staticmethod
    def _coord_is_inside_solution(
        coord: SkyCoord, solution: PlateSolution, margin_fraction: float = 0.05
    ) -> bool:
        """Return True when *coord* falls inside the solved image footprint.

        Work in a local tangent plane around the solved centre and rotate that
        plane into the image axes using the WCS orientation.  A small margin
        absorbs catalogue/WCS rounding without turning this into a proximity
        test.
        """
        centre = SkyCoord(
            ra=solution.ra_deg, dec=solution.dec_deg, unit=("deg", "deg"), frame="icrs"
        )
        try:
            dx, dy = centre.spherical_offsets_to(coord.icrs)
            x = float(dx.deg)
            y = float(dy.deg)
        except Exception:
            return False

        theta = math.radians(float(solution.orientation_deg or 0.0))
        # Rotate sky offsets into image-axis offsets.  The sign convention does
        # not affect the rectangular containment test so long as it is applied
        # consistently.
        image_x = x * math.cos(theta) + y * math.sin(theta)
        image_y = -x * math.sin(theta) + y * math.cos(theta)
        half_w = max(float(solution.image_width_deg), 0.0) * 0.5
        half_h = max(float(solution.image_height_deg), 0.0) * 0.5
        if half_w <= 0.0 or half_h <= 0.0:
            return False
        margin = max(0.0, float(margin_fraction))
        return (
            abs(image_x) <= half_w * (1.0 + margin)
            and abs(image_y) <= half_h * (1.0 + margin)
        )


    @staticmethod
    def _split_catalogue_identifier(identifier: str) -> tuple[str, str] | None:
        """Return (catalogue, number) for simple photographic catalogue IDs."""
        text = re.sub(r"\s+", " ", str(identifier or "").strip().upper())
        match = re.fullmatch(r"(NGC|IC|M|RCW|GUM|SH2-|SH2|SH)\s*[- ]?\s*(\d+)", text)
        if not match:
            return None
        prefix, digits = match.groups()
        if prefix in {"SH", "SH2", "SH2-"}:
            prefix = "SH2"
        return prefix, digits

    @staticmethod
    def _damerau_distance(left: str, right: str) -> int:
        """Small unrestricted-enough edit distance with adjacent transpositions.

        Catalogue numbers are short, so the compact dynamic-programming form is
        preferable to generating arbitrary typo permutations.
        """
        if left == right:
            return 0
        rows, cols = len(left) + 1, len(right) + 1
        d = [[0] * cols for _ in range(rows)]
        for i in range(rows):
            d[i][0] = i
        for j in range(cols):
            d[0][j] = j
        for i in range(1, rows):
            for j in range(1, cols):
                cost = 0 if left[i - 1] == right[j - 1] else 1
                d[i][j] = min(
                    d[i - 1][j] + 1,
                    d[i][j - 1] + 1,
                    d[i - 1][j - 1] + cost,
                )
                if (
                    i > 1
                    and j > 1
                    and left[i - 1] == right[j - 2]
                    and left[i - 2] == right[j - 1]
                ):
                    d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
        return d[-1][-1]

    def _verified_in_field_filename_correction(
        self, solution: PlateSolution, filename_hint: str | None
    ) -> dict[str, object] | None:
        """Correct a failed filename hint from objects *actually in the solved field*.

        Rather than manufacturing typo permutations, compare the failed hint only
        with catalogue objects already verified by the Knowledge Engine as lying in
        the WCS footprint.  A correction is accepted only when there is one unique
        closest object in the same catalogue and the catalogue-number edit distance
        is one (including a single adjacent transposition).
        """
        parsed = self._split_catalogue_identifier(filename_hint or "")
        if parsed is None:
            return None
        prefix, digits = parsed
        try:
            in_field = self.knowledge_store.entries_in_field(
                solution.ra_deg,
                solution.dec_deg,
                solution.image_width_deg,
                solution.image_height_deg,
                solution.orientation_deg or 0.0,
            )
        except Exception:
            return None

        # De-duplicate targets that occur in more than one imported collection.
        unique_targets: dict[str, object] = {}
        for target, _collection, _entry, _sep in in_field:
            unique_targets[target.id] = target

        scored: list[tuple[int, object, str]] = []
        for target in unique_targets.values():
            names = [target.canonical_name, *(target.aliases or [])]
            if target.common_name:
                names.append(target.common_name)
            best_for_target: tuple[int, str] | None = None
            for name in names:
                candidate = self._split_catalogue_identifier(str(name))
                if candidate is None or candidate[0] != prefix:
                    continue
                distance = self._damerau_distance(digits, candidate[1])
                if best_for_target is None or distance < best_for_target[0]:
                    best_for_target = (distance, str(name))
            if best_for_target is not None:
                scored.append((best_for_target[0], target, best_for_target[1]))

        if not scored:
            return None
        scored.sort(key=lambda item: item[0])
        best_distance = scored[0][0]
        best = [item for item in scored if item[0] == best_distance]
        if best_distance != 1 or len(best) != 1:
            return None

        _distance, target, matched_name = best[0]
        corrected_name = target.canonical_name or matched_name
        self._append_solver_log(
            f"Filename suggested {filename_hint}; solved field uniquely supports {corrected_name}. "
            "Using the verified in-field catalogue object."
        )
        result: dict[str, object] = {
            "name": corrected_name,
            "identification_source": "automatic",
            "identification_version": self.SUBJECT_IDENTIFICATION_VERSION,
            "filename_verified": True,
            "filename_hint_corrected_from": filename_hint,
        }
        if target.object_type:
            result["object_type"] = target.object_type
        if target.constellation:
            result["constellation"] = target.constellation
        return result

    @staticmethod
    def _filename_transposition_candidates(filename_hint: str) -> list[str]:
        """Return conservative one-swap corrections for catalogue-number typos.

        This is intentionally narrow: only adjacent digit transpositions are
        considered (for example IC 4682 -> IC 4628).  A correction is never
        accepted merely because it looks plausible; it must independently
        resolve and land inside the solved image footprint.
        """
        match = re.fullmatch(r"([A-Za-z0-9-]+)\s+(\d+)", filename_hint.strip())
        if not match:
            return []
        prefix, digits = match.groups()
        if len(digits) < 2:
            return []
        candidates: list[str] = []
        for index in range(len(digits) - 1):
            if digits[index] == digits[index + 1]:
                continue
            swapped = list(digits)
            swapped[index], swapped[index + 1] = swapped[index + 1], swapped[index]
            candidate = f"{prefix} {''.join(swapped)}"
            if candidate != filename_hint and candidate not in candidates:
                candidates.append(candidate)
        return candidates

    def _verified_filename_target(
        self, solution: PlateSolution, filename_hint: str | None
    ) -> dict[str, object] | None:
        """Resolve a filename catalogue hint and verify it against the WCS.

        If the exact catalogue number does not verify, AstroFrame may test a
        single adjacent-digit transposition.  That handles common filename
        slips such as IC 4682 versus IC 4628 without ever trusting the filename
        over the plate solution.  A corrected designation is accepted only
        when exactly one candidate independently resolves inside the image.
        """
        if not filename_hint:
            return None

        verified: list[tuple[str, SkyCoord, bool]] = []
        for candidate, corrected in [(filename_hint, False), *[(item, True) for item in self._filename_transposition_candidates(filename_hint)]]:
            try:
                coord, _resolved_as = self._resolve_target_coordinates(candidate)
            except Exception:
                continue
            if self._coord_is_inside_solution(coord, solution):
                verified.append((candidate, coord, corrected))
                if not corrected:
                    break

        if not verified:
            return None

        # Exact verified filename always wins.  For typo correction, require a
        # unique in-frame candidate so we never guess between alternatives.
        exact = [item for item in verified if not item[2]]
        if exact:
            chosen_name, coord, corrected = exact[0]
        else:
            corrected_matches = [item for item in verified if item[2]]
            if len(corrected_matches) != 1:
                return None
            chosen_name, coord, corrected = corrected_matches[0]
            self._append_solver_log(
                f"Filename hint {filename_hint} does not match the solved field; "
                f"using verified catalogue object {chosen_name}."
            )

        object_type: str | None = None
        try:
            _main_id, object_type, _aliases = self._lookup_target_aliases(chosen_name)
        except Exception:
            object_type = None

        result: dict[str, object] = {
            "name": chosen_name,
            "identification_source": "automatic",
            "identification_version": self.SUBJECT_IDENTIFICATION_VERSION,
            "filename_verified": True,
            # Keep the independently resolved coordinate with the identified
            # subject.  Display markers can then use this verified position even
            # if an older imported catalogue row carried stale coordinates.
            "subject_ra_deg": float(coord.icrs.ra.deg),
            "subject_dec_deg": float(coord.icrs.dec.deg),
        }
        if corrected:
            result["filename_hint_corrected_from"] = filename_hint
        if object_type:
            result["object_type"] = object_type
        try:
            result["constellation"] = get_constellation(coord, short_name=False)
        except Exception:
            pass
        return result

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
        # Bracketed bibliography/survey designations (for example [BGC2014]
        # 365) are useful database identifiers but poor photographic subject
        # labels.  They should never outrank a familiar catalogue/common name.
        if upper.startswith("["):
            return 4, shown

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
        if any(token in value for token in ("NOVA", "SUPERNOVA", "TRANSIENT")):
            return -50
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

    def _strong_local_subject_candidate(self, solution: PlateSolution) -> dict[str, object] | None:
        """Return a high-confidence photographic subject from local collections.

        Crowded fields can fill SIMBAD's nearest-object limit with individual
        stars/transients before a large galaxy or nebula appears.  This local
        cross-check only wins when a familiar, sizeable curated DSO is both near
        the composition centre and clearly stronger than the next candidate.
        """
        try:
            entries = self.knowledge_store.entries_in_field(
                solution.ra_deg, solution.dec_deg,
                solution.image_width_deg, solution.image_height_deg,
                solution.orientation_deg or 0.0,
            )
        except Exception:
            return None
        unique: dict[str, object] = {}
        for target, _collection, _entry, _sep in entries:
            unique[target.id] = target
        if not unique:
            return None

        ref_area = max(float(solution.image_width_deg) * float(solution.image_height_deg), 1e-6)
        half_diag = max(0.5 * math.hypot(float(solution.image_width_deg), float(solution.image_height_deg)), 0.05)
        scored: list[tuple[float, object, str]] = []
        for target in unique.values():
            if target.ra_deg is None or target.dec_deg is None:
                continue
            names = [str(target.canonical_name or "")]
            if target.common_name:
                names.append(str(target.common_name))
            names.extend(str(a) for a in (target.aliases or []) if a)
            id_score, display = self._best_subject_identifier(names, str(target.canonical_name or ""))
            if id_score < 80:
                continue
            _east, _north, sep = self.knowledge_store._field_offsets_deg(
                solution.ra_deg, solution.dec_deg, float(target.ra_deg), float(target.dec_deg)
            )
            if sep > half_diag * 0.55:
                continue
            w = float(target.angular_width_deg or 0.0)
            h = float(target.angular_height_deg or 0.0)
            if w <= 0.0 and h > 0.0:
                w = h
            if h <= 0.0 and w > 0.0:
                h = w
            area_ratio = (w * h / ref_area) if w > 0 and h > 0 else 0.0
            # A tiny catalogue object only qualifies when essentially centred;
            # a large known DSO gets a strong framing-significance bonus.
            if area_ratio < 0.008 and sep > half_diag * 0.08:
                continue
            centre_bonus = 65.0 * max(0.0, 1.0 - sep / (half_diag * 0.55))
            extent_bonus = 85.0 * min(1.0, math.sqrt(max(area_ratio, 0.0)))
            type_bonus = float(self._subject_type_priority(target.object_type))
            score = float(id_score) + type_bonus + centre_bonus + extent_bonus
            scored.append((score, target, display))

        if not scored:
            return None
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, target, display = scored[0]
        if len(scored) > 1 and best_score - scored[1][0] < 16.0:
            return None
        result: dict[str, object] = {
            "name": display or target.canonical_name,
            "identification_source": "automatic",
            "identification_version": self.SUBJECT_IDENTIFICATION_VERSION,
            "local_subject_verified": True,
        }
        if target.object_type:
            result["object_type"] = target.object_type
        if target.constellation:
            result["constellation"] = target.constellation
        return result

    def _identify_target_from_solution(self, solution: PlateSolution) -> dict[str, object] | None:
        """Identify the likely photographic subject, not merely the nearest row.

        SIMBAD coordinate results are ordered by increasing distance.  We use
        that ordering as one signal, but combine it with catalogue familiarity
        and object type so anonymous survey sources do not displace a clearly
        framed Messier/NGC/IC/nebula/cluster subject.
        """
        filename_hint = self._filename_catalogue_hint(self.current_image_path)
        # First trust only an exact filename designation that independently
        # verifies.  If it fails, compare the hint against catalogue objects
        # already known to lie inside the solved WCS footprint.
        verified_filename = self._verified_filename_target(solution, filename_hint)
        if verified_filename is not None and not verified_filename.get("filename_hint_corrected_from"):
            return verified_filename
        field_correction = self._verified_in_field_filename_correction(solution, filename_hint)
        if field_correction is not None:
            return field_correction
        if verified_filename is not None:
            return verified_filename

        local_subject = self._strong_local_subject_candidate(solution)
        if local_subject is not None:
            return local_subject

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

        filename_hint = self._filename_catalogue_hint(self.current_image_path)
        hint_key = self._identifier_key(filename_hint) if filename_hint else ""

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

            # A catalogue designation in the photographer's filename is a
            # strong intent signal, but only after the solved-field SIMBAD
            # query independently verifies that exact object is present.
            candidate_keys = {
                self._identifier_key(value)
                for value in [main_id, *identifiers]
                if value
            }
            if hint_key and hint_key in candidate_keys:
                total += 220.0
                display_name = filename_hint or display_name

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

        # A catalogue designation in the photographer's filename is a strong
        # statement of intent.  Never let a cached automatic identification
        # with a different designation suppress re-verification of that hint.
        # This matters even when the cache was written by the current
        # identification algorithm version.
        filename_hint = self._filename_catalogue_hint(self.current_image_path)
        if filename_hint:
            cached_key = self._identifier_key(str(target_info.get("name") or ""))
            hint_key = self._identifier_key(filename_hint)
            print(
                f"version={target_info.get('identification_version')!r} hint={filename_hint!r} "
                f"cached_key={cached_key!r} hint_key={hint_key!r}",
                flush=True,
            )
            if cached_key != hint_key:
                return True

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
        self._refresh_collections_summary(str(target_name))

        # The Equipment Advisor now uses the identified primary subject when
        # ranking "Shoot this instead" detail crops.  Some images restore a
        # plate solution immediately from cache and only identify their subject
        # a moment later in the background.  Rebuild the advisor here so those
        # images cannot be left with a hidden/stale advisor simply because the
        # first advisor pass happened before subject identification completed.
        if self.current_solution is not None and hasattr(self, "advisor_section"):
            self._schedule_equipment_advisor_refresh()

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
        self.online_fallback_button.hide()
        if hasattr(self, "observing_season_result"):
            self.observing_season_result.hide()
        if hasattr(self, "observability_find_button"):
            self.observability_find_button.show()
        self.width_spin.blockSignals(True)
        self.width_spin.setValue(solution.image_width_deg)
        self.width_spin.blockSignals(False)
        self.viewer.set_reference_width(solution.image_width_deg)
        self.width_spin.setEnabled(False)

        external = solution.solver.startswith("External —")
        # New cache records carry orientation_known explicitly.  Legacy external
        # records do not, so treat them conservatively as unknown rather than
        # inventing a meaningful 0° rotation.
        orientation_known = (
            bool(solution.orientation_known)
            if solution.orientation_known is not None
            else (not external)
        )
        self.solve_status.setObjectName("externalStatus" if external else "verifiedStatus")
        source = f"AstroFrame cache — originally {solution.solver}" if cached else solution.solver
        if external:
            self.solve_status.setText(f"●  External astrometry — {source}")
        else:
            self.solve_status.setText(f"●  Verified — {source}")
        self.solve_status.style().unpolish(self.solve_status)
        self.solve_status.style().polish(self.solve_status)
        orientation_line = (
            f"Orientation: {float(solution.orientation_deg):.2f}°"
            if orientation_known and solution.orientation_deg is not None
            else "Orientation: Unknown"
        )
        detail_lines = [
            f"Centre: RA {solution.ra_deg:.5f}°, Dec {solution.dec_deg:+.5f}°",
            f"Scale: {solution.pixel_scale_arcsec:.3f} arcsec/px",
            f"Image: {solution.image_width_deg:.3f}° × {solution.image_height_deg:.3f}°",
            orientation_line,
            f"Mode: {solution.solve_mode}",
        ]
        if solution.solve_seconds is not None:
            detail_lines.append(f"Solve time: {solution.solve_seconds:.1f} s")
        self.solve_details.setText("\n".join(detail_lines))
        # QLabel can retain a stale height after the longer external provenance
        # line wraps. Reserve enough room for every astrometric detail line so
        # the Plate solver section can never crowd or clip this block.
        line_height = self.solve_details.fontMetrics().lineSpacing()
        self.solve_details.setMinimumHeight(line_height * len(detail_lines) + 14)
        self.solve_details.setContentsMargins(0, 4, 0, 6)
        self.solve_details.updateGeometry()
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
            + (f"{float(solution.orientation_deg):.2f}°"
               if orientation_known and solution.orientation_deg is not None
               else "Unknown")
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
            ("Astrometry source\n" if external else "Solved with\n")
            + f"{solve_source}"
            + f"{solve_time}"
            + ("\nExternally supplied; not independently verified by AstroFrame." if external else "")
        )
        self._update_visibility_summary(solution)
        self.image_summary.show()
        # Build the Advisor after the cached-image/load signal chain has settled.
        # This also coalesces the target-summary refresh above into one rebuild.
        self._schedule_equipment_advisor_refresh()

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

        local_exhausted = message.startswith("LOCAL_SOLVE_EXHAUSTED::")
        clue_exhausted = message.startswith("CLUE_SOLVE_EXHAUSTED::")
        clean_message = message.split("::", 1)[1] if (local_exhausted or clue_exhausted) else message
        automatic = str(self.solver_combo.currentData()) == "automatic"

        # Automatic means what the selector says: local first, then online if needed.
        # Before uploading, exploit a high-confidence catalogue token already present
        # in the filename, but only after asking the user to confirm that it is in-frame.
        if automatic and (local_exhausted or clue_exhausted):
            if local_exhausted and self.solving_hint_ra_hours is None:
                filename_hint = self._filename_catalogue_hint(self.current_image_path)
                if filename_hint:
                    answer = QMessageBox.question(
                        self,
                        "Possible target found in filename",
                        f"The filename suggests {filename_hint}.\n\n"
                        f"Is this an image that includes {filename_hint}?\n\n"
                        "If yes, AstroFrame will use it as a positional clue and retry ASTAP locally before going online.",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.Yes,
                    )
                    if answer == QMessageBox.StandardButton.Yes:
                        try:
                            centre, _targets = self._resolve_target_entry(filename_hint)
                            ra_hours = float(centre.ra.hour)
                            dec_deg = float(centre.dec.deg)
                        except Exception as exc:
                            self._append_solver_log(
                                f"Filename clue {filename_hint} could not be resolved: {exc}; continuing to Astrometry.net"
                            )
                        else:
                            self.solving_hint_name = filename_hint
                            self.solving_hint_ra_hours = ra_hours
                            self.solving_hint_dec_deg = dec_deg
                            self.solving_hint_search_radius_deg = 30.0
                            self.solving_hint_label.setText(f"Solver clue\n{filename_hint} (from filename)")
                            self.assisted_solve_button.setText("Change Solver Clue…")
                            self._append_solver_log(
                                f"Filename clue confirmed: {filename_hint}; retrying local ASTAP"
                            )
                            self._pending_auto_solve = (
                                "astap", ra_hours, dec_deg, 30.0, filename_hint
                            )
                            QTimer.singleShot(0, self._start_pending_auto_solve_if_ready)
                            self.solve_status.setObjectName("solvingStatus")
                            self.solve_status.setText("⏳  Blind solve failed — retrying ASTAP with filename clue…")
                            self.solve_details.hide()
                            return
                    else:
                        self._append_solver_log(
                            f"Filename clue declined: {filename_hint}; continuing to Astrometry.net"
                        )

            # Do not silently upload after local solving fails.  A manual clue is
            # often faster and more private, so Automatic mode pauses here and
            # lets the user choose the next rung of the solve ladder.
            choice = QMessageBox(self)
            choice.setWindowTitle("ASTAP needs a little help")
            choice.setIcon(QMessageBox.Icon.Question)
            choice.setText("ASTAP could not identify this image locally.")
            choice.setInformativeText(
                "Would you like to give AstroFrame an object-name/RA-Dec clue before "
                "sending the image to Astrometry.net?"
            )
            clue_button = choice.addButton("Give Solver a Clue…", QMessageBox.ButtonRole.AcceptRole)
            online_button = choice.addButton("Try Astrometry.net", QMessageBox.ButtonRole.ActionRole)
            choice.addButton("Stop Here", QMessageBox.ButtonRole.RejectRole)
            choice.setDefaultButton(clue_button)
            choice.exec()
            clicked = choice.clickedButton()
            if clicked is clue_button:
                self.solve_status.setObjectName("failedStatus")
                self.solve_status.setText("●  ASTAP needs a clue")
                self.solve_details.setText(clean_message)
                self.solve_details.show()
                QTimer.singleShot(0, self.target_assisted_solve)
                return
            if clicked is online_button:
                self._pending_auto_solve = ("online", None, None, None, None)
                QTimer.singleShot(0, self._start_pending_auto_solve_if_ready)
                self.solve_status.setObjectName("solvingStatus")
                self.solve_status.setText("⏳  Continuing with Astrometry.net…")
                self.solve_details.setText(
                    clean_message + "\n\nLocal ASTAP solving was exhausted. You chose the Astrometry.net fallback."
                )
                self.solve_details.show()
                return
            self.solve_status.setObjectName("failedStatus")
            self.solve_status.setText("●  Local solve stopped")
            self.solve_details.setText(
                clean_message + "\n\nNo image was sent to Astrometry.net. You can give the solver a clue or retry later."
            )
            self.solve_details.show()
            self.assisted_solve_button.setEnabled(True)
            self.online_fallback_button.show()
            return

        self.solve_status.setObjectName("failedStatus")
        self.solve_status.setText("●  Plate solve needs help")
        self.solve_status.style().unpolish(self.solve_status)
        self.solve_status.style().polish(self.solve_status)

        if local_exhausted:
            help_text = (
                "ASTAP could not solve this image without a position clue.\n\n"
                "Fastest next step: Give Solver a Clue… using any known object or RA/Dec coordinates visible anywhere in the frame.\n\n"
                "Or choose Try Astrometry.net blind solve… if you want a remote blind solve."
            )
            self.online_fallback_button.show()
        else:
            help_text = (
                "The solve did not complete. You can give ASTAP an object-name or RA/Dec clue, or retry another solver."
            )
            self.online_fallback_button.setVisible(
                str(self.solver_combo.currentData()) != "online"
            )

        self.solve_details.setText(clean_message + "\n\n" + help_text)
        self.solve_details.show()
        self.assisted_solve_button.setText("Give Solver a Clue…")
        self.assisted_solve_button.setObjectName("identifyTargetButton")
        self.assisted_solve_button.setEnabled(True)
        self.assisted_solve_button.style().unpolish(self.assisted_solve_button)
        self.assisted_solve_button.style().polish(self.assisted_solve_button)
        self.astrometry_job_button.setEnabled(bool(self.current_image_path))

    def _solve_finished_for(
        self, thread: QThread, worker: PlateSolveWorker
    ) -> None:
        # Retired jobs are allowed to wind down quietly after immediate UI
        # cancellation.  Never let an old thread clear a newer active solve.
        self._retired_solve_jobs = [
            pair for pair in self._retired_solve_jobs
            if pair[0] is not thread
        ]
        if self.solve_thread is not thread:
            return
        if self.current_solution is None and self.solve_in_progress:
            self.solve_in_progress = False
            self.solve_button.setText("Plate Solve")
            self.solve_button.setToolTip("")
            self.solve_button.setEnabled(self.current_image_path is not None)
        self.solve_thread = None
        self.solve_worker = None
        self.astrometry_job_button.setEnabled(bool(self.current_image_path))

        self._start_pending_auto_solve_if_ready()

    def _start_pending_auto_solve_if_ready(self) -> None:
        """Start an Automatic-mode follow-up without another Plate Solve click."""
        if self._pending_auto_solve is None or not self.current_image_path:
            return

        thread = self.solve_thread
        if self.solve_in_progress or (thread is not None and thread.isRunning()):
            QTimer.singleShot(50, self._start_pending_auto_solve_if_ready)
            return

        pending_auto = self._pending_auto_solve
        self._pending_auto_solve = None
        mode, ra_hours, dec_deg, radius_deg, shown_name = pending_auto

        if mode == "astap":
            self._append_solver_log(
                f"Automatic: starting clue-assisted ASTAP{f' with {shown_name}' if shown_name else ''}"
            )
            QTimer.singleShot(
                0,
                lambda ra=ra_hours, dec=dec_deg, radius=radius_deg: self.plate_solve(
                    ra, dec,
                    target_search_radius_deg=radius,
                    solver_preference_override="astap",
                ),
            )
        else:
            self._append_solver_log(
                "Automatic: local solving exhausted; starting Astrometry.net fallback"
            )
            QTimer.singleShot(
                0,
                lambda: self.plate_solve(solver_preference_override="online"),
            )

    def _reference_width_changed(self, value: float) -> None:
        if self.current_solution is None:
            if value > 0:
                self.viewer.set_reference_width(value)
                self.settings.setValue("referenceWidthEstimate", value)
            else:
                self.viewer.set_reference_width(DEFAULT_REFERENCE_WIDTH)

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
            active_key = str((self._working_framing or {}).get("rig_key") or "")
            if active_key:
                # Reset is a composition reset, not merely a visual recenter: keep
                # the working/exported sky centre in sync with the displayed frame.
                self._activate_reference_rig_framing(active_key)
                self._enable_working_framing_placement()
            self._update_equipment_advisor()
        if self.current_solution is None:
            self.width_spin.setValue(0.0)
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
