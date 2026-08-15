from __future__ import annotations

import hashlib
import html
import json
import math
import re
import time
from dataclasses import asdict
from pathlib import Path
from threading import Event
from urllib.parse import parse_qs, urlparse

import requests
from PIL import Image
from PySide6.QtCore import QObject, Signal, Slot

from .plate_solve import AstrometryNetClient, PlateSolution


class AstroBinImportError(RuntimeError):
    pass


def _angular_distance_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    value = (
        math.sin(d1) * math.sin(d2)
        + math.cos(d1) * math.cos(d2) * math.cos(r1 - r2)
    )
    return math.degrees(math.acos(max(-1.0, min(1.0, value))))


def _position_angle_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    y = math.sin(r2 - r1) * math.cos(d2)
    x = math.cos(d1) * math.sin(d2) - math.sin(d1) * math.cos(d2) * math.cos(r2 - r1)
    return math.degrees(math.atan2(y, x)) % 180.0


def _strip_tags(source: str) -> str:
    source = re.sub(r"(?is)<script\b.*?</script>", " ", source)
    source = re.sub(r"(?is)<style\b.*?</style>", " ", source)
    source = re.sub(r"(?s)<[^>]+>", " ", source)
    return " ".join(html.unescape(source).split())


def _hms_to_deg(hours: float, minutes: float, seconds: float) -> float:
    return (hours + minutes / 60.0 + seconds / 3600.0) * 15.0


def _dms_to_deg(sign: str, degrees: float, minutes: float, seconds: float) -> float:
    value = degrees + minutes / 60.0 + seconds / 3600.0
    return -value if sign == "-" else value


def astrobin_image_id(reference: str) -> str:
    value = reference.strip()
    if not value:
        raise AstroBinImportError("Paste an AstroBin image URL.")

    parsed = urlparse(value if "://" in value else "https://" + value)
    host = parsed.netloc.lower()
    if "astrobin.com" not in host:
        raise AstroBinImportError("That does not look like an AstroBin URL.")

    query = parse_qs(parsed.query)
    if query.get("i"):
        candidate = query["i"][0]
        if re.fullmatch(r"[A-Za-z0-9]+", candidate):
            return candidate

    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        raise AstroBinImportError("I couldn't find an AstroBin image ID in that URL.")

    # Common modern forms: /i/abc123 and /abc123/.  Ignore known routing
    # segments and select the first plausible image identifier.
    ignored = {"i", "u", "users", "image", "images", "full", "forum", "api"}
    for index, part in enumerate(parts):
        if part.lower() in ignored:
            if part.lower() in {"i", "image", "images"} and index + 1 < len(parts):
                candidate = parts[index + 1]
                if re.fullmatch(r"[A-Za-z0-9]+", candidate):
                    return candidate
            continue
        if re.fullmatch(r"[A-Za-z0-9]{4,16}", part):
            return part

    raise AstroBinImportError("I couldn't find an AstroBin image ID in that URL.")


class AstroBinImporter:
    """Import a public AstroBin image and reuse its published plate solution.

    The official AstroBin API requires credentials, and new API keys are not
    always available.  AstroFrame therefore uses only the public page the user
    explicitly pasted, making one user-initiated request for metadata and one
    for the displayed image.  No background scraping is performed.
    """

    def __init__(self, cache_root: Path | None = None) -> None:
        self.cache_root = cache_root or (
            Path.home() / "Library" / "Application Support" / "AstroFrame" / "reference_cache"
        )
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "AstroFrame/0.9.2-dev6 (desktop astrophotography planning; explicit user import)",
                "Accept-Language": "en-US,en;q=0.8",
            }
        )

    def _cache_dir(self, image_id: str) -> Path:
        path = self.cache_root / "astrobin" / image_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _meta_content(source: str, key: str) -> str | None:
        patterns = (
            rf'<meta[^>]+(?:property|name)=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)',
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{re.escape(key)}["\']',
        )
        for pattern in patterns:
            match = re.search(pattern, source, flags=re.I)
            if match:
                return html.unescape(match.group(1)).strip()
        return None

    @staticmethod
    def _decimal_field(text: str, label: str) -> float | None:
        # Modern AstroBin table pages include human-readable coordinates and a
        # decimal degree value in parentheses.
        match = re.search(
            rf"{re.escape(label)}\s*.*?\(\s*([-+]?\d+(?:\.\d+)?)\s*degrees\s*\)",
            text,
            flags=re.I,
        )
        return float(match.group(1)) if match else None

    def _solution_from_page(self, source: str, *, width_px: int, height_px: int) -> PlateSolution:
        text = _strip_tags(source)

        ra = self._decimal_field(text, "RA (center)")
        dec = self._decimal_field(text, "Dec (center)")

        # Older/simple table pages sometimes expose decimal coordinates in a
        # compact Center (RA, Dec) form.
        if ra is None or dec is None:
            compact = re.search(
                r"Center\s*\(RA,\s*Dec\)\s*:\s*\(\s*([-+]?\d+(?:\.\d+)?)\s*,\s*([-+]?\d+(?:\.\d+)?)\s*\)",
                text,
                flags=re.I,
            )
            if compact:
                ra, dec = float(compact.group(1)), float(compact.group(2))

        # Classic/current AstroBin pages also publish the solution summary as
        # sexagesimal values, e.g. "RA center: 9h 55' 35"" and
        # "DEC center: +69° 3' 59"".  These pages are much more stable than
        # relying on one specific HTML table layout.
        if ra is None:
            ra_match = re.search(
                r"RA\s*center\s*:?\s*(\d+(?:\.\d+)?)\s*h(?:ours?)?\s*(\d+(?:\.\d+)?)?\s*(?:['′m]|min(?:ute)?s?)?\s*(\d+(?:\.\d+)?)?\s*(?:[\"″s]|sec(?:ond)?s?)?",
                text,
                flags=re.I,
            )
            if ra_match:
                ra = _hms_to_deg(
                    float(ra_match.group(1)),
                    float(ra_match.group(2) or 0.0),
                    float(ra_match.group(3) or 0.0),
                )

        if dec is None:
            dec_match = re.search(
                r"DEC?\s*center\s*:?\s*([+-]?)\s*(\d+(?:\.\d+)?)\s*(?:°|deg(?:rees?)?)\s*(\d+(?:\.\d+)?)?\s*(?:['′m]|min(?:ute)?s?)?\s*(\d+(?:\.\d+)?)?\s*(?:[\"″s]|sec(?:ond)?s?)?",
                text,
                flags=re.I,
            )
            if dec_match:
                dec = _dms_to_deg(
                    dec_match.group(1),
                    float(dec_match.group(2)),
                    float(dec_match.group(3) or 0.0),
                    float(dec_match.group(4) or 0.0),
                )

        if ra is None or dec is None:
            raise AstroBinImportError(
                "AstroBin did not expose a usable plate-solution centre for this image."
            )

        corner_names = ("top/left", "top/right", "bottom/right", "bottom/left")
        corners: dict[str, tuple[float, float]] = {}
        for name in corner_names:
            cra = self._decimal_field(text, f"RA ({name})")
            cdec = self._decimal_field(text, f"Dec ({name})")
            if cra is not None and cdec is not None:
                corners[name] = (cra, cdec)

        orientation = 0.0
        if len(corners) == 4:
            tl, tr = corners["top/left"], corners["top/right"]
            br, bl = corners["bottom/right"], corners["bottom/left"]
            width_deg = (
                _angular_distance_deg(*tl, *tr) + _angular_distance_deg(*bl, *br)
            ) / 2.0
            height_deg = (
                _angular_distance_deg(*tl, *bl) + _angular_distance_deg(*tr, *br)
            ) / 2.0
            orientation = _position_angle_deg(*tl, *tr)
        else:
            size = re.search(
                r"Size\s*:\s*([0-9.]+)\s*[x×]\s*([0-9.]+)\s*deg",
                text,
                flags=re.I,
            )
            pixel_scale_match = re.search(
                r"Pixel\s*scale\s*:?\s*([0-9.]+)\s*(?:arcsec(?:onds?)?/?pixel|arcsec/pixel|\"/?pixel)",
                text,
                flags=re.I,
            )
            radius_match = re.search(
                r"Field\s*radius\s*:?\s*([0-9.]+)\s*(?:deg(?:rees?)?)",
                text,
                flags=re.I,
            )
            orientation_match = re.search(
                r"Orientation\s*:?\s*([-+]?[0-9.]+)\s*(?:deg(?:rees?)?)",
                text,
                flags=re.I,
            )
            if orientation_match:
                orientation = float(orientation_match.group(1)) % 180.0

            if size:
                width_deg, height_deg = float(size.group(1)), float(size.group(2))
            elif pixel_scale_match:
                published_scale = float(pixel_scale_match.group(1))
                width_deg = published_scale * max(width_px, 1) / 3600.0
                height_deg = published_scale * max(height_px, 1) / 3600.0
            elif radius_match:
                # AstroBin's field radius is centre-to-corner. Recover width
                # and height from the downloaded reference aspect ratio.
                radius = float(radius_match.group(1))
                aspect_norm = math.hypot(max(width_px, 1), max(height_px, 1))
                width_deg = 2.0 * radius * max(width_px, 1) / aspect_norm
                height_deg = 2.0 * radius * max(height_px, 1) / aspect_norm
            else:
                raise AstroBinImportError(
                    "AstroBin exposes a plate-solved centre, but AstroFrame could not determine the published field of view."
                )

        if width_deg <= 0 or height_deg <= 0:
            raise AstroBinImportError("AstroBin returned an invalid field size.")

        scale_x = width_deg * 3600.0 / max(width_px, 1)
        scale_y = height_deg * 3600.0 / max(height_px, 1)
        pixel_scale = (scale_x + scale_y) / 2.0
        radius = math.hypot(width_deg, height_deg) / 2.0

        return PlateSolution(
            ra_deg=ra % 360.0,
            dec_deg=dec,
            pixel_scale_arcsec=pixel_scale,
            orientation_deg=orientation,
            parity=0.0,
            radius_deg=radius,
            image_width_deg=width_deg,
            image_height_deg=height_deg,
            solver="AstroBin",
            job_id=None,
            solve_mode="Imported existing solution",
            solve_seconds=None,
        )

    def _solution_from_embedded_astrometry_reference(
        self,
        source: str,
        *,
        width_px: int,
        height_px: int,
        progress=None,
        log=None,
        cancelled=None,
    ) -> PlateSolution | None:
        """Reuse an Astrometry.net result linked by AstroBin, without upload.

        AstroBin itself uses Astrometry.net as one of its plate-solving
        backends.  Some modern AstroBin pages do not server-render the numeric
        WCS table, but they still contain/link the existing nova result.  That
        existing job is exactly what AstroFrame wants: retrieve it, never
        upload the reference again.
        """
        normalized = html.unescape(source).replace('\\/', '/')
        references: list[str] = []
        for pattern in (
            r'https?://nova\.astrometry\.net/user_images/\d+(?:#[A-Za-z0-9_-]+)?',
            r'https?://nova\.astrometry\.net/(?:status|jobs?)/\d+',
            r'(?<![A-Za-z0-9_])(?:/)?user_images/\d+(?:#[A-Za-z0-9_-]+)?',
        ):
            for match in re.finditer(pattern, normalized, flags=re.I):
                value = match.group(0)
                if value.startswith('/') or value.lower().startswith('user_images/'):
                    value = 'https://nova.astrometry.net/' + value.lstrip('/')
                if value not in references:
                    references.append(value)

        if not references:
            return None

        client = AstrometryNetClient('', session=self.session)
        for reference in references:
            if cancelled and cancelled():
                raise AstroBinImportError('AstroBin import cancelled.')
            try:
                if log:
                    log(f"AstroBin: found existing Astrometry.net reference {reference}")
                job_id = client.resolve_job_reference(reference, progress=progress)
                solution = client.solution_from_job(
                    job_id,
                    image_width_px=width_px,
                    image_height_px=height_px,
                    progress=progress,
                    cancelled=cancelled,
                )
                solution.solver = 'AstroBin / Astrometry.net'
                solution.solve_mode = 'Imported AstroBin-linked existing job'
                if log:
                    log(
                        f"AstroBin: reused existing Astrometry.net job #{job_id}; "
                        "no image uploaded"
                    )
                return solution
            except Exception as exc:
                if log:
                    log(f"AstroBin: linked Astrometry.net result could not be reused ({exc})")
                continue
        return None

    def import_reference(
        self,
        reference: str,
        *,
        progress=None,
        log=None,
        cancelled=None,
    ) -> tuple[str, PlateSolution | None, str]:
        """Fetch/cache the displayed AstroBin reference image only.

        AstroFrame deliberately does not depend on undocumented AstroBin
        plate-solution HTML.  Astrometry belongs to AstroFrame's own
        content-addressed SolveCache: once these exact image bytes have been
        solved successfully, MainWindow restores that solution immediately on
        every later load regardless of where the file came from.
        """
        image_id = astrobin_image_id(reference)
        cache_dir = self._cache_dir(image_id)
        metadata_path = cache_dir / "reference.json"

        # Fast path: the reference image itself is already cached.  Do not
        # contact AstroBin merely to re-check astrometry; MainWindow will check
        # the image fingerprint against AstroFrame's solution cache.
        if metadata_path.exists():
            try:
                cached = json.loads(metadata_path.read_text(encoding="utf-8"))
                image_path = Path(cached["image_path"])
                if image_path.exists():
                    title = cached.get("title", f"AstroBin {image_id}")
                    if progress:
                        progress("Loading cached AstroBin reference…")
                    if log:
                        log("AstroBin: using cached reference image")
                        log("AstroBin: plate-solution lookup left to AstroFrame cache")
                    return str(image_path), None, title
            except Exception:
                pass

        if cancelled and cancelled():
            raise AstroBinImportError("AstroBin import cancelled.")

        # Use one public/server-rendered page solely to discover the displayed
        # image and title.  No plate-solution scraping or Astrometry.net upload
        # is performed here.
        page_urls = (
            f"https://www.astrobin.com/{image_id}/0/?no-redirect=",
            f"https://www.astrobin.com/{image_id}/?no-redirect=",
            f"https://ssr.app.astrobin.com/i/{image_id}",
        )
        source = None
        last_error = None
        if progress:
            progress("Reading AstroBin reference…")
        if log:
            log(f"AstroBin: image {image_id}")
            log("AstroBin: fetching displayed reference image only")

        for page_url in page_urls:
            if cancelled and cancelled():
                raise AstroBinImportError("AstroBin import cancelled.")
            try:
                response = self.session.get(page_url, timeout=(5, 20), allow_redirects=True)
                response.raise_for_status()
                candidate = response.text
                image_url = self._meta_content(candidate, "og:image") or self._meta_content(candidate, "twitter:image")
                if not image_url:
                    match = re.search(
                        r'https://[^"\'<> ]+\.(?:jpg|jpeg|png|webp)(?:\?[^"\'<> ]*)?',
                        candidate,
                        flags=re.I,
                    )
                    image_url = html.unescape(match.group(0)) if match else None
                if image_url:
                    source = candidate
                    break
            except requests.RequestException as exc:
                last_error = exc
                continue

        if source is None or not image_url:
            detail = f": {last_error}" if last_error else ""
            raise AstroBinImportError(
                "AstroBin did not expose a downloadable reference image on its public page" + detail
            )

        title = self._meta_content(source, "og:title") or f"AstroBin {image_id}"

        if cancelled and cancelled():
            raise AstroBinImportError("AstroBin import cancelled.")

        if progress:
            progress("Downloading AstroBin reference image…")
        if log:
            log("AstroBin: downloading displayed reference image")
        try:
            image_response = self.session.get(image_url, timeout=(5, 30), allow_redirects=True)
            image_response.raise_for_status()
        except requests.RequestException as exc:
            raise AstroBinImportError(f"Could not download the AstroBin image: {exc}") from exc

        content_type = image_response.headers.get("content-type", "").lower()
        extension = ".jpg"
        if "png" in content_type:
            extension = ".png"
        elif "webp" in content_type:
            extension = ".webp"
        elif "tiff" in content_type:
            extension = ".tif"
        image_path = cache_dir / f"reference{extension}"
        image_path.write_bytes(image_response.content)

        try:
            with Image.open(image_path) as img:
                if image_path.suffix.lower() == ".webp":
                    png_path = cache_dir / "reference.png"
                    img.convert("RGB").save(png_path)
                    image_path = png_path
        except Exception as exc:
            image_path.unlink(missing_ok=True)
            raise AstroBinImportError(f"The downloaded AstroBin image could not be opened: {exc}") from exc

        metadata_path.write_text(
            json.dumps(
                {
                    "source_url": reference,
                    "image_id": image_id,
                    "title": title,
                    "image_path": str(image_path),
                    "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        if log:
            log("AstroBin: reference image cached; AstroFrame will reuse any matching local solution")
        return str(image_path), None, title


class AstroBinImportWorker(QObject):
    progress = Signal(str)
    log = Signal(str)
    succeeded = Signal(str, object, str)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, reference: str) -> None:
        super().__init__()
        self.reference = reference
        self._cancel_event = Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @Slot()
    def run(self) -> None:
        try:
            importer = AstroBinImporter()
            path, solution, title = importer.import_reference(
                self.reference,
                progress=self.progress.emit,
                log=self.log.emit,
                cancelled=self._cancel_event.is_set,
            )
            if self._cancel_event.is_set():
                self.failed.emit("AstroBin import cancelled.")
            else:
                self.succeeded.emit(path, solution, title)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()
