from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import shlex
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Any

import requests
from astropy.io import fits
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales


API_ROOT = "https://nova.astrometry.net/api"


class PlateSolveError(RuntimeError):
    """Raised when the remote solver cannot produce a usable solution."""


@dataclass(frozen=True)
class PlateSolution:
    ra_deg: float
    dec_deg: float
    pixel_scale_arcsec: float
    orientation_deg: float
    parity: float
    radius_deg: float
    image_width_deg: float
    image_height_deg: float
    solver: str = "Astrometry.net"
    job_id: int | None = None
    solve_mode: str = "Blind"
    solve_seconds: float | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "PlateSolution":
        return cls(**data)


class SolveCache:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or (
            Path.home()
            / "Library"
            / "Application Support"
            / "AstroFrame"
            / "solve_cache"
        )
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def image_hash(path: str | Path) -> str:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _path_for(self, image_path: str | Path) -> Path:
        return self.root / f"{self.image_hash(image_path)}.json"

    def load(self, image_path: str | Path) -> PlateSolution | None:
        cache_path = self._path_for(image_path)
        if not cache_path.exists():
            return None
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            return PlateSolution.from_json(data["solution"])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def save(self, image_path: str | Path, solution: PlateSolution) -> None:
        cache_path = self._path_for(image_path)
        payload = {
            "image_name": Path(image_path).name,
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "solution": asdict(solution),
        }
        cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def remove(self, image_path: str | Path) -> None:
        cache_path = self._path_for(image_path)
        cache_path.unlink(missing_ok=True)


class AstapClient:
    """Local ASTAP command-line plate solver."""

    DEFAULT_PATHS = (
        Path("/Applications/ASTAP.app/Contents/MacOS/astap"),
        Path("/Applications/ASTAP.app/Contents/MacOS/astap.app/Contents/MacOS/astap"),
    )

    def __init__(
        self,
        executable: str | Path | None = None,
        *,
        timeout_seconds: float = 180.0,
    ) -> None:
        self.executable = self.find_executable(executable)
        self.timeout_seconds = timeout_seconds
        if self.executable is None:
            raise PlateSolveError(
                "ASTAP was not found. Expected it in /Applications/ASTAP.app."
            )

    @classmethod
    def find_executable(
        cls, preferred: str | Path | None = None
    ) -> Path | None:
        candidates: list[Path] = []

        if preferred:
            candidates.append(Path(preferred).expanduser())

        command = shutil.which("astap")
        if command:
            candidates.append(Path(command))

        candidates.extend(cls.DEFAULT_PATHS)

        for candidate in candidates:
            try:
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return candidate.resolve()
            except OSError:
                continue

        return None

    @staticmethod
    def _read_header(path: Path) -> fits.Header:
        """Read ASTAP's WCS sidecar in any format used by its macOS builds."""
        errors: list[str] = []

        # Some ASTAP builds write a complete FITS file with a .wcs suffix.
        try:
            return fits.getheader(path, 0)
        except Exception as exc:
            errors.append(f"FITS reader: {exc}")

        # Other builds write one FITS card per text line.
        try:
            return fits.Header.fromtextfile(path, endcard=False)
        except Exception as exc:
            errors.append(f"text-card reader: {exc}")

        # A third form is a raw stream of contiguous 80-character FITS cards.
        try:
            raw = path.read_bytes().decode("ascii", errors="ignore")
            separator = "\n" if "\n" in raw else ""
            return fits.Header.fromstring(raw, sep=separator)
        except Exception as exc:
            errors.append(f"raw-card reader: {exc}")

        raise PlateSolveError(
            "Could not parse ASTAP WCS sidecar "
            f"'{path.name}'. " + " | ".join(errors)
        )

    def solve(
        self,
        image_path: str | Path,
        *,
        image_width_px: int,
        image_height_px: int,
        estimated_width_deg: float | None = None,
        ra_hours: float | None = None,
        dec_deg: float | None = None,
        search_radius_deg: float | None = None,
        use_auto_fov: bool = True,
        progress: Callable[[str], None] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> PlateSolution:
        source = Path(image_path)
        if not source.exists():
            raise PlateSolveError("The reference image no longer exists.")

        with tempfile.TemporaryDirectory(prefix="astroframe_astap_") as temp_dir:
            temp_root = Path(temp_dir)
            working = temp_root / source.name
            shutil.copy2(source, working)

            command = [
                str(self.executable), "-f", str(working),
                "-z", "0", "-wcs",
            ]

            if use_auto_fov:
                command.extend(["-fov", "0"])
                fov_mode = "Auto"
            elif estimated_width_deg and estimated_width_deg > 0:
                # ASTAP expects approximate image height, not width.
                estimated_height = (
                    estimated_width_deg * image_height_px / image_width_px
                )
                command.extend(["-fov", f"{estimated_height:.6f}"])
                fov_mode = f"Estimated height {estimated_height:.4f}°"
            else:
                command.extend(["-fov", "0"])
                fov_mode = "Auto"

            assisted = ra_hours is not None and dec_deg is not None
            if assisted:
                command.extend([
                    "-ra", f"{ra_hours:.8f}",
                    "-spd", f"{dec_deg + 90.0:.8f}",
                    "-r", f"{search_radius_deg or 10.0:.3f}",
                ])
                if progress:
                    progress("Target-assisted solve with ASTAP…")
            else:
                command.extend(["-r", f"{search_radius_deg or 180.0:.3f}"])
                if progress:
                    progress("Blind-solving locally with ASTAP…")

            if log:
                log(f"ASTAP executable: {self.executable}")
                log(f"Mode: {'Target-assisted' if assisted else 'Blind'}")
                log(f"Field setting: {fov_mode}")
                if assisted:
                    log(
                        f"Target hint: RA {ra_hours:.6f} h, "
                        f"Dec {dec_deg:+.6f}°, radius "
                        f"{search_radius_deg or 10.0:.1f}°"
                    )
                log("Command: " + " ".join(shlex.quote(part) for part in command))

            started = time.monotonic()
            try:
                result = subprocess.run(
                    command,
                    cwd=temp_root,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise PlateSolveError(
                    f"ASTAP did not finish within {int(self.timeout_seconds)} seconds."
                ) from exc

            elapsed = time.monotonic() - started
            if log:
                log(f"ASTAP exit code: {result.returncode}")
                output = (result.stdout or "").strip()
                error = (result.stderr or "").strip()
                if output:
                    log("ASTAP stdout:\n" + output)
                if error:
                    log("ASTAP stderr:\n" + error)
                log(f"ASTAP elapsed: {elapsed:.2f} s")

            created_files = sorted(
                path.relative_to(temp_root).as_posix()
                for path in temp_root.rglob("*")
                if path.is_file()
            )
            if log:
                log(
                    "ASTAP temporary files: "
                    + (", ".join(created_files) if created_files else "(none)")
                )

            sidecars = (
                working.with_suffix(".wcs"),
                Path(str(working) + ".wcs"),
            )
            wcs_path = next((path for path in sidecars if path.exists()), None)

            if result.returncode != 0 or wcs_path is None:
                detail = (result.stderr or result.stdout or "").strip()
                if len(detail) > 600:
                    detail = detail[-600:]

                message = "ASTAP could not solve this image."
                if detail:
                    message += f"\n\nASTAP: {detail}"
                raise PlateSolveError(message)

            if progress:
                progress("Reading ASTAP solution…")

            try:
                header = self._read_header(wcs_path)
                wcs = WCS(header).celestial

                centre_x = (image_width_px - 1) / 2.0
                centre_y = (image_height_px - 1) / 2.0
                ra_deg, dec_deg = wcs.pixel_to_world_values(centre_x, centre_y)

                scales_deg = proj_plane_pixel_scales(wcs)
                scale_x_arcsec = abs(float(scales_deg[0])) * 3600.0
                scale_y_arcsec = abs(float(scales_deg[1])) * 3600.0
                pixel_scale = (scale_x_arcsec + scale_y_arcsec) / 2.0

                matrix = wcs.pixel_scale_matrix
                orientation = math.degrees(
                    math.atan2(float(matrix[0, 1]), float(matrix[0, 0]))
                )

                determinant = float(
                    matrix[0, 0] * matrix[1, 1]
                    - matrix[0, 1] * matrix[1, 0]
                )
                parity = 1.0 if determinant >= 0 else -1.0

                width_deg = scale_x_arcsec * image_width_px / 3600.0
                height_deg = scale_y_arcsec * image_height_px / 3600.0
                radius_deg = math.hypot(width_deg, height_deg) / 2.0
            except Exception as exc:
                if log:
                    log(f"ASTAP WCS import error: {type(exc).__name__}: {exc}")
                raise PlateSolveError(
                    "ASTAP produced a solution, but AstroFrame could not read "
                    f"its WCS data: {exc}"
                ) from exc

            return PlateSolution(
                ra_deg=float(ra_deg) % 360.0,
                dec_deg=float(dec_deg),
                pixel_scale_arcsec=float(pixel_scale),
                orientation_deg=float(orientation),
                parity=parity,
                radius_deg=radius_deg,
                image_width_deg=width_deg,
                image_height_deg=height_deg,
                solver="ASTAP",
                job_id=None,
                solve_mode="Target-assisted" if assisted else "Blind",
                solve_seconds=elapsed,
            )


class AstrometryNetClient:
    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 90.0,
        session: requests.Session | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("An Astrometry.net API key is required.")
        self.api_key = api_key.strip()
        self.timeout_seconds = timeout_seconds
        self.http = session or requests.Session()
        self.session_id: str | None = None

    def _post(
        self,
        endpoint: str,
        payload: dict[str, Any],
        *,
        files: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self.http.post(
            f"{API_ROOT}/{endpoint.lstrip('/')}",
            data={"request-json": json.dumps(payload)},
            files=files,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        result = response.json()
        if result.get("status") == "error":
            raise PlateSolveError(result.get("errormessage", "Astrometry.net returned an error."))
        return result

    def _get(self, endpoint: str) -> dict[str, Any]:
        response = self.http.get(
            f"{API_ROOT}/{endpoint.lstrip('/')}",
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def login(self) -> None:
        result = self._post("login", {"apikey": self.api_key})
        session_id = result.get("session")
        if not session_id:
            raise PlateSolveError("Astrometry.net did not return a login session.")
        self.session_id = str(session_id)

    def upload(
        self,
        image_path: str | Path,
        *,
        estimated_width_deg: float | None = None,
    ) -> int:
        if not self.session_id:
            raise PlateSolveError("The Astrometry.net client is not logged in.")

        payload: dict[str, Any] = {
            "session": self.session_id,
            "publicly_visible": "n",
            "allow_modifications": "d",
            "allow_commercial_use": "d",
            "crpix_center": True,
            "downsample_factor": 2,
        }
        if estimated_width_deg and estimated_width_deg > 0:
            payload.update(
                {
                    "scale_units": "degwidth",
                    "scale_type": "ul",
                    "scale_lower": max(0.05, estimated_width_deg / 3.0),
                    "scale_upper": min(180.0, estimated_width_deg * 3.0),
                }
            )

        path = Path(image_path)
        with path.open("rb") as handle:
            result = self._post(
                "upload",
                payload,
                files={"file": (path.name, handle, "application/octet-stream")},
            )
        subid = result.get("subid")
        if subid is None:
            raise PlateSolveError("Astrometry.net did not return a submission ID.")
        return int(subid)

    def wait_for_job(
        self,
        submission_id: int,
        *,
        progress: Callable[[str], None] | None = None,
        poll_seconds: float = 3.0,
        timeout_seconds: float = 420.0,
    ) -> int:
        started = time.monotonic()
        while time.monotonic() - started < timeout_seconds:
            submission = self._get(f"submissions/{submission_id}")
            jobs = [job for job in submission.get("jobs", []) if job is not None]
            if jobs:
                return int(jobs[0])
            if progress:
                progress("Waiting for Astrometry.net to start the solve…")
            time.sleep(poll_seconds)
        raise PlateSolveError("Timed out while waiting for Astrometry.net to start the solve.")

    def wait_for_solution(
        self,
        job_id: int,
        *,
        progress: Callable[[str], None] | None = None,
        poll_seconds: float = 3.0,
        timeout_seconds: float = 420.0,
    ) -> dict[str, Any]:
        started = time.monotonic()
        while time.monotonic() - started < timeout_seconds:
            status = self._get(f"jobs/{job_id}").get("status")
            if status == "success":
                return self._get(f"jobs/{job_id}/calibration/")
            if status == "failure":
                raise PlateSolveError("Astrometry.net could not solve this image.")
            if progress:
                progress("Matching the star field…")
            time.sleep(poll_seconds)
        raise PlateSolveError("Timed out while waiting for the plate-solve result.")

    def solve(
        self,
        image_path: str | Path,
        *,
        image_width_px: int,
        image_height_px: int,
        estimated_width_deg: float | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> PlateSolution:
        started = time.monotonic()
        if progress:
            progress("Connecting to Astrometry.net…")
        self.login()
        if progress:
            progress("Uploading reference image…")
        submission_id = self.upload(
            image_path,
            estimated_width_deg=estimated_width_deg,
        )
        job_id = self.wait_for_job(submission_id, progress=progress)
        calibration = self.wait_for_solution(job_id, progress=progress)

        try:
            pixel_scale = float(calibration["pixscale"])
            width_deg = pixel_scale * image_width_px / 3600.0
            height_deg = pixel_scale * image_height_px / 3600.0
            return PlateSolution(
                ra_deg=float(calibration["ra"]),
                dec_deg=float(calibration["dec"]),
                pixel_scale_arcsec=pixel_scale,
                orientation_deg=float(calibration["orientation"]),
                parity=float(calibration.get("parity", 0.0)),
                radius_deg=float(calibration.get("radius", 0.0)),
                image_width_deg=width_deg,
                image_height_deg=height_deg,
                job_id=job_id,
                solve_mode="Online blind",
                solve_seconds=elapsed,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PlateSolveError("Astrometry.net returned an incomplete calibration.") from exc
