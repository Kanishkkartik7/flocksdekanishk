from app.network import build_network
from app.portal.parsers import parse_export_meter, parse_transformer
from app.query import MeterFilter, Near, filter_meters, haversine_km
from tests.conftest import load_fixture


def sample():
    meters = [parse_export_meter(r) for r in load_fixture("export_sample.json")]
    transformers = {t.code: t for t in map(parse_transformer, load_fixture("dts_sample.json")) if t}
    return meters, transformers


def issues_of(kind, network):
    return [i for i in network.issues if i.kind == kind]


def test_tree_keeps_every_meter_and_counts_add_up():
    meters, transformers = sample()
    network = build_network(meters, transformers)
    assert sum(z.meter_count for z in network.zones) == len(meters)
    for zone in network.zones:
        assert zone.meter_count == sum(c.meter_count for c in zone.children)


def test_transformer_capacity_is_attached_to_dt_nodes():
    meters, transformers = sample()
    network = build_network(meters, transformers)

    def dt_nodes(node):
        if node.level == "dt":
            yield node
        for child in node.children:
            yield from dt_nodes(child)

    dt001 = next(n for zone in network.zones for n in dt_nodes(zone) if n.code == "DT-001")
    assert dt001.capacity_kva == transformers["DT-001"].capacity_kva


def test_data_issues_are_reported():
    meters, transformers = sample()
    network = build_network(meters, transformers)

    conflicting = issues_of("conflicting_names", network)
    assert any(i.code == "DT-007" and "Old Malviya Nagar Xfmr" in i.message for i in conflicting)

    blank_circle = [i for i in issues_of("blank_code", network) if i.level == "circle"]
    assert blank_circle and "J100011" in blank_circle[0].meter_ids

    assert any(i.level == "division" and i.code == "D-01" for i in issues_of("multiple_parents", network))


def test_feeder_mismatch_with_transformer_list_is_reported():
    meters, transformers = sample()
    transformers["DT-001"] = transformers["DT-001"].model_copy(update={"feeder_code": "F-999"})
    network = build_network(meters, transformers)
    assert any(i.code == "DT-001" for i in issues_of("feeder_mismatch", network))


def test_filters_combine_and_are_case_insensitive():
    meters, _ = sample()
    found = [m for m, _ in filter_meters(meters, MeterFilter(status=frozenset({"Faulty"}), build=frozenset({"V2"})))]
    assert "J100004" in [m.meter_id for m in found]
    assert all(m.status == "faulty" and m.build == "v2" for m in found)
    by_dt = filter_meters(meters, MeterFilter(network={"dt": frozenset({"dt-007"})}))
    assert {m.network.dt.code for m, _ in by_dt} == {"DT-007"}
    by_text = filter_meters(meters, MeterFilter(q="se339"))
    assert [m.meter_id for m, _ in by_text] == ["J100000"]


def test_near_search_sorts_by_distance():
    meters, _ = sample()
    origin = meters[0].location
    found = filter_meters(meters, MeterFilter(near=Near(origin.latitude, origin.longitude, radius_km=50)))
    assert found[0][0].meter_id == meters[0].meter_id
    assert found[0][1] == 0
    distances = [d for _, d in found]
    assert distances == sorted(distances)


def test_haversine_is_roughly_right():
    # Jaipur to Delhi is about 240 km in a straight line.
    assert 230 < haversine_km(26.9124, 75.7873, 28.6139, 77.2090) < 250
