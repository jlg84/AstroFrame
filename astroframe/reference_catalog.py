from __future__ import annotations

from dataclasses import asdict, dataclass
import csv
import io
import json
import math
from pathlib import Path

import requests
from astropy.coordinates import SkyCoord
import astropy.units as u


VIZIER_ENDPOINT = "https://vizier.cds.unistra.fr/viz-bin/asu-tsv"
BICA_TABLES = {
    "clusters": ("J/MNRAS/389/678/table3", "Cluster"),
    "associations": ("J/MNRAS/389/678/table4", "Association"),
    "nebulae": ("J/MNRAS/389/678/table5", "Nebula"),
}
_CACHE_VERSION = 1
_MEMORY_CACHE: dict[str, tuple["ReferenceObject", ...]] = {}


@dataclass(frozen=True)
class ReferenceObject:
    id: str
    name: str
    ra_deg: float
    dec_deg: float
    category: str
    object_type: str = ""
    major_arcmin: float | None = None
    minor_arcmin: float | None = None
    aliases: str = ""
    source: str = "Bica et al. 2008"

    @property
    def feature_score(self) -> float:
        size = max(self.major_arcmin or 0.0, self.minor_arcmin or 0.0, 0.15)
        weight = {"Nebula": 1.35, "Association": 1.12, "Cluster": 1.0}.get(self.category, 1.0)
        return size * weight


def _float_or_none(value: str | None) -> float | None:
    if value is None:
        return None
    value = str(value).strip()
    if not value or value in {"--", "-", "nan", "NaN"}:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _short_name(names: str, simbad_name: str) -> str:
    simbad_name = (simbad_name or "").strip()
    if simbad_name and simbad_name not in {"--", "-"}:
        return simbad_name
    names = (names or "").strip()
    if not names:
        return "Bica object"
    for sep in (",", ";", "="):
        if sep in names:
            names = names.split(sep, 1)[0].strip()
    return names or "Bica object"


def _parse_vizier_tsv(text: str, table_key: str, category: str) -> list[ReferenceObject]:
    lines = text.splitlines()
    header_index = None
    headers: list[str] = []
    for idx, line in enumerate(lines):
        if line.startswith("#"):
            continue
        cols = [c.strip() for c in line.split("\t")]
        if "RAJ2000" in cols and "DEJ2000" in cols:
            header_index = idx
            headers = cols
            break
    if header_index is None:
        raise ValueError("VizieR response did not contain the expected RAJ2000/DEJ2000 columns.")

    objects: list[ReferenceObject] = []
    for row_index, line in enumerate(lines[header_index + 1 :], start=1):
        if not line.strip() or line.startswith("#"):
            continue
        stripped = line.replace("\t", "").strip()
        if stripped and set(stripped) <= {"-", " "}:
            continue
        values = line.split("\t")
        if len(values) < len(headers):
            values += [""] * (len(headers) - len(values))
        row = dict(zip(headers, values))
        ra_text = (row.get("RAJ2000") or "").strip()
        dec_text = (row.get("DEJ2000") or "").strip()
        if not ra_text or not dec_text:
            continue
        try:
            coord = SkyCoord(ra_text, dec_text, unit=(u.hourangle, u.deg), frame="icrs")
        except Exception:
            continue
        names = (row.get("Names") or "").strip()
        simbad = (row.get("SimbadName") or "").strip()
        name = _short_name(names, simbad)
        obj_type = (row.get("Type") or "").strip()
        objects.append(
            ReferenceObject(
                id=f"ref:bica:{table_key}:{row_index}",
                name=name,
                ra_deg=float(coord.ra.deg),
                dec_deg=float(coord.dec.deg),
                category=category,
                object_type=obj_type,
                major_arcmin=_float_or_none(row.get("amaj")),
                minor_arcmin=_float_or_none(row.get("amin")),
                aliases=names,
            )
        )
    return objects


def _cache_dir() -> Path:
    """Persistent per-user reference cache shared by future AstroFrame builds."""
    path = Path.home() / ".astroframe" / "reference_catalogs" / f"bica2008-v{_CACHE_VERSION}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_path(table_key: str) -> Path:
    return _cache_dir() / f"{table_key}.json"


def bica_cache_ready(mode: str) -> bool:
    mode = (mode or "featured").lower()
    keys = ("clusters", "associations", "nebulae") if mode == "featured" else (mode,)
    return all(key in BICA_TABLES and _cache_path(key).exists() for key in keys)


def _save_table_cache(table_key: str, objects: list[ReferenceObject]) -> None:
    path = _cache_path(table_key)
    tmp = path.with_suffix(".tmp")
    payload = {
        "cache_version": _CACHE_VERSION,
        "table": table_key,
        "objects": [asdict(obj) for obj in objects],
    }
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def _load_table_cache(table_key: str) -> tuple[ReferenceObject, ...] | None:
    path = _cache_path(table_key)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("cache_version") != _CACHE_VERSION or payload.get("table") != table_key:
            return None
        return tuple(ReferenceObject(**row) for row in payload.get("objects", []))
    except Exception:
        # A partial/corrupt cache should repair itself on the next fetch.
        try:
            path.unlink()
        except OSError:
            pass
        return None


def _download_full_table(table_key: str, timeout: float) -> tuple[ReferenceObject, ...]:
    source, category = BICA_TABLES[table_key]
    params = {
        "-source": source,
        "-out": "Names,RAJ2000,DEJ2000,Type,amaj,amin,SimbadName",
        "-out.max": "unlimited",
    }
    response = requests.get(
        VIZIER_ENDPOINT,
        params=params,
        timeout=timeout,
        headers={"User-Agent": "AstroFrame/0.9.2-dev11j"},
    )
    response.raise_for_status()
    objects = _parse_vizier_tsv(response.text, table_key, category)
    if not objects:
        raise ValueError(f"VizieR returned no Bica {table_key} records.")
    _save_table_cache(table_key, objects)
    return tuple(objects)


def _load_or_download_table(table_key: str, timeout: float) -> tuple[ReferenceObject, ...]:
    cached = _MEMORY_CACHE.get(table_key)
    if cached is not None:
        return cached
    cached = _load_table_cache(table_key)
    if cached is None:
        cached = _download_full_table(table_key, timeout)
    _MEMORY_CACHE[table_key] = cached
    return cached


def _angular_separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Fast great-circle separation; sufficient for filtering a few thousand local rows."""
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    cos_sep = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(r1 - r2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos_sep))))


def query_bica(
    ra_deg: float,
    dec_deg: float,
    width_deg: float,
    height_deg: float,
    mode: str,
    timeout: float = 45.0,
) -> list[ReferenceObject]:
    """Return Bica objects from AstroFrame's persistent local catalogue cache.

    The first use of each Bica table downloads that complete table from VizieR once
    and converts it to a compact local JSON cache under ~/.astroframe.  Every later
    field search -- including searches from future AstroFrame builds -- is local.
    AstroFrame still performs the definitive WCS→pixel in-frame test after this fast
    circular pre-filter.
    """
    mode = (mode or "featured").lower()
    if mode == "featured":
        table_keys = ("clusters", "associations", "nebulae")
    elif mode in BICA_TABLES:
        table_keys = (mode,)
    else:
        raise ValueError(f"Unknown Bica reference mode: {mode}")

    radius_deg = 0.5 * math.hypot(float(width_deg), float(height_deg)) + 0.18
    result: list[ReferenceObject] = []
    for table_key in table_keys:
        for obj in _load_or_download_table(table_key, timeout):
            if _angular_separation_deg(float(ra_deg), float(dec_deg), obj.ra_deg, obj.dec_deg) <= radius_deg:
                result.append(obj)
    return result
