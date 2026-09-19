from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image
from scripts import generate_geo
from scripts.generate_geo import CITIES, _cities, _coastal_coverage, _polygons, _rasterize_ocean, city_display_name, city_key

ROOT = Path(__file__).resolve().parents[2]

CANADIAN_ADMIN1 = {
    "Alberta",
    "British Columbia",
    "Manitoba",
    "New Brunswick",
    "Newfoundland and Labrador",
    "Northwest Territories",
    "Nova Scotia",
    "Nunavut",
    "Ontario",
    "Prince Edward Island",
    "Québec",
    "Saskatchewan",
    "Yukon",
}

US_ADMIN1 = {
    "Alabama",
    "Alaska",
    "Arizona",
    "Arkansas",
    "California",
    "Colorado",
    "Connecticut",
    "Delaware",
    "District of Columbia",
    "Florida",
    "Georgia",
    "Idaho",
    "Illinois",
    "Indiana",
    "Iowa",
    "Kansas",
    "Kentucky",
    "Louisiana",
    "Maine",
    "Maryland",
    "Massachusetts",
    "Michigan",
    "Minnesota",
    "Mississippi",
    "Missouri",
    "Montana",
    "Nebraska",
    "Nevada",
    "New Hampshire",
    "New Jersey",
    "New Mexico",
    "New York",
    "North Carolina",
    "North Dakota",
    "Ohio",
    "Oklahoma",
    "Oregon",
    "Pennsylvania",
    "Rhode Island",
    "South Carolina",
    "South Dakota",
    "Tennessee",
    "Texas",
    "Utah",
    "Vermont",
    "Virginia",
    "Washington",
    "West Virginia",
    "Wisconsin",
    "Wyoming",
}

MEXICO_CITY_NAMES = {"Tijuana", "Guadalajara", "Monterrey", "Mexico City"}
ALASKA_CITY_NAMES = {"Anchorage", "Fairbanks", "Juneau", "Nome"}
EXPECTED_CITY_COUNT = 74
EXPECTED_MAP_CITY_COUNTS = {"CAN": 255, "USA": 767, "MEX": 189}
EXPECTED_HAWAII_LABELS = {"Lihue", "Wahiawa", "Wailuku", "Kailua-Kona", "Hilo", "Honolulu"}
EXPECTED_COVERAGE_HASHES = {
    "smoke-coverage-mask-v1.png": "c9532854ad8a4e845f5fe18d8c09cbaad04d17876ce889ccfc7ab26306f79692",
    "smoke-coverage-mask-v2.png": "723f1478bdb3dfa8d2cc9cac2b4cf7c5cdab845e10f419cdb4b3082e99de4c61",
    "smoke-coverage-mask-v3.png": "874e90af2e0d52795670c3d6aa486ca0c14f6affb4c79d662dd3f4013827e552",
}
PUBLIC_CITY_FIELDS = {"name", "searchName", "region", "country", "lon", "lat", "priority", "mobile"}
MOBILE_CITY_NAMES = {
    "Vancouver",
    "Toronto",
    "Los Angeles",
    "Chicago",
    "New York",
    "Mexico City",
    "Anchorage",
    "Whitehorse",
    "Yellowknife",
    "Iqaluit",
}
HOMONYM_DISPLAY_NAMES = {"Portland, ME", "Portland, OR", "Charleston, SC", "Charleston, WV"}
KEPT_EXTRA_DISPLAY_NAMES = {
    "Calgary",
    "Edmonton",
    "Toronto",
    "Ottawa",
    "Los Angeles",
    "San Francisco",
    "Dallas",
    "Houston",
} | ALASKA_CITY_NAMES
CAN_ADMIN1_CITY_COUNTS = {"Alberta": 2, "Ontario": 2}
US_ADMIN1_CITY_COUNTS = {"California": 2, "Texas": 2, "Alaska": 4}
ORIGINAL_CITY_PRIORITIES = {
    "Vancouver": 1,
    "Edmonton": 2,
    "Calgary": 2,
    "Winnipeg": 2,
    "Toronto": 1,
    "Ottawa": 2,
    "Montreal": 1,
    "Seattle": 1,
    "San Francisco": 2,
    "Los Angeles": 1,
    "Denver": 1,
    "Dallas": 1,
    "Houston": 2,
    "Chicago": 1,
    "Atlanta": 1,
    "Miami": 2,
    "Washington, D.C.": 1,
    "New York": 1,
    "Boston": 2,
    "Anchorage": 1,
    "Fairbanks": 2,
    "Juneau": 2,
    "Nome": 2,
    "Tijuana": 2,
    "Guadalajara": 2,
    "Monterrey": 1,
    "Mexico City": 1,
}


def _generated_geo() -> dict:
    path = ROOT / "public" / "geo" / "north-america-v3.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _admin1_counts(country: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for city in CITIES:
        if city["country"] == country:
            counts[city["admin1"]] = counts.get(city["admin1"], 0) + 1
    return counts


def _city_feature(nameascii: str, country: str, admin1: str, lon: float, lat: float) -> dict:
    return {
        "properties": {"nameascii": nameascii, "adm0_a3": country, "adm1name": admin1},
        "geometry": {"coordinates": [lon, lat]},
    }


def test_allowlist_covers_required_admin1() -> None:
    canadian = {city["admin1"] for city in CITIES if city["country"] == "CAN"}
    american = {city["admin1"] for city in CITIES if city["country"] == "USA"}
    assert canadian >= CANADIAN_ADMIN1
    assert american >= US_ADMIN1
    assert "Hawaii" not in american
    assert not any(city["admin1"] == "Hawaii" for city in CITIES)


def test_generated_cities_match_allowlist_and_coverage() -> None:
    path = ROOT / "public" / "geo" / "north-america-v3.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    names = {city["name"] for city in payload["cities"]}
    assert names == {city_display_name(city) for city in CITIES}
    assert {city["name"] for city in payload["cities"] if city["country"] == "MEX"} == MEXICO_CITY_NAMES
    assert names >= ALASKA_CITY_NAMES
    assert "Honolulu" not in names
    assert not any("Hawaii" in city["name"] for city in payload["cities"])


def test_generated_geography_is_complete_and_budgeted() -> None:
    path = ROOT / "public" / "geo" / "north-america-v3.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 3
    assert payload["source"]["license"] == "Public domain"
    assert payload["coastlines"]
    assert payload["countryBorders"]
    assert payload["regionBorders"]
    assert {city["country"] for city in payload["cities"]} == {"CAN", "USA", "MEX"}
    assert {label["name"] for label in payload["countryLabels"]} == {"CANADA", "UNITED STATES", "MEXICO"}
    assert payload["contextBounds"] == [-145.0, 10.0, -45.0, 72.0]
    assert payload["displayBounds"] == [-170.0, 10.0, -45.0, 84.0]
    assert {city["name"] for city in payload["cities"]} >= ALASKA_CITY_NAMES
    assert path.stat().st_size <= 750_000
    assert not (ROOT / "public" / "geo" / "north-america.json").exists()
    for key in ("coastlines", "countryBorders", "regionBorders"):
        for point in (point for line in payload[key] for point in line):
            assert len(point) == 2
            assert all(math.isfinite(value) for value in point)
            assert -180 <= point[0] <= -45 or 170 <= point[0] <= 180
            assert 10 <= point[1] <= 84
    assert any(point[1] > 72 for line in payload["coastlines"] for point in line)
    assert all(len(value) == 64 for value in payload["source"]["sha256"].values())
    assert payload["source"]["sha256"] == {name: digest for name, (_, digest) in generate_geo.SOURCES.items()}


def test_v3_map_label_catalog_is_complete_and_deterministic() -> None:
    payload = _generated_geo()
    labels = payload["mapLabels"]
    cities = [label for label in labels if label["kind"] == "city"]
    landmarks = [label for label in labels if label["kind"] == "landmark"]
    assert len(payload["cities"]) == 74
    assert len(cities) == 1211
    assert len(landmarks) == 60
    assert {
        country: sum(label["country"] == country for label in cities) for country in EXPECTED_MAP_CITY_COUNTS
    } == EXPECTED_MAP_CITY_COUNTS
    assert [label["collisionRank"] for label in labels] == list(range(len(labels)))
    assert len({label["id"] for label in labels}) == len(labels)
    assert all(label["id"].startswith("ne:") for label in cities)
    assert all(label["id"].startswith("wikidata:Q") for label in landmarks)
    assert all(label["tier"] in generate_geo.MAP_LABEL_TIERS for label in labels)
    assert all(label["country"] in {"CAN", "USA", "MEX"} for label in labels)
    assert all(math.isfinite(label[axis]) for label in labels for axis in ("lon", "lat"))

    by_country = {country: [label for label in landmarks if label["country"] == country] for country in EXPECTED_MAP_CITY_COUNTS}
    for country, country_landmarks in by_country.items():
        assert len(country_landmarks) == 20, country
        assert {
            category: sum(label["category"] == category for label in country_landmarks) for category in generate_geo.LANDMARK_CATEGORIES
        } == {
            "natural": 7,
            "park": 7,
            "cultural": 6,
        }
    assert payload["source"]["landmarks"] == {
        "name": "Wikidata",
        "catalogVersion": "landmarks-v1",
        "license": "CC0 1.0",
        "url": "https://www.wikidata.org/",
        "manifest": "scripts/geo_landmarks.json",
    }


def test_curated_and_hawaiian_map_label_tiers_preserve_product_semantics() -> None:
    payload = _generated_geo()
    city_labels = [label for label in payload["mapLabels"] if label["kind"] == "city"]
    for curated in payload["cities"]:
        matches = [
            label
            for label in city_labels
            if label["country"] == curated["country"]
            and label["name"] == curated["name"]
            and label["lon"] == curated["lon"]
            and label["lat"] == curated["lat"]
        ]
        assert len(matches) == 1, curated["name"]
        spec = next(city for city in CITIES if city_display_name(city) == curated["name"])
        expected_tier = (
            "overview" if city_key(spec) in generate_geo.OVERVIEW_CITY_KEYS else ("primary" if curated["priority"] == 1 else "secondary")
        )
        assert matches[0]["tier"] == expected_tier
    assert {label["name"] for label in city_labels if label["tier"] == "overview"} == {
        "Anchorage",
        "Vancouver",
        "Los Angeles",
        "Chicago",
        "New York",
        "Mexico City",
    }
    hawaii = [label for label in city_labels if label["name"] in EXPECTED_HAWAII_LABELS]
    assert {label["name"] for label in hawaii} == EXPECTED_HAWAII_LABELS
    assert all(label["tier"] == "local" for label in hawaii)
    assert not any(city["name"] in EXPECTED_HAWAII_LABELS for city in payload["cities"])
    assert not {"Atka", "Gambell"} & {label["name"] for label in city_labels}
    duplicate_names = {label["name"] for label in city_labels if sum(other["name"] == label["name"] for other in city_labels) > 1}
    assert duplicate_names
    for name in duplicate_names:
        matches = [label for label in city_labels if label["name"] == name]
        assert len({label["id"] for label in matches}) == len(matches)


def test_forecast_coverage_assets_are_unchanged_by_label_generation() -> None:
    import hashlib

    for filename, expected in EXPECTED_COVERAGE_HASHES.items():
        assert hashlib.sha256((ROOT / "shared" / filename).read_bytes()).hexdigest() == expected


def test_generated_earth_texture_is_correct_size_and_budgeted() -> None:
    path = ROOT / "public" / "geo" / "earth-dark-v2.webp"
    with Image.open(path) as image:
        assert image.size == (4096, 2048)
        assert image.format == "WEBP"
        pixels = np.asarray(image.convert("RGB")).reshape(-1, 3)
    dark, midpoint, highlight = np.percentile(pixels, (10, 75, 95), axis=0)
    assert tuple(dark) == pytest.approx((22, 43, 55), abs=4)
    assert tuple(midpoint) == pytest.approx((42, 66, 73), abs=5)
    assert tuple(highlight) == pytest.approx((66, 87, 94), abs=6)
    assert path.stat().st_size <= 1_500_000
    assert not (ROOT / "public" / "geo" / "earth-dark.webp").exists()


def _coverage_sample(values: Any, lon: float, lat: float) -> int:
    x = round((lon + 145.0) / 100.0 * (values.shape[1] - 1))
    y = round((72.0 - lat) / 62.0 * (values.shape[0] - 1))
    return int(values[y, x])


def test_generated_coastal_coverage_mask_is_binary_and_geographically_sane() -> None:
    path = ROOT / "shared" / "smoke-coverage-mask-v1.png"
    with Image.open(path) as image:
        assert image.size == (1024, 635)
        values = np.asarray(image.convert("L"))
    assert set(np.unique(values)) == {0, 255}
    for lon, lat in ((-104.99, 39.74), (-87.5, 47.7), (-82.36, 23.11), (-125.0, 40.0), (-72.0, 40.0)):
        assert _coverage_sample(values, lon, lat) == 255
    for lon, lat in ((-140.0, 35.0), (-50.0, 35.0)):
        assert _coverage_sample(values, lon, lat) == 0

    detail_path = ROOT / "shared" / "smoke-coverage-mask-v2.png"
    with Image.open(detail_path) as image:
        assert image.size == (2047, 1269)
        detail = np.asarray(image.convert("L"))
    assert set(np.unique(detail)) == {0, 255}
    for lon, lat in ((-104.99, 39.74), (-87.5, 47.7), (-82.36, 23.11), (-125.0, 40.0), (-72.0, 40.0)):
        assert _coverage_sample(detail, lon, lat) == 255
    for lon, lat in ((-140.0, 35.0), (-50.0, 35.0)):
        assert _coverage_sample(detail, lon, lat) == 0

    v8_path = ROOT / "shared" / "smoke-coverage-mask-v3.png"
    with Image.open(v8_path) as image:
        assert image.size == (4093, 2537)
        v8 = np.asarray(image.convert("L"))
    assert set(np.unique(v8)) == {0, 255}
    for lon, lat in ((-104.99, 39.74), (-87.5, 47.7), (-82.36, 23.11), (-125.0, 40.0), (-72.0, 40.0)):
        assert _coverage_sample(v8, lon, lat) == 255
    for lon, lat in ((-140.0, 35.0), (-50.0, 35.0)):
        assert _coverage_sample(v8, lon, lat) == 0


def test_polygon_iterator_handles_polygon_and_multipolygon() -> None:
    polygon = [[[0, 0], [1, 0], [1, 1], [0, 0]]]
    assert list(_polygons({"type": "Polygon", "coordinates": polygon})) == [polygon]
    assert list(_polygons({"type": "MultiPolygon", "coordinates": [polygon, polygon]})) == [polygon, polygon]


def test_ocean_rasterization_preserves_polygon_holes_as_land(monkeypatch: Any) -> None:
    monkeypatch.setattr(generate_geo, "CONTEXT_BOUNDS", (0.0, 0.0, 4.0, 4.0))
    monkeypatch.setattr(generate_geo, "CONTEXT_WIDTH", 5)
    monkeypatch.setattr(generate_geo, "CONTEXT_HEIGHT", 5)
    monkeypatch.setattr(generate_geo, "COASTAL_BUFFER_KM", 100)
    outer = [[-10, -10], [10, -10], [10, 10], [-10, 10], [-10, -10]]
    island = [[1, 1], [3, 1], [3, 3], [1, 3], [1, 1]]
    payload = {"features": [{"geometry": {"type": "Polygon", "coordinates": [outer, island]}}]}
    ocean, x_halo, y_halo = _rasterize_ocean(payload)
    assert ocean[y_halo, x_halo]
    assert not ocean[y_halo + 2, x_halo + 2]


def test_coastal_distance_includes_the_limit_and_accounts_for_latitude(monkeypatch: Any) -> None:
    monkeypatch.setattr(generate_geo, "CONTEXT_WIDTH", 5)
    monkeypatch.setattr(generate_geo, "CONTEXT_HEIGHT", 3)
    monkeypatch.setattr(generate_geo, "COASTAL_BUFFER_KM", 200)
    lon_step = math.degrees(100 / generate_geo.EARTH_RADIUS_KM)
    monkeypatch.setattr(generate_geo, "CONTEXT_BOUNDS", (0.0, -1.0, 4 * lon_step, 1.0))
    ocean = np.ones((3, 5), dtype=bool)
    ocean[:, 0] = False
    equatorial = _coastal_coverage(ocean, 0, 0)
    assert tuple(equatorial[1]) == (True, True, True, True, False)

    monkeypatch.setattr(generate_geo, "CONTEXT_BOUNDS", (0.0, -1.0, 4.0, 1.0))
    monkeypatch.setattr(generate_geo, "COASTAL_BUFFER_KM", 120)
    equatorial = _coastal_coverage(ocean, 0, 0)
    assert tuple(equatorial[1]) == (True, True, True, False, False)

    monkeypatch.setattr(generate_geo, "CONTEXT_BOUNDS", (0.0, 59.0, 4.0, 61.0))
    high_latitude = _coastal_coverage(ocean, 0, 0)
    assert tuple(high_latitude[1]) == (True, True, True, True, False)


def test_allowlist_keys_and_display_names_are_unique() -> None:
    keys = [city_key(city) for city in CITIES]
    displays = [city_display_name(city) for city in CITIES]
    assert len(CITIES) == EXPECTED_CITY_COUNT
    assert len(keys) == len(set(keys))
    assert len(displays) == len(set(displays))
    assert all(len(key) == 3 and all(part for part in key) for key in keys)


def test_city_key_and_display_name_helpers_handle_valid_and_invalid_inputs() -> None:
    portland_or = {
        "nameascii": "Portland",
        "country": "USA",
        "admin1": "Oregon",
        "display": "Portland, OR",
    }
    assert city_key(portland_or) == ("Portland", "USA", "Oregon")
    assert city_display_name(portland_or) == "Portland, OR"
    assert city_display_name({"nameascii": "Boise"}) == "Boise"
    assert city_display_name({"nameascii": "Boise", "display": ""}) == "Boise"
    assert city_display_name({"nameascii": "Boise", "display": None}) == "Boise"
    with pytest.raises(KeyError):
        city_key({"nameascii": "Boise", "country": "USA"})
    with pytest.raises(KeyError):
        city_key({"country": "USA", "admin1": "Idaho"})
    with pytest.raises(KeyError):
        city_display_name({})


def test_kept_extra_cities_remain_and_singleton_admin1_stay_singleton() -> None:
    generated_names = {city["name"] for city in _generated_geo()["cities"]}
    allowlist_names = {city_display_name(city) for city in CITIES}
    assert generated_names >= KEPT_EXTRA_DISPLAY_NAMES
    assert allowlist_names >= KEPT_EXTRA_DISPLAY_NAMES
    canadian = _admin1_counts("CAN")
    american = _admin1_counts("USA")
    mexican = _admin1_counts("MEX")
    assert canadian == {admin1: CAN_ADMIN1_CITY_COUNTS.get(admin1, 1) for admin1 in CANADIAN_ADMIN1}
    assert american == {admin1: US_ADMIN1_CITY_COUNTS.get(admin1, 1) for admin1 in US_ADMIN1}
    assert sum(mexican.values()) == len(MEXICO_CITY_NAMES)
    assert {city_display_name(city) for city in CITIES if city["country"] == "MEX"} == MEXICO_CITY_NAMES


def test_phoenix_is_only_new_priority_1_city() -> None:
    generated = {city["name"]: city for city in _generated_geo()["cities"]}
    assert generated["Phoenix"]["priority"] == 1
    assert generated["Phoenix"]["country"] == "USA"
    new_priority_1 = [
        city_display_name(city) for city in CITIES if city["nameascii"] not in ORIGINAL_CITY_PRIORITIES and city["priority"] == 1
    ]
    assert new_priority_1 == ["Phoenix"]
    for city in CITIES:
        if city["nameascii"] in ORIGINAL_CITY_PRIORITIES:
            assert city["priority"] == ORIGINAL_CITY_PRIORITIES[city["nameascii"]]
        elif city["nameascii"] != "Phoenix":
            assert city["priority"] == 2
        generated_city = generated[city_display_name(city)]
        assert generated_city["priority"] == city["priority"]


def test_mobile_set_includes_arctic_cities_after_zoom() -> None:
    generated = _generated_geo()["cities"]
    allowlist_mobile = {city_display_name(city) for city in CITIES if city.get("mobile")}
    generated_mobile = {city["name"] for city in generated if city["mobile"]}
    assert allowlist_mobile == MOBILE_CITY_NAMES
    assert generated_mobile == MOBILE_CITY_NAMES
    assert all(isinstance(city["mobile"], bool) for city in generated)
    assert not any(city.get("mobile") for city in CITIES if city_display_name(city) not in MOBILE_CITY_NAMES)


def test_public_json_city_objects_do_not_leak_internal_fields() -> None:
    cities = _generated_geo()["cities"]
    assert len(cities) == EXPECTED_CITY_COUNT
    leaked = {"admin1", "nameascii", "display", "adm1name", "adm0_a3"}
    for city in cities:
        assert set(city) == PUBLIC_CITY_FIELDS
        assert leaked.isdisjoint(city)
        assert isinstance(city["name"], str) and city["name"]
        assert city["country"] in {"CAN", "USA", "MEX"}
        assert city["priority"] in {1, 2}
        assert isinstance(city["lon"], (int, float))
        assert isinstance(city["lat"], (int, float))


def test_homonym_display_names_are_disambiguated() -> None:
    generated_names = {city["name"] for city in _generated_geo()["cities"]}
    allowlist_names = {city_display_name(city) for city in CITIES}
    assert HOMONYM_DISPLAY_NAMES <= generated_names
    assert HOMONYM_DISPLAY_NAMES <= allowlist_names
    assert "Portland" not in generated_names
    assert "Charleston" not in generated_names
    portlands = [city for city in CITIES if city["nameascii"] == "Portland"]
    charlestons = [city for city in CITIES if city["nameascii"] == "Charleston"]
    assert {city["admin1"] for city in portlands} == {"Maine", "Oregon"}
    assert {city["admin1"] for city in charlestons} == {"South Carolina", "West Virginia"}
    assert len({city_key(city) for city in portlands}) == 2
    assert len({city_key(city) for city in charlestons}) == 2
    assert {city_display_name(city) for city in portlands} == {"Portland, ME", "Portland, OR"}
    assert {city_display_name(city) for city in charlestons} == {"Charleston, SC", "Charleston, WV"}


def test_hawaii_is_absent_from_allowlist_and_generated_json() -> None:
    generated = _generated_geo()["cities"]
    assert not any(city["admin1"] == "Hawaii" for city in CITIES)
    assert not any(city["nameascii"] == "Honolulu" for city in CITIES)
    assert all("Hawaii" not in value for city in CITIES for value in city.values() if isinstance(value, str))
    assert "Honolulu" not in {city["name"] for city in generated}
    assert not any("Hawaii" in str(value) for city in generated for value in city.values())


def test_cities_rejects_duplicate_keys_and_display_names(monkeypatch: pytest.MonkeyPatch) -> None:
    duplicate_key = [
        {"nameascii": "Portland", "country": "USA", "admin1": "Oregon", "priority": 2},
        {"nameascii": "Portland", "country": "USA", "admin1": "Oregon", "priority": 2, "display": "Portland, OR"},
    ]
    monkeypatch.setattr("scripts.generate_geo.CITIES", duplicate_key)
    with pytest.raises(RuntimeError, match="city allowlist keys must be unique"):
        _cities({"features": []})

    duplicate_display = [
        {"nameascii": "Portland", "country": "USA", "admin1": "Maine", "priority": 2},
        {"nameascii": "Portland", "country": "USA", "admin1": "Oregon", "priority": 2},
    ]
    monkeypatch.setattr("scripts.generate_geo.CITIES", duplicate_display)
    with pytest.raises(RuntimeError, match="city display names must be unique"):
        _cities({"features": []})


def test_cities_matches_only_nameascii_country_admin1(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = {
        "nameascii": "Portland",
        "country": "USA",
        "admin1": "Oregon",
        "priority": 2,
        "display": "Portland, OR",
    }
    monkeypatch.setattr("scripts.generate_geo.CITIES", [spec])
    with pytest.raises(RuntimeError, match="missing expected cities"):
        _cities({"features": [_city_feature("Portland", "USA", "Maine", -70.2553, 43.6615)]})
    with pytest.raises(RuntimeError, match="missing expected cities"):
        _cities({"features": [_city_feature("Portland", "CAN", "Oregon", -122.6742, 45.5202)]})
    selected = _cities({"features": [_city_feature("Portland", "USA", "Oregon", -122.6742, 45.5202)]})
    assert selected == [
        {
            "name": "Portland, OR",
            "searchName": "Portland",
            "region": "Oregon",
            "country": "USA",
            "lon": -122.6742,
            "lat": 45.5202,
            "priority": 2,
            "mobile": False,
        }
    ]
    assert "admin1" not in selected[0]


def test_cities_skips_invalid_or_out_of_bounds_coordinates(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = {"nameascii": "Boston", "country": "USA", "admin1": "Massachusetts", "priority": 2}
    monkeypatch.setattr("scripts.generate_geo.CITIES", [spec])
    invalid = _city_feature("Boston", "USA", "Massachusetts", -71.057, 42.361)
    invalid["geometry"]["coordinates"] = [None, 42.361]
    out_of_bounds = _city_feature("Boston", "USA", "Massachusetts", 0.0, 51.5)
    non_numeric = _city_feature("Boston", "USA", "Massachusetts", -71.057, 42.361)
    non_numeric["geometry"]["coordinates"] = ["-71.057", 42.361]
    cases: list[dict[str, Any]] = [
        {"features": [invalid]},
        {"features": [out_of_bounds]},
        {"features": [non_numeric]},
        {"features": []},
    ]
    for payload in cases:
        with pytest.raises(RuntimeError, match="missing expected cities"):
            _cities(payload)
