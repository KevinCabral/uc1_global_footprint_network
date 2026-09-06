"""Orchestration: the Step Functions workflow from the diagram.

    1. discover the years to load
    2. for each year: Lambda Extract -> S3 event -> Lambda Transform -> load
    3. collect a per-year status

Years are processed sequentially. In AWS this is a Map state with concurrency 3;
here a loop keeps the code readable, and each year is independent either way.
"""

from __future__ import annotations

import logging

from . import extract, load, transform
from .api_client import FootprintApiClient
from .config import Settings
from .storage import LocalS3

log = logging.getLogger(__name__)


def s3_put_event(bucket: str, key: str) -> dict:
    """The S3 Event Trigger arrow, made explicit.

    Same shape as the event S3 sends to Lambda, so transform.lambda_handler is
    exercised locally exactly as it would be in AWS.
    """
    return {"Records": [{"eventName": "ObjectCreated:Put",
                         "s3": {"bucket": {"name": bucket}, "object": {"key": key}}}]}


def discover_years(client: FootprintApiClient, start_year: int) -> list[int]:
    """Years available from the API, from `start_year` onwards, oldest first."""
    return sorted(year for year in client.get_years() if year >= start_year)


def _buckets(settings: Settings) -> tuple[LocalS3, LocalS3]:
    raw = LocalS3(settings.raw_dir, name="footprint-raw", immutable=True)
    curated = LocalS3(settings.curated_dir, name="footprint-curated")
    return raw, curated


def run_year(year: int, client: FootprintApiClient, settings: Settings, run_id: str) -> dict:
    """One year end to end. Returns a status row; never raises past the caller's loop."""
    raw_bucket, curated_bucket = _buckets(settings)

    extracted = extract.lambda_handler(
        {"year": year, "record": settings.record_code, "run_id": run_id},
        client=client,
        bucket=raw_bucket,
    )

    event = s3_put_event(raw_bucket.name, extracted["key"])
    transformed = transform.lambda_handler(
        event, raw_bucket=raw_bucket, curated_bucket=curated_bucket
    )

    loaded = load.load_year(settings.duckdb_path, transformed["curated_path"], year)

    return {
        "year": year,
        "status": "SUCCEEDED",
        "raw_key": extracted["key"],
        "raw_rows": extracted["row_count"],
        "curated_key": transformed["curated_key"],
        "rows_loaded": loaded["rows_loaded"],
    }


def run(settings: Settings, client: FootprintApiClient | None = None,
        years: list[int] | None = None) -> dict:
    """Run the pipeline. `client` is injectable so tests can run without network."""
    client = client or FootprintApiClient(
        api_key=settings.api_key,
        base_url=settings.api_base_url,
        username=settings.api_username,
    )
    run_id = extract.new_run_id()
    years = years or discover_years(client, settings.start_year)
    log.info("run %s: %s year(s) to process: %s", run_id, len(years), years)

    results = []
    for year in years:
        try:
            results.append(run_year(year, client, settings, run_id))
        except Exception as exc:  # one bad year must not sink the backfill
            log.error("year %s failed: %s", year, exc)
            results.append({"year": year, "status": "FAILED", "error": str(exc)})

    succeeded = [r for r in results if r["status"] == "SUCCEEDED"]
    return {
        "run_id": run_id,
        "years": results,
        "succeeded": len(succeeded),
        "failed": len(results) - len(succeeded),
        "rows_loaded": sum(r.get("rows_loaded", 0) for r in succeeded),
    }
