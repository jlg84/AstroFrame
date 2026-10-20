from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QObject, Signal, Slot

from .annotations import annotate_galaxies, annotate_openngc_objects, annotate_barnard_objects
from .coordinate_grid import render_coordinate_grid


class AnnotationWorker(QObject):
    progress = Signal(str)
    succeeded = Signal(int, str)
    failed = Signal(str)
    finished = Signal()

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
        output_max_edge: int = 0,
        annotation_style: str = "automatic",
    ) -> None:
        super().__init__()
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
        self.output_max_edge = int(output_max_edge)
        self.annotation_style = annotation_style

    @Slot()
    def run(self) -> None:
        try:
            from .annotations import render_annotated_image
            from .annotations import image_dimensions, image_sky_coverage
            from .annotations import sky_to_pixel, galaxy_ellipse_pixels
            from .annotations import Annotation

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

            self.succeeded.emit(count, str(self.output_path))
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()
