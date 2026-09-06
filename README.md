# Use Case 1 — Step 3: Ingestion Code

Local implementation of the ingestion pipeline designed in
`AWS Architecture for Footprint.drawio.png`. Every AWS service is simulated on
disk, so the whole pipeline runs with `python main.py run` and no AWS account.

Carbon footprint by country, from the Global Footprint Network API, 2010 → latest.

## Quick start

```powershell
..\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

python main.py run                 # full backfill, 2010 -> latest (~15 s, 16 years)
python main.py run --year 2021     # a single year
python main.py query               # what landed in duckdb
python main.py sql "SELECT ..."    # your own SQL against the warehouse
python main.py api data --year 2021  # the raw API response, before transformation
pytest -v                          # 8 tests, no network
```

**Other docs:** [RUNBOOK.md](RUNBOOK.md) for resetting the data, querying duckdb
and reading test output. [SETUP.md](SETUP.md) for installing on a fresh machine
or preparing the project to be shared.

## How the code maps to the architecture

| Diagram box | Module | Simulated by |
|---|---|---|
| Secrets Manager | `src/footprint/config.py` | `.env` file, read only by `get_api_key()` |
| Global Footprint Network API | `src/footprint/api_client.py` | real HTTP calls (Basic auth) |
| S3 raw / landing zone | `src/footprint/storage.py` | `data/raw/`, write-once |
| Lambda Extract (1 year per invocation) | `src/footprint/extract.py` | `lambda_handler({"year": ...})` |
| S3 Event Trigger | `pipeline.s3_put_event()` | a real S3 event dict, built in-process |
| Lambda Transform | `src/footprint/transform.py` | `lambda_handler(s3_event)` |
| S3 curated zone (Parquet) | `src/footprint/storage.py` | `data/curated/`, ZSTD Parquet |
| Snowflake (MERGE → curated) | `src/footprint/load.py` | duckdb `data/warehouse/footprint.duckdb` |
| Step Functions | `src/footprint/pipeline.py` | discover years, loop, per-year status |
| CloudWatch | stdlib `logging` | console |

The handlers keep the AWS signature (`lambda_handler(event, context)`) and the
transform accepts a genuine S3 put-event, so moving to AWS is a matter of swapping
`LocalS3` for boto3 and `.env` for Secrets Manager — not a rewrite.

## Data flow

```
API  /v1/data/all/{year}/EFCpc          153 records, ~50 KB per year
 |
 v  extract.py                          untouched payload + _manifest.json
data/raw/footprintnetwork/EFCpc/year=2021/run_id=20260906T164822Z-2c0e5e/
 |                                      immutable, one folder per run
 v  transform.py (S3 event)             normalize + Parquet
data/curated/carbon_footprint/year=2021/data.parquet
 |                                      overwritable: derived data
 v  load.py                             DELETE year + INSERT, in one transaction
duckdb: curated.carbon_footprint
```

`EFCpc` is "Ecological Footprint per person". Its `carbon` column is the carbon
footprint in global hectares per person — the measure the dashboard needs. `value`
is the total footprint, so `carbon_share_pct = carbon / value * 100` shows how much
of a country's footprint is carbon.

## Idempotency

Re-running a year must not duplicate it. Three things guarantee that:

1. **`run_id`** — timestamp plus a random suffix, unique per run. Without the
   suffix, two runs in the same second collide (a test caught exactly this).
2. **Raw is immutable** — each run writes to its own `run_id=` prefix and
   `LocalS3(immutable=True)` refuses to overwrite. Every version is replayable.
3. **The load is a partition overwrite** — `DELETE FROM ... WHERE year = ?` then
   `INSERT`, inside one transaction. A year's Parquet file is the full truth for
   that year, so this is equivalent to the Snowflake `MERGE` in the diagram.

`_manifest.json` sits next to every raw payload with `run_id`, `row_count`,
`sha256` and the source URL — lineage, and enough to detect a truncated download.

## What the data actually looks like

Facts from the live API, worth knowing before building a dashboard on it:

- **153 records per year, but only 126 are countries.** The other 27 are rollups
  (`Europe`, `Oceania`, `World`) with no ISO code. They are kept and flagged with
  `is_region`, because summing all 153 rows would count every country twice.
- **Country-level carbon data ends at 2023.** 2024 and 2025 return rows, but
  every country has `carbon = null`; only the regional rollups have values.
  The dashboard's usable range is 2010–2023.
- **~93 of 126 countries have carbon values** in any given year. Those nulls are
  kept as NULL rather than filled with 0 — "unknown" is not "zero emissions".
- The API's own quality grade is carried through as `data_quality_score` (`3A`,
  `2B`, …).

## Not built — the productionisation backlog

Deliberately out of scope here, but these are the gaps between this and production:

- **Retry / DLQ** — the API client retries 429/5xx three times, but there is no
  dead-letter queue. In AWS: Step Functions `Retry`/`Catch` plus the SQS DLQ.
- **Data quality gates** — nothing currently blocks a bad year from being
  published. A completeness check would have stopped 2024 from loading with zero
  usable carbon values.
- **Observability** — only console logging. The diagram's CloudWatch → SNS →
  MongoDB log store has no local counterpart.
- **Watermark** — every run re-fetches every year. A last-successful-year
  watermark would make the daily schedule cheap; the backfill stays as-is.
- **Schema contract** — a test asserting the raw payload's keys would catch an
  API change before the transform silently produces nulls.
- **Snowflake specifics** — external stage on the curated bucket, Snowpipe
  auto-ingest, Stream + Task running the `MERGE`, clustering on `year`.
- **IaC / CI-CD / rotation** — CDK stack, deployment pipeline, and rotating the
  API key that currently lives in `.env`.
- **PII** — the transform box in the diagram mentions PII. This dataset has none;
  it is country-level aggregates only.
- **Concurrency** — years are processed sequentially. They are independent, so
  the diagram's `concurrency 3` Map state is a drop-in change.
