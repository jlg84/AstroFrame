from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
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
NOVA_ROOT = "https://nova.astrometry.net"

SOLVE_CACHE_SCHEMA_VERSION = 3
WCS_CACHE_MODEL_VERSION = 3


class PlateSolveError(RuntimeError):
    """Raised when a solver cannot produce a usable solution."""


class PlateSolveCancelled(PlateSolveError):
    """Raised when the user cancels an active plate solve."""


@dataclass(frozen=True)
class PlateSolution:
    ra_deg: float
    dec_deg: float
    pixel_scale_arcsec: float
    orientation_deg: float | None
    parity: float
    radius_deg: float
    image_width_deg: float
    image_height_deg: float
    solver: str = "Astrometry.net"
    job_id: int | None = None
    solve_mode: str = "Blind"
    solve_seconds: float | None = None
    orientation_known: bool | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "PlateSolution":
        return cls(**data)


class SolveCache:
    """Content-addressed plate-solution cache.

    Entries are keyed by SHA-256 of the exact image bytes, never by file path.
    The JSON record also carries optional target/provenance metadata so a
    restored solution can behave like a freshly solved reference.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or (
            Path.home()
            / "Library"
            / "Application Support"
            / "AstroFrame"
            / "solve_cache"
        )
        self.root.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_records()

    @staticmethod
    def image_hash(path: str | Path) -> str:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _path_for(self, image_path: str | Path) -> Path:
        return self.root / f"{self.image_hash(image_path)}.json"

    def load_record(
        self,
        image_path: str | Path,
        *,
        expected_size: tuple[int, int] | None = None,
    ) -> dict[str, Any] | None:
        """Load a cached solution, including safe legacy records.

        RC22q was deliberately conservative and rejected every pre-RC22q cache
        record.  RC22r restores those solutions when they are content-addressed
        to the *exact same image* and their basic geometry is sane.  Accepted
        legacy records are upgraded in place to the current cache model so the
        user does not have to plate-solve an established image library again.
        """
        cache_path = self._path_for(image_path)
        if not cache_path.exists():
            return None
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or "solution" not in data:
                return None

            current_hash = self.image_hash(image_path)
            stored_hash = data.get("image_sha256")
            # A modern record must match its content fingerprint. Older records
            # may not have stored the fingerprint internally, but because this
            # file was reached through _path_for(), its filename itself is the
            # SHA-256 of the exact current image.
            if stored_hash and stored_hash != current_hash:
                return None
            if cache_path.stem != current_hash:
                return None

            if expected_size is not None:
                cached_size = data.get("image_size_px")
                if cached_size is not None:
                    try:
                        cached_tuple = tuple(int(v) for v in cached_size)
                    except (TypeError, ValueError):
                        return None
                    if cached_tuple != tuple(int(v) for v in expected_size):
                        return None

            solution = data.get("solution")
            if not isinstance(solution, dict):
                return None
            try:
                ra = float(solution["ra_deg"])
                dec = float(solution["dec_deg"])
                scale = float(solution["pixel_scale_arcsec"])
                parity = float(solution["parity"])
                width = float(solution["image_width_deg"])
                height = float(solution["image_height_deg"])
            except (KeyError, TypeError, ValueError):
                return None
            if not (
                math.isfinite(ra) and math.isfinite(dec) and math.isfinite(scale)
                and math.isfinite(parity) and math.isfinite(width) and math.isfinite(height)
                and -90.0 <= dec <= 90.0 and scale > 0.0
                and width > 0.0 and height > 0.0 and abs(parity) > 0.0
            ):
                return None
            orientation = solution.get("orientation_deg")
            if orientation is not None:
                try:
                    if not math.isfinite(float(orientation)):
                        return None
                except (TypeError, ValueError):
                    return None

            legacy = (
                data.get("schema_version") != SOLVE_CACHE_SCHEMA_VERSION
                or data.get("wcs_cache_model_version") != WCS_CACHE_MODEL_VERSION
                or data.get("image_sha256") != current_hash
                or (expected_size is not None and data.get("image_size_px") is None)
            )
            if legacy:
                data["schema_version"] = SOLVE_CACHE_SCHEMA_VERSION
                data["wcs_cache_model_version"] = WCS_CACHE_MODEL_VERSION
                data["image_sha256"] = current_hash
                if expected_size is not None:
                    data["image_size_px"] = [int(expected_size[0]), int(expected_size[1])]
                data["cache_migrated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                try:
                    cache_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
                except OSError:
                    pass
            return data
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def load(
        self,
        image_path: str | Path,
        *,
        expected_size: tuple[int, int] | None = None,
    ) -> PlateSolution | None:
        record = self.load_record(image_path, expected_size=expected_size)
        if record is None:
            return None
        try:
            return PlateSolution.from_json(record["solution"])
        except (KeyError, TypeError, ValueError):
            return None

    def save(
        self,
        image_path: str | Path,
        solution: PlateSolution,
        *,
        target: dict[str, Any] | None = None,
        source_url: str | None = None,
        image_size_px: tuple[int, int] | None = None,
    ) -> None:
        cache_path = self._path_for(image_path)
        previous = self.load_record(image_path, expected_size=image_size_px) or {}
        payload = {
            "schema_version": SOLVE_CACHE_SCHEMA_VERSION,
            "wcs_cache_model_version": WCS_CACHE_MODEL_VERSION,
            "image_sha256": self.image_hash(image_path),
            "image_name": Path(image_path).name,
            "image_size_px": list(image_size_px) if image_size_px is not None else None,
            "saved_at": previous.get("saved_at") or time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "solution": asdict(solution),
        }
        if target is not None:
            payload["target"] = target
        elif previous.get("target"):
            payload["target"] = previous["target"]
        if source_url:
            payload["source_url"] = source_url
        elif previous.get("source_url"):
            payload["source_url"] = previous["source_url"]
        cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def update_metadata(
        self,
        image_path: str | Path,
        *,
        target: dict[str, Any] | None = None,
        source_url: str | None = None,
    ) -> None:
        record = self.load_record(image_path)
        if record is None:
            return
        if target is not None:
            record["target"] = target
        if source_url:
            record["source_url"] = source_url
        record["schema_version"] = SOLVE_CACHE_SCHEMA_VERSION
        record["wcs_cache_model_version"] = WCS_CACHE_MODEL_VERSION
        record["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self._path_for(image_path).write_text(json.dumps(record, indent=2), encoding="utf-8")

    def remove(self, image_path: str | Path) -> None:
        self._path_for(image_path).unlink(missing_ok=True)

    def _migrate_legacy_records(self) -> None:
        """Best-effort migration of old path-based cache records.

        Older development builds sometimes retained an image_path/source_path
        in a JSON record.  If the referenced file still exists, rewrite that
        record under the modern SHA-256 key. Ambiguous records are untouched.
        """
        for old_path in tuple(self.root.glob("*.json")):
            if re.fullmatch(r"[0-9a-f]{64}\.json", old_path.name):
                continue
            try:
                data = json.loads(old_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            candidate = data.get("image_path") or data.get("source_path") or data.get("path")
            if not candidate:
                continue
            image_path = Path(candidate).expanduser()
            if not image_path.is_file() or "solution" not in data:
                continue
            try:
                new_path = self._path_for(image_path)
            except OSError:
                continue
            if not new_path.exists():
                data["schema_version"] = 2
                data["image_sha256"] = self.image_hash(image_path)
                data["image_name"] = image_path.name
                data["migrated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                try:
                    new_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
                except OSError:
                    continue



class AstrometrySubmissionCache:
    """Remember Astrometry.net submissions by image content hash.

    This prevents AstroFrame from uploading the same image repeatedly when a
    prior submission is still pending or already solved, even if the normal
    solution cache has not yet been applied to the UI.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or (
            Path.home()
            / "Library"
            / "Application Support"
            / "AstroFrame"
            / "astrometry_submissions"
        )
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, image_path: str | Path) -> Path:
        digest = SolveCache.image_hash(image_path)
        return self.root / f"{digest}.json"

    def load(self, image_path: str | Path) -> dict[str, Any] | None:
        path = self._path_for(image_path)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def save(self, image_path: str | Path, **values: Any) -> None:
        path = self._path_for(image_path)
        current = self.load(image_path) or {}
        current.update(values)
        current["image_name"] = Path(image_path).name
        current["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        path.write_text(json.dumps(current, indent=2), encoding="utf-8")

    def remove(self, image_path: str | Path) -> None:
        self._path_for(image_path).unlink(missing_ok=True)

    def reserve_upload(self, image_path: str | Path) -> bool:
        """Atomically reserve the one permitted upload for this exact file.

        Returns False when any previous online attempt is already recorded.
        The record is deliberately retained even if the remote request fails,
        so an automatic retry can never create duplicate submissions.
        """
        path = self._path_for(image_path)
        payload = {
            "image_name": Path(image_path).name,
            "status": "reserved",
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        return True


def clean_astap_output(output: str) -> str:
    """Remove repetitive, non-actionable ASTAP implementation messages."""
    hidden_phrases = (
        "warning, bucket size increased!",
    )
    useful_lines: list[str] = []
    for line in output.splitlines():
        lowered = line.strip().lower()
        if any(phrase in lowered for phrase in hidden_phrases):
            continue
        useful_lines.append(line)
    return "\n".join(useful_lines).strip()

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
        """Read ASTAP's FITS-style .wcs header.

        With the ``-wcs`` switch ASTAP writes fixed 80-byte FITS cards with
        no line breaks.  ``Header.fromtextfile`` expects newline-delimited
        text, so it can reject a perfectly valid ASTAP solution.  Parse the
        cards explicitly and then hand them to Astropy as newline-separated
        FITS cards.  The fallbacks retain compatibility with older ASTAP text
        sidecars.
        """
        raw_bytes = path.read_bytes()

        # ASTAP's standards-compatible form: contiguous 80-byte cards.
        if len(raw_bytes) >= 80:
            cards: list[str] = []
            for offset in range(0, len(raw_bytes), 80):
                block = raw_bytes[offset : offset + 80]
                if len(block) < 80:
                    break
                card = block.decode("ascii", errors="replace")
                cards.append(card)
                if card.startswith("END"):
                    break
            if cards and any(card.startswith("END") for card in cards):
                return fits.Header.fromstring("\n".join(cards), sep="\n")

        # Older/default ASTAP output may be ordinary line-oriented text.
        try:
            return fits.Header.fromtextfile(path, endcard=False)
        except Exception:
            raw = raw_bytes.decode("ascii", errors="ignore")
            return fits.Header.fromstring(raw, sep="\n")

    @staticmethod
    def _read_astap_keywords(path: Path) -> dict[str, str]:
        """Read keyword values directly from an ASTAP .wcs sidecar.

        ASTAP can copy non-standard cards from the source image into the WCS
        header. Astropy may reject one of those unrelated cards even though all
        celestial WCS keywords are valid. This deliberately extracts only
        ordinary KEYWORD = VALUE cards and ignores comments/history.
        """
        raw = path.read_bytes()
        cards: list[str] = []

        # Standards-compatible form produced by ASTAP's -wcs option.
        if len(raw) >= 80 and b"\n" not in raw[:320]:
            for offset in range(0, len(raw), 80):
                block = raw[offset : offset + 80]
                if len(block) < 80:
                    break
                card = block.decode("ascii", errors="ignore")
                cards.append(card)
                if card[:8].strip() == "END":
                    break
        else:
            cards = raw.decode("ascii", errors="ignore").splitlines()

        values: dict[str, str] = {}
        for card in cards:
            if len(card) < 10 or card[8:10] != "= ":
                continue
            key = card[:8].strip().upper()
            value_text = card[10:]
            # FITS comments begin with /, except inside quoted strings.
            in_quote = False
            cut = len(value_text)
            for index, character in enumerate(value_text):
                if character == "'":
                    in_quote = not in_quote
                elif character == "/" and not in_quote:
                    cut = index
                    break
            value = value_text[:cut].strip()
            if value.startswith("'") and value.endswith("'"):
                value = value[1:-1].replace("''", "'")
            values[key] = value
        return values

    @staticmethod
    def _float_keyword(values: dict[str, str], key: str) -> float:
        raw = values.get(key)
        if raw is None:
            raise KeyError(key)
        # FITS occasionally uses Fortran D exponents.
        return float(raw.replace("D", "E").replace("d", "e"))

    @classmethod
    def _solution_from_astap_keywords(
        cls,
        path: Path,
        *,
        image_width_px: int,
        image_height_px: int,
    ) -> tuple[float, float, float, float, float, float, float]:
        values = cls._read_astap_keywords(path)

        ra_deg = cls._float_keyword(values, "CRVAL1") % 360.0
        dec_deg = cls._float_keyword(values, "CRVAL2")

        # ASTAP normally supplies a CD matrix. Support PC+CDELT as a fallback.
        try:
            cd11 = cls._float_keyword(values, "CD1_1")
            cd12 = cls._float_keyword(values, "CD1_2")
            cd21 = cls._float_keyword(values, "CD2_1")
            cd22 = cls._float_keyword(values, "CD2_2")
        except KeyError:
            cdelt1 = cls._float_keyword(values, "CDELT1")
            cdelt2 = cls._float_keyword(values, "CDELT2")
            pc11 = float(values.get("PC1_1", "1"))
            pc12 = float(values.get("PC1_2", "0"))
            pc21 = float(values.get("PC2_1", "0"))
            pc22 = float(values.get("PC2_2", "1"))
            cd11, cd12 = cdelt1 * pc11, cdelt1 * pc12
            cd21, cd22 = cdelt2 * pc21, cdelt2 * pc22

        scale_x_arcsec = math.hypot(cd11, cd21) * 3600.0
        scale_y_arcsec = math.hypot(cd12, cd22) * 3600.0
        pixel_scale = (scale_x_arcsec + scale_y_arcsec) / 2.0

        orientation = math.degrees(math.atan2(cd12, cd11))
        determinant = cd11 * cd22 - cd12 * cd21
        parity = 1.0 if determinant >= 0 else -1.0

        width_deg = scale_x_arcsec * image_width_px / 3600.0
        height_deg = scale_y_arcsec * image_height_px / 3600.0
        radius_deg = math.hypot(width_deg, height_deg) / 2.0

        return (
            ra_deg,
            dec_deg,
            pixel_scale,
            orientation,
            parity,
            width_deg,
            height_deg,
            radius_deg,
        )

    @staticmethod
    def _solution_from_astap_output(
        output: str,
        *,
        image_width_px: int,
        image_height_px: int,
    ) -> tuple[float, float, float, float, float, float, float, float]:
        """Recover a complete solution from ASTAP's successful console output.

        ASTAP prints both the solved centre and a linear pixel-to-sky transform.
        This is independent of the .wcs sidecar format and therefore provides a
        reliable fallback when a particular macOS ASTAP build writes a sidecar
        that Astropy or the direct FITS-card reader cannot interpret.
        """
        centre_match = re.search(
            r"Solution found:\s*"
            r"(\d{1,2})\s*:\s*(\d{1,2})\s+([0-9.]+)\s+"
            r"([+-]?\d{1,2})[°\s]+(\d{1,2})\s+([0-9.]+)",
            output,
            flags=re.IGNORECASE,
        )
        if not centre_match:
            raise ValueError("ASTAP output did not contain a solved centre.")

        hours = float(centre_match.group(1))
        minutes = float(centre_match.group(2))
        seconds = float(centre_match.group(3))
        ra_deg = (hours + minutes / 60.0 + seconds / 3600.0) * 15.0

        dec_degrees_text = centre_match.group(4)
        sign = -1.0 if dec_degrees_text.startswith("-") else 1.0
        dec_whole = abs(float(dec_degrees_text))
        dec_minutes = float(centre_match.group(5))
        dec_seconds = float(centre_match.group(6))
        dec_deg = sign * (
            dec_whole + dec_minutes / 60.0 + dec_seconds / 3600.0
        )

        transform_match = re.search(
            r'Solution\["\]\s*x:=\s*'
            r'([+-]?[0-9.eEdD]+)x\+\s*([+-]?[0-9.eEdD]+)y\+\s*'
            r'([+-]?[0-9.eEdD]+),\s*y:=\s*'
            r'([+-]?[0-9.eEdD]+)x\+\s*([+-]?[0-9.eEdD]+)y\+\s*'
            r'([+-]?[0-9.eEdD]+)',
            output,
        )
        if not transform_match:
            raise ValueError("ASTAP output did not contain its scale transform.")

        coefficients = [
            float(value.replace("D", "E").replace("d", "e"))
            for value in transform_match.groups()
        ]
        a, b, _c, d, e, _f = coefficients

        # ASTAP's printed transform coefficients are arcseconds per pixel.
        scale_x_arcsec = math.hypot(a, d)
        scale_y_arcsec = math.hypot(b, e)
        pixel_scale = (scale_x_arcsec + scale_y_arcsec) / 2.0
        orientation = math.degrees(math.atan2(b, a))
        determinant = a * e - b * d
        parity = 1.0 if determinant >= 0 else -1.0

        width_deg = scale_x_arcsec * image_width_px / 3600.0
        height_deg = scale_y_arcsec * image_height_px / 3600.0
        radius_deg = math.hypot(width_deg, height_deg) / 2.0

        return (
            ra_deg % 360.0,
            dec_deg,
            pixel_scale,
            orientation,
            parity,
            width_deg,
            height_deg,
            radius_deg,
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
        speed_mode: str | None = None,
        max_stars: int | None = None,
        quad_tolerance: float | None = None,
        progress: Callable[[str], None] | None = None,
        log: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
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

            estimated_height = None
            if estimated_width_deg and estimated_width_deg > 0:
                # ASTAP's -fov value is the approximate IMAGE HEIGHT, not width.
                # Keep the conversion explicit in the diagnostic log: this is
                # easy to mistake when comparing AstroFrame's width control with
                # ASTAP's own console output.
                estimated_height = (
                    estimated_width_deg * image_height_px / image_width_px
                )
                command.extend(["-fov", f"{estimated_height:.6f}"])
            else:
                command.extend(["-fov", "0"])

            if speed_mode in {"slow", "auto"}:
                command.extend(["-speed", speed_mode])
            if max_stars is not None and max_stars > 0:
                command.extend(["-s", str(int(max_stars))])
            if quad_tolerance is not None and quad_tolerance > 0:
                command.extend(["-t", f"{quad_tolerance:.4f}"])

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
                # dev16c diagnostic block: report every value that materially
                # changes ASTAP's search, without changing solver behaviour.
                aspect = (image_width_px / image_height_px) if image_height_px else 0.0
                log("ASTAP invocation diagnostics:")
                log(f"  image pixels: {image_width_px} x {image_height_px} (aspect {aspect:.4f})")
                if estimated_width_deg and estimated_width_deg > 0:
                    log(f"  AstroFrame estimated WIDTH: {estimated_width_deg:.6f} deg")
                    log(f"  ASTAP -fov IMAGE HEIGHT: {estimated_height:.6f} deg")
                else:
                    log("  AstroFrame estimated WIDTH: not supplied to ASTAP")
                    log("  ASTAP -fov: 0 (Auto)")
                if assisted:
                    log(f"  clue RA: {ra_hours:.8f} h ({ra_hours * 15.0:.6f} deg)")
                    log(f"  clue Dec: {dec_deg:.8f} deg")
                    log(f"  ASTAP -spd: {dec_deg + 90.0:.8f} deg")
                    log(f"  search radius: {search_radius_deg or 10.0:.3f} deg")
                else:
                    log("  positional clue: none")
                    log(f"  search radius: {search_radius_deg or 180.0:.3f} deg")
                log("  downsampling/binning: ASTAP automatic (AstroFrame does not set it)")
                if speed_mode:
                    log(f"  ASTAP search speed: {speed_mode}")
                if max_stars is not None:
                    log(f"  max stars: {max_stars}")
                if quad_tolerance is not None:
                    log(f"  quad tolerance: {quad_tolerance:.4f}")
                quoted = " ".join(f'"{part}"' if " " in part else part for part in command)
                log(f"  exact command: {quoted}")

            started = time.monotonic()
            process = subprocess.Popen(
                command,
                cwd=temp_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                while process.poll() is None:
                    if cancelled and cancelled():
                        process.terminate()
                        try:
                            process.wait(timeout=2.0)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=2.0)
                        raise PlateSolveCancelled("Plate solve cancelled.")
                    if time.monotonic() - started > self.timeout_seconds:
                        process.terminate()
                        try:
                            process.wait(timeout=2.0)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=2.0)
                        raise PlateSolveError(
                            f"ASTAP did not finish within {int(self.timeout_seconds)} seconds."
                        )
                    time.sleep(0.1)
                stdout, stderr = process.communicate()
                result = subprocess.CompletedProcess(
                    command, process.returncode, stdout, stderr
                )
            finally:
                if process.poll() is None:
                    process.kill()

            if log:
                log(f"ASTAP exit code: {result.returncode}")
                output = clean_astap_output(
                    (result.stdout or result.stderr or "").strip()
                )
                if output:
                    log("ASTAP output:\n" + output[-4000:])

            sidecars = (
                working.with_suffix(".wcs"),
                Path(str(working) + ".wcs"),
            )
            wcs_path = next((path for path in sidecars if path.exists()), None)

            if result.returncode != 0 or wcs_path is None:
                detail = clean_astap_output(
                    (result.stderr or result.stdout or "").strip()
                )
                if len(detail) > 600:
                    detail = detail[-600:]

                message = "ASTAP could not solve this image."
                if detail:
                    message += f"\n\nASTAP: {detail}"
                raise PlateSolveError(message)

            if progress:
                progress("Reading ASTAP solution…")

            direct_exc: Exception | None = None
            output_exc: Exception | None = None
            try:
                (
                    ra_deg,
                    dec_deg,
                    pixel_scale,
                    orientation,
                    parity,
                    width_deg,
                    height_deg,
                    radius_deg,
                ) = self._solution_from_astap_keywords(
                    wcs_path,
                    image_width_px=image_width_px,
                    image_height_px=image_height_px,
                )
            except Exception as exc:
                direct_exc = exc
                try:
                    (
                        ra_deg,
                        dec_deg,
                        pixel_scale,
                        orientation,
                        parity,
                        width_deg,
                        height_deg,
                        radius_deg,
                    ) = self._solution_from_astap_output(
                        result.stdout or result.stderr or "",
                        image_width_px=image_width_px,
                        image_height_px=image_height_px,
                    )
                    if log:
                        log("ASTAP solution read from successful console output.")
                except Exception as exc:
                    output_exc = exc
                    # Retain Astropy as a final parser for unusual but valid
                    # WCS representations.
                    try:
                        header = self._read_header(wcs_path)
                        wcs = WCS(header).celestial

                        centre_x = (image_width_px - 1) / 2.0
                        centre_y = (image_height_px - 1) / 2.0
                        ra_deg, dec_deg = wcs.pixel_to_world_values(
                            centre_x, centre_y
                        )

                        scales_deg = proj_plane_pixel_scales(wcs)
                        scale_x_arcsec = abs(float(scales_deg[0])) * 3600.0
                        scale_y_arcsec = abs(float(scales_deg[1])) * 3600.0
                        pixel_scale = (
                            scale_x_arcsec + scale_y_arcsec
                        ) / 2.0

                        matrix = wcs.pixel_scale_matrix
                        orientation = math.degrees(
                            math.atan2(
                                float(matrix[0, 1]),
                                float(matrix[0, 0]),
                            )
                        )
                        determinant = float(
                            matrix[0, 0] * matrix[1, 1]
                            - matrix[0, 1] * matrix[1, 0]
                        )
                        parity = 1.0 if determinant >= 0 else -1.0
                        width_deg = (
                            scale_x_arcsec * image_width_px / 3600.0
                        )
                        height_deg = (
                            scale_y_arcsec * image_height_px / 3600.0
                        )
                        radius_deg = math.hypot(
                            width_deg, height_deg
                        ) / 2.0
                    except Exception as astropy_exc:
                        if log:
                            log(
                                "ASTAP direct WCS parser error: "
                                f"{direct_exc!r}"
                            )
                            log(
                                "ASTAP console parser error: "
                                f"{output_exc!r}"
                            )
                            log(
                                "ASTAP Astropy parser error: "
                                f"{astropy_exc!r}"
                            )
                            values = self._read_astap_keywords(wcs_path)
                            available = ", ".join(sorted(values))
                            log("ASTAP WCS keywords found: " + available)
                        raise PlateSolveError(
                            "ASTAP produced a solution, but AstroFrame could "
                            "not read its solution data."
                        ) from astropy_exc

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
                solve_seconds=time.monotonic() - started,
            )


def _elapsed_label(started: float) -> str:
    seconds = max(0, int(time.monotonic() - started))
    minutes, seconds = divmod(seconds, 60)
    if minutes:
        return f"{minutes} min {seconds:02d} sec"
    return f"{seconds} sec"


class AstrometryNetClient:
    """Resilient client for the public nova.astrometry.net service.

    Network requests are deliberately short and retryable.  The much longer
    submission/job deadlines are handled separately so a transient timeout
    does not discard an already-accepted Astrometry.net submission.
    """

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 15.0,
        session: requests.Session | None = None,
        log: Callable[[str], None] | None = None,
        request_attempts: int = 5,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.timeout_seconds = timeout_seconds
        self.http = session or requests.Session()
        self.session_id: str | None = None
        self.submission_cache = AstrometrySubmissionCache()
        self.log = log
        self.request_attempts = max(1, int(request_attempts))

    def _say(self, message: str) -> None:
        if self.log:
            self.log(message)

    @staticmethod
    def _rewind_files(files: dict[str, Any] | None) -> None:
        if not files:
            return
        for value in files.values():
            # requests accepts tuples such as (filename, fileobj, mimetype).
            fileobj = value[1] if isinstance(value, tuple) and len(value) > 1 else value
            seek = getattr(fileobj, "seek", None)
            if seek:
                try:
                    seek(0)
                except Exception:
                    pass

    def _retry_delay(self, attempt: int) -> float:
        # 1, 2, 4, 8 seconds; deliberately modest because this is a UI app.
        return min(8.0, float(2 ** max(0, attempt - 1)))

    def _request_json(
        self,
        method: str,
        endpoint: str,
        *,
        payload: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{API_ROOT}/{endpoint.lstrip('/')}"
        last_exc: Exception | None = None
        for attempt in range(1, self.request_attempts + 1):
            try:
                if files:
                    self._rewind_files(files)
                if method == "POST":
                    response = self.http.post(
                        url,
                        data={"request-json": json.dumps(payload or {})},
                        files=files,
                        timeout=self.timeout_seconds,
                    )
                else:
                    response = self.http.get(url, timeout=self.timeout_seconds)

                # Retry server congestion/rate limiting, but not ordinary 4xx
                # client errors such as a genuinely invalid API request.
                if response.status_code == 429 or response.status_code >= 500:
                    raise requests.HTTPError(
                        f"HTTP {response.status_code}", response=response
                    )
                response.raise_for_status()
                result = response.json()
                if result.get("status") == "error":
                    raise PlateSolveError(
                        result.get("errormessage", "Astrometry.net returned an error.")
                    )
                if attempt > 1:
                    self._say(
                        f"Astrometry.net: network request recovered on attempt {attempt}."
                    )
                return result
            except PlateSolveError:
                raise
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError, ValueError) as exc:
                last_exc = exc
                retriable_http = not isinstance(exc, requests.HTTPError) or (
                    getattr(getattr(exc, "response", None), "status_code", 500) == 429
                    or getattr(getattr(exc, "response", None), "status_code", 500) >= 500
                )
                if not retriable_http or attempt >= self.request_attempts:
                    break
                delay = self._retry_delay(attempt)
                self._say(
                    f"Astrometry.net: temporary network problem ({type(exc).__name__}); "
                    f"retrying in {int(delay)} sec ({attempt}/{self.request_attempts})."
                )
                time.sleep(delay)

        raise PlateSolveError(
            "Astrometry.net could not be reached reliably after several attempts: "
            + str(last_exc or "network error")
        )

    def _post(
        self,
        endpoint: str,
        payload: dict[str, Any],
        *,
        files: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._request_json("POST", endpoint, payload=payload, files=files)

    def _get(self, endpoint: str) -> dict[str, Any]:
        return self._request_json("GET", endpoint)

    def login(self) -> None:
        if not self.api_key:
            raise PlateSolveError(
                "An Astrometry.net API key is required to upload an image."
            )
        self._say("Astrometry.net: logging in…")
        result = self._post("login", {"apikey": self.api_key})
        session_id = result.get("session")
        if not session_id:
            raise PlateSolveError("Astrometry.net did not return a login session.")
        self.session_id = str(session_id)
        self._say("Astrometry.net: login accepted.")

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
            # Finished internet images can contain small/dense stars that are
            # useful to Astrometry.net even when ASTAP cannot use them.  Do not
            # throw half the image information away on this independent fallback.
            "downsample_factor": 1,
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
            self._say(
                f"Astrometry.net: submitting with broad scale range "
                f"{payload['scale_lower']:.3f}°–{payload['scale_upper']:.3f}°."
            )
        else:
            self._say("Astrometry.net: submitting as a true blind solve (no scale constraint).")

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
        self._say(f"Astrometry.net: upload accepted as submission #{int(subid)}.")
        return int(subid)

    def wait_for_job(
        self,
        submission_id: int,
        *,
        progress: Callable[[str], None] | None = None,
        poll_seconds: float = 4.0,
        timeout_seconds: float = 600.0,
        cancelled: Callable[[], bool] | None = None,
    ) -> int:
        started = time.monotonic()
        last_report_bucket = -1
        while time.monotonic() - started < timeout_seconds:
            if cancelled and cancelled():
                raise PlateSolveCancelled("Plate solve cancelled.")
            submission = self._get(f"submissions/{submission_id}")
            jobs = [job for job in submission.get("jobs", []) if job is not None]
            if jobs:
                job_id = int(jobs[0])
                self._say(
                    f"Astrometry.net: submission #{submission_id} assigned job #{job_id}."
                )
                return job_id
            elapsed = time.monotonic() - started
            bucket = int(elapsed // 15)
            if progress and bucket != last_report_bucket:
                progress(
                    f"Astrometry.net queued • {_elapsed_label(started)} • "
                    f"submission #{submission_id}"
                )
                last_report_bucket = bucket
            waited = 0.0
            while waited < poll_seconds:
                if cancelled and cancelled():
                    raise PlateSolveCancelled("Plate solve cancelled.")
                delay = min(0.1, poll_seconds - waited)
                time.sleep(delay)
                waited += delay
        raise PlateSolveError(
            f"Astrometry.net submission #{submission_id} is still queued after 10 minutes. "
            "The submission is preserved and can be resumed later."
        )

    def wait_for_solution(
        self,
        job_id: int,
        *,
        progress: Callable[[str], None] | None = None,
        poll_seconds: float = 4.0,
        timeout_seconds: float = 600.0,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        last_report_bucket = -1
        while time.monotonic() - started < timeout_seconds:
            if cancelled and cancelled():
                raise PlateSolveCancelled("Plate solve cancelled.")
            status = self._get(f"jobs/{job_id}").get("status")
            if status == "success":
                self._say(f"Astrometry.net: job #{job_id} solved successfully.")
                return self._get(f"jobs/{job_id}/calibration/")
            if status == "failure":
                raise PlateSolveError(f"Astrometry.net job #{job_id} could not solve this image.")
            elapsed = time.monotonic() - started
            bucket = int(elapsed // 15)
            if progress and bucket != last_report_bucket:
                progress(
                    f"Astrometry.net solving • {_elapsed_label(started)} • job #{job_id}"
                )
                last_report_bucket = bucket
            waited = 0.0
            while waited < poll_seconds:
                if cancelled and cancelled():
                    raise PlateSolveCancelled("Plate solve cancelled.")
                delay = min(0.1, poll_seconds - waited)
                time.sleep(delay)
                waited += delay
        raise PlateSolveError(
            f"Astrometry.net job #{job_id} is still running after 10 minutes. "
            "The job is preserved and can be resumed later."
        )

    def resolve_job_reference(
        self,
        reference: str | int,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> int:
        """Resolve a job number from a bare ID or common nova.astrometry.net URL.

        ``/user_images/<id>`` URLs contain a *user-image* identifier, not a
        job identifier.  For those, fetch the public results page and extract
        the associated job link.  This keeps the UI simple: users can paste
        the URL they actually have in the browser.
        """
        text = str(reference).strip()
        if not text:
            raise PlateSolveError("Enter an Astrometry.net job number or URL.")

        direct_patterns = (
            r"^\s*(\d+)\s*$",
            r"/jobs?/(\d+)(?:/|$|[?#])",
            r"/status/(\d+)(?:/|$|[?#])",
            r"[?&]job(?:_id)?=(\d+)",
        )
        for pattern in direct_patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                job_id = int(match.group(1))
                if job_id > 0:
                    return job_id

        user_image = re.search(
            r"/user_images/(\d+)(?:/|$|[?#])",
            text,
            flags=re.IGNORECASE,
        )
        if not user_image:
            raise PlateSolveError(
                "I couldn't recognise that Astrometry.net reference. "
                "Paste a job number, a job/status URL, or a user_images URL."
            )

        user_image_id = int(user_image.group(1))
        if progress:
            progress(
                f"Astrometry.net: finding the job for user image "
                f"#{user_image_id}…"
            )
        response = self.http.get(
            f"{NOVA_ROOT}/user_images/{user_image_id}",
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        html = response.text

        # Nova's result page links to several job-specific resources.  Prefer
        # an explicit status/job link, then fall back to well-known result
        # products whose numeric component is also the job ID.
        page_patterns = (
            r'href=["\'](?:https?://nova\.astrometry\.net)?/status/(\d+)',
            r'href=["\'](?:https?://nova\.astrometry\.net)?/jobs?/(\d+)',
            r'(?:annotated_display|wcs_file|new_fits_file)/(\d+)',
        )
        for pattern in page_patterns:
            match = re.search(pattern, html, flags=re.IGNORECASE)
            if match:
                job_id = int(match.group(1))
                if job_id > 0:
                    if progress:
                        progress(
                            f"Astrometry.net user image #{user_image_id} "
                            f"uses job #{job_id}."
                        )
                    return job_id

        raise PlateSolveError(
            f"Astrometry.net user image #{user_image_id} did not expose a "
            "completed job. It may still be solving, may have failed, or the "
            "image may not be public."
        )

    def _solution_from_wcs_job(
        self,
        job_id: int,
        *,
        image_width_px: int,
        image_height_px: int,
        solve_mode: str,
        solve_seconds: float | None = None,
    ) -> PlateSolution:
        """Build a PlateSolution from Astrometry.net's actual WCS product."""
        url = f"{NOVA_ROOT}/wcs_file/{int(job_id)}"
        self._say(f"Astrometry.net: downloading WCS for job #{int(job_id)}.")

        response = self.http.get(url, timeout=self.timeout_seconds)
        response.raise_for_status()

        with tempfile.NamedTemporaryFile(suffix=".wcs", delete=False) as handle:
            handle.write(response.content)
            wcs_path = Path(handle.name)

        try:
            # Astrometry.net's wcs_file is a FITS file, not ASTAP's text
            # sidecar.  Read the primary header directly.
            with fits.open(wcs_path) as hdul:
                header = hdul[0].header.copy()

            wcs = WCS(header).celestial

            centre_x = (max(image_width_px, 1) - 1) / 2.0
            centre_y = (max(image_height_px, 1) - 1) / 2.0
            ra_deg, dec_deg = wcs.pixel_to_world_values(centre_x, centre_y)

            scales_deg = proj_plane_pixel_scales(wcs)
            scale_x_arcsec = float(scales_deg[0]) * 3600.0
            scale_y_arcsec = float(scales_deg[1]) * 3600.0
            pixel_scale = (scale_x_arcsec + scale_y_arcsec) / 2.0

            matrix = wcs.pixel_scale_matrix
            cd11 = float(matrix[0, 0])
            cd12 = float(matrix[0, 1])
            cd21 = float(matrix[1, 0])
            cd22 = float(matrix[1, 1])

            # Astropy/WCS expresses the transform in FITS pixel coordinates,
            # whose image Y handedness is opposite to AstroFrame's internal
            # sky-plane convention.  Normalise here so every PlateSolution uses
            # the same orientation/parity convention regardless of solver.
            orientation = -math.degrees(math.atan2(cd12, cd11))
            determinant = cd11 * cd22 - cd12 * cd21
            parity = -1.0 if determinant >= 0 else 1.0

            width_deg = scale_x_arcsec * max(image_width_px, 1) / 3600.0
            height_deg = scale_y_arcsec * max(image_height_px, 1) / 3600.0
            radius_deg = math.hypot(width_deg, height_deg) / 2.0

            self._say(
                f"Astrometry.net WCS: centre {float(ra_deg):.8f}, "
                f"{float(dec_deg):.8f}; scale {pixel_scale:.6f} arcsec/px; "
                f"orientation {orientation:.6f} deg; parity {parity:+.0f}."
            )

            return PlateSolution(
                ra_deg=float(ra_deg) % 360.0,
                dec_deg=float(dec_deg),
                pixel_scale_arcsec=pixel_scale,
                orientation_deg=orientation,
                parity=parity,
                radius_deg=radius_deg,
                image_width_deg=width_deg,
                image_height_deg=height_deg,
                solver="Astrometry.net",
                job_id=int(job_id),
                solve_mode=solve_mode,
                solve_seconds=solve_seconds,
            )
        finally:
            try:
                wcs_path.unlink()
            except OSError:
                pass

    def solution_from_job(
        self,
        job_id: int,
        *,
        image_width_px: int,
        image_height_px: int,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> PlateSolution:
        """Retrieve a public Astrometry.net job without re-uploading."""
        job_id = int(job_id)
        if job_id <= 0:
            raise PlateSolveError(
                "Astrometry.net job number must be greater than zero."
            )
        if cancelled and cancelled():
            raise PlateSolveCancelled("Plate solve cancelled.")
        if progress:
            progress(f"Checking Astrometry.net job #{job_id}…")

        status = self._get(f"jobs/{job_id}").get("status")
        if status == "success":
            calibration = self._get(f"jobs/{job_id}/calibration/")
        elif status == "failure":
            raise PlateSolveError(
                f"Astrometry.net job #{job_id} finished without a solution."
            )
        else:
            calibration = self.wait_for_solution(
                job_id, progress=progress, cancelled=cancelled
            )

        try:
            return self._solution_from_wcs_job(
                job_id,
                image_width_px=image_width_px,
                image_height_px=image_height_px,
                solve_mode="Online existing job",
            )
        except Exception as exc:
            self._say(
                f"Astrometry.net WCS could not be used for job #{job_id}; "
                f"falling back to calibration summary: {exc}"
            )

        published_pixel_scale = float(calibration["pixscale"])
        published_radius = float(calibration.get("radius", 0.0) or 0.0)
        if published_radius > 0:
            # Existing-job imports are often displayed/downloaded at a smaller
            # pixel size than the file Astrometry.net originally solved.  The
            # published radius is invariant under resizing, so recover the
            # angular width/height from that radius and the displayed aspect
            # ratio instead of multiplying the original pixscale by the new
            # pixel dimensions.
            diagonal_px = math.hypot(max(image_width_px, 1), max(image_height_px, 1))
            width_deg = 2.0 * published_radius * max(image_width_px, 1) / diagonal_px
            height_deg = 2.0 * published_radius * max(image_height_px, 1) / diagonal_px
            pixel_scale = (
                (width_deg * 3600.0 / max(image_width_px, 1))
                + (height_deg * 3600.0 / max(image_height_px, 1))
            ) / 2.0
        else:
            pixel_scale = published_pixel_scale
            width_deg = pixel_scale * image_width_px / 3600.0
            height_deg = pixel_scale * image_height_px / 3600.0
        return PlateSolution(
            ra_deg=float(calibration["ra"]),
            dec_deg=float(calibration["dec"]),
            pixel_scale_arcsec=pixel_scale,
            orientation_deg=float(calibration["orientation"]),
            parity=float(calibration.get("parity", 0.0)),
            radius_deg=published_radius or math.hypot(width_deg, height_deg) / 2.0,
            image_width_deg=width_deg,
            image_height_deg=height_deg,
            job_id=job_id,
            solve_mode="Online existing job",
            solver="Astrometry.net",
        )

    def solve(
        self,
        image_path: str | Path,
        *,
        image_width_px: int,
        image_height_px: int,
        estimated_width_deg: float | None = None,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> PlateSolution:
        started = time.monotonic()
        if cancelled and cancelled():
            raise PlateSolveCancelled("Plate solve cancelled.")
        if progress:
            progress("Connecting to Astrometry.net…")
        self.login()
        if cancelled and cancelled():
            raise PlateSolveCancelled("Plate solve cancelled.")
        prior = self.submission_cache.load(image_path)
        submission_id: int | None = None
        job_id: int | None = None

        if prior:
            stored_job = prior.get("job_id")
            stored_submission = prior.get("submission_id")
            stored_status = str(prior.get("status", "")).lower()

            # A confirmed failed job is historical information, not a ban on
            # trying again. A new click on Plate Solve is explicit user intent.
            if stored_job is not None:
                job_id = int(stored_job)
                if progress:
                    progress(f"Checking previous Astrometry.net job #{job_id}…")
                status = self._get(f"jobs/{job_id}").get("status")
                if status == "success":
                    calibration = self._get(f"jobs/{job_id}/calibration/")
                elif status == "failure":
                    self.submission_cache.save(
                        image_path, job_id=job_id, status="failure"
                    )
                    raise PlateSolveError(
                        f"Previous Astrometry.net job #{job_id} failed. "
                        "AstroFrame has not uploaded this image again. "
                        "Press Plate Solve again if you explicitly want a fresh upload."
                    )
                else:
                    calibration = self.wait_for_solution(
                        job_id, progress=progress, cancelled=cancelled
                    )
            elif stored_submission is not None:
                submission_id = int(stored_submission)
                if progress:
                    progress(f"Checking previous Astrometry.net submission #{submission_id}…")

                # A cached submission may have acquired a failed job after
                # AstroFrame last looked at it.  Do not make the user click
                # Plate Solve twice: inspect the job and, when it is a
                # confirmed failure, discard the stale cache and continue
                # straight on to a fresh upload in this same solve request.
                job_id = self.wait_for_job(
                    submission_id, progress=progress, cancelled=cancelled
                )
                status = self._get(f"jobs/{job_id}").get("status")
                if status == "failure":
                    self.submission_cache.save(
                        image_path,
                        submission_id=submission_id,
                        job_id=job_id,
                        status="failure",
                    )
                    raise PlateSolveError(
                        f"Previous Astrometry.net job #{job_id} failed. "
                        "AstroFrame has not uploaded this image again. "
                        "Press Plate Solve again if you explicitly want a fresh upload."
                    )
                elif status == "success":
                    calibration = self._get(f"jobs/{job_id}/calibration/")
                    self.submission_cache.save(
                        image_path, job_id=job_id, status="success"
                    )
                else:
                    self.submission_cache.save(
                        image_path, job_id=job_id, status="solving"
                    )
                    calibration = self.wait_for_solution(
                        job_id, progress=progress, cancelled=cancelled
                    )
            elif stored_status in {"upload_error", "failure", "reserved"}:
                raise PlateSolveError(
                    "A previous Astrometry.net upload attempt for this exact image did not complete. "
                    "AstroFrame has not uploaded it again. Press Plate Solve again if you explicitly "
                    "want a fresh upload."
                )
            else:
                raise PlateSolveError(
                    "An Astrometry.net upload attempt is already recorded for "
                    "this exact image and may still be active."
                )

        if not prior:
            if not self.submission_cache.reserve_upload(image_path):
                raise PlateSolveError(
                    "An Astrometry.net upload attempt is already recorded for "
                    "this exact image. AstroFrame has not uploaded it again."
                )
            if progress:
                progress("Uploading reference image…")
            try:
                submission_id = self.upload(
                    image_path, estimated_width_deg=estimated_width_deg
                )
            except Exception as exc:
                self.submission_cache.save(
                    image_path, status="upload_error", error=str(exc)
                )
                raise
            self.submission_cache.save(
                image_path,
                submission_id=submission_id,
                status="submitted",
                estimated_width_deg=estimated_width_deg,
            )
            if progress:
                progress(
                    f"Astrometry.net accepted the image • submission #{submission_id}"
                )
            if cancelled and cancelled():
                raise PlateSolveCancelled("Plate solve cancelled.")
            job_id = self.wait_for_job(
                submission_id, progress=progress, cancelled=cancelled
            )
            self.submission_cache.save(
                image_path, job_id=job_id, status="solving"
            )
            calibration = self.wait_for_solution(
                job_id, progress=progress, cancelled=cancelled
            )

        self.submission_cache.save(
            image_path,
            submission_id=submission_id,
            job_id=job_id,
            status="success",
        )

        if job_id is not None:
            try:
                return self._solution_from_wcs_job(
                    job_id,
                    image_width_px=image_width_px,
                    image_height_px=image_height_px,
                    solve_mode="Online blind",
                    solve_seconds=time.monotonic() - started,
                )
            except Exception as exc:
                self._say(
                    f"Astrometry.net WCS could not be used for job #{job_id}; "
                    f"falling back to calibration summary: {exc}"
                )

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
                solve_seconds=time.monotonic() - started,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PlateSolveError("Astrometry.net returned an incomplete calibration.") from exc
