from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QObject, Signal, Slot

from .annotations import annotate_galaxies, annotate_openngc_objects
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
        output_max_edge: int = 0,
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
        self.output_max_edge = int(output_max_edge)

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

            needs_galaxies = self.include_galaxies
            needs_grid = self.include_grid
            count = 0

            if not selected_modes and needs_galaxies and not needs_grid:
                count = annotate_galaxies(
                    self.image_path,
                    self.wcs_path,
                    self.catalogue_path,
                    self.output_path,
                    progress=self.progress.emit,
                )
            elif not selected_modes and not needs_galaxies and needs_grid:
                self.progress.emit("Rendering coordinate grid…")
                render_coordinate_grid(
                    self.image_path,
                    self.wcs_path,
                    self.output_path,
                )
            else:
                with TemporaryDirectory() as temp_dir:
                    working_image = self.image_path
                    if needs_galaxies:
                        galaxy_path = Path(temp_dir) / "galaxies.png"
                        count = annotate_galaxies(
                            working_image,
                            self.wcs_path,
                            self.catalogue_path,
                            galaxy_path,
                            progress=self.progress.emit,
                        )
                        working_image = galaxy_path

                    if selected_modes:
                        catalogue_path = Path(temp_dir) / "catalogue.png"
                        working_catalogue_image = working_image
                        total_objects = 0

                        for mode in selected_modes:
                            self.progress.emit(
                                f"Annotating OpenNGC {mode}…"
                            )
                            intermediate = (
                                Path(temp_dir) / f"{mode}.png"
                            )
                            total_objects += annotate_openngc_objects(
                                working_catalogue_image,
                                self.wcs_path,
                                intermediate,
                                mode=mode,
                                progress=self.progress.emit,
                            )
                            working_catalogue_image = intermediate

                        working_image = working_catalogue_image
                        count += total_objects

                    if needs_grid:
                        self.progress.emit("Adding coordinate grid…")
                        render_coordinate_grid(
                            working_image,
                            self.wcs_path,
                            self.output_path,
                        )
                    elif working_image != self.image_path:
                        import shutil
                        shutil.copy2(working_image, self.output_path)

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
