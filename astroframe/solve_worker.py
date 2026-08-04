from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from .plate_solve import AstapClient, AstrometryNetClient, PlateSolution


class PlateSolveWorker(QObject):
    progress = Signal(str)
    diagnostic = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        *,
        api_key: str | None,
        image_path: str,
        image_width_px: int,
        image_height_px: int,
        estimated_width_deg: float,
        target_ra_hours: float | None = None,
        target_dec_deg: float | None = None,
    ) -> None:
        super().__init__()
        self.api_key = (api_key or '').strip()
        self.image_path = image_path
        self.image_width_px = image_width_px
        self.image_height_px = image_height_px
        self.estimated_width_deg = estimated_width_deg
        self.target_ra_hours = target_ra_hours
        self.target_dec_deg = target_dec_deg

    @Slot()
    def run(self) -> None:
        astap_errors: list[str] = []

        try:
            local = AstapClient()

            attempts: list[dict] = []
            assisted = (
                self.target_ra_hours is not None
                and self.target_dec_deg is not None
            )

            # Evidence from ASTAP testing: let ASTAP determine the field first.
            attempts.append({
                "label": "ASTAP auto-field" + (" + target" if assisted else ""),
                "auto": True,
                "ra": self.target_ra_hours,
                "dec": self.target_dec_deg,
                "radius": 10.0 if assisted else 180.0,
            })

            # For an assisted solve, retry with the estimated field only after
            # ASTAP's Auto field has failed.
            if assisted:
                attempts.append({
                    "label": "ASTAP estimated-field + target",
                    "auto": False,
                    "ra": self.target_ra_hours,
                    "dec": self.target_dec_deg,
                    "radius": 10.0,
                })

            for number, attempt in enumerate(attempts, start=1):
                self.diagnostic.emit(
                    f"Attempt {number}: {attempt['label']}"
                )
                try:
                    solution: PlateSolution = local.solve(
                        self.image_path,
                        image_width_px=self.image_width_px,
                        image_height_px=self.image_height_px,
                        estimated_width_deg=self.estimated_width_deg,
                        ra_hours=attempt["ra"],
                        dec_deg=attempt["dec"],
                        search_radius_deg=attempt["radius"],
                        use_auto_fov=attempt["auto"],
                        progress=self.progress.emit,
                        log=self.diagnostic.emit,
                    )
                    self.diagnostic.emit(
                        f"SUCCESS: {attempt['label']}"
                    )
                    self.succeeded.emit(solution)
                    return
                except Exception as exc:
                    message = str(exc)
                    astap_errors.append(
                        f"{attempt['label']}: {message}"
                    )
                    self.diagnostic.emit(
                        f"FAILED: {attempt['label']}\n{message}"
                    )

            if not self.api_key:
                raise RuntimeError(
                    "\n\n".join(astap_errors)
                    + "\n\nNo Astrometry.net API key is stored, so the "
                    "online fallback was not attempted."
                )

            self.diagnostic.emit(
                "All local ASTAP attempts failed."
            )
            self.progress.emit(
                "ASTAP did not solve it; trying Astrometry.net…"
            )
            self.diagnostic.emit("Fallback: Astrometry.net")
            remote = AstrometryNetClient(self.api_key)
            solution = remote.solve(
                self.image_path,
                image_width_px=self.image_width_px,
                image_height_px=self.image_height_px,
                estimated_width_deg=self.estimated_width_deg,
                progress=self.progress.emit,
            )
            self.succeeded.emit(solution)

        except Exception as exc:  # worker boundary
            self.diagnostic.emit("FINAL FAILURE: " + str(exc))
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()
