"""Integration test of the whole pipeline against mock data, plus unit tests of
the transformation rules.

Expected rows from the fixtures:
  2021 -> 4 rows (6 records: one has no countryCode and is dropped, and country
                  code 5 appears twice and is de-duplicated). One of the 4 is the
                  "World" rollup, flagged as a region.
  2022 -> 3 rows
"""

from __future__ import annotations

import hashlib
import json

import pytest

from footprint import extract, load, pipeline, transform
from footprint.storage import LocalS3, ObjectExistsError

EXPECTED_ROWS = {2021: 4, 2022: 3}
TOTAL_ROWS = sum(EXPECTED_ROWS.values())


# --------------------------------------------------------------------------
# 1. End to end
# --------------------------------------------------------------------------

def test_end_to_end(settings, fake_client):
    summary = pipeline.run(settings, client=fake_client)

    assert summary["succeeded"] == 2
    assert summary["failed"] == 0
    assert summary["rows_loaded"] == TOTAL_ROWS

    # Raw landing zone: the payload and its manifest, partitioned by year.
    raw_keys = LocalS3(settings.raw_dir).list_keys()
    for year in EXPECTED_ROWS:
        assert any(f"year={year}" in k and k.endswith("data.json") for k in raw_keys)
        assert any(f"year={year}" in k and k.endswith("_manifest.json") for k in raw_keys)

    # Curated zone: one Parquet file per year.
    for year in EXPECTED_ROWS:
        assert (settings.curated_dir / f"carbon_footprint/year={year}/data.parquet").is_file()

    # Warehouse: the rows are queryable and the numbers survived the round trip.
    rows = load.query(
        settings.duckdb_path,
        f"SELECT year, count(*), count(carbon_gha_per_person) FROM {load.TABLE} "
        "GROUP BY year ORDER BY year",
    )
    assert rows == [(2021, 4, 3), (2022, 3, 2)]  # one null-carbon country per year

    # Regional rollups are flagged, so a country-level chart can exclude them.
    regions = load.query(
        settings.duckdb_path,
        f"SELECT country_name FROM {load.TABLE} WHERE is_region ORDER BY year",
    )
    assert [r[0] for r in regions] == ["World"]

    carbon = load.query(
        settings.duckdb_path,
        f"SELECT carbon_gha_per_person FROM {load.TABLE} WHERE year = 2021 AND country_code = 5",
    )
    assert carbon[0][0] == pytest.approx(4.6)  # the de-duplicated record, last one wins


def test_pipeline_discovers_years_from_the_api(settings, fake_client):
    pipeline.run(settings, client=fake_client)
    # 2008 and 2009 are offered by the API but are before start_year=2010.
    assert sorted(year for year, _ in fake_client.calls) == [2021, 2022]


# --------------------------------------------------------------------------
# 2. Transformation rules (pure function, no I/O)
# --------------------------------------------------------------------------

def test_normalize_rules(fake_client):
    raw = fake_client.get_data_for_year(2021)
    rows = transform.normalize(raw, source_key="raw/key.json", run_id="run-1")

    assert len(rows) == 4  # dropped the code-less record, de-duplicated country 5
    by_code = {row["country_code"]: row for row in rows}

    # A row with no ISO code is a regional rollup, not a country.
    assert by_code[5001]["country_name"] == "World"
    assert by_code[5001]["is_region"] is True
    assert all(by_code[code]["is_region"] is False for code in (3, 5, 21))

    # Renames and derived column.
    testland = by_code[5]
    assert testland["carbon_gha_per_person"] == pytest.approx(4.6)
    assert testland["ef_total_gha_per_person"] == pytest.approx(9.2)
    assert testland["carbon_share_pct"] == pytest.approx(50.0)
    assert testland["country_name"] == "Testland"  # whitespace stripped

    # String-typed numbers are cast; an empty string becomes NULL, not 0.0.
    stringia = by_code[21]
    assert stringia["year"] == 2021 and isinstance(stringia["year"], int)
    assert stringia["carbon_gha_per_person"] == pytest.approx(1.5)
    assert stringia["fishing_ground"] is None

    # A missing carbon value stays NULL rather than being filled with 0.
    nullavia = by_code[3]
    assert nullavia["carbon_gha_per_person"] is None
    assert nullavia["carbon_share_pct"] is None
    assert nullavia["ef_total_gha_per_person"] == pytest.approx(2.0)

    # Lineage columns are stamped on every row.
    assert all(row["source_key"] == "raw/key.json" and row["run_id"] == "run-1" for row in rows)


def test_normalize_handles_an_empty_payload():
    assert transform.normalize([], source_key="k", run_id="r") == []


# --------------------------------------------------------------------------
# 3. Idempotency
# --------------------------------------------------------------------------

def test_pipeline_is_idempotent(settings, fake_client):
    first = pipeline.run(settings, client=fake_client)
    second = pipeline.run(settings, client=fake_client)

    assert second["failed"] == 0
    assert first["run_id"] != second["run_id"]

    # The target table holds each year once, not twice.
    total = load.query(settings.duckdb_path, f"SELECT count(*) FROM {load.TABLE}")[0][0]
    assert total == TOTAL_ROWS

    # Only the newest run's rows remain: the year was replaced, not appended to.
    run_ids = load.query(settings.duckdb_path, f"SELECT DISTINCT run_id FROM {load.TABLE}")
    assert [r[0] for r in run_ids] == [second["run_id"]]

    # The raw zone kept both runs: it is immutable and versioned.
    raw_2021 = [k for k in LocalS3(settings.raw_dir).list_keys() if "year=2021" in k]
    assert len([k for k in raw_2021 if k.endswith("data.json")]) == 2


def test_raw_bucket_refuses_to_overwrite(tmp_path):
    bucket = LocalS3(tmp_path, name="raw", immutable=True)
    bucket.put_json("a/b.json", {"x": 1})
    with pytest.raises(ObjectExistsError):
        bucket.put_json("a/b.json", {"x": 2})


# --------------------------------------------------------------------------
# 4. Manifest
# --------------------------------------------------------------------------

def test_manifest_matches_the_raw_payload(settings, fake_client):
    bucket = LocalS3(settings.raw_dir, name="raw", immutable=True)
    result = extract.lambda_handler(
        {"year": 2021, "record": "EFCpc", "run_id": "run-1"},
        client=fake_client,
        bucket=bucket,
    )

    prefix = result["key"].rsplit("/", 1)[0]
    stored = bucket.get_json(result["key"])
    manifest = bucket.get_json(f"{prefix}/_manifest.json")

    assert manifest["row_count"] == len(stored) == 6  # raw is untouched: nothing dropped yet
    assert manifest["year"] == 2021
    assert manifest["run_id"] == "run-1"
    assert manifest["source_url"] == fake_client.data_url(2021, "EFCpc")
    expected = hashlib.sha256(json.dumps(stored, ensure_ascii=False).encode("utf-8")).hexdigest()
    assert manifest["sha256"] == expected


# --------------------------------------------------------------------------
# 5. Failure handling
# --------------------------------------------------------------------------

def test_a_failing_year_does_not_stop_the_others(settings):
    from conftest import FakeApiClient

    client = FakeApiClient(years=[2021, 2022, 2099])  # no fixture for 2099
    summary = pipeline.run(settings, client=client)

    assert summary["succeeded"] == 2
    assert summary["failed"] == 1
    failed = [r for r in summary["years"] if r["status"] == "FAILED"]
    assert failed[0]["year"] == 2099
    assert load.query(settings.duckdb_path, f"SELECT count(*) FROM {load.TABLE}")[0][0] == TOTAL_ROWS


# --------------------------------------------------------------------------
# Optional: hits the real API. Run with `pytest -m live`.
# --------------------------------------------------------------------------

@pytest.mark.live
def test_live_api_returns_a_full_year():
    from footprint.api_client import FootprintApiClient
    from footprint.config import get_settings

    real = get_settings()
    client = FootprintApiClient(real.api_key, real.api_base_url, real.api_username)
    rows = client.get_data_for_year(2021, real.record_code)

    assert len(rows) > 100
    assert {"year", "countryCode", "carbon", "value"} <= set(rows[0])
