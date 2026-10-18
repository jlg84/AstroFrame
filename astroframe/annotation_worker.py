from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from .annotations import annotate_galaxies


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
    ) -> None:
        super().__init__()
        self.image_path = Path(image_path)
        self.wcs_path = Path(wcs_path)
        self.catalogue_path = Path(catalogue_path)
        self.output_path = Path(output_path)

    @Slot()
    def run(self) -> None:
        try:
            count = annotate_galaxies(
                self.image_path,
                self.wcs_path,
                self.catalogue_path,
                self.output_path,
                progress=self.progress.emit,
            )
            self.succeeded.emit(count, str(self.output_path))
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()
