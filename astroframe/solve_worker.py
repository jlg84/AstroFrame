from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal, Slot

from .plate_solve import (
    AstapClient,
    AstrometryNetClient,
    PlateSolution,
    SolveCancelled,
)


class PlateSolveWorker(QObject):
    progress = Signal(str)
    diagnostic = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
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
        solver_strategy: str = "smart",
    ) -> None:
        super().__init__()
        self.api_key = (api_key or '').strip()
        self.image_path = image_path
        self.image_width_px = image_width_px
        self.image_height_px = image_height_px
        self.estimated_width_deg = estimated_width_deg
        self.target_ra_hours = target_ra_hours
        self.target_dec_deg = target_dec_deg
        self.solver_strategy = solver_strategy
        self.cancel_event = threading.Event()
        self.local_client: AstapClient | None = None
        self.remote_client: AstrometryNetClient | None = None

    def cancel(self) -> None:
        self.cancel_event.set()
        if self.local_client is not None:
            self.local_client.cancel()
        if self.remote_client is not None:
            self.remote_client.cancel()

    def _check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise SolveCancelled("Solve cancelled by user.")

    @Slot()
    def run(self) -> None:
        astap_errors: list[str] = []

        try:
            strategy = self.solver_strategy
            if strategy not in {"smart", "local", "online"}:
                strategy = "smart"

            self.diagnostic.emit(f"Solver strategy: {strategy}")
            self._check_cancelled()

            if strategy != "online":
                local = AstapClient()
                self.local_client = local

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
                    self._check_cancelled()
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
                            cancel_event=self.cancel_event,
                        )
                        self.diagnostic.emit(
                            f"SUCCESS: {attempt['label']}"
                        )
                        self.succeeded.emit(solution)
                        return
                    except SolveCancelled:
                        raise
                    except Exception as exc:
                        message = str(exc)
                        astap_errors.append(
                            f"{attempt['label']}: {message}"
                        )
                        self.diagnostic.emit(
                            f"FAILED: {attempt['label']}\n{message}"
                        )

                self.diagnostic.emit("All local ASTAP attempts failed.")

                if strategy == "local":
                    raise RuntimeError(
                        "\n\n".join(astap_errors)
                        + "\n\nSolver strategy is Local only, so the online "
                        "fallback was not attempted."
                    )

            if not self.api_key:
                prefix = "\n\n".join(astap_errors)
                if prefix:
                    prefix += "\n\n"
                raise RuntimeError(
                    prefix
                    + "No Astrometry.net API key is stored, so the online "
                    "solve was not attempted."
                )

            if strategy == "online":
                self.progress.emit("Connecting to Astrometry.net…")
                self.diagnostic.emit("Online-only solve: Astrometry.net")
            else:
                self.progress.emit(
                    "Local solve unsuccessful — trying Astrometry.net…"
                )
                self.diagnostic.emit("Fallback: Astrometry.net")

            self._check_cancelled()
            remote = AstrometryNetClient(
                self.api_key, cancel_event=self.cancel_event
            )
            self.remote_client = remote
            solution = remote.solve(
                self.image_path,
                image_width_px=self.image_width_px,
                image_height_px=self.image_height_px,
                estimated_width_deg=self.estimated_width_deg,
                progress=self.progress.emit,
                log=self.diagnostic.emit,
            )
            self._check_cancelled()
            self.succeeded.emit(solution)

        except SolveCancelled:
            self.cancelled.emit()
        except Exception as exc:  # worker boundary
            self.diagnostic.emit("FINAL FAILURE: " + str(exc))
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()
