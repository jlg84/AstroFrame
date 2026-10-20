from __future__ import annotations

from pathlib import Path
from threading import Event
from tempfile import TemporaryDirectory

from PySide6.QtCore import QObject, Signal, Slot

from .annotations import annotate_galaxies, annotate_openngc_objects, annotate_barnard_objects
from .annotations import annotate_bright_stars
from .coordinate_grid import render_coordinate_grid


class AnnotationWorker(QObject):
    progress = Signal(str)
    succeeded = Signal(int, str)
    failed = Signal(str)
    finished = Signal()
    cancelled = Signal()

    def cancel(self) -> None:
        self._cancel_event.set()

    def _check_cancelled(self) -> None:
        if self._cancel_event.is_set():
            raise InterruptedError("Annotation cancelled")

    def __init__(
        self,
        image_path: str | Path,
        wcs_path: str | Path,
        catalogue_path: str | Path,
        output_path: str | Path,
        *,
        include_galaxies: bool = True,
        include_grid: bool = False,
        include_nebulae: bool = False,
        include_clusters: bool = False,
        include_planetary_nebulae: bool = False,
        include_open_clusters: bool = False,
        include_globular_clusters: bool = False,
        include_dark_nebulae: bool = False,
        include_bright_stars: bool = False,
        star_magnitude_limit: float = 6.0,
        output_max_edge: int = 0,
        annotation_style: str = "automatic",
        primary_subject: dict | None = None,
    ) -> None:
        super().__init__()
        self._cancel_event = Event()
        self.image_path = Path(image_path)
        self.wcs_path = Path(wcs_path)
        self.catalogue_path = Path(catalogue_path) if catalogue_path is not None else None
        self.output_path = Path(output_path)
        self.include_galaxies = include_galaxies
        self.include_grid = include_grid
        self.include_nebulae = include_nebulae
        self.include_clusters = include_clusters
        self.include_planetary_nebulae = include_planetary_nebulae
        self.include_open_clusters = include_open_clusters
        self.include_globular_clusters = include_globular_clusters
        self.include_dark_nebulae = include_dark_nebulae
        self.include_bright_stars = include_bright_stars
        self.star_magnitude_limit = float(star_magnitude_limit)
        self.output_max_edge = int(output_max_edge)
        self.annotation_style = annotation_style
        self.primary_subject = primary_subject

    @Slot()
    def run(self) -> None:
        try:
            from .annotations import render_annotated_image
            from .annotations import image_dimensions, image_sky_coverage
            from .annotations import sky_to_pixel, galaxy_ellipse_pixels
            from .annotations import Annotation

            self._check_cancelled()
            selected_modes = []
            if self.include_nebulae:
                selected_modes.append("nebulae")
            if self.include_clusters:
                selected_modes.append("clusters")
            if self.include_planetary_nebulae:
                selected_modes.append("planetary_nebulae")
            if self.include_open_clusters:
                selected_modes.append("open_clusters")
            if self.include_globular_clusters:
                selected_modes.append("globular_clusters")

            needs_galaxies = self.include_galaxies
            needs_grid = self.include_grid
            count = 0

            annotations = []
            if needs_galaxies:
                self.progress.emit("Collecting galaxy annotations…")
                annotations.extend(annotate_galaxies(
                    self.image_path, self.wcs_path, self.catalogue_path,
                    self.output_path, progress=self.progress.emit,
                    collect_only=True,
                ))

            for mode in selected_modes:
                self.progress.emit(f"Collecting OpenNGC {mode}…")
                annotations.extend(annotate_openngc_objects(
                    self.image_path, self.wcs_path, self.output_path,
                    mode=mode, progress=self.progress.emit,
                    collect_only=True,
                ))

            if self.include_dark_nebulae:
                self.progress.emit("Collecting Barnard dark nebulae…")
                annotations.extend(annotate_barnard_objects(
                    self.image_path, self.wcs_path, self.output_path,
                    progress=self.progress.emit,
                    collect_only=True,
                ))

            if self.include_bright_stars:
                self.progress.emit("Collecting Yale bright stars…")
                annotations.extend(annotate_bright_stars(
                    self.image_path, self.wcs_path, self.output_path,
                    magnitude_limit=self.star_magnitude_limit,
                    progress=self.progress.emit,
                    collect_only=True,
                ))

            self._check_cancelled()

            # Avoid duplicating the titled primary subject in the catalogue
            # annotations. Normalize spacing and leading zeros in IDs.
            if self.primary_subject and self.primary_subject.get("name"):
                import re

                def normalized_identifier(value):
                    text = str(value or "").strip().upper()
                    match = re.fullmatch(
                        r"(NGC|IC|M)\s*0*(\d+)", text
                    )
                    if match:
                        return f"{match.group(1)}{int(match.group(2))}"
                    return " ".join(text.split())

                primary_id = normalized_identifier(
                    self.primary_subject["name"]
                )
                annotations = [
                    item for item in annotations
                    if normalized_identifier(item.name) != primary_id
                ]

            count = len(annotations)
            if annotations:
                self.progress.emit(f"Rendering {count} annotations together…")
                if needs_grid:
                    with TemporaryDirectory() as temp_dir:
                        annotated_path = Path(temp_dir) / "annotated.png"
                        render_annotated_image(
                            self.image_path, annotations, annotated_path,
                            annotation_style=self.annotation_style,
                        )
                        self.progress.emit("Adding coordinate grid…")
                        render_coordinate_grid(
                            annotated_path, self.wcs_path, self.output_path,
                        )
                else:
                    render_annotated_image(
                        self.image_path, annotations, self.output_path,
                        annotation_style=self.annotation_style,
                    )
            elif needs_grid:
                self.progress.emit("Rendering coordinate grid…")
                render_coordinate_grid(
                    self.image_path, self.wcs_path, self.output_path,
                )
            else:
                import shutil
                shutil.copy2(self.image_path, self.output_path)

            # Add the primary-subject title last, above annotations and grid.
            if self.primary_subject and self.primary_subject.get("name"):
                from .annotations import render_primary_subject_title
                self._check_cancelled()
                self.progress.emit("Adding primary subject title…")
                render_primary_subject_title(
                    self.output_path,
                    self.primary_subject,
                    self.output_path,
                )

            # Export the final opaque annotation as RGB PNG, without alpha.
            if self.output_path.suffix.lower() == ".png":
                from PySide6.QtGui import QImage
                final_image = QImage(str(self.output_path))
                if final_image.isNull():
                    raise OSError("Could not read completed annotated PNG")
                if self.output_max_edge > 0:
                    from PySide6.QtCore import Qt
                    long_edge = max(final_image.width(), final_image.height())
                    if long_edge > self.output_max_edge:
                        self.progress.emit("Resizing annotated image…")
                        final_image = final_image.scaled(
                            self.output_max_edge,
                            self.output_max_edge,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                rgb = final_image.convertToFormat(QImage.Format.Format_RGB888)
                if not rgb.save(str(self.output_path), "PNG"):
                    raise OSError("Could not save RGB annotated PNG")

            self._check_cancelled()
            self.succeeded.emit(count, str(self.output_path))
        except InterruptedError:
            self.output_path.unlink(missing_ok=True)
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()
