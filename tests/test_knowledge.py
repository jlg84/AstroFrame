from pathlib import Path

from astroframe.knowledge import (
    CollectionEntry,
    CollectionRecord,
    KnowledgeStore,
    TargetRecord,
    normalise_identifier,
    target_id_for,
)


def test_identifier_normalisation():
    assert normalise_identifier("NGC6188") == "NGC 6188"
    assert normalise_identifier("ngc 6188") == "NGC 6188"
    assert target_id_for("NGC 6188") == "ngc_6188"


def test_collection_round_trip(tmp_path: Path):
    store = KnowledgeStore(tmp_path)
    target = store.upsert_target(TargetRecord(
        id="ngc_6188",
        canonical_name="NGC 6188",
        common_name="Dragons of Ara",
        aliases=["Rim Nebula"],
        object_type="Emission nebula",
    ))
    store.save_collection(CollectionRecord(
        id="test_collection",
        name="Test Collection",
        entries=[CollectionEntry(target_id=target.id, rank=1, tier="Top")],
    ))
    matches = store.entries_for_target_name("Rim Nebula")
    assert len(matches) == 1
    assert matches[0][0].name == "Test Collection"
    assert matches[0][1].rank == 1


def test_decorated_catalogue_name_matches(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge")
    store.upsert_target(TargetRecord(id="ngc_5139", canonical_name="NGC 5139", common_name="Omega Centauri"))
    assert store.find_target("NGC 5139 106").canonical_name == "NGC 5139"


def test_entries_in_field_rejects_object_inside_old_circumscribed_radius(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge")
    inside = store.upsert_target(TargetRecord(id="inside", canonical_name="Inside", ra_deg=0.2, dec_deg=0.1))
    outside = store.upsert_target(TargetRecord(id="outside", canonical_name="Outside", ra_deg=1.2, dec_deg=0.8))
    store.save_collection(CollectionRecord(
        id="field_test", name="Field Test", entries=[CollectionEntry(target_id=inside.id), CollectionEntry(target_id=outside.id)]
    ))
    # 2 x 1 degree frame: Outside is within the old diagonal-radius circle (~1.118°
    # is the corner radius; choose a point demonstrating rectangular rejection).
    outside_target = store.get_target("outside")
    outside_target.ra_deg = 0.9
    outside_target.dec_deg = 0.7
    store._save_targets()
    found = store.entries_in_field(0.0, 0.0, 2.0, 1.0, 0.0)
    names = {item[0].canonical_name for item in found}
    assert "Inside" in names
    assert "Outside" not in names

def test_entries_in_field_allows_extended_overlap(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge")
    nebula = store.upsert_target(TargetRecord(
        id="nebula", canonical_name="Nebula", ra_deg=1.1, dec_deg=0.0, angular_width_deg=0.4
    ))
    store.save_collection(CollectionRecord(id="field_test", name="Field Test", entries=[CollectionEntry(target_id=nebula.id)]))
    found = store.entries_in_field(0.0, 0.0, 2.0, 1.0, 0.0)
    assert [item[0].canonical_name for item in found] == ["Nebula"]


def test_entries_in_field_rejects_huge_region_whose_centre_is_not_meaningfully_near_frame(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge")
    region = store.upsert_target(TargetRecord(
        id="huge_region", canonical_name="Huge Region", ra_deg=1.0, dec_deg=0.0,
        angular_width_deg=8.0, angular_height_deg=8.0
    ))
    store.save_collection(CollectionRecord(
        id="field_test", name="Field Test", entries=[CollectionEntry(target_id=region.id)]
    ))
    # A 0.4 x 0.4 degree image one degree from the catalogue centre must not be
    # claimed merely because the catalogue's very large nominal extent covers it.
    found = store.entries_in_field(0.0, 0.0, 0.4, 0.4, 0.0)
    assert found == []

def test_entries_in_field_keeps_meaningful_extended_edge_overlap(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge")
    nebula = store.upsert_target(TargetRecord(
        id="edge_nebula", canonical_name="Edge Nebula", ra_deg=1.08, dec_deg=0.0,
        angular_width_deg=0.4, angular_height_deg=0.4
    ))
    store.save_collection(CollectionRecord(
        id="field_test", name="Field Test", entries=[CollectionEntry(target_id=nebula.id)]
    ))
    found = store.entries_in_field(0.0, 0.0, 2.0, 1.0, 0.0)
    assert [item[0].canonical_name for item in found] == ["Edge Nebula"]
