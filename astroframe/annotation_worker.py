from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QObject, Signal, Slot

from .annotations import annotate_galaxies
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
    ) -> None:
        super().__init__()
        self.image_path = Path(image_path)
        self.wcs_path = Path(wcs_path)
        self.catalogue_path = Path(catalogue_path) if catalogue_path is not None else None
        self.output_path = Path(output_path)
        self.include_galaxies = include_galaxies
        self.include_grid = include_grid

    @Slot()
    def run(self) -> None:
        try:
            count = 0

            if self.include_galaxies and not self.include_grid:
                count = annotate_galaxies(
                    self.image_path,
                    self.wcs_path,
                    self.catalogue_path,
                    self.output_path,
                    progress=self.progress.emit,
                )

            elif self.include_grid and not self.include_galaxies:
                self.progress.emit("Rendering coordinate grid…")
                render_coordinate_grid(
                    self.image_path,
                    self.wcs_path,
                    self.output_path,
                )

            elif self.include_galaxies and self.include_grid:
                with TemporaryDirectory() as temp_dir:
                    intermediate = Path(temp_dir) / "galaxies.png"
                    count = annotate_galaxies(
                        self.image_path,
                        self.wcs_path,
                        self.catalogue_path,
                        intermediate,
                        progress=self.progress.emit,
                    )
                    self.progress.emit("Adding coordinate grid…")
                    render_coordinate_grid(
                        intermediate,
                        self.wcs_path,
                        self.output_path,
                    )

            self.succeeded.emit(count, str(self.output_path))
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()
