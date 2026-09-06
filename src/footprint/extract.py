"""Lambda Extract: one year of data per invocation.

Calls the API, writes the untouched payload to the raw landing zone, and writes a
manifest beside it. The payload is stored exactly as received: the raw zone is the
replay point, so no cleaning happens here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone

from .api_client import FootprintApiClient
from .storage import LocalS3

log = logging.getLogger(__name__)


def new_run_id() -> str:
    """Unique id for one pipeline run, e.g. 20260906T120000Z-a3f9c1.

    Sortable timestamp plus a random suffix. The suffix matters: the raw zone is
    immutable, so two runs starting in the same second would otherwise collide on
    the same key.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


def raw_prefix(record: str, year: int, run_id: str) -> str:
    """S3 key prefix. Partitioned by year, versioned by run so nothing is overwritten."""
    return f"footprintnetwork/{record}/year={year}/run_id={run_id}"


def lambda_handler(event: dict, context=None, *, client: FootprintApiClient, bucket: LocalS3) -> dict:
    """Entry point. `event` = {"year": 2021, "record": "EFCpc", "run_id": "..."}.

    `client` and `bucket` are injected rather than built here so the tests can run
    without network or disk surprises. A real Lambda would build them from the
    environment at module scope.
    """
    year = int(event["year"])
    record = event.get("record", "EFCpc")
    run_id = event.get("run_id") or new_run_id()

    rows = client.get_data_for_year(year, record)
    payload = json.dumps(rows, ensure_ascii=False).encode("utf-8")

    prefix = raw_prefix(record, year, run_id)
    data_key = f"{prefix}/data.json"
    bucket.put_json(data_key, rows)

    manifest = {
        "run_id": run_id,
        "year": year,
        "record": record,
        "source_url": client.data_url(year, record),
        "row_count": len(rows),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }
    bucket.put_json(f"{prefix}/_manifest.json", manifest)

    log.info("extract: year=%s rows=%s key=%s", year, len(rows), data_key)
    return {"bucket": bucket.name, "key": data_key, "row_count": len(rows), "run_id": run_id}
