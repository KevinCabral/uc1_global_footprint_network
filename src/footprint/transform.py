"""Lambda Transform: triggered by an S3 event when a raw object lands.

Reads the raw JSON, normalizes it, and writes one Parquet file per year to the
curated zone. The curated zone is derived data, so unlike the raw zone it may be
overwritten: re-running a year replaces its file.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import duckdb

from .storage import LocalS3

log = logging.getLogger(__name__)

# The analytics schema. Owned here because this module produces it; load.py
# imports it so the duckdb table and the Parquet file can never drift apart.
CURATED_SCHEMA: list[tuple[str, str]] = [
    ("year", "INTEGER"),
    ("country_code", "INTEGER"),
    ("country_name", "VARCHAR"),
    ("short_name", "VARCHAR"),
    ("iso2", "VARCHAR"),
    ("is_region", "BOOLEAN"),
    ("record", "VARCHAR"),
    ("crop_land", "DOUBLE"),
    ("grazing_land", "DOUBLE"),
    ("forest_land", "DOUBLE"),
    ("fishing_ground", "DOUBLE"),
    ("builtup_land", "DOUBLE"),
    ("carbon_gha_per_person", "DOUBLE"),
    ("ef_total_gha_per_person", "DOUBLE"),
    ("carbon_share_pct", "DOUBLE"),
    ("data_quality_score", "VARCHAR"),
    ("source_key", "VARCHAR"),
    ("run_id", "VARCHAR"),
    ("ingested_at", "TIMESTAMP"),
]

CURATED_COLUMNS = [name for name, _ in CURATED_SCHEMA]

# API field -> curated column, for the plain numeric columns.
_LAND_COLUMNS = {
    "cropLand": "crop_land",
    "grazingLand": "grazing_land",
    "forestLand": "forest_land",
    "fishingGround": "fishing_ground",
    "builtupLand": "builtup_land",
}


def _to_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _clean_str(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize(rows: list[dict], source_key: str, run_id: str, ingested_at=None) -> list[dict]:
    """Pure function: raw API records in, curated rows out.

    Rules:
      - rename to snake_case analytics names; `carbon` is the carbon footprint
        in global hectares per person, `value` the total Ecological Footprint;
      - cast everything, tolerating strings and empty values from the API;
      - derive carbon_share_pct = carbon / total * 100;
      - drop records with no year or no country code (they are unusable);
      - de-duplicate on (country_code, year, record), last record wins;
      - keep carbon nulls as NULL. About 33 of 153 countries per year have no
        carbon value, and 2024 has 127. Filling those with 0 would turn "unknown"
        into "zero emissions" on the dashboard;
      - flag regional aggregates. The API returns 126 countries plus 27 rollups
        ("Europe", "Oceania", "World") in the same list, with no ISO code and a
        country_code above 1000. They are kept, because regional totals are useful,
        but flagged so a country-level chart can exclude them instead of summing
        each country twice.
    """
    stamp = ingested_at or datetime.now(timezone.utc)
    deduped: dict[tuple, dict] = {}

    for raw in rows:
        year = _to_int(raw.get("year"))
        country_code = _to_int(raw.get("countryCode"))
        if year is None or country_code is None:
            log.warning("dropping record with no year/countryCode: %s", raw)
            continue

        record = _clean_str(raw.get("record")) or ""
        iso2 = _clean_str(raw.get("isoa2"))
        carbon = _to_float(raw.get("carbon"))
        total = _to_float(raw.get("value"))
        share = round(carbon / total * 100, 4) if carbon is not None and total else None

        row = {
            "year": year,
            "country_code": country_code,
            "country_name": _clean_str(raw.get("countryName")),
            "short_name": _clean_str(raw.get("shortName")),
            "iso2": iso2,
            "is_region": iso2 is None,
            "record": record,
            "carbon_gha_per_person": carbon,
            "ef_total_gha_per_person": total,
            "carbon_share_pct": share,
            "data_quality_score": _clean_str(raw.get("score")),
            "source_key": source_key,
            "run_id": run_id,
            "ingested_at": stamp,
        }
        for api_field, column in _LAND_COLUMNS.items():
            row[column] = _to_float(raw.get(api_field))

        deduped[(country_code, year, record)] = row

    return sorted(deduped.values(), key=lambda r: (r["year"], r["country_code"]))


def write_parquet(rows: list[dict], destination) -> str:
    """Write curated rows to Parquet using duckdb (no pandas / pyarrow needed)."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    columns = ", ".join(f"{name} {sql_type}" for name, sql_type in CURATED_SCHEMA)
    placeholders = ", ".join("?" for _ in CURATED_SCHEMA)

    con = duckdb.connect()
    try:
        con.execute(f"CREATE TABLE staged ({columns})")
        if rows:
            con.executemany(
                f"INSERT INTO staged VALUES ({placeholders})",
                [tuple(row[name] for name in CURATED_COLUMNS) for row in rows],
            )
        escaped = str(destination).replace("'", "''")
        con.execute(f"COPY staged TO '{escaped}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    finally:
        con.close()
    return str(destination)


def lambda_handler(event: dict, context=None, *, raw_bucket: LocalS3, curated_bucket: LocalS3) -> dict:
    """Entry point. `event` is a real S3 put-event, so this would run in Lambda unchanged."""
    key = event["Records"][0]["s3"]["object"]["key"]
    prefix = key.rsplit("/", 1)[0]

    rows = raw_bucket.get_json(key)
    manifest = raw_bucket.get_json(f"{prefix}/_manifest.json")
    year, run_id = manifest["year"], manifest["run_id"]

    curated = normalize(rows, source_key=key, run_id=run_id)
    curated_key = f"carbon_footprint/year={year}/data.parquet"
    path = write_parquet(curated, curated_bucket.local_path(curated_key))

    log.info("transform: year=%s rows=%s -> %s", year, len(curated), curated_key)
    return {
        "year": year,
        "run_id": run_id,
        "curated_key": curated_key,
        "curated_path": path,
        "row_count": len(curated),
    }
