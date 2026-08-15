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
        target_search_radius_deg: float | None = None,
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
        self.target_search_radius_deg = target_search_radius_deg
        self.astrometry_job_reference = astrometry_job_reference
        self._cancel_event = Event()

    def cancel(self) -> None:
        """Request cancellation from the GUI thread."""
        self._cancel_event.set()

    def _is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def _run_astap(
        self,
        timeout_seconds: float | None = None,
        *,
        use_scale_hint: bool = False,
        estimated_width_override: float | None = None,
        search_radius_override: float | None = None,
        speed_mode: str | None = None,
        max_stars: int | None = None,
        quad_tolerance: float | None = None,
    ) -> PlateSolution:
        self.log.emit("ASTAP: locating executable")
        local = AstapClient(timeout_seconds=timeout_seconds or 180.0)
        self.log.emit(f"ASTAP executable: {local.executable}")
        requested_width = estimated_width_override
        if requested_width is None and use_scale_hint and self.estimated_width_deg > 0:
            requested_width = self.estimated_width_deg
        field_label = (
            f"explicit image width {requested_width:.3f}° (converted to ASTAP image-height FOV)"
            if requested_width is not None and requested_width > 0
            else "Field = Auto"
        )
        self.log.emit(
            "ASTAP attempt: " + field_label
            + (
                " · clue radius %.0f°" % (self.target_search_radius_deg or 30.0)
                if self.target_ra_hours is not None
                else ""
            )
        )
        return local.solve(
            self.image_path,
            image_width_px=self.image_width_px,
            image_height_px=self.image_height_px,
            estimated_width_deg=(requested_width if requested_width is not None and requested_width > 0 else None),
            ra_hours=self.target_ra_hours,
            dec_deg=self.target_dec_deg,
            search_radius_deg=(
                search_radius_override
                if search_radius_override is not None
                else (
                    self.target_search_radius_deg
                    if self.target_ra_hours is not None and self.target_search_radius_deg is not None
                    else (30.0 if self.target_ra_hours is not None else 180.0)
                )
            ),
            speed_mode=speed_mode,
            max_stars=max_stars,
            quad_tolerance=quad_tolerance,
            progress=self.progress.emit,
            log=self.log.emit,
            cancelled=self._is_cancelled,
        )

    def _run_clue_recovery(self) -> PlateSolution:
        """Recover an internet image using a sky clue plus a precision scale sweep.

        ASTAP documents that a supplied FOV should preferably be within ~5% of
        the true image height.  Its Auto sweep uses much coarser scale steps,
        which is excellent for ordinary acquisition frames but can miss heavily
        processed internet images.  With a known object in the field we can keep
        the positional search compact and try a denser set of explicit scales.
        """
        if self.target_ra_hours is None or self.target_dec_deg is None:
            return self._run_astap(timeout_seconds=45.0)

        self.progress.emit("ASTAP: using your clue to test plausible image scales…")
        self.log.emit("ASTAP: precision clue recovery (position + dense scale sweep)")

        aspect = (self.image_width_px / self.image_height_px) if self.image_height_px else 1.0

        # ASTAP wants image HEIGHT.  Sweep 0.30–10 degrees in ~9% steps,
        # tight enough to satisfy its documented preference for a near-correct FOV.
        heights: list[float] = []
        value = 0.30
        while value <= 10.0:
            heights.append(value)
            value *= 1.09

        errors: list[str] = []
        total = len(heights)
        for index, height_deg in enumerate(heights, start=1):
            if self._is_cancelled():
                raise PlateSolveCancelled("Plate solve cancelled.")
            width_deg = height_deg * aspect
            # A clue may sit at the edge of the photograph.  Half the diagonal
            # is the physically relevant centre offset; add margin, but avoid
            # the old 30-degree spiral on every scale hypothesis.
            half_diag = 0.5 * (height_deg ** 2 + width_deg ** 2) ** 0.5
            radius = min(12.0, max(1.5, half_diag * 1.35))
            self.progress.emit(
                f"ASTAP clue recovery: scale {index}/{total} · "
                f"about {width_deg:.2f}° × {height_deg:.2f}°"
            )
            self.log.emit(
                f"ASTAP clue scale {index}/{total}: "
                f"width {width_deg:.3f}°, height {height_deg:.3f}°, radius {radius:.2f}°"
            )
            try:
                return self._run_astap(
                    timeout_seconds=4.0,
                    estimated_width_override=width_deg,
                    search_radius_override=radius,
                    max_stars=500,
                )
            except PlateSolveCancelled:
                raise
            except Exception as exc:
                errors.append(str(exc))

        # Processed wide-field images can retain many stars but distort quad
        # geometry enough that ASTAP's normal overlap misses.  One final pass
        # uses ASTAP's documented slow-search mode and a slightly looser quad
        # tolerance, still anchored to the clue.
        self.progress.emit("ASTAP: trying a distortion-tolerant recovery pass…")
        self.log.emit("ASTAP: final clue recovery pass · Auto FOV · slow overlap · tolerance 0.010")
        try:
            return self._run_astap(
                timeout_seconds=45.0,
                search_radius_override=12.0,
                speed_mode="slow",
                max_stars=500,
                quad_tolerance=0.010,
            )
        except PlateSolveCancelled:
            raise
        except Exception as exc:
            errors.append(str(exc))

        detail = errors[-1] if errors else "ASTAP did not find a solution."
        raise RuntimeError("CLUE_SOLVE_EXHAUSTED::" + detail)

    def _run_online(self) -> PlateSolution:
        if not self.api_key and self.astrometry_job_reference is None:
            raise RuntimeError(
                "No Astrometry.net API key is configured. Open Plate Solving "
                "settings and enter the key before using Online only."
            )
        remote = AstrometryNetClient(self.api_key, log=self.log.emit)
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
                if self.target_ra_hours is not None and self.target_dec_deg is not None:
                    solution = self._run_clue_recovery()
                else:
                    solution = self._run_astap()
                self.succeeded.emit(solution)
                return

            # Automatic mode is deliberately local-first and local-only.
            # A slow public web service must never take control of the UI just
            # because the fast local blind solve failed.  The GUI offers the
            # user a clue-assisted ASTAP retry or an explicit Astrometry.net
            # fallback after these local attempts are exhausted.
            if self.target_ra_hours is not None and self.target_dec_deg is not None:
                solution = self._run_clue_recovery()
                self.succeeded.emit(solution)
                return

            local_errors: list[str] = []
            try:
                self.log.emit("ASTAP: blind attempt (Field = Auto, up to 20 seconds, 300 stars)")
                solution = self._run_astap(timeout_seconds=20.0, max_stars=300)
                self.succeeded.emit(solution)
                return
            except PlateSolveCancelled:
                raise
            except Exception as exc:
                local_errors.append(str(exc))
                self.log.emit(f"ASTAP fast blind attempt: {exc}")

            if self._is_cancelled():
                raise PlateSolveCancelled("Plate solve cancelled.")

            if self.estimated_width_deg > 0:
                try:
                    self.progress.emit("ASTAP: retrying with the estimated image scale…")
                    self.log.emit("ASTAP: scale-guided blind retry (20 seconds)")
                    solution = self._run_astap(
                        timeout_seconds=20.0, use_scale_hint=True
                    )
                    self.succeeded.emit(solution)
                    return
                except PlateSolveCancelled:
                    raise
                except Exception as exc:
                    local_errors.append(str(exc))
                    self.log.emit(f"ASTAP scale-guided retry: {exc}")

            detail = local_errors[-1] if local_errors else "ASTAP did not find a solution."
            raise RuntimeError(
                "LOCAL_SOLVE_EXHAUSTED::"
                + detail
            )

        except PlateSolveCancelled:
            self.log.emit("Plate solve cancelled by user")
            self.cancelled.emit()
        except Exception as exc:
            self.log.emit(f"Solve failed: {exc}")
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()
