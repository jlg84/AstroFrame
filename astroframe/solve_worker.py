from __future__ import annotations

from threading import Event

from PySide6.QtCore import QObject, Signal, Slot

from .plate_solve import (
    AstapClient,
    AstrometryNetClient,
    PlateSolution,
    PlateSolveCancelled,
)


class PlateSolveWorker(QObject):
    progress = Signal(str)
    log = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    finished = Signal()

    def __init__(
        self,
        *,
        solver_preference: str,
        api_key: str | None,
        image_path: str,
        image_width_px: int,
        image_height_px: int,
        estimated_width_deg: float,
        target_ra_hours: float | None = None,
        target_dec_deg: float | None = None,
        astrometry_job_reference: str | int | None = None,
    ) -> None:
        super().__init__()
        self.solver_preference = solver_preference
        self.api_key = (api_key or "").strip()
        self.image_path = image_path
        self.image_width_px = image_width_px
        self.image_height_px = image_height_px
        self.estimated_width_deg = estimated_width_deg
        self.target_ra_hours = target_ra_hours
        self.target_dec_deg = target_dec_deg
        self.astrometry_job_reference = astrometry_job_reference
        self._cancel_event = Event()

    def cancel(self) -> None:
        """Request cancellation from the GUI thread."""
        self._cancel_event.set()

    def _is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def _run_astap(self) -> PlateSolution:
        self.log.emit("ASTAP: locating executable")
        local = AstapClient()
        self.log.emit(f"ASTAP executable: {local.executable}")
        self.log.emit("ASTAP attempt: Field = Auto")
        return local.solve(
            self.image_path,
            image_width_px=self.image_width_px,
            image_height_px=self.image_height_px,
            estimated_width_deg=None,  # Field=Auto is the first-instance strategy.
            ra_hours=self.target_ra_hours,
            dec_deg=self.target_dec_deg,
            search_radius_deg=10.0 if self.target_ra_hours is not None else 180.0,
            progress=self.progress.emit,
            log=self.log.emit,
            cancelled=self._is_cancelled,
        )

    def _run_online(self) -> PlateSolution:
        if not self.api_key and self.astrometry_job_reference is None:
            raise RuntimeError(
                "No Astrometry.net API key is configured. Open Plate Solving "
                "settings and enter the key before using Online only."
            )
        remote = AstrometryNetClient(self.api_key)
        if self.astrometry_job_reference is not None:
            job_id = remote.resolve_job_reference(
                self.astrometry_job_reference,
                progress=self.progress.emit,
            )
            self.log.emit(f"Astrometry.net: using existing job #{job_id}")
            return remote.solution_from_job(
                job_id,
                image_width_px=self.image_width_px,
                image_height_px=self.image_height_px,
                progress=self.progress.emit,
                cancelled=self._is_cancelled,
            )

        self.log.emit("Astrometry.net: logging in and uploading image")
        return remote.solve(
            self.image_path,
            image_width_px=self.image_width_px,
            image_height_px=self.image_height_px,
            estimated_width_deg=self.estimated_width_deg,
            progress=self.progress.emit,
            cancelled=self._is_cancelled,
        )

    @Slot()
    def run(self) -> None:
        try:
            preference = self.solver_preference
            self.log.emit(f"Solver preference: {preference}")

            if self.astrometry_job_reference is not None:
                solution = self._run_online()
                self.succeeded.emit(solution)
                return

            if preference == "online":
                solution = self._run_online()
                self.succeeded.emit(solution)
                return

            if preference == "astap":
                solution = self._run_astap()
                self.succeeded.emit(solution)
                return

            # Automatic: local first, online fallback.
            try:
                solution = self._run_astap()
                self.succeeded.emit(solution)
                return
            except PlateSolveCancelled:
                raise
            except Exception as exc:
                self.log.emit(f"ASTAP failed: {exc}")
                self.progress.emit("Local solve failed; trying Astrometry.net…")

            if self._is_cancelled():
                raise PlateSolveCancelled("Plate solve cancelled.")
            solution = self._run_online()
            self.succeeded.emit(solution)

        except PlateSolveCancelled:
            self.log.emit("Plate solve cancelled by user")
            self.cancelled.emit()
        except Exception as exc:
            self.log.emit(f"Solve failed: {exc}")
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()
