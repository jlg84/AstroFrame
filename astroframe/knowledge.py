from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


APP_SUPPORT = Path.home() / "Library" / "Application Support" / "AstroFrame"


def slugify(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "_", value.strip()).strip("_").lower()
    return value or "target"


def normalise_identifier(value: str) -> str:
    """Normalise common catalogue identifiers without destroying their identity.

    RC22v also removes insignificant leading zeroes from numeric catalogue
    designations, so M031/M31 and NGC0224/NGC224 share one identity.
    """
    text = re.sub(r"\s+", " ", str(value or "").strip()).upper()
    text = re.sub(r"^(M|NGC|IC|SH2|SH|RCW|LDN|LBN|B|CED|PK)\s*[- ]?\s*(\d)", r"\1 \2", text)
    m = re.fullmatch(r"(M|NGC|IC|SH2|SH|RCW|LDN|LBN|B|CED|PK)\s+0*(\d+)([A-Z]?)", text)
    if m:
        number = str(int(m.group(2)))
        text = f"{m.group(1)} {number}{m.group(3)}"
    return text


def catalogue_identifiers(value: str) -> set[str]:
    """Extract recognisable catalogue IDs even from decorated solver names."""
    text = normalise_identifier(value)
    patterns = [
        r"\bNGC\s*\d+[A-Z]?\b", r"\bIC\s*\d+[A-Z]?\b",
        r"\bM\s*\d+\b", r"\bRCW\s*\d+\b",
        r"\bLDN\s*\d+\b", r"\bLBN\s*\d+\b",
        r"\bSH2\s*[- ]?\s*\d+\b",
    ]
    found: set[str] = set()
    for pattern in patterns:
        for match in re.findall(pattern, text):
            found.add(normalise_identifier(match))
    return found


def target_id_for(canonical_name: str) -> str:
    normal = normalise_identifier(canonical_name)
    return slugify(normal)


@dataclass
class TargetRecord:
    id: str
    canonical_name: str
    common_name: str | None = None
    aliases: list[str] = field(default_factory=list)
    ra_deg: float | None = None
    dec_deg: float | None = None
    angular_width_deg: float | None = None
    angular_height_deg: float | None = None
    apparent_size_text: str | None = None
    position_angle_deg: float | None = None
    object_type: str | None = None
    constellation: str | None = None
    parent_region: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "TargetRecord":
        return cls(**data)


@dataclass
class CollectionEntry:
    target_id: str
    source_name: str | None = None
    rating: float | None = None
    rank: int | None = None
    tier: str | None = None
    difficulty: str | None = None
    fov_class: str | None = None
    narrowband: str | None = None
    broadband: str | None = None
    sho: str | None = None
    hoo: str | None = None
    moon_ok: str | None = None
    best_month: str | None = None
    visibility: str | None = None
    notes: str | None = None
    source_fields: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "CollectionEntry":
        return cls(**data)


@dataclass
class CollectionRecord:
    id: str
    name: str
    author: str | None = None
    description: str | None = None
    version: str = "1"
    source_type: str = "user"
    imported_from: str | None = None
    imported_at: str | None = None
    entries: list[CollectionEntry] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "CollectionRecord":
        values = dict(data)
        values["entries"] = [CollectionEntry.from_json(v) for v in data.get("entries", [])]
        return cls(**values)


class KnowledgeStore:
    """Persistent, local-first target and collection store."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or APP_SUPPORT / "knowledge"
        self.collections_root = self.root / "collections"
        self.targets_path = self.root / "targets.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self.collections_root.mkdir(parents=True, exist_ok=True)
        self._targets: dict[str, TargetRecord] = {}
        self._load_targets()
        # RC22u one-time coordinate-precision migration. Older Imm imports
        # retained exact source strings but canonical targets used rounded
        # workbook display columns. Rebuild once with the corrected parser,
        # then keep normal launches fast.
        migration = self.root / ".coord_precision_v2"
        if not migration.exists():
            self.rebuild_canonical_coordinates(save=True)
            try:
                migration.write_text("RC22u\n", encoding="utf-8")
            except OSError:
                pass

        # RC1 safety migration: a few legacy/flexible imports could persist
        # compact sexagesimal values as if they were decimal degrees (for
        # example M31 as RA 4244, Dec 411608). Do not require users to edit or
        # delete their knowledge database; repair invalid sky positions from
        # the best surviving collection source whenever such a record exists.
        if any(not self._coordinates_valid(t.ra_deg, t.dec_deg) for t in self._targets.values()):
            self.rebuild_canonical_coordinates(save=True)

    @staticmethod
    def _coordinates_valid(ra_deg: float | None, dec_deg: float | None) -> bool:
        try:
            ra = float(ra_deg)
            dec = float(dec_deg)
        except (TypeError, ValueError):
            return False
        return 0.0 <= ra < 360.0 and -90.0 <= dec <= 90.0

    def _load_targets(self) -> None:
        if not self.targets_path.exists():
            return
        try:
            raw = json.loads(self.targets_path.read_text(encoding="utf-8"))
            for item in raw.get("targets", []):
                target = TargetRecord.from_json(item)
                self._targets[target.id] = target
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self._targets = {}

    def _save_targets(self) -> None:
        payload = {
            "schema_version": 1,
            "targets": [asdict(self._targets[key]) for key in sorted(self._targets)],
        }
        self.targets_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def upsert_target(self, incoming: TargetRecord, *, save: bool = True) -> TargetRecord:
        existing = self.find_target(incoming.canonical_name, incoming.aliases)
        if existing is None:
            self._targets[incoming.id] = incoming
            if save:
                self._save_targets()
            return incoming

        existing_coords_valid = self._coordinates_valid(existing.ra_deg, existing.dec_deg)
        incoming_coords_valid = self._coordinates_valid(incoming.ra_deg, incoming.dec_deg)
        use_incoming_coords = incoming_coords_valid and not existing_coords_valid

        # Facts can be enriched by later imports, but never replaced by blanks.
        # Invalid legacy coordinates are the exception: a valid newly imported
        # pair must replace them immediately rather than preserving corruption.
        merged = TargetRecord(
            id=existing.id,
            canonical_name=existing.canonical_name or incoming.canonical_name,
            common_name=existing.common_name or incoming.common_name,
            aliases=sorted(set(existing.aliases + incoming.aliases + ([incoming.common_name] if incoming.common_name else []))),
            ra_deg=incoming.ra_deg if use_incoming_coords else existing.ra_deg,
            dec_deg=incoming.dec_deg if use_incoming_coords else existing.dec_deg,
            angular_width_deg=existing.angular_width_deg if existing.angular_width_deg is not None else incoming.angular_width_deg,
            angular_height_deg=existing.angular_height_deg if existing.angular_height_deg is not None else incoming.angular_height_deg,
            apparent_size_text=existing.apparent_size_text or incoming.apparent_size_text,
            position_angle_deg=existing.position_angle_deg if existing.position_angle_deg is not None else incoming.position_angle_deg,
            object_type=existing.object_type or incoming.object_type,
            constellation=existing.constellation or incoming.constellation,
            parent_region=existing.parent_region or incoming.parent_region,
        )
        self._targets[existing.id] = merged
        if save:
            self._save_targets()
        return merged

    def find_target(self, name: str, aliases: list[str] | None = None) -> TargetRecord | None:
        wanted = {normalise_identifier(name)}
        wanted.update(normalise_identifier(a) for a in (aliases or []) if a)
        wanted_catalogues: set[str] = set()
        for item in wanted:
            wanted_catalogues.update(catalogue_identifiers(item))
        for target in self._targets.values():
            candidates = {normalise_identifier(target.canonical_name)}
            if target.common_name:
                candidates.add(normalise_identifier(target.common_name))
            candidates.update(normalise_identifier(a) for a in target.aliases)
            if wanted & candidates:
                return target
            candidate_catalogues: set[str] = set()
            for item in candidates:
                candidate_catalogues.update(catalogue_identifiers(item))
            if wanted_catalogues and wanted_catalogues & candidate_catalogues:
                return target
        return None

    def get_target(self, target_id: str) -> TargetRecord | None:
        return self._targets.get(target_id)

    def save_collection(self, collection: CollectionRecord) -> None:
        if collection.imported_at is None:
            collection.imported_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        path = self.collections_root / f"{collection.id}.json"
        payload = {"schema_version": 1, **asdict(collection)}
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        # The collection file now contains the source-coordinate evidence PACM
        # needs. Re-evaluate canonical positions immediately so an import can
        # repair a stale target without requiring an application restart.
        self.rebuild_canonical_coordinates(save=True)

    def list_collections(self) -> list[CollectionRecord]:
        result: list[CollectionRecord] = []
        for path in sorted(self.collections_root.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                data.pop("schema_version", None)
                result.append(CollectionRecord.from_json(data))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return result

    @staticmethod
    def _compact_sexagesimal(value: Any, *, ra: bool) -> float | None:
        """Recover compact HHMMSS/DDMMSS values that Excel stored numerically."""
        if value is None:
            return None
        text = str(value).strip().replace("−", "-").replace("–", "-")
        try:
            numeric = float(text)
        except (TypeError, ValueError):
            return None
        if numeric != int(numeric):
            return None
        sign = -1.0 if numeric < 0 else 1.0
        digits = str(abs(int(numeric)))
        if len(digits) > 6:
            return None
        digits = digits.zfill(6)
        a, b, c = int(digits[:2]), int(digits[2:4]), int(digits[4:6])
        if b >= 60 or c >= 60:
            return None
        if ra:
            if a >= 24:
                return None
            return (a + b / 60.0 + c / 3600.0) * 15.0
        if a > 90 or (a == 90 and (b or c)):
            return None
        return sign * (a + b / 60.0 + c / 3600.0)

    @staticmethod
    def _source_coordinate_candidate(entry: CollectionEntry) -> tuple[float, float, int] | None:
        """Recover a coordinate pair from preserved source fields.

        The score rewards textual precision (seconds > minutes > coarse decimal)
        so PACM can prefer a precise source without hard-coding catalogue names.
        """
        try:
            from .collection_import import _ra_to_deg, _dec_to_deg
        except Exception:
            return None
        fields = entry.source_fields or {}
        # Newer imports may preserve normalised coordinates explicitly.
        try:
            if "_af_ra_deg" in fields and "_af_dec_deg" in fields:
                ra = float(fields["_af_ra_deg"])
                dec = float(fields["_af_dec_deg"])
                if KnowledgeStore._coordinates_valid(ra, dec):
                    return ra, dec, int(fields.get("_af_coord_precision", 50))
        except (TypeError, ValueError):
            pass
        ra_val = fields.get("ra_source")
        dec_val = fields.get("dec_source")
        if ra_val is None or dec_val is None:
            for key, value in fields.items():
                k = re.sub(r"[^a-z]", "", str(key).lower())
                if ra_val is None and (k in {"ra", "rightascension", "rahms"} or k.startswith("rightascension")):
                    ra_val = value
                if dec_val is None and (k in {"dec", "declination", "decdms"} or k.startswith("declination")):
                    dec_val = value
        if ra_val is None or dec_val is None:
            return None
        ra = _ra_to_deg(ra_val)
        dec = _dec_to_deg(dec_val)
        # Legacy spreadsheet cells sometimes lost leading zeroes because Excel
        # stored compact sexagesimal coordinates as numbers. If the normal
        # parser produces an impossible sky coordinate, reinterpret that source
        # as zero-padded HHMMSS/DDMMSS rather than accepting thousands of degrees.
        if ra is None or not (0.0 <= float(ra) < 360.0):
            ra = KnowledgeStore._compact_sexagesimal(ra_val, ra=True)
        if dec is None or not (-90.0 <= float(dec) <= 90.0):
            dec = KnowledgeStore._compact_sexagesimal(dec_val, ra=False)
        if not KnowledgeStore._coordinates_valid(ra, dec):
            return None
        text = f"{ra_val} {dec_val}"
        # Sexagesimal seconds are strongest; decimal detail is next.
        separators = text.count(":") + len(re.findall(r"\s+\d+(?:\.\d+)?", text))
        decimals = max([len(x) for x in re.findall(r"\.(\d+)", text)] or [0])
        score = min(40, separators * 6 + decimals * 3)
        return float(ra), float(dec), score

    def coordinate_provenance(self, target_id: str) -> list[tuple[str, float, float, int]]:
        result: list[tuple[str, float, float, int]] = []
        for collection in self.list_collections():
            for entry in collection.entries:
                if entry.target_id != target_id:
                    continue
                candidate = self._source_coordinate_candidate(entry)
                if candidate is not None:
                    ra, dec, score = candidate
                    result.append((collection.name, ra, dec, score))
        return sorted(result, key=lambda x: x[3], reverse=True)

    def rebuild_canonical_coordinates(self, *, save: bool = True) -> int:
        """PACM: rebuild canonical positions from the best surviving source record."""
        changed = 0
        for target in self._targets.values():
            candidates = self.coordinate_provenance(target.id)
            if not candidates:
                continue
            _source, ra, dec, _score = candidates[0]
            if not self._coordinates_valid(ra, dec):
                continue
            if target.ra_deg != ra or target.dec_deg != dec:
                target.ra_deg, target.dec_deg = ra, dec
                changed += 1
        if changed and save:
            self._save_targets()
        return changed

    def remove_collection(self, collection_id: str) -> bool:
        """Remove one imported collection without deleting shared target knowledge.

        Collection membership is the thing being removed. Target records are kept
        deliberately because the same target may be referenced by solver aliases,
        cached subject metadata, or another collection imported later.
        """
        safe_id = slugify(collection_id)
        path = self.collections_root / f"{safe_id}.json"
        if not path.exists():
            return False
        try:
            path.unlink()
            self.rebuild_canonical_coordinates(save=True)
            return True
        except OSError:
            return False

    def entries_for_target_name(self, name: str) -> list[tuple[CollectionRecord, CollectionEntry]]:
        target = self.find_target(name)
        if target is None:
            return []
        matches: list[tuple[CollectionRecord, CollectionEntry]] = []
        for collection in self.list_collections():
            for entry in collection.entries:
                if entry.target_id == target.id:
                    matches.append((collection, entry))
        return matches

    @staticmethod
    def _field_offsets_deg(centre_ra_deg: float, centre_dec_deg: float, ra_deg: float, dec_deg: float) -> tuple[float, float, float]:
        """Return local east/north offsets and great-circle separation in degrees."""
        import math
        ra1, dec1 = math.radians(centre_ra_deg), math.radians(centre_dec_deg)
        ra2, dec2 = math.radians(ra_deg), math.radians(dec_deg)
        dra = math.atan2(math.sin(ra2 - ra1), math.cos(ra2 - ra1))
        cos_sep = math.sin(dec1) * math.sin(dec2) + math.cos(dec1) * math.cos(dec2) * math.cos(dra)
        sep = math.acos(max(-1.0, min(1.0, cos_sep)))
        if sep < 1e-12:
            return 0.0, 0.0, 0.0
        bearing = math.atan2(
            math.sin(dra) * math.cos(dec2),
            math.cos(dec1) * math.sin(dec2) - math.sin(dec1) * math.cos(dec2) * math.cos(dra),
        )
        sep_deg = math.degrees(sep)
        return sep_deg * math.sin(bearing), sep_deg * math.cos(bearing), sep_deg

    def entries_in_field(
        self, centre_ra_deg: float, centre_dec_deg: float, width_deg: float, height_deg: float, orientation_deg: float = 0.0
    ) -> list[tuple[TargetRecord, CollectionRecord, CollectionEntry, float]]:
        """Collection entries intersecting the actual rectangular solved image footprint.

        Compact objects must have their catalogue position inside the frame. Extended
        objects receive an allowance based on their catalogue angular size so a nebula
        whose centre is just outside the frame can still count when it overlaps it.
        """
        import math
        matches = []
        half_w = max(0.0, width_deg) / 2.0
        half_h = max(0.0, height_deg) / 2.0
        theta = math.radians(orientation_deg)
        c, sn = math.cos(theta), math.sin(theta)
        for collection in self.list_collections():
            for entry in collection.entries:
                target = self.get_target(entry.target_id)
                if target is None or not self._coordinates_valid(target.ra_deg, target.dec_deg):
                    continue
                east, north, sep = self._field_offsets_deg(
                    centre_ra_deg, centre_dec_deg, target.ra_deg, target.dec_deg
                )
                # Rotate sky offsets into the image's rectangular axes. The sign
                # convention does not affect zero-rotation fields and follows the
                # plate solution orientation used by AstroFrame's overlay.
                x = east * c + north * sn
                y = -east * sn + north * c
                extent = 0.5 * max(target.angular_width_deg or 0.0, target.angular_height_deg or 0.0)
                if abs(x) <= half_w + extent and abs(y) <= half_h + extent:
                    matches.append((target, collection, entry, sep))
        matches.sort(key=lambda item: item[3])
        return matches

    def entries_near_position(self, ra_deg: float, dec_deg: float, radius_deg: float) -> list[tuple[TargetRecord, CollectionRecord, CollectionEntry, float]]:
        """Legacy circular proximity query retained for callers that need it."""
        matches = []
        for collection in self.list_collections():
            for entry in collection.entries:
                target = self.get_target(entry.target_id)
                if target is None or not self._coordinates_valid(target.ra_deg, target.dec_deg):
                    continue
                _east, _north, sep = self._field_offsets_deg(ra_deg, dec_deg, target.ra_deg, target.dec_deg)
                allowance = radius_deg + 0.5 * max(target.angular_width_deg or 0.0, target.angular_height_deg or 0.0)
                if sep <= allowance:
                    matches.append((target, collection, entry, sep))
        matches.sort(key=lambda item: item[3])
        return matches
