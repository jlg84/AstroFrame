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


def test_minkowski_designation_not_messier():
    from astroframe.knowledge import catalogue_identifiers

    assert catalogue_identifiers("M 1-3") == set()
    assert catalogue_identifiers("M1-3") == set()
    assert catalogue_identifiers("M 1 - 3") == set()
    assert catalogue_identifiers("M4-15") == set()
    assert catalogue_identifiers("M 4 - 15") == set()
    assert catalogue_identifiers("M 004") == {"M 4"}
    assert catalogue_identifiers("M1") == {"M 1"}
    assert catalogue_identifiers("M 31") == {"M 31"}
    assert catalogue_identifiers("NGC 1360") == {"NGC 1360"}


def test_minkowski_objects_do_not_merge_with_messier(tmp_path):
    from itertools import permutations

    cases = [
        (
            TargetRecord(
                id="m_1",
                canonical_name="M1",
                common_name="Crab Nebula",
                ra_deg=83.633,
                dec_deg=22.014,
            ),
            TargetRecord(
                id="ngc_1360",
                canonical_name="NGC 1360",
                aliases=["M 1-3"],
                ra_deg=53.3125,
                dec_deg=-25.87194,
            ),
        ),
        (
            TargetRecord(
                id="m_4",
                canonical_name="M4",
                ra_deg=245.897,
                dec_deg=-26.526,
            ),
            TargetRecord(
                id="minkowski_1_91",
                canonical_name="Minkowski 1-91",
                aliases=["M4-15"],
                ra_deg=293.24167,
                dec_deg=26.87806,
            ),
        ),
    ]

    for case_number, pair in enumerate(cases):
        for order_number, ordered in enumerate(permutations(pair)):
            store = KnowledgeStore(
                tmp_path / f"case_{case_number}_{order_number}"
            )

            for incoming in ordered:
                store.upsert_target(incoming, save=False)

            assert len(store._targets) == 2
            for original in pair:
                actual = store.get_target(original.id)
                assert actual is not None
                assert actual.canonical_name == original.canonical_name
                assert actual.ra_deg == original.ra_deg
                assert actual.dec_deg == original.dec_deg


def test_conflicting_target_identities_do_not_merge(tmp_path):
    from itertools import permutations

    cases = [
        (
            TargetRecord(
                id="test_object_a",
                canonical_name="Test Object A",
                aliases=["Shared Alias"],
                ra_deg=10.0,
                dec_deg=-20.0,
            ),
            TargetRecord(
                id="test_object_b",
                canonical_name="Test Object B",
                aliases=["Shared Alias"],
                ra_deg=200.0,
                dec_deg=40.0,
            ),
        ),
        (
            TargetRecord(
                id="m_4",
                canonical_name="M4",
                ra_deg=245.897,
                dec_deg=-26.526,
            ),
            TargetRecord(
                id="m_4",
                canonical_name="M4",
                ra_deg=293.24167,
                dec_deg=26.87806,
            ),
        ),
    ]

    for case_number, pair in enumerate(cases):
        for order_number, ordered in enumerate(permutations(pair)):
            store = KnowledgeStore(
                tmp_path / f"conflict_{case_number}_{order_number}"
            )
            store.upsert_target(ordered[0], save=False)

            try:
                store.upsert_target(ordered[1], save=False)
            except ValueError:
                pass  # Explicit rejection is acceptable.

            assert len(store._targets) == 2 if case_number == 0 else len(store._targets) == 1

            if case_number == 0:
                assert store.get_target("test_object_a") is not None
                assert store.get_target("test_object_b") is not None
            else:
                surviving = store.get_target("m_4")
                assert surviving is not None
                assert surviving.ra_deg == ordered[0].ra_deg
                assert surviving.dec_deg == ordered[0].dec_deg


def test_ambiguous_alias_prefers_matching_coordinates(tmp_path):
    from astroframe.knowledge import KnowledgeStore, TargetRecord

    store = KnowledgeStore(tmp_path / "knowledge")

    first = TargetRecord(
        id="first_object",
        canonical_name="First Object",
        aliases=["Shared Alias"],
        ra_deg=10.0,
        dec_deg=-20.0,
    )
    second = TargetRecord(
        id="second_object",
        canonical_name="Second Object",
        aliases=["Shared Alias"],
        ra_deg=200.0,
        dec_deg=40.0,
    )

    store._replace_indexed_target(first)
    store._replace_indexed_target(second)

    incoming = TargetRecord(
        id="third_designation",
        canonical_name="Third Designation",
        aliases=["Shared Alias"],
        ra_deg=200.01,
        dec_deg=40.01,
    )

    result = store.upsert_target(incoming, save=False)

    assert result.id == "second_object"
    assert len(store._targets) == 2
    assert store.get_target("first_object").ra_deg == 10.0
    assert store.get_target("second_object").ra_deg == 200.0


def test_missing_coordinates_do_not_merge_on_shared_alias(tmp_path):
    from astroframe.knowledge import KnowledgeStore, TargetRecord

    store = KnowledgeStore(tmp_path / "knowledge")

    first = TargetRecord(
        id="unknown_object",
        canonical_name="Unknown Object",
        aliases=["Shared Designation"],
        ra_deg=None,
        dec_deg=None,
    )
    second = TargetRecord(
        id="known_object",
        canonical_name="Known Object",
        aliases=["Shared Designation"],
        ra_deg=200.0,
        dec_deg=40.0,
    )

    store.upsert_target(first, save=False)
    store.upsert_target(second, save=False)

    assert len(store._targets) == 2
    assert store.get_target("unknown_object") is not None
    assert store.get_target("known_object") is not None


def test_exact_target_id_preferred_over_closer_alias(tmp_path):
    from astroframe.knowledge import KnowledgeStore, TargetRecord

    store = KnowledgeStore(tmp_path / "knowledge")

    original = TargetRecord(
        id="original_object",
        canonical_name="Original Object",
        aliases=["Shared Alias"],
        ra_deg=100.0,
        dec_deg=20.0,
    )
    neighbour = TargetRecord(
        id="neighbour_object",
        canonical_name="Neighbour Object",
        aliases=["Shared Alias"],
        ra_deg=100.5,
        dec_deg=20.0,
    )

    store._replace_indexed_target(original)
    store._replace_indexed_target(neighbour)

    incoming = TargetRecord(
        id="original_object",
        canonical_name="Original Object",
        aliases=["Shared Alias", "New Alias"],
        ra_deg=100.49,
        dec_deg=20.0,
    )

    result = store.upsert_target(incoming, save=False)

    assert result.id == "original_object"
    assert len(store._targets) == 2
    assert "New Alias" in store.get_target("original_object").aliases
    assert "New Alias" not in store.get_target("neighbour_object").aliases
