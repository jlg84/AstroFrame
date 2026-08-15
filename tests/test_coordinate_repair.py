from __future__ import annotations

import json

from astroframe.knowledge import CollectionEntry, CollectionRecord, KnowledgeStore, TargetRecord


def test_compact_numeric_coordinates_are_recovered():
    entry = CollectionEntry(
        target_id="m_31",
        source_name="M 31",
        source_fields={"ra_source": 4244, "dec_source": 411608},
    )
    candidate = KnowledgeStore._source_coordinate_candidate(entry)
    assert candidate is not None
    _source_ra, _source_dec, _score = candidate
    assert abs(_source_ra - 10.6833333333) < 1e-6
    assert abs(_source_dec - 41.2688888889) < 1e-6


def test_invalid_persisted_target_self_heals_from_collection(tmp_path):
    root = tmp_path / "knowledge"
    collections = root / "collections"
    collections.mkdir(parents=True)

    targets = {
        "schema_version": 1,
        "targets": [
            {
                "id": "m_31",
                "canonical_name": "M 031",
                "common_name": "Andromeda",
                "aliases": ["Andromeda Galaxy", "NGC 224"],
                "ra_deg": 4244.0,
                "dec_deg": 411608.0,
                "angular_width_deg": 3.3333333333333335,
                "angular_height_deg": 3.3333333333333335,
                "apparent_size_text": "200′",
                "position_angle_deg": 35.0,
                "object_type": "Gal",
                "constellation": "And",
                "parent_region": None,
            }
        ],
    }
    (root / "targets.json").write_text(json.dumps(targets), encoding="utf-8")
    (root / ".coord_precision_v2").write_text("RC22u\n", encoding="utf-8")

    collection = CollectionRecord(
        id="gary_imm_deep_sky_compendium",
        name="Gary Imm's Deep Sky Compendium",
        entries=[
            CollectionEntry(
                target_id="m_31",
                source_name="M 31",
                source_fields={"ra_source": 4244, "dec_source": 411608},
            )
        ],
    )
    payload = {"schema_version": 1, **collection.__dict__}
    payload["entries"] = [entry.__dict__ for entry in collection.entries]
    (collections / "gary_imm_deep_sky_compendium.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    store = KnowledgeStore(root)
    repaired = store.get_target("m_31")
    assert repaired is not None
    assert abs(repaired.ra_deg - 10.6833333333) < 1e-6
    assert abs(repaired.dec_deg - 41.2688888889) < 1e-6


def test_valid_import_replaces_invalid_existing_coordinates(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge")
    store._targets["m_31"] = TargetRecord(
        id="m_31",
        canonical_name="M 031",
        aliases=["NGC 224"],
        ra_deg=4244.0,
        dec_deg=411608.0,
    )
    incoming = TargetRecord(
        id="m_31",
        canonical_name="M 31",
        aliases=["NGC 224"],
        ra_deg=10.6847,
        dec_deg=41.2692,
    )
    merged = store.upsert_target(incoming, save=False)
    assert merged.ra_deg == incoming.ra_deg
    assert merged.dec_deg == incoming.dec_deg
